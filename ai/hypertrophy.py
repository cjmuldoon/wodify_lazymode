"""
Generate hypertrophy training suggestions via Claude API.

Only called for days that don't already have a hypertrophy entry, so we never
burn API credits re-processing known workouts.
"""

import json
import logging
import re

import anthropic

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = """You are a strength and conditioning coach specialising in
concurrent training — combining CrossFit/WOD-style conditioning with
hypertrophy accessory work. Your advice must be brief, practical, and safe."""

_USER_TEMPLATE = """Here is today's CrossFit workout:

{workout_summary}

Based on the primary muscle groups taxed by the strength movements and metcon,
provide hypertrophy accessory work that COMPLEMENTS rather than compounds fatigue.

Respond ONLY with valid JSON in exactly this structure (no extra keys, no markdown):
{{
  "recommended_muscles": ["<muscle 1>", "<muscle 2>"],
  "avoid": ["<muscle 1>", "<muscle 2>"],
  "reasoning": "<one sentence>",
  "suggested_exercises": [
    "<exercise with sets x reps>",
    "<exercise with sets x reps>",
    "<exercise with sets x reps>"
  ]
}}

Rules:
- recommended_muscles: 2–4 muscles that are NOT primary movers today
- avoid: muscles that ARE heavily loaded today
- suggested_exercises: 3–5 specific exercises with sets and reps
- reasoning: ≤ 25 words explaining the logic
"""


def _build_workout_summary(workout: dict) -> str:
    lines: list[str] = []

    strength = workout.get("strength") or []
    if strength:
        lines.append("Strength:")
        for s in strength:
            desc = s.get("description", "")
            lines.append(f"  - {s['name']}" + (f": {desc}" if desc else ""))

    metcon = workout.get("metcon")
    if metcon:
        lines.append(f"Metcon: {metcon.get('name', 'Workout')} – {metcon.get('type', '')}")
        if metcon.get("description"):
            # Truncate long descriptions
            desc = metcon["description"][:400]
            lines.append(f"  {desc}")
        if metcon.get("time_cap"):
            lines.append(f"  Time cap: {metcon['time_cap']}")

    gymnastics = workout.get("gymnastics") or []
    if gymnastics:
        lines.append("Gymnastics/Skills:")
        for g in gymnastics:
            lines.append(f"  - {g}")

    if not lines:
        lines.append(workout.get("raw_text", "")[:500])

    return "\n".join(lines)


def generate_hypertrophy_suggestion(workout: dict) -> dict | None:
    """
    Call Claude Haiku to generate hypertrophy suggestions for the given workout.

    Returns a structured dict, or None on failure.
    """
    summary = _build_workout_summary(workout)
    if not summary.strip():
        logger.warning("Empty workout summary for %s; skipping hypertrophy generation", workout.get("date"))
        return None

    prompt = _USER_TEMPLATE.format(workout_summary=summary)

    try:
        client = anthropic.Anthropic()
        message = client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()

        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.M)
            raw = re.sub(r"\n?```$", "", raw, flags=re.M)

        result = json.loads(raw)
        logger.info("Hypertrophy suggestion generated for %s", workout.get("date"))
        return result

    except json.JSONDecodeError as exc:
        logger.error("Could not parse JSON from Claude response: %s", exc)
        return None
    except Exception as exc:
        logger.error("Claude API error: %s", exc)
        return None


