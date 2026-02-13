"""Chart generation using matplotlib — returns PNG bytes for Telegram."""

from __future__ import annotations

import io
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _requires_matplotlib(func):
    """Decorator: log a warning and return None if matplotlib is unavailable."""
    def wrapper(*args, **kwargs):
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            logger.warning("matplotlib not installed; skipping chart generation")
            return None
        return func(*args, **kwargs)
    return wrapper


@_requires_matplotlib
def generate_weekly_chart(rows: list[Any], goals: dict[str, float] | None = None) -> bytes | None:
    """Generate a bar chart of daily steps and sleep for the last 7 days.

    Args:
        rows: List of DailyMetrics ORM objects ordered by date.
        goals: Optional user goals for reference lines.

    Returns:
        PNG image as bytes, or None if generation fails.
    """
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    steps_goal = (goals or {}).get("steps", 10000)
    sleep_goal = (goals or {}).get("sleep_hours", 7.0)

    try:
        dates = [r.date.strftime("%d/%m") for r in rows]
        steps = [r.steps or 0 for r in rows]
        sleep = [r.sleep_hours or 0 for r in rows]

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), facecolor="#1a1a2e")
        fig.suptitle("Últimos 7 dias", color="white", fontsize=14, fontweight="bold")

        bar_colors = ["#4ecca3" if s >= steps_goal else "#e94560" for s in steps]
        ax1.bar(dates, steps, color=bar_colors, edgecolor="none")
        ax1.axhline(steps_goal, color="white", linestyle="--", linewidth=0.8, alpha=0.5)
        ax1.set_ylabel("Passos", color="white")
        ax1.set_facecolor("#16213e")
        ax1.tick_params(colors="white")
        ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}".replace(",", ".")))
        for spine in ax1.spines.values():
            spine.set_edgecolor("#444")

        sleep_colors = ["#4ecca3" if h >= sleep_goal else "#e94560" for h in sleep]
        ax2.bar(dates, sleep, color=sleep_colors, edgecolor="none")
        ax2.axhline(sleep_goal, color="white", linestyle="--", linewidth=0.8, alpha=0.5)
        ax2.set_ylabel("Sono (h)", color="white")
        ax2.set_facecolor("#16213e")
        ax2.tick_params(colors="white")
        for spine in ax2.spines.values():
            spine.set_edgecolor("#444")

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as exc:
        logger.error("Weekly chart generation failed: %s", exc)
        return None


@_requires_matplotlib
def generate_monthly_chart(rows: list[Any], goals: dict[str, float] | None = None) -> bytes | None:
    """Generate a line chart of daily steps and sleep for up to 30 days.

    Includes a 7-day moving average trend line on each panel.

    Args:
        rows: List of DailyMetrics ORM objects ordered by date.
        goals: Optional user goals for reference lines.

    Returns:
        PNG image as bytes, or None if generation fails.
    """
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    import numpy as np

    steps_goal = (goals or {}).get("steps", 10000)
    sleep_goal = (goals or {}).get("sleep_hours", 7.0)

    try:
        dates = [r.date.strftime("%d/%m") for r in rows]
        steps = [r.steps or 0 for r in rows]
        sleep = [r.sleep_hours or 0 for r in rows]
        x = range(len(dates))

        def _moving_avg(values: list[float], window: int = 7) -> list[float | None]:
            result = []
            for i in range(len(values)):
                if i < window - 1:
                    result.append(None)
                else:
                    result.append(sum(values[i - window + 1:i + 1]) / window)
            return result

        steps_ma = _moving_avg(steps)
        sleep_ma = _moving_avg(sleep)

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), facecolor="#1a1a2e")
        fig.suptitle("Últimos 30 dias", color="white", fontsize=14, fontweight="bold")

        # Steps line chart
        ax1.plot(list(x), steps, color="#4ecca3", linewidth=1.5, alpha=0.6, label="Passos")
        ma_x = [i for i, v in enumerate(steps_ma) if v is not None]
        ma_y = [v for v in steps_ma if v is not None]
        if ma_x:
            ax1.plot(ma_x, ma_y, color="#f8b500", linewidth=2, label="Média 7d")
        ax1.axhline(steps_goal, color="white", linestyle="--", linewidth=0.8, alpha=0.4)
        ax1.fill_between(list(x), steps, alpha=0.15, color="#4ecca3")
        ax1.set_ylabel("Passos", color="white")
        ax1.set_facecolor("#16213e")
        ax1.tick_params(colors="white")
        ax1.legend(facecolor="#16213e", labelcolor="white", fontsize=8)
        ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}".replace(",", ".")))
        # Show only every 5th date label to avoid crowding
        tick_positions = list(range(0, len(dates), max(1, len(dates) // 6)))
        ax1.set_xticks(tick_positions)
        ax1.set_xticklabels([dates[i] for i in tick_positions])
        for spine in ax1.spines.values():
            spine.set_edgecolor("#444")

        # Sleep line chart
        ax2.plot(list(x), sleep, color="#4ecca3", linewidth=1.5, alpha=0.6, label="Sono")
        ma_x2 = [i for i, v in enumerate(sleep_ma) if v is not None]
        ma_y2 = [v for v in sleep_ma if v is not None]
        if ma_x2:
            ax2.plot(ma_x2, ma_y2, color="#f8b500", linewidth=2, label="Média 7d")
        ax2.axhline(sleep_goal, color="white", linestyle="--", linewidth=0.8, alpha=0.4)
        ax2.fill_between(list(x), sleep, alpha=0.15, color="#4ecca3")
        ax2.set_ylabel("Sono (h)", color="white")
        ax2.set_facecolor("#16213e")
        ax2.tick_params(colors="white")
        ax2.legend(facecolor="#16213e", labelcolor="white", fontsize=8)
        ax2.set_xticks(tick_positions)
        ax2.set_xticklabels([dates[i] for i in tick_positions])
        for spine in ax2.spines.values():
            spine.set_edgecolor("#444")

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as exc:
        logger.error("Monthly chart generation failed: %s", exc)
        return None
