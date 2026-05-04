"""
Wodify mobile API client.

Authenticates against app-clientapp.wodify.com (the OutSystems mobile backend)
using a plain requests.Session — no browser required.

Login flow (discovered via mitmproxy capture of the iOS app):
  1. GET /WodifyClient/ to initialise session cookies (osVisit, nr2W_Theme_UI, etc.)
  2. GET moduleversioninfo to fetch the current module version token
  3. POST ActionPrepare_Login with email + password → user params + updated cookies
  4. POST ActionDo_Login → finalise authenticated session
  5. Discover current apiVersions from JS chunks (rotates on every Wodify update)
  6. For each day: POST DataActionGetAllWorkoutData with user params
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta
from urllib.parse import unquote

import requests

logger = logging.getLogger(__name__)

_BASE = "https://app-clientapp.wodify.com/WodifyClient"
_MODULE_VERSION_URL = f"{_BASE}/moduleservices/moduleversioninfo"
_MODULE_INFO_URL = f"{_BASE}/moduleservices/moduleinfo"
_PREPARE_LOGIN_URL = f"{_BASE}/screenservices/WodifyClient/ActionPrepare_Login"
_DO_LOGIN_URL = f"{_BASE}/screenservices/WodifyClient/ActionDo_Login"
_WOD_DATA_URL = (
    f"{_BASE}/screenservices/WodifyClient_DataFetch_WB"
    "/WOD_Flow/GetAllWorkoutData_WB/DataActionGetAllWorkoutData"
)

# Fixed device UUID — the server uses it for analytics, not auth
_DEVICE_UUID = "D379F005-2CA8-5C4F-B4EE-3889E2F60D93"
_USER_AGENT = (
    "Mozilla/5.0 (iPad; CPU OS 18_7 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Mobile/15E148 OutSystemsApp v.225.1.8"
)

# OutSystems anonymous CSRF token — used when no session cookie is set.
_ANON_CSRF_TOKEN = "T6C+9iB49TLra4jEsMeSckDMNhQ="

# Fallback apiVersions — used only if dynamic discovery fails.
_FALLBACK_API_VERSIONS = {
    "Prepare_Login": "Y5lcETfFR6OMjcuV1v837g",
    "Do_Login": "gdwtSFxlPeK3R0kvRCgkuw",
    "GetAllWorkoutData": "oUtY6BduyoWB9hUTWxNqFw",
}

_CHUNK_ACTIONS = {
    "WodifyClient.controller__": ("Prepare_Login", "Do_Login"),
    "GetAllWorkoutData_WB.mvc__": ("GetAllWorkoutData",),
}

_ACTION_ALIASES = {
    "DataActionGetAllWorkoutData": "GetAllWorkoutData",
}


# ── Internal helpers ────────────────────────────────────────────────────────────

def _csrf(session: requests.Session) -> str:
    """Extract the current CSRF token from the nr2W_Theme_UI session cookie.

    Falls back to the anonymous CSRF token when no session cookie exists
    (OutSystems sets cookies lazily after the first API call).
    """
    for cookie in session.cookies:
        if cookie.name == "nr2W_Theme_UI":
            for part in unquote(cookie.value).split(";"):
                if part.strip().startswith("crf="):
                    return part.strip()[4:]
    return _ANON_CSRF_TOKEN


def _headers(session: requests.Session) -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json; charset=UTF-8",
        "outsystems-device-uuid": _DEVICE_UUID,
        "accept-language": "en-AU,en;q=0.9",
        "user-agent": _USER_AGENT,
        "x-csrftoken": _csrf(session),
    }


def _module_version(session: requests.Session) -> str:
    """Fetch the current OutSystems module version token."""
    r = session.get(
        f"{_MODULE_VERSION_URL}?{int(time.time() * 1000)}",
        headers=_headers(session),
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("versionToken", "")


def _discover_api_versions(session: requests.Session) -> dict:
    """Extract current apiVersions from Wodify's JS chunks.

    The JS chunks are only accessible to authenticated sessions, so this must
    be called after login. Wodify rotates apiVersions on every module update;
    discovery keeps us resilient without manual re-capturing.
    """
    versions = dict(_FALLBACK_API_VERSIONS)
    try:
        r = session.get(
            f"{_MODULE_INFO_URL}?cached",
            headers=_headers(session),
            timeout=15,
        )
        r.raise_for_status()
        urls = r.json().get("manifest", {}).get("urlVersions", {})
    except Exception as e:
        logger.warning("Manifest fetch failed — using fallback apiVersions: %s", e)
        return versions

    call_pattern = re.compile(
        r'call(?:ServerAction|DataAction)\("([^"]+)",\s*"[^"]+",\s*"([^"]+)"'
    )
    for chunk_key, actions in _CHUNK_ACTIONS.items():
        matching_url = next((u for u in urls if chunk_key in u), None)
        if not matching_url:
            continue
        try:
            chunk_r = session.get(
                f"{_BASE.rsplit('/', 1)[0]}{matching_url}{urls[matching_url]}",
                headers={"user-agent": _USER_AGENT},
                timeout=20,
            )
            if chunk_r.status_code != 200 or not chunk_r.text:
                continue
            for match in call_pattern.finditer(chunk_r.text):
                action_name = match.group(1)
                api_version = match.group(2)
                key = _ACTION_ALIASES.get(action_name, action_name)
                if key in actions or key in versions:
                    versions[key] = api_version
        except Exception as e:
            logger.warning("Chunk %s fetch failed: %s", chunk_key, e)
    return versions


def _api_version(session: requests.Session, action: str) -> str:
    discovered = getattr(session, "api_versions", None) or {}
    return discovered.get(action, _FALLBACK_API_VERSIONS.get(action, ""))


# ── Public API ──────────────────────────────────────────────────────────────────

def login(email: str, password: str) -> tuple[requests.Session, dict, str]:
    """
    Authenticate with the Wodify mobile API.

    Returns (session, user_params, module_version).
    user_params keys: Customer, CustomerId, UserId, GlobalUserId,
                      ActiveLocationId, GymProgramId
    """
    session = requests.Session()

    # Step 1: Initialise session cookies.
    logger.info("Initialising session…")
    session.get(
        f"{_BASE}/",
        headers={"user-agent": _USER_AGENT, "accept": "text/html"},
        timeout=30,
        allow_redirects=True,
    )

    # Step 2: Module version.
    mv = _module_version(session)
    logger.info("Module version: %s", mv)

    # Step 2b: Discover current apiVersions from JS chunks. Login chunks are
    # accessible pre-auth, so we discover BEFORE login — otherwise a rotated
    # Prepare_Login version breaks us before we ever get to fix it.
    api_versions = _discover_api_versions(session)
    session.api_versions = api_versions  # type: ignore[attr-defined]
    logger.info("Discovered apiVersions: %s", api_versions)

    # Step 3: Authenticate.
    logger.info("Logging in as %s…", email)
    body = {
        "versionInfo": {
            "moduleVersion": mv,
            "apiVersion": _api_version(session, "Prepare_Login"),
        },
        "viewName": "Home.Login",
        "inputParameters": {
            "Request": {
                "UserName": email,
                "Password": password,
                "IsToLogin": True,
                "CustomerId": "0",
                "UserId": "0",
                "WhiteLabelAppCustomerId": "0",
                "IsOTPSignIn": False,
                "IsSocialSignIn": False,
                "NotCheckSingleUserIsActive": False,
            },
            "IsMFAEnabled_Customers": False,
        },
    }

    r = session.post(_PREPARE_LOGIN_URL, json=body, headers=_headers(session), timeout=30)
    r.raise_for_status()
    response = r.json()["data"]["Response"]

    if response.get("Error", {}).get("HasError"):
        raise ValueError(f"Login failed: {response['Error']['ErrorMessage']}")

    customer = response["Customer"]
    ud = response.get("ResponseUserData") or response.get("ResponseGetUserData")
    if not ud:
        raise ValueError("Login response missing user data")

    # Step 4: ActionDo_Login — establishes the authenticated session cookies.
    do_login_body = {
        "versionInfo": {
            "moduleVersion": mv,
            "apiVersion": _api_version(session, "Do_Login"),
        },
        "viewName": "Home.Login",
        "inputParameters": {
            "ValidatedLogin": {
                "Customer": customer,
                "UserData": ud,
            },
            "IsToLogin": False,
            "IsTrustDeviceForMFA": False,
            "TrustDeviceForDays": 0,
            "BrowserType": "",
            "DeviceType": "",
            "OSType": "",
            "IsCordovaDefined": True,
            "SocialLoginUserId": "",
            "SocialLoginProviderTypeId": "0",
        },
    }
    r2 = session.post(_DO_LOGIN_URL, json=do_login_body, headers=_headers(session), timeout=30)
    r2.raise_for_status()

    user_params = {
        "Customer": customer,
        "CustomerId": ud["CustomerId"],
        "UserId": ud["UserId"],
        "GlobalUserId": ud["GlobalUserId"],
        "ActiveLocationId": ud["ActiveLocationId"],
        "GymProgramId": ud["GymProgramId"],
    }
    logger.info("Logged in — CustomerId=%s UserId=%s", ud["CustomerId"], ud["UserId"])
    return session, user_params, mv


def fetch_workout(
    session: requests.Session,
    user_params: dict,
    module_version: str,
    target_date: date,
) -> dict | None:
    """
    Fetch the WOD data for a single date.

    Returns the ResponseWorkout dict from the API, or None if unpublished/empty.
    """
    date_str = target_date.isoformat()
    logger.info("Fetching workout for %s…", date_str)

    body = {
        "versionInfo": {
            "moduleVersion": module_version,
            "apiVersion": _api_version(session, "GetAllWorkoutData"),
        },
        "viewName": "MainScreens.Exercise",
        "screenData": {
            "variables": {
                "PriorDateTime": "1900-01-01T00:00:00",
                "In_Request": {
                    "UOMWeightId": 3,
                    "Customer": user_params["Customer"],
                    "SelectedDate": date_str,
                    "ActiveLocationId": user_params["ActiveLocationId"],
                    "CustomerId": user_params["CustomerId"],
                    "GlobalUserId": user_params["GlobalUserId"],
                    "GymProgramId": user_params["GymProgramId"],
                    "UserId": user_params["UserId"],
                    "DateTime": "1900-01-01T00:00:00",
                    "IsChangeDate": False,
                    "IsCoachOrAbove": False,
                    "IsCoachViewEnabled": False,
                    "IsRefreshingButtons": False,
                },
                "_in_RequestInDataFetchStatus": 1,
            }
        },
    }

    r = session.post(_WOD_DATA_URL, json=body, headers=_headers(session), timeout=30)
    r.raise_for_status()

    response = r.json()["data"]["Response"]
    wod = response["ResponseWOD"]

    if wod["WorkoutError"]["HasError"]:
        logger.warning("WOD error for %s: %s", date_str, wod["WorkoutError"]["ErrorMessage"])
        return None

    workout = wod["ResponseWorkout"]
    if workout.get("EmptyOrNotPublished"):
        logger.info("No workout published for %s", date_str)
        return None

    return workout


def scrape_week(email: str, password: str, target_monday: date) -> dict[str, dict]:
    """
    Log in and fetch WOD data for every day of the target week (Mon–Sun).

    Returns dict mapping ISO date strings → ResponseWorkout dicts.
    Only days with published workouts are included.
    """
    session, user_params, mv = login(email, password)

    results: dict[str, dict] = {}
    for offset in range(7):
        day = target_monday + timedelta(days=offset)
        workout = fetch_workout(session, user_params, mv, day)
        if workout is not None:
            results[day.isoformat()] = workout

    return results
