import pytest
from app.config.settings import Settings


def test_defaults_to_paper(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    assert Settings.from_env().trading_mode == "PAPER"


def test_invalid_mode_rejected(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "YOLO")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_live_requires_confirmation(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.delenv("CONFIRM_LIVE_TRADING", raising=False)
    with pytest.raises(ValueError):
        Settings.from_env()