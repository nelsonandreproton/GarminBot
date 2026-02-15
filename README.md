# GarminBot

A Python bot that syncs Garmin Connect data daily and sends formatted health summaries to Telegram, with weekly reports, trend charts, smart insights, and optional nutrition tracking.

## Features

- **Daily sync** — fetches yesterday's sleep and activity data from Garmin Connect at a configurable time
- **Daily Telegram report** — sends a formatted message with sleep quality, steps, and calorie data
- **Weekly report** — every Sunday: 7-day stats, a bar chart, and smart insights
- **Monthly stats** — via `/mes` command
- **Nutrition tracking** — log food via text or barcode photo, with Groq LLM parsing (optional, free tier)
- **Macro goals** — set daily targets for calories, protein, fat, and carbs; see remaining macros after each meal
- **Nutrition recommendations** — LLM-generated daily advice based on yesterday's intake vs goals and Garmin data
- **Workout recommendations** — daily gym workout based on sleep, nutrition, equipment, and movement patterns (Squat/Push/Pull/Hinge/Carry)
- **All commands in Portuguese** — `/hoje`, `/ontem`, `/semana`, `/mes`, `/sync`, `/status`, `/comi`, `/nutricao`, `/treino`
- **Robust error handling** — retries with exponential backoff, partial data support, Telegram error alerts
- **Token persistence** — Garmin OAuth2 token saved to disk, reused across restarts
- **Automatic backups** — weekly SQLite backup with 7-copy retention
- **Smart insights** — streak detection, weekend vs weekday sleep patterns, declining trends

## Requirements

