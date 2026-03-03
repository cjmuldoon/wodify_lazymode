"""
Wodify API response → structured workout dict.

Parses the ResponseWorkout dict returned by DataActionGetAllWorkoutData.
Note: Wodify gyms put workout content in the 'Comment' field (HTML),
while 'Description' is either empty or used for older plain-text content.
"""

import re
import logging
from datetime import date

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_METCON_TYPE_PATTERNS = [
    (re.compile(r"\bamrap\b", re.I), "AMRAP"),
    (re.compile(r"\brft\b|rounds?\s+for\s+time", re.I), "Rounds for Time"),  # before "for time"
    (re.compile(r"\bfor time\b", re.I), "For Time"),
    (re.compile(r"\bemom\b|every minute on the minute|e\d+mom", re.I), "EMOM"),
    (re.compile(r"\btabata\b", re.I), "Tabata"),
    (re.compile(r"\bchipper\b", re.I), "Chipper"),
    (re.compile(r"\bdeath by\b", re.I), "Death By"),
]
# Only extract time cap from lines that explicitly mention "cap"
def _time_cap(text: str) -> str | None:
    for line in text.split("\n"):
        if re.search(r"\bcap\b", line, re.I):
            m = re.search(r"(\d+)\s*(?:min(?:ute)?s?|mins?)", line, re.I)
            if m:
                return f"{m.group(1)} min"
    return None

_WARMUP_NAME_RE = re.compile(r"warm.?up|warmup|mobility|activation", re.I)
_METCON_NAME_RE = re.compile(r"\bmetcon\b|workout\b", re.I)
_GYMNASTICS_NAME_RE = re.compile(r"gymnastics|skill", re.I)


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text, suppressing BeautifulSoup filename warnings."""
    if not html or not html.strip():
        return ""
    # Only parse if the string looks like HTML (has angle brackets)
    if "<" not in html:
        return html.strip()
    text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _content(comp: dict) -> str:
    """Return the best available text content for a component."""
    # Gyms typically put programming in 'Comment' (HTML); Description may also carry content
    comment = _html_to_text(comp.get("Comment", ""))
    desc = _html_to_text(comp.get("Description", ""))
    # Prefer Comment if it has more detail; otherwise fall back to Description
    if len(comment) >= len(desc):
        return comment
    return desc


def _detect_metcon_type(text: str) -> str | None:
    for pattern, label in _METCON_TYPE_PATTERNS:
        if pattern.search(text):
            return label
    return None


def parse_workout_api(workout: dict, date_str: str) -> dict | None:
    """
    Parse a ResponseWorkout dict from the Wodify API into a structured dict.

    Returns None if the workout is empty/unpublished or has no parseable content.
    """
    if workout.get("EmptyOrNotPublished"):
        return None

    components = workout.get("WorkoutComponents", {}).get("List", [])
    if not components:
        return None

    warm_up_parts: list[str] = []
    strength: list[dict] = []
    gymnastics: list[str] = []
    metcon: dict | None = None
    raw_parts: list[str] = []

    for comp in components:
        name = comp.get("Name", "").strip()
        text = _content(comp)
        sets = comp.get("MeasureSets") or 0
        reps = comp.get("MeasureReps") or 0
        rep_scheme = _html_to_text(comp.get("MeasureRepScheme", ""))

        # Build raw text entry for change-detection hashing
        raw_tokens = [name]
        if sets and reps:
            raw_tokens.append(f"{sets}x{reps}")
        if rep_scheme:
            raw_tokens.append(rep_scheme)
        if text:
            raw_tokens.append(text)
        if any(t for t in raw_tokens[1:]):  # Skip name-only entries
            raw_parts.append(" | ".join(t for t in raw_tokens if t))

        # ── Classify by explicit type flags first ──────────────────────────
        if comp.get("IsWarmup"):
            warm_up_parts.append(f"{name}: {text}" if text else name)
            continue

        if comp.get("IsWeightlifting"):
            desc_parts = []
            if sets and reps:
                desc_parts.append(f"{sets}x{reps}")
            if rep_scheme:
                desc_parts.append(rep_scheme)
            if text:
                desc_parts.append(text)
            strength.append({"name": name, "description": " | ".join(desc_parts)})
            continue

        if comp.get("IsGymnastics"):
            gymnastics.append(f"{name}: {text}" if text else name)
            continue

        # Name-based gymnastics check BEFORE IsMetcon — Wodify sometimes tags
        # gymnastics skill components with IsMetcon=True instead of IsGymnastics
        if _GYMNASTICS_NAME_RE.search(name) and text:
            gymnastics.append(text)
            continue

        if comp.get("IsMetcon"):
            full_text = f"{name}\n{text}" if text else name
            metcon_type = _detect_metcon_type(full_text) or "Workout"
            metcon = {
                "name": name,
                "type": metcon_type,
                "description": text or name,
                "time_cap": _time_cap(full_text),
            }
            continue

        # ── No explicit flag: classify by name if content is present ──────
        if not text and not sets and not reps:
            continue  # Pure section header with no content — skip

        if _WARMUP_NAME_RE.search(name):
            warm_up_parts.append(text or name)
        elif _GYMNASTICS_NAME_RE.search(name):
            gymnastics.append(f"{name}: {text}" if text else name)
        elif _METCON_NAME_RE.search(name) and text:
            # Section header "Metcon"/"Workout" with actual content
            full_text = f"{name}\n{text}"
            metcon_type = _detect_metcon_type(full_text) or "Workout"
            if metcon is None:
                metcon = {
                    "name": name.strip(),
                    "type": metcon_type,
                    "description": text,
                    "time_cap": _time_cap(full_text),
                }
        elif text:
            # Has content but unclassified — treat as strength note
            strength.append({"name": name, "description": text})

    raw_text = "\n".join(raw_parts)

    if not warm_up_parts and not strength and not gymnastics and metcon is None:
        logger.info("No structured content parsed for %s", date_str)
        return None

    day_obj = date.fromisoformat(date_str)
    return {
        "date": date_str,
        "day_name": day_obj.strftime("%A"),
        "warm_up": " | ".join(warm_up_parts) or None,
        "strength": strength,
        "metcon": metcon,
        "gymnastics": gymnastics,
        "hypertrophy": None,
        "raw_text": raw_text,
    }
