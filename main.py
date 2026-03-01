"""
Wodify Lazy Mode — orchestration entrypoint.

Flow:
  1. Load existing workouts.json
  2. Determine target week (Mon–Sun; check next week too on Sundays)
  3. Scrape Wodify for raw HTML per day
  4. Parse each day into structured dicts
  5. Diff against existing data (raw_text hash)
  6. For new/changed days: generate hypertrophy suggestions
  7. Write updated workouts.json
  8. Send Telegram notification if new workouts found
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from scraper.wodify_client import scrape_week
from scraper.parser import parse_workout_api
from ai.hypertrophy import generate_hypertrophy_suggestion
from notifier.telegram import send_message, format_new_workouts_message, format_error_message

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("main")

DATA_FILE = Path(__file__).parent / "data" / "workouts.json"

# ── Config from env ────────────────────────────────────────────────────────────

WODIFY_EMAIL = os.environ["WODIFY_EMAIL"]
WODIFY_PASSWORD = os.environ["WODIFY_PASSWORD"]
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# ANTHROPIC_API_KEY is read automatically by the anthropic library


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_existing() -> dict:
    if DATA_FILE.exists():
        with DATA_FILE.open() as f:
            return json.load(f)
    return {"last_updated": None, "week_of": None, "workouts": {}}


def save_workouts(data: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DATA_FILE.open("w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Saved %s", DATA_FILE)

    # Save individual per-day files so iOS Shortcuts can fetch by date URL
    day_dir = DATA_FILE.parent / "workouts"
    day_dir.mkdir(parents=True, exist_ok=True)
    for date_str, workout in data.get("workouts", {}).items():
        day_file = day_dir / f"{date_str}.json"
        with day_file.open("w") as f:
            json.dump(workout, f, indent=2, ensure_ascii=False)
    logger.info("Saved per-day files to %s", day_dir)


def raw_text_hash(raw_text: str) -> str:
    return hashlib.sha256(raw_text.encode()).hexdigest()[:16]


def build_siri_text(w: dict) -> str:
    """Generate a natural-language string suitable for Siri to speak."""
    import re
    parts = []

    parts.append(f"{w.get('day_name', 'Today')}'s workout.")

    for s in w.get("strength") or []:
        name = s.get("name", "")
        desc = s.get("description", "")
        m = re.search(r"(\d+)x(\d+)", desc)
        if m:
            sets, reps = m.group(1), m.group(2)
            pcts = re.findall(r"@\s*(\d+)%", desc)
            pct = f" at {pcts[-1]} percent" if pcts else ""
            parts.append(f"Strength: {name}, {sets} by {reps}{pct}.")
        else:
            parts.append(f"Strength: {name}.")

    for g in w.get("gymnastics") or []:
        brief = g.split("\n")[0].strip()
        parts.append(f"Gymnastics: {brief}.")

    mc = w.get("metcon")
    if mc:
        mc_type = mc.get("type", "Workout")
        mc_name = mc.get("name", "").strip()
        mc_desc = mc.get("description", "")
        time_cap = mc.get("time_cap", "")

        dur = re.search(r"(\d+)[- ]?(?:min(?:ute)?s?|mins)", mc_desc, re.I)
        if mc_type == "AMRAP" and dur:
            label = f"AMRAP {dur.group(1)} minutes"
        elif mc_type == "EMOM" and dur:
            label = f"EMOM {dur.group(1)} minutes"
        elif mc_type == "For Time" and time_cap:
            label = f"For Time, {time_cap} cap"
        else:
            label = mc_type

        generic = re.match(r"workout\s*$|metcon\s*$", mc_name, re.I)
        if mc_name and not generic:
            label = f"{mc_name}. {label}"

        _SKIP = re.compile(
            r"min(?:ute)?|sec|cap|amrap|emom|rft|for time|rounds?|"
            r"build|hold|rest|aim|note|put your|score|@\s*\d+%",
            re.I,
        )
        movements = []
        for line in mc_desc.split("\n"):
            line = line.strip()
            if re.match(r"^\d+", line) and not _SKIP.search(line):
                movements.append(line)
            elif not re.search(r"\d", line) and not _SKIP.search(line) and len(line) > 3:
                movements.append(line)

        mc_str = f"Metcon: {label}"
        if movements:
            mc_str += ": " + ", ".join(movements[:5])
        parts.append(mc_str + ".")

    hyp = w.get("hypertrophy")
    if hyp and hyp.get("recommended_muscles"):
        muscles = ", ".join(hyp["recommended_muscles"][:3])
        parts.append(f"For hypertrophy, focus on {muscles}.")

    return " ".join(parts)


def target_mondays() -> list[date]:
    """
    Return the Monday(s) to check.
    - Always check the current week.
    - On Sunday (or Monday early), also check next week since gyms often post
      the following week's schedule Sunday night.
    """
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    mondays = [this_monday, this_monday + timedelta(days=7)]
    return mondays


# ── Main ───────────────────────────────────────────────────────────────────────

async def run() -> None:
    existing_data = load_existing()
    existing_workouts: dict = existing_data.get("workouts", {})

    all_new_dates: list[str] = []
    merged_workouts = dict(existing_workouts)
    week_of_str: str | None = existing_data.get("week_of")

    for monday in target_mondays():
        logger.info("Processing week starting %s", monday.isoformat())
        week_of_str = monday.isoformat()

        try:
            raw_by_day = scrape_week(WODIFY_EMAIL, WODIFY_PASSWORD, monday)
        except Exception as exc:
            logger.error("Scrape failed for week %s: %s", monday, exc)
            if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
                send_message(
                    TELEGRAM_BOT_TOKEN,
                    TELEGRAM_CHAT_ID,
                    format_error_message(str(exc)),
                )
            continue

        new_dates_this_week: list[str] = []

        for day_str, workout_data in raw_by_day.items():
            parsed = parse_workout_api(workout_data, day_str)
            if parsed is None:
                logger.info("No workout content for %s — skipping", day_str)
                continue

            existing_entry = existing_workouts.get(day_str)
            new_hash = raw_text_hash(parsed["raw_text"])
            old_hash = (
                raw_text_hash(existing_entry["raw_text"])
                if existing_entry and existing_entry.get("raw_text")
                else None
            )

            if new_hash == old_hash:
                logger.info("%s unchanged — skipping", day_str)
                # Keep existing entry (which may already have hypertrophy)
                continue

            logger.info("%s is new or changed", day_str)

            # Preserve hypertrophy if raw_text didn't change much
            # (old_hash != new_hash means content changed, so regenerate)
            parsed["hypertrophy"] = None

            # Generate hypertrophy suggestion
            if os.getenv("ANTHROPIC_API_KEY"):
                logger.info("Generating hypertrophy suggestion for %s…", day_str)
                hyp = generate_hypertrophy_suggestion(parsed)
                parsed["hypertrophy"] = hyp
            else:
                logger.warning("ANTHROPIC_API_KEY not set — skipping hypertrophy generation")

            merged_workouts[day_str] = parsed
            new_dates_this_week.append(day_str)

        all_new_dates.extend(new_dates_this_week)

    # ── Add/refresh siri_text for all workouts ─────────────────────────────────
    for w in merged_workouts.values():
        w["siri_text"] = build_siri_text(w)

    # ── Persist ────────────────────────────────────────────────────────────────
    from datetime import datetime

    updated_data = {
        "last_updated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
        "week_of": week_of_str,
        "workouts": merged_workouts,
    }
    save_workouts(updated_data)

    # ── Notify ─────────────────────────────────────────────────────────────────
    if all_new_dates:
        logger.info("New/changed dates: %s", all_new_dates)
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            msg = format_new_workouts_message(all_new_dates, merged_workouts)
            send_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)
        else:
            logger.warning("Telegram not configured — skipping notification")
    else:
        logger.info("No new workouts found")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logger.critical("Fatal error: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
