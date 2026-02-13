# GarminBot

A Python bot that syncs Garmin Connect data daily and sends formatted health summaries to Telegram, with weekly reports, trend charts, and smart insights.

## Features

- **Daily sync** — fetches yesterday's sleep and activity data from Garmin Connect at a configurable time
- **Daily Telegram report** — sends a formatted message with sleep quality, steps, and calorie data
- **Weekly report** — every Sunday: 7-day stats, a bar chart, and smart insights
- **Monthly stats** — via `/mes` command
- **All commands in Portuguese** — `/hoje`, `/ontem`, `/semana`, `/mes`, `/sync`, `/status`
- **Robust error handling** — retries with exponential backoff, partial data support, Telegram error alerts
- **Token persistence** — Garmin OAuth2 token saved to disk, reused across restarts
- **Automatic backups** — weekly SQLite backup with 7-copy retention
- **Smart insights** — streak detection, weekend vs weekday sleep patterns, declining trends

## Requirements

- Python 3.10+
- A Garmin Connect account
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))

## Setup

### 1. Clone and install

```bash
git clone <repo-url>
cd garmin-telegram-bot
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

## Telegram Commands

| Command | Description |
|---|---|
| `/hoje` | Today's metrics (if already synced) |
| `/ontem` | Yesterday's full summary with weekly comparison |
| `/semana` | Last 7 days averages |
| `/mes` | Last 30 days averages |
| `/sync` | Force an immediate Garmin sync |
| `/status` | Bot status, last sync time, recent errors, next jobs |

## Expected Message Formats

### Daily Summary
```
📊 Resumo de 13/02/2026

😴 Sono
• Duração: 7h 23min
• Score: 82/100 ⭐⭐⭐⭐
• Avaliação: Excelente

👟 Atividade
• Passos: 12.340
• Calorias ativas: 487 kcal 🔥
• Calorias repouso: 1.680 kcal

📈 Comparação semanal:
• Sono médio: 7h 15min (+8min)
• Passos médios: 11.280 (+1.060)
```

### Weekly Report
```
📅 Relatório Semanal (07-13 Fev)

😴 Sono
• Média: 7h 18min
• Melhor: 8h 02min (Sábado)
• Pior: 6h 14min (Quarta)
• Score médio: 79/100

👟 Atividade
• Total passos: 78.920
• Média diária: 11.274
• Calorias ativas: 3.214 kcal
• Calorias repouso: 11.760 kcal
```

## Deployment

### Systemd (recommended for VPS/Raspberry Pi)

Create `/etc/systemd/system/garminbot.service`:

```ini
[Unit]
Description=GarminBot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/garmin-telegram-bot
EnvironmentFile=/path/to/garmin-telegram-bot/.env
ExecStart=/path/to/.venv/bin/python -m src.main
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable garminbot
sudo systemctl start garminbot
sudo journalctl -u garminbot -f
```

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
CMD ["python", "-m", "src.main"]
```

```bash
docker build -t garminbot .
docker run -d --env-file .env -v $(pwd)/data:/app/data -v $(pwd)/logs:/app/logs garminbot
```

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

## Project Structure

```
garmin-telegram-bot/
├── src/
│   ├── main.py              # Entry point
│   ├── config.py            # Environment variable loading
│   ├── garmin/
│   │   ├── auth.py          # OAuth2 authentication
│   │   └── client.py        # Sleep and activity data fetching
│   ├── database/
│   │   ├── models.py        # SQLAlchemy ORM models
│   │   └── repository.py    # All read/write operations
│   ├── telegram/
│   │   ├── bot.py           # Bot application and command handlers
│   │   └── formatters.py    # Message formatting
│   ├── scheduler/
│   │   └── jobs.py          # APScheduler job definitions
│   └── utils/
│       ├── logger.py        # Logging configuration
│       ├── charts.py        # Matplotlib chart generation
│       ├── insights.py      # Pattern detection and milestones
│       └── backup.py        # Database backup
├── tests/                   # Pytest test suite
├── data/                    # Database and backups (gitignored)
├── logs/                    # Log files (gitignored)
├── .env.example
├── requirements.txt
└── README.md
```
