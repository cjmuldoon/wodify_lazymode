"""
Telegram notification sender.
"""

import re
import logging
import requests

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"

MAX_MESSAGE_LEN = 4096  # Telegram hard limit


def send_message(bot_token: str, chat_id: str, text: str) -> bool:
    """
    Send a plain-text message via Telegram Bot API.
    Returns True on success.
    """
    url = _API_BASE.format(token=bot_token)
    # Truncate if somehow over the limit
    if len(text) > MAX_MESSAGE_LEN:
        text = text[: MAX_MESSAGE_LEN - 3] + "…"

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info("Telegram message sent successfully")
        return True
    except requests.RequestException as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


def _brief_strength(s: dict) -> str:
    """Format one strength movement as 'Name NxN @ X%'."""
    name = s.get("name", "")
    desc = s.get("description", "")

    sets_reps = re.search(r"(\d+)x(\d+)", desc)
    sr = f"{sets_reps.group(1)}x{sets_reps.group(2)}" if sets_reps else ""

    # Take the last percentage mentioned — usually the working/hold set
    pcts = re.findall(r"@\s*(\d+)%", desc)
    pct = f" @ {pcts[-1]}%" if pcts else ""

    return f"{name} {sr}{pct}".strip() if sr else name


def _brief_gymnastics(g: str) -> str:
    """
    Summarise a gymnastics block by extracting the key skill/movement names.

    Strategy:
    - Clean the first line (strip EMOM timing prefix, parenthetical notes, leading rep counts)
    - Also grab any short lines that look like named skill sections (contain 'Progression',
      'Skill', 'Drill', or are short standalone titles)
    """
    _TIMING_PREFIX = re.compile(r"^E\d*MOM\s+x?\s*[\d.]+\s*", re.I)
    _PAREN = re.compile(r"\s*\([^)]*\)")
    _LEADING_NUM = re.compile(r"^\d+\s*[x×]?\s*")
    _SKILL_HEADER = re.compile(r"progression|skill|drill", re.I)

    def clean(line: str) -> str:
        line = _TIMING_PREFIX.sub("", line)
        line = _PAREN.sub("", line)
        line = _LEADING_NUM.sub("", line)
        return line.strip(" -–")

    raw_lines = [l.strip() for l in g.split("\n") if l.strip()]
    if not raw_lines:
        return ""

    key: list[str] = []
    seen: set[str] = set()

    def add(line: str) -> None:
        c = clean(line)
        if c and c.lower() not in seen:
            key.append(c)
            seen.add(c.lower())

    # Always include the cleaned first line
    add(raw_lines[0])

    # Grab named skill section headers from the rest
    for line in raw_lines[1:]:
        if len(line) < 70 and _SKILL_HEADER.search(line):
            add(line)
        if len(key) >= 4:
            break

    return ", ".join(key)


def _brief_metcon(mc: dict) -> str:
    """Format a metcon as 'Type Duration: Move1, Move2, Move3'."""
    metcon_type = mc.get("type", "Workout")
    desc = mc.get("description", "")
    time_cap = mc.get("time_cap", "")
    name = mc.get("name", "").strip()

    # Build the type/format label
    dur = re.search(r"(\d+)[- ]?(?:min(?:ute)?s?|mins)", desc, re.I)
    if metcon_type == "AMRAP" and dur:
        label = f"AMRAP {dur.group(1)}min"
    elif metcon_type == "EMOM" and dur:
        label = f"EMOM {dur.group(1)}min"
    elif metcon_type == "For Time" and time_cap:
        label = f"For Time ({time_cap} cap)"
    elif metcon_type == "For Time":
        label = "For Time"
    else:
        label = metcon_type

    # For named benchmark workouts, prefix with the name
    generic = re.match(r"workout\s*$|metcon\s*$", name, re.I)
    if name and not generic:
        label = f"{name} – {label}"

    # Extract movement lines: lines that start with a digit (reps) and
    # don't look like timing/header lines
    _SKIP_RE = re.compile(
        r"min(?:ute)?|sec|cap|amrap|emom|rft|for time|rounds?|build|hold|"
        r"rest|aim|note|put your|score|scaled|^rx|^f:|^m:|@\s*\d+%",
        re.I,
    )
    movements = []
    for line in desc.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Movement lines start with a number (reps)
        if re.match(r"^\d+", line) and not _SKIP_RE.search(line):
            movements.append(line)
        # Or are plain named movements (no digits, not a header)
        elif not re.search(r"\d", line) and not _SKIP_RE.search(line) and len(line) > 3:
            movements.append(line)

    if movements:
        mvt_str = ", ".join(movements[:5])
        return f"{label}: {mvt_str}"
    return label


def format_new_workouts_message(new_dates: list[str], workouts: dict) -> str:
    """
    Build a human-readable Telegram message listing new/updated workouts.
    """
    lines = ["🏋️ <b>New workouts posted!</b>", ""]

    for date_str in sorted(new_dates):
        w = workouts.get(date_str)
        if not w:
            continue

        day_name = w.get("day_name", "")
        try:
            from datetime import date as date_cls
            d = date_cls.fromisoformat(date_str)
            date_label = f"{day_name} {d.day} {d.strftime('%b')}"
        except Exception:
            date_label = f"{day_name} {date_str}"

        lines.append(f"<b>{date_label}:</b>")

        for s in w.get("strength") or []:
            lines.append(f"💪 {_brief_strength(s)}")

        for g in w.get("gymnastics") or []:
            lines.append(f"🤸 {_brief_gymnastics(g)}")

        metcon = w.get("metcon")
        if metcon:
            lines.append(f"⚡ {_brief_metcon(metcon)}")

        hyp = w.get("hypertrophy")
        if hyp and hyp.get("recommended_muscles"):
            muscles = ", ".join(hyp["recommended_muscles"])
            lines.append(f"🎯 {muscles}")

        lines.append("")

    return "\n".join(lines).strip()


def format_error_message(error: str) -> str:
    return f"⚠️ <b>Wodify scraper error</b>\n\n<code>{error[:500]}</code>"
