"""Database backup: daily backup with retention of last 7 copies."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_BACKUP_DIR = Path("./data/backups")
_KEEP_BACKUPS = 7


def create_backup(database_path: str) -> Path | None:
    """Copy the SQLite database to a timestamped backup file.

    Args:
        database_path: Path to the source database file.

    Returns:
        Path to the created backup, or None on failure.
    """
    source = Path(database_path)
    if not source.exists():
        logger.warning("Backup: source database not found: %s", source)
        return None

    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = _BACKUP_DIR / f"garmin_data_{timestamp}.db"

    try:
        shutil.copy2(source, dest)
        logger.info("Backup created: %s", dest)
        _prune_old_backups()
        return dest
    except Exception as exc:
        logger.error("Backup failed: %s", exc)
        return None


def _prune_old_backups() -> None:
    """Remove backups older than the retention limit."""
    backups = sorted(_BACKUP_DIR.glob("garmin_data_*.db"))
    to_remove = backups[:-_KEEP_BACKUPS] if len(backups) > _KEEP_BACKUPS else []
    for path in to_remove:
        try:
            path.unlink()
            logger.debug("Removed old backup: %s", path)
        except Exception as exc:
            logger.warning("Could not remove old backup %s: %s", path, exc)
