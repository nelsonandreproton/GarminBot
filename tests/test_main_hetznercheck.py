"""main.py wires HetznerCheck handlers in snapshot mode."""

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from src.main import _register_hetznercheck_handlers


@pytest.fixture
def fake_bot_handler():
    pkg = ModuleType("monitor")
    mod = ModuleType("monitor.bot_handler")
    mod.register_server_status_handler = MagicMock()
    mod.register_container_disk_handler = MagicMock()
    with patch.dict(sys.modules, {"monitor": pkg, "monitor.bot_handler": mod}):
        yield mod


def test_register_passes_status_dir_from_env(fake_bot_handler):
    app = MagicMock()
    env = {"HETZNERCHECK_CONFIG_PATH": "/c.yml", "HETZNERCHECK_STATUS_DIR": "/hetznerstatus"}
    with patch.dict("os.environ", env, clear=True):
        _register_hetznercheck_handlers(app)
    fake_bot_handler.register_server_status_handler.assert_called_once_with(
        app, config_path="/c.yml", status_dir="/hetznerstatus")
    fake_bot_handler.register_container_disk_handler.assert_called_once_with(
        app, status_dir="/hetznerstatus")


def test_register_status_dir_none_when_unset(fake_bot_handler):
    app = MagicMock()
    with patch.dict("os.environ", {"HETZNERCHECK_CONFIG_PATH": "/c.yml"}, clear=True):
        _register_hetznercheck_handlers(app)
    fake_bot_handler.register_container_disk_handler.assert_called_once_with(app, status_dir=None)


def test_register_skipped_without_config_path(fake_bot_handler):
    with patch.dict("os.environ", {}, clear=True):
        _register_hetznercheck_handlers(MagicMock())
    fake_bot_handler.register_server_status_handler.assert_not_called()


def test_register_survives_missing_module():
    with patch.dict(sys.modules, {"monitor.bot_handler": None}):
        with patch.dict("os.environ", {"HETZNERCHECK_CONFIG_PATH": "/c.yml"}, clear=True):
            _register_hetznercheck_handlers(MagicMock())  # logs a warning, no raise
