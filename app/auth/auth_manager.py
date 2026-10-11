import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "users.db"


def get_db_connection() -> sqlite3.Connection:
    """Returns a SQLite connection to the user database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_user_db():
    """Initializes the users table and seeds a default admin user if empty."""
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    full_name TEXT,
                    email TEXT,
                    role TEXT DEFAULT 'trader',
                    created_at TEXT NOT NULL,
                    last_login TEXT
                );
            """)

            # Check if any user exists; if none, seed default admin
            cursor = conn.execute("SELECT COUNT(*) as count FROM users")
            count = cursor.fetchone()["count"]
            if count == 0:
                _create_user_internal(
                    conn,
                    username="admin",
                    password="admin123",
                    full_name="Master Trader",
                    email="admin@autotrading.ai",
                    role="admin",
                )
                logger.info("Initialized default user: admin / admin123")
    finally:
        conn.close()


def _hash_password(password: str, salt: str) -> str:
    """Hashes password with salt using PBKDF2-HMAC-SHA256 (100,000 iterations)."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    ).hex()


def _create_user_internal(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    full_name: Optional[str] = None,
    email: Optional[str] = None,
    role: str = "trader",
) -> int:
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(password, salt)
    now = datetime.now().isoformat()
    cursor = conn.execute(
        """
        INSERT INTO users (username, password_hash, salt, full_name, email, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (username.strip(), pw_hash, salt, (full_name or "").strip(), (email or "").strip(), role, now),
    )
    return cursor.lastrowid


def register_user(
    username: str,
    password: str,
    full_name: Optional[str] = None,
    email: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Registers a new user account.
    Validates username format and password complexity.
    Returns (success: bool, message: str).
    """
    init_user_db()

    u = username.strip()
    if not u or len(u) < 3:
        return False, "Username must be at least 3 characters long."
    if len(u) > 30:
        return False, "Username cannot exceed 30 characters."
    if not re.match(r"^[a-zA-Z0-9_.-]+$", u):
        return False, "Username can only contain letters, numbers, dots, and underscores."

    if not password or len(password) < 6:
        return False, "Password must be at least 6 characters long."

    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.execute("SELECT id FROM users WHERE username = ?", (u,))
            if cursor.fetchone() is not None:
                return False, f"Username '{u}' is already registered. Please choose another."

            _create_user_internal(conn, u, password, full_name, email)
            return True, f"Account '{u}' created successfully! You can now log in."
    except sqlite3.IntegrityError:
        return False, f"Username '{u}' is already taken."
    except Exception as e:
        logger.exception("User registration failed: %s", e)
        return False, f"Registration failed: {str(e)}"
    finally:
        conn.close()


def authenticate_user(username: str, password: str) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Authenticates username and password.
    Returns (success: bool, message: str, user_dict: dict).
    """
    init_user_db()

    u = username.strip()
    if not u or not password:
        return False, "Username and password cannot be empty.", {}

    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "SELECT id, username, password_hash, salt, full_name, email, role FROM users WHERE username = ?",
            (u,),
        )
        row = cursor.fetchone()
        if not row:
            return False, "Invalid username or password.", {}

        calc_hash = _hash_password(password, row["salt"])
        if not hmac.compare_digest(row["password_hash"], calc_hash):
            return False, "Invalid username or password.", {}

        # Update last_login timestamp
        now = datetime.now().isoformat()
        with conn:
            conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, row["id"]))

        user_info = {
            "id": row["id"],
            "username": row["username"],
            "full_name": row["full_name"] or row["username"],
            "email": row["email"] or "",
            "role": row["role"],
        }
        return True, "Login successful!", user_info
    except Exception as e:
        logger.exception("Authentication error: %s", e)
        return False, f"Authentication error: {str(e)}", {}
    finally:
        conn.close()


def get_user(username: str) -> Optional[Dict[str, Any]]:
    """Fetches user details by username."""
    init_user_db()
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "SELECT id, username, full_name, email, role, created_at, last_login FROM users WHERE username = ?",
            (username.strip(),),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
