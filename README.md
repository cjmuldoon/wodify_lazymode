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

Create a new shortcut in the iOS Shortcuts app with these actions:

1. `Format Date` — **Current Date**, Custom format: `yyyy-MM-dd`
2. `Text` — `https://raw.githubusercontent.com/YOUR_USERNAME/wodify_lazymode/main/data/workouts/` + **Formatted Date** + `.json`
3. `Get Contents of URL` — **Text** *(enable error handling → Continue)*
4. `If` — **Contents of URL** contains `siri_text`
   - `Get Dictionary from Input` — **Contents of URL**
   - `Get Dictionary Value` — key: `siri_text`, in **Dictionary**
   - `Show Result` — **Dictionary Value**
   - `Otherwise`
   - `Show Result` — `No workout posted yet for today.`
5. `End If`

Add it to Siri as *"Workout"* — say *"Hey Siri, Workout"* each morning.

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

## Sharing with gym mates

Since everyone at the gym follows the same programming, others can install the iOS Shortcut pointing at your repo's JSON — no setup required on their end. The scraper credentials stay private in your GitHub Secrets.

---

## Local development

```bash
cp .env.example .env
# Fill in your credentials

pip install -r requirements.txt
python main.py
```
