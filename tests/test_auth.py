import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch

from app.auth import auth_manager


@pytest.fixture(autouse=True)
def temp_user_db(tmp_path):
    """Overrides DB_PATH to use a fresh temporary database for each test."""
    temp_db = tmp_path / "test_users.db"
    with patch.object(auth_manager, "DB_PATH", temp_db):
        auth_manager.init_user_db()
        yield temp_db


def test_default_admin_created():
    admin = auth_manager.get_user("admin")
    assert admin is not None
    assert admin["username"] == "admin"
    assert admin["role"] == "admin"

    # Authenticate admin
    ok, msg, data = auth_manager.authenticate_user("admin", "admin123")
    assert ok is True
    assert data["username"] == "admin"


def test_register_and_authenticate_user():
    success, msg = auth_manager.register_user(
        username="trader_bob",
        password="securePassword123",
        full_name="Bob Trader",
        email="bob@example.com",
    )
    assert success is True
    assert "created successfully" in msg.lower()

    # Successful login
    ok, msg, data = auth_manager.authenticate_user("trader_bob", "securePassword123")
    assert ok is True
    assert data["username"] == "trader_bob"
    assert data["full_name"] == "Bob Trader"

    # Bad password
    ok, msg, data = auth_manager.authenticate_user("trader_bob", "wrongPassword")
    assert ok is False
    assert "invalid" in msg.lower()


def test_register_duplicate_username():
    ok1, _ = auth_manager.register_user("unique_trader", "password123")
    assert ok1 is True

    ok2, msg2 = auth_manager.register_user("unique_trader", "anotherPassword")
    assert ok2 is False
    assert "already registered" in msg2.lower()


def test_register_invalid_inputs():
    # Username too short
    ok, msg = auth_manager.register_user("ab", "password123")
    assert ok is False

    # Password too short
    ok, msg = auth_manager.register_user("valid_user", "123")
    assert ok is False

    # Invalid characters in username
    ok, msg = auth_manager.register_user("bad user!", "password123")
    assert ok is False


def test_full_access_users():
    assert auth_manager.is_full_access_user("admin") is True
    assert auth_manager.is_full_access_user("nambirajan") is True
    assert auth_manager.is_full_access_user("seetharaman") is True
    assert auth_manager.is_full_access_user("Nambirajan") is True
    assert auth_manager.is_full_access_user("SEETHARAMAN") is True
    assert auth_manager.is_full_access_user("trader_bob") is False
    assert auth_manager.is_full_access_user("new_user") is False
    assert auth_manager.is_full_access_user(None) is False

