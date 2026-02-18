"""
Live integration test — corre tudo de imediato sem esperar pelos jobs agendados.

Uso:
    python test_live.py              # sync + daily report + weekly report
    python test_live.py --sync       # só sync
    python test_live.py --daily      # só daily report (usa dados já em DB)
    python test_live.py --weekly     # só weekly report
    python test_live.py --status     # mostra status do DB e último sync
    python test_live.py --fake       # injeta dados fictícios e envia os 3 reports
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta

# Garante que o módulo src é encontrado ao correr a partir da raiz do projeto
sys.path.insert(0, ".")

from src.config import ConfigError, load_config
from src.database.repository import Repository
from src.garmin.client import GarminClient
from src.scheduler.jobs import (
    make_report_callback,
    make_sync_job,
    make_weekly_report_job,
)
from src.telegram.bot import TelegramBot
from src.utils.logger import setup_logging


def _run(coro) -> None:
    asyncio.get_event_loop().run_until_complete(coro)


def cmd_sync(garmin: GarminClient, repo: Repository) -> None:
    print("▶ A sincronizar dados do Garmin...")
    job = make_sync_job(garmin, repo)
    job()
    print("✅ Sync concluído.")


def cmd_daily(repo: Repository, bot: TelegramBot) -> None:
    print("▶ A enviar daily report...")
    job = make_daily_report_job(repo, bot)
    job()
    print("✅ Daily report enviado.")


def cmd_weekly(repo: Repository, bot: TelegramBot, db_path: str) -> None:
    print("▶ A enviar weekly report + chart + insights + backup...")
    job = make_weekly_report_job(repo, bot, db_path)
    job()
    print("✅ Weekly report enviado.")


def cmd_status(repo: Repository) -> None:
    days = repo.count_stored_days()
    last = repo.get_last_successful_sync()
    logs = repo.get_recent_sync_logs(5)

    print(f"\n📊 Dias armazenados: {days}")
    print(f"🕐 Último sync bem-sucedido: {last.sync_date if last else 'Nunca'}")
    if logs:
        print("\n📋 Últimos 5 logs de sync:")
        for log in logs:
            print(f"  [{log.status}] {log.sync_date}  {log.error_message or ''}")


def cmd_fake(repo: Repository, bot: TelegramBot, db_path: str) -> None:
    """Injeta 14 dias de dados fictícios no DB e envia os 3 reports."""
    print("▶ A injetar dados fictícios...")
    import random

    today = date.today()
    for i in range(14):
        day = today - timedelta(days=i + 1)
        metrics = {
            "sleep_hours": round(random.uniform(5.5, 8.5), 2),
            "sleep_score": random.randint(55, 92),
            "sleep_quality": "Bom",
            "steps": random.randint(6000, 15000),
            "active_calories": random.randint(250, 600),
            "resting_calories": random.randint(1500, 1900),
            "garmin_sync_success": True,
        }
        repo.save_daily_metrics(day, metrics)
    print(f"✅ 14 dias de dados fictícios inseridos.")

    cmd_daily(repo, bot)
    cmd_weekly(repo, bot, db_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="GarminBot live test runner")
    parser.add_argument("--sync",   action="store_true", help="Só sincronizar Garmin")
    parser.add_argument("--daily",  action="store_true", help="Só enviar daily report")
    parser.add_argument("--weekly", action="store_true", help="Só enviar weekly report")
    parser.add_argument("--status", action="store_true", help="Mostrar estado do DB")
    parser.add_argument("--fake",   action="store_true", help="Dados fictícios + todos os reports")
    args = parser.parse_args()

    setup_logging("DEBUG")

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"❌ Erro de configuração: {exc}")
        print("   Copia .env.example para .env e preenche as credenciais.")
        sys.exit(1)

    repo = Repository(config.database_path)
    repo.init_database()

    garmin = GarminClient(config.garmin_email, config.garmin_password)
    bot = TelegramBot(config, repo)

    # Sem argumentos → corre tudo
    run_all = not any([args.sync, args.daily, args.weekly, args.status, args.fake])

    if args.status:
        cmd_status(repo)
        return

    if args.fake:
        cmd_fake(repo, bot, config.database_path)
        return

    if args.sync or run_all:
        cmd_sync(garmin, repo)

    if args.daily or run_all:
        cmd_daily(repo, bot)

    if args.weekly or run_all:
        cmd_weekly(repo, bot, config.database_path)

    if run_all:
        print("\n✅ Tudo concluído. Verifica o Telegram!")


if __name__ == "__main__":
    main()
