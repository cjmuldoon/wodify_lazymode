# Wodify Lazy Mode

Automatically scrapes your Wodify gym's weekly programming, generates hypertrophy accessory suggestions via Claude AI, and delivers everything to Telegram and Siri — with zero ongoing cost.

## What it does

1. **Scrapes Wodify** nightly via the mobile API (no browser required)
2. **Parses** each day's strength, gymnastics, and metcon into structured data
3. **Generates hypertrophy suggestions** using Claude Haiku — accessory work that complements rather than compounds fatigue from the WOD
4. **Sends a Telegram notification** when new workouts are posted
5. **Commits `data/workouts.json`** and per-day files to the repo so an iOS Shortcut can read them via Siri

Everything runs on GitHub Actions — no server, no database, no ongoing infrastructure.

---

## Cost

| Component | Cost |
|---|---|
| GitHub Actions (public repo) | Free |
| Telegram bot | Free |
| Anthropic API (Claude Haiku) | ~$0.003 / week |

---

## Setup

### 1. Fork or clone this repo

Make it public if you want the iOS Shortcut to work without authentication.

### 2. Add GitHub Actions secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|---|---|
| `WODIFY_EMAIL` | Your Wodify login email |
| `WODIFY_PASSWORD` | Your Wodify password |
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Your chat ID from [@userinfobot](https://t.me/userinfobot) |
| `ANTHROPIC_API_KEY` | API key from [console.anthropic.com](https://console.anthropic.com) |

### 3. Trigger a test run

Go to **Actions → Nightly Wodify Check → Run workflow**.

Enable **"Send Telegram for all current workouts"** to force a full Telegram notification even if nothing has changed.

### 4. Set up the iOS Shortcut

Create a new shortcut in the iOS Shortcuts app. Replace `YOUR_USERNAME` with your GitHub username throughout.

#### Actions (in order)

**Step 1 — Receive date input from Siri**

Add `Receive Input from Shortcut` → set type to **Text**

This captures whatever you say after "Workout" (e.g. "Hey Siri, Workout Monday").

---

**Step 2 — Parse the date or fall back to today**

Add `If` → **Shortcut Input** → `has any value`

*Inside the If branch:*
- `Get Dates from Input` → from **Shortcut Input**
- `Format Date` → **Date** (result above), Format: Custom, `yyyy-MM-dd`
- `Text` → insert **Formatted Date** magic variable *(this forces it to a plain string)*
- `Set Variable` → Name: `TargetDate`, Value: **Text**

*Inside the Otherwise branch:*
- `Format Date` → **Current Date**, Format: Custom, `yyyy-MM-dd`
- `Text` → insert **Formatted Date** magic variable
- `Set Variable` → Name: `TargetDate`, Value: **Text**

Add `End If`

> **Note on day name parsing:** iOS date parsing is inconsistent with relative phrases.
> "Monday" resolves to *next* Monday; "last Monday" resolves to *two* Mondays ago.
> For current-week days use explicit dates like "3 March" or "march 3", or just say
> "Hey Siri, Workout" with no input to get today's workout.

---

**Step 3 — Build the URL**

Add `Text`:
```
https://raw.githubusercontent.com/YOUR_USERNAME/wodify_lazymode/main/data/workouts/
```
Then insert the **TargetDate** magic variable, then type `.json`

The full text should look like:
`https://raw.githubusercontent.com/YOUR_USERNAME/wodify_lazymode/main/data/workouts/[TargetDate].json`

---

**Step 4 — Fetch the workout**

Add `Get Contents of URL` → URL: **Text** (from step 3)

⚠️ **Important:** Enable error handling on this action so a 404 (no workout for that date) doesn't crash the shortcut.
- Long-press the action → **Add Error Handling** → set to **Continue**

---

**Step 5 — Force response to text type**

Add `Text` → insert **Contents of URL** magic variable

*(iOS Shortcuts treats URL responses as a file/data type. Wrapping it in a Text action lets you use the "contains" condition in the next step.)*

---

**Step 6 — Display the result**

Add `If` → **Text** (from step 5) → `contains` → type `siri_text`

*Inside the If branch:*
- `Get Dictionary from Input` → **Contents of URL**
- `Get Dictionary Value` → Key: `siri_text`, Dictionary: **Dictionary**
- `Show Result` → **Dictionary Value**

*Inside the Otherwise branch:*
- `Show Result` → type `No workout found for that day.`

Add `End If`

---

#### Siri usage

In the Shortcuts app, tap the shortcut's **ⓘ** button → **Add to Siri** → record the phrase *"Workout"*.

| What you say | Result |
|---|---|
| *"Hey Siri, Workout"* | Today's workout |
| *"Hey Siri, Workout 3 March"* | Workout for March 3 |
| *"Hey Siri, Workout yesterday"* | Yesterday's workout |

#### Sharing with gym mates

Everyone at the gym follows the same programming. Share the shortcut and they can use your repo's JSON directly — no credentials or setup required on their end.

---

## Schedule

The workflow runs automatically:

- **Monday–Saturday**: once at 6 PM AEDT (07:00 UTC)
- **Sunday**: hourly from 2 PM–11 PM AEDT (03:00–12:00 UTC), watching for next week's programming to drop

---

## Project structure

```
wodify_lazymode/
├── .github/workflows/nightly.yml   # Cron schedule + manual trigger
├── scraper/
│   ├── wodify_client.py            # Mobile API auth + WOD fetch (requests, no browser)
│   └── parser.py                   # API response → structured workout dict
├── ai/
│   └── hypertrophy.py              # Claude Haiku → accessory suggestions
├── notifier/
│   └── telegram.py                 # Telegram Bot API notifications
├── data/
│   ├── workouts.json               # Full weekly data (committed by the bot)
│   └── workouts/
│       └── YYYY-MM-DD.json         # Per-day files for iOS Shortcut URL lookup
├── main.py                         # Orchestration entrypoint
├── requirements.txt
└── .env.example
```

## Data format

Each day in `data/workouts.json` and the per-day files follows this structure:

```json
{
  "date": "2026-03-03",
  "day_name": "Tuesday",
  "warm_up": "...",
  "strength": [
    { "name": "Back Squat", "description": "4x6 @ 70%" }
  ],
  "metcon": {
    "name": "Fran",
    "type": "For Time",
    "description": "21-15-9: Thrusters, Pull-ups",
    "time_cap": "10 min"
  },
  "gymnastics": [],
  "hypertrophy": {
    "recommended_muscles": ["back", "biceps", "rear delts"],
    "avoid": ["quads", "shoulders"],
    "reasoning": "Heavy squats + thrusters tax lower body and anterior chain.",
    "suggested_exercises": ["Barbell Rows 3x8", "Face Pulls 3x15"]
  },
  "siri_text": "Tuesday's workout. Strength: Back Squat, 4 by 6 at 70 percent. ..."
}
```

## Local development

```bash
cp .env.example .env
# Fill in your credentials

pip install -r requirements.txt
python main.py
```
