"""Command mixin classes for TelegramBot."""

from .body import BodyMixin
from .health import HealthMixin
from .nutrition import NutritionMixin
from .system import SystemMixin
from .training import TrainingMixin
from .xread import XreadMixin

__all__ = [
    "BodyMixin",
    "HealthMixin",
    "NutritionMixin",
    "SystemMixin",
    "TrainingMixin",
    "XreadMixin",
]