- Python 3.10+
- A Garmin Connect account
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- (Optional) A [Groq API key](https://console.groq.com/) for nutrition tracking (free tier)

## Setup

### 1. Clone and install

```bash
git clone <repo-url>
cd GarminBot
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Create your Telegram bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the bot token (format: `123456789:ABC-DEF...`)

### 3. Get your Telegram Chat ID

1. Send any message to your new bot
2. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat":{"id":123456789}` and copy the number

### 4. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```bash
GARMIN_EMAIL=your@email.com
GARMIN_PASSWORD=your_garmin_password
TELEGRAM_BOT_TOKEN=123456789:ABC-DEF...
TELEGRAM_CHAT_ID=123456789

# Optional: enable nutrition tracking (free at console.groq.com)
GROQ_API_KEY=gsk_...
```

### 5. Run

```bash
python -m src.main
```

On first run, the bot authenticates with Garmin and saves the token. A startup health check verifies Telegram connectivity.

## Configuration

All settings live in `.env`. See `.env.example` for the full list with comments.

| Variable | Default | Description |
|---|---|---|
| `GARMIN_EMAIL` | — | Garmin Connect account email |
| `GARMIN_PASSWORD` | — | Garmin Connect password |
| `TELEGRAM_BOT_TOKEN` | — | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | — | Your Telegram chat ID |
| `DATABASE_PATH` | `./data/garmin_data.db` | SQLite database location |
| `DAILY_SYNC_TIME` | `07:00` | When to sync Garmin data (HH:MM) |
| `DAILY_REPORT_TIME` | `08:00` | When to send the daily report (HH:MM) |
| `WEEKLY_REPORT_DAY` | `sunday` | Day of the week for weekly report |
| `WEEKLY_REPORT_TIME` | `20:00` | Time for the weekly report (HH:MM) |
| `TIMEZONE` | `Europe/Lisbon` | Timezone for scheduling |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FILE` | `./logs/bot.log` | Log file path |
| `GROQ_API_KEY` | — | Groq API key (optional, enables `/comi`, `/nutricao`, `/treino`) |
| `GYM_EQUIPMENT` | — | Equipment list (optional, enables workout recommendations) |
| `GYM_TRAINING_MINUTES` | `45` | Max workout duration in minutes |

## Telegram Commands

| Command | Description |
|---|---|
| `/hoje` | Today's metrics (if already synced) |
| `/ontem` | Yesterday's full summary with weekly comparison |
| `/semana` | Last 7 days averages |
| `/mes` | Last 30 days averages |
| `/sync` | Force an immediate Garmin sync |
| `/status` | Bot status, last sync time, recent errors, next jobs |
| `/comi` | Register food eaten (text or barcode photo) |
| `/nutricao` | Daily nutrition summary |
| `/apagar` | Delete last food entry |
| `/treino` | Generate a workout recommendation for today |
| `/objetivo` | View or set goals (passos/sono/peso/calorias/proteina/gordura/hidratos) |

## Deployment

### Server Setup (Hetzner / Ubuntu VPS)

Run the setup script on a fresh Ubuntu VPS as root:

```bash
scp server-setup.sh root@your-server:
ssh root@your-server bash server-setup.sh
```

This creates a `garminbot` user, installs Docker, configures the firewall (SSH only), and hardens SSH. The script is idempotent and safe to re-run.

### First Deploy

```bash
ssh garminbot@your-server

git clone <repo-url> ~/GarminBot
cd ~/GarminBot
cp .env.example .env
nano .env  # fill in credentials

docker compose up -d --build
docker compose logs -f
```

### Updating

```bash
cd ~/GarminBot
bash deploy.sh
```

Pulls latest code, rebuilds the image, and restarts the container.

### Monitoring

```bash
# Container status and health
docker compose ps

# Live logs
docker compose logs -f

# Last 100 log lines
docker compose logs --tail=100
```

The bot includes a built-in health check: Docker automatically monitors it and restarts on failure. Log rotation is configured (30MB max) to prevent disk fill.

## Running Tests

```bash
python -m pytest tests/ -v
```

## Troubleshooting

**Garmin authentication fails**
- Verify email/password in `.env`
- Check if Garmin requires 2FA (not supported yet)
- Delete `./data/garmin_tokens.json` to force fresh login

**No data after sync**
- Some devices don't sync all metrics to Garmin Connect immediately
- Check the Garmin Connect app — data must be uploaded there first

**Telegram bot not responding**
- Ensure `TELEGRAM_CHAT_ID` matches the chat you're messaging from
- Verify the bot token is correct
- Check if the bot was blocked: try `/start` in the chat

**Scheduler jobs not running**
- Check the `TIMEZONE` setting matches your local timezone
- Use `/status` to see next scheduled run times
- Check `./logs/bot.log` for errors

**Database locked error**
- Only one instance of the bot should run at a time
- If the bot crashed, wait a few seconds before restarting

**Nutrition commands disabled**
- Set `GROQ_API_KEY` in `.env` (get a free key at https://console.groq.com/)

## Project Structure

```
GarminBot/
├── src/
│   ├── main.py              # Entry point
│   ├── config.py            # Environment variable loading
│   ├── garmin/
│   │   ├── auth.py          # OAuth2 authentication
│   │   └── client.py        # Sleep and activity data fetching
│   ├── database/
│   │   ├── models.py        # SQLAlchemy ORM models
│   │   └── repository.py    # All read/write operations
│   ├── nutrition/
│   │   ├── parser.py        # Groq LLM: text → structured food items
│   │   ├── service.py       # Orchestrates parse → lookup → fallback
│   │   ├── openfoodfacts.py # OpenFoodFacts API client
│   │   └── barcode.py       # Barcode decoding from photos
│   ├── training/
│   │   └── recommender.py   # Groq LLM: workout generation
│   ├── telegram/
│   │   ├── bot.py           # Bot application and command handlers
│   │   └── formatters.py    # Message formatting
│   ├── scheduler/
│   │   └── jobs.py          # APScheduler job definitions
│   └── utils/
│       ├── logger.py        # Logging configuration
│       ├── charts.py        # Matplotlib chart generation
│       ├── insights.py      # Pattern detection and milestones
│       ├── backup.py        # Database backup
│       └── healthcheck.py   # Optional HTTP health server
├── tests/                   # Pytest test suite
├── data/                    # Database and backups (gitignored)
├── logs/                    # Log files (gitignored)
├── docker-compose.yml       # Container orchestration
├── Dockerfile
├── .dockerignore
├── .env.example
├── deploy.sh                # Pull, rebuild, restart
├── server-setup.sh          # Ubuntu VPS initial setup
├── requirements.txt
└── README.md
```
