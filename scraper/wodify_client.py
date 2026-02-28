"""
Wodify mobile API client.

Authenticates against app-clientapp.wodify.com (the OutSystems mobile backend)
using a plain requests.Session — no browser required.

Login flow (discovered via mitmproxy capture of the iOS app):
  1. GET /WodifyClient/ to initialise session cookies (osVisit, nr2W_Theme_UI, etc.)
  2. GET moduleversioninfo to fetch the current module version token
  3. POST ActionPrepare_Login with email + password → user params + updated cookies
  4. For each day: POST DataActionGetAllWorkoutData with user params
"""

import logging
import time
from datetime import date, timedelta
from urllib.parse import unquote

import requests

logger = logging.getLogger(__name__)

_BASE = "https://app-clientapp.wodify.com/WodifyClient"
_MODULE_VERSION_URL = f"{_BASE}/moduleservices/moduleversioninfo"
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


# ── Internal helpers ────────────────────────────────────────────────────────────

def _csrf(session: requests.Session) -> str:
    """Extract the current CSRF token from the nr2W_Theme_UI session cookie."""
    for cookie in session.cookies:
        if cookie.name == "nr2W_Theme_UI":
            # Cookie value format (URL-encoded): crf=<token>;uid=<id>;unm=<name>
            for part in unquote(cookie.value).split(";"):
                if part.strip().startswith("crf="):
                    return part.strip()[4:]
    return ""


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
    return r.json().get("versionToken", "4ryDH2ntbp14RDvJzz6wgA")


# ── Public API ──────────────────────────────────────────────────────────────────

def login(email: str, password: str) -> tuple[requests.Session, dict, str]:
    """
    Authenticate with the Wodify mobile API.

    Returns (session, user_params, module_version).
    user_params keys: Customer, CustomerId, UserId, GlobalUserId,
                      ActiveLocationId, GymProgramId
    """
    session = requests.Session()

    # Step 1: Initialise session cookies (sets osVisit, W_Theme_UI, nr2W_Theme_UI…)
    logger.info("Initialising session…")
    session.get(
        f"{_BASE}/",
        headers={"user-agent": _USER_AGENT, "accept": "text/html"},
        timeout=30,
        allow_redirects=True,
    )

    # Step 2: Module version (needed in every request body)
    mv = _module_version(session)
    logger.info("Module version: %s", mv)

    # Step 3: Authenticate
    logger.info("Logging in as %s…", email)
    body = {
        "versionInfo": {
            "moduleVersion": mv,
            "apiVersion": "krWEEplh8BG9Mc3hz5Tj7w",
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
            },
            "IsMFAEnabled_Customers": True,
        },
    }

    r = session.post(_PREPARE_LOGIN_URL, json=body, headers=_headers(session), timeout=30)
    r.raise_for_status()
    response = r.json()["data"]["Response"]

    if response.get("Error", {}).get("HasError"):
        raise ValueError(f"Login failed: {response['Error']['ErrorMessage']}")

    customer = response["Customer"]
    ud = response["ResponseUserData"]

    # Step 4: ActionDo_Login — establishes the authenticated session cookies
    # (nr1W_Theme_UI is updated by the server's Set-Cookie response)
    do_login_body = {
        "versionInfo": {
            "moduleVersion": mv,
            "apiVersion": "4XxaqGhfwgmnjLaLtXTwUQ",
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
            "apiVersion": "_2udCxN4vHxjdOMQVO8Cog",
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
