"""
Naija Scholar V2
Property of Lighthouse Intel Academy

FastAPI backend for the Telegram Mini App and All-Seeing Eye Intelligence Portal.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import io
import json
import logging
import os
import queue
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import random
import zipfile
import zlib
from collections import Counter, deque
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Generator, List, Literal, Optional, Tuple
from urllib.parse import parse_qsl, quote

import psycopg2
import psycopg2.extras
import psycopg2.pool
import qrcode
import redis
import requests
import bot
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fpdf import FPDF
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ROOT = Path(__file__).resolve().parent

MAX_SYNC_PAYLOAD_LENGTH = 10_000
MAX_DECOMPRESSED_BYTES = 1_000_000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=APP_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "Naija Scholar V2"
    APP_BASE_URL: str = "https://t.me/Lighthouse_exam_prep_bot"
    ENVIRONMENT: str = "development"
    DEV_ENABLE_AUTH_BYPASS: bool = False
    CORS_ALLOW_ORIGINS: str = ""
    LOG_LEVEL: str = "INFO"
    PORT: int = 8000

    TELEGRAM_BOT_TOKEN: str = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
    TELEGRAM_BOT_USERNAME: str = "Lighthouse_exam_prep_bot"
    TELEGRAM_BOT_ENABLED: bool = True
    TELEGRAM_POLLING_ENABLED: bool = True
    TELEGRAM_WEBHOOK_URL: str = ""

    # Payment providers - enable what you have active
    OPAY_MERCHANT_ID: str = ""
    OPAY_PRIVATE_KEY: str = ""
    OPAY_PUBLIC_KEY: str = ""
    OPAY_WEBHOOK_SECRET: str = ""
    OPAY_BASE_URL: str = "https://api.opay.com"

    ZENITH_MERCHANT_ID: str = ""
    ZENITH_API_KEY: str = ""
    ZENITH_WEBHOOK_SECRET: str = ""
    ZENITH_BASE_URL: str = "https://api.zenithbank.com"

    PALMPAY_MERCHANT_ID: str = ""
    PALMPAY_PRIVATE_KEY: str = ""
    PALMPAY_WEBHOOK_SECRET: str = ""
    PALMPAY_BASE_URL: str = "https://api.palmpay.com"

    PAYSTACK_SECRET_KEY: str = ""
    PAYSTACK_WEBHOOK_SECRET: str = ""

    FLUTTERWAVE_SECRET_KEY: str = ""
    FLUTTERWAVE_WEBHOOK_SECRET: str = ""
    FLUTTERWAVE_PUBLIC_KEY: str = ""

    # Default payment provider to use
    DEFAULT_PAYMENT_PROVIDER: str = "opay"

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "naija_scholar"
    DATABASE_URL: str = ""

    SQLITE_PATH: str = str(APP_ROOT / "naija_scholar.sqlite3")
    ENABLE_SQLITE_FALLBACK: bool = True

    REDIS_URL: str = "redis://localhost:6379/0"

    SEED_ENABLED: bool = False
    SEED_INTERVAL_HOURS: float = 6.0

    # Redis-backed sliding-window rate limits (fail open when Redis is down)
    RATE_LIMIT_WEBHOOK_PER_MIN: int = 30
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_TELEGRAM_PER_MIN: int = 60
    RATE_LIMIT_TELEGRAM_WINDOW_SECONDS: int = 60

    # Interactive exam engine (multi-step sessions with timer + recovery)
    EXAM_IDLE_PAUSE_SECONDS: int = 180
    EXAM_RESUME_WINDOW_MINUTES: int = 120
    EXAM_MAX_QUESTIONS: int = 50
    EXAM_MAX_DURATION_MINUTES: int = 180

    TERMII_API_KEY: str = ""
    WATI_API_KEY: str = ""

    TUITION_AMOUNT_NAIRA: float = 2500.0
    PREMIUM_PRICE_NAIRA: float = 1500.0
    PARENT_PREMIUM_PRICE_NAIRA: float = 2500.0
    TEACHER_PREMIUM_PRICE_NAIRA: float = 2500.0
    SCHOOL_QUARTERLY_FEE_NAIRA: float = 50000.0
    SUPER_ADMIN_IDS: str = ""
    SUPER_ADMIN_CAP: int = 5


settings = Settings()

# Telegram bot integration state (populated at startup when enabled).
telegram_bot: Optional[bot.TelegramBot] = None
_polling_thread: Optional[threading.Thread] = None
QUIZ_SESSIONS: Dict[int, Dict[str, Any]] = {}
# Interactive exam engine state (Telegram wizard + active session pointers).
EXAM_WIZARDS: Dict[int, Dict[str, Any]] = {}
EXAM_ACTIVE: Dict[int, Dict[str, Any]] = {}
_start_time = time.time()

class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter for production observability."""
    def format(self, record: logging.LogRecord) -> str:
        import json
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data, ensure_ascii=False)


logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("naija-scholar-v2")

# Optional JSON logging for production (enable via env)
if os.environ.get("JSON_LOGS", "").lower() in ("1", "true", "yes"):
    json_handler = logging.StreamHandler(sys.stdout)
    json_handler.setFormatter(JsonFormatter())
    logger.handlers = [json_handler]


LOCAL_METRICS: List[Dict[str, Any]] = [
    {
        "class_arm": "SS2 Science",
        "subject": "Mathematics",
        "mean_score": 46,
        "median_score": 48,
        "high_error_topic": "Logarithms",
        "error_rate": 62,
        "mastery": {"recall": 58, "conceptual": 43, "problem_solving": 39, "speed": 64},
        "speed_accuracy": {"speed": 72, "accuracy": 44},
        "current_score": 46,
        "previous_term_score": 33,
    },
    {
        "class_arm": "SS2 Science",
        "subject": "English",
        "mean_score": 61,
        "median_score": 63,
        "high_error_topic": "Summary Writing",
        "error_rate": 34,
        "mastery": {"recall": 66, "conceptual": 59, "problem_solving": 55, "speed": 68},
        "speed_accuracy": {"speed": 61, "accuracy": 74},
        "current_score": 61,
        "previous_term_score": 58,
    },
    {
        "class_arm": "SS2 Science",
        "subject": "Physics",
        "mean_score": 54,
        "median_score": 55,
        "high_error_topic": "Waves",
        "error_rate": 41,
        "mastery": {"recall": 57, "conceptual": 49, "problem_solving": 52, "speed": 50},
        "speed_accuracy": {"speed": 45, "accuracy": 66},
        "current_score": 54,
        "previous_term_score": 47,
    },
]

LOCAL_QUESTIONS: List[Dict[str, Any]] = [
    {
        "subject": "Mathematics",
        "topic": "Logarithms",
        "exam_type": "WAEC",
        "class_level": "SS2",
        "question_text": "If log10 1000 = x, what is the value of x?",
        "options": ["2", "3", "4", "10"],
        "correct_answer": "3",
        "explanation": "Step 1: Rewrite 1000 as a power of 10. Step 2: 1000 = 10^3. Step 3: A logarithm gives the exponent. Step 4: Therefore x = 3.",
        "difficulty": "Easy",
    },
    {
        "subject": "Mathematics",
        "topic": "Logarithms",
        "exam_type": "JAMB",
        "class_level": "SS2",
        "question_text": "Evaluate log2 32.",
        "options": ["4", "5", "6", "8"],
        "correct_answer": "5",
        "explanation": "Step 1: Express 32 in index form with base 2. Step 2: 32 = 2^5. Step 3: log2 32 asks for the exponent of 2 that gives 32. Step 4: The value is 5.",
        "difficulty": "Medium",
    },
    {
        "subject": "English Language",
        "topic": "Summary Writing",
        "exam_type": "WAEC",
        "class_level": "SS2",
        "question_text": "The most important sentence in a summary is the sentence that states the?",
        "options": ["date", "main idea", "author", "paragraph length"],
        "correct_answer": "main idea",
        "explanation": "Step 1: A summary focuses on the central thought of a passage. Step 2: The central thought is called the main idea. Step 3: Details like date or paragraph length are secondary. Step 4: Therefore the correct answer is main idea.",
        "difficulty": "Medium",
    },
    {
        "subject": "Physics",
        "topic": "Waves",
        "exam_type": "NECO",
        "class_level": "SS2",
        "question_text": "Wave speed is equal to frequency multiplied by?",
        "options": ["amplitude", "wavelength", "period", "energy"],
        "correct_answer": "wavelength",
        "explanation": "Step 1: Recall the wave equation v = f x lambda. Step 2: In that equation, f is frequency and lambda is wavelength. Step 3: Therefore wave speed is the product of frequency and wavelength. Step 4: The correct answer is wavelength.",
        "difficulty": "Medium",
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=True)


def build_referral_code(user_id: int) -> str:
    return f"NS{user_id:06d}"


def build_referral_link(referral_code: str) -> str:
    return f"{settings.APP_BASE_URL}?start=ref_{referral_code}"


def build_access_code() -> str:
    return f"NS-{secrets.token_hex(4).upper()}"


def compress_payload(data: Dict[str, Any]) -> str:
    raw = json_dumps(data).encode("utf-8")
    encoded = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii")
    return encoded.rstrip("=")


def decompress_payload(compact: str) -> Dict[str, Any]:
    if compact.startswith("plain."):
        plain = compact.split(".", 1)[1]
        padded_plain = plain + ("=" * ((4 - len(plain) % 4) % 4))
        raw_plain = base64.urlsafe_b64decode(padded_plain.encode("ascii")).decode("utf-8")
        if len(raw_plain) > MAX_DECOMPRESSED_BYTES:
            raise ValueError("Decoded payload exceeds size limit")
        loaded_plain = json.loads(raw_plain)
        if not isinstance(loaded_plain, dict):
            raise ValueError("Decoded payload must be an object")
        return loaded_plain
    padded = compact + ("=" * ((4 - len(compact) % 4) % 4))
    compressed = base64.urlsafe_b64decode(padded.encode("ascii"))
    loaded = None
    last_error: Optional[BaseException] = None
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            decompressor = zlib.decompressobj(wbits)
            raw = decompressor.decompress(compressed, MAX_DECOMPRESSED_BYTES + 1)
            if decompressor.unconsumed_tail:
                raise ValueError("Decoded payload exceeds size limit")
            loaded = json.loads(raw.decode("utf-8"))
            break
        except zlib.error as exc:
            last_error = exc
    if loaded is None:
        raise ValueError("Invalid compressed payload") from last_error
    if not isinstance(loaded, dict):
        raise ValueError("Decoded payload must be an object")
    return loaded


def verify_telegram_init_data(init_data: str, bot_token: str) -> Optional[Dict[str, Any]]:
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
        received_hash = pairs.pop("hash", "")
        if not received_hash:
            return None

        data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        expected_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash):
            return None

        try:
            auth_timestamp = int(pairs.get("auth_date") or 0)
        except (TypeError, ValueError):
            auth_timestamp = 0
        if auth_timestamp <= 0 or abs(time.time() - auth_timestamp) > 86400:
            return None

        user_blob = pairs.get("user")
        if user_blob:
            user = json.loads(user_blob)
            if isinstance(user, dict):
                user["auth_date"] = pairs.get("auth_date")
                user["start_param"] = pairs.get("start_param")
                return user
        return pairs
    except (ValueError, json.JSONDecodeError, TypeError):
        return None


class DatabaseManager:
    def __init__(self) -> None:
        self.mode: Literal["postgres", "sqlite"] = "postgres"
        self.pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
        self.sqlite_path = settings.SQLITE_PATH
        self._lock = threading.Lock()

    def init(self) -> None:
        with self._lock:
            if self.pool is not None:
                return
            try:
                dsn = (
                    f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
                    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
                )
                if settings.DATABASE_URL:
                    dsn = settings.DATABASE_URL
                self.pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=dsn)
                self.mode = "postgres"
                logger.info("PostgreSQL pool initialized")
            except Exception as exc:  # pragma: no cover - fallback path depends on host state
                self.pool = None
                if not settings.ENABLE_SQLITE_FALLBACK:
                    raise
                self.mode = "sqlite"
                logger.warning("PostgreSQL unavailable, using SQLite fallback: %s", exc)

    def close(self) -> None:
        if self.pool is not None:
            self.pool.closeall()
            self.pool = None

    @contextmanager
    def cursor(self, commit: bool = False) -> Generator[Tuple[Any, Any], None, None]:
        if self.mode == "postgres":
            if self.pool is None:
                raise RuntimeError("Database pool not initialized")
            conn = self.pool.getconn()
            try:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                try:
                    yield conn, cur
                    if commit:
                        conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    cur.close()
            finally:
                self.pool.putconn(conn)
            return

        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            try:
                yield conn, cur
                if commit:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
        finally:
            conn.close()

    def fetch_all(self, query: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        with self.cursor() as (_, cur):
            cur.execute(query, params)
            rows = cur.fetchall()
        return [self._row_to_dict(row) for row in rows]

    def fetch_one(self, query: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
        with self.cursor() as (_, cur):
            cur.execute(query, params)
            row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def execute(self, query: str, params: Tuple[Any, ...] = ()) -> None:
        with self.cursor(commit=True) as (_, cur):
            cur.execute(query, params)

    @contextmanager
    def transaction(self) -> Generator[Tuple[Any, Any], None, None]:
        if self.mode == "postgres":
            if self.pool is None:
                raise RuntimeError("Database pool not initialized")
            conn = self.pool.getconn()
            try:
                conn.autocommit = False
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                try:
                    cur.execute("BEGIN")
                    yield conn, cur
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.autocommit = True
                    cur.close()
            finally:
                self.pool.putconn(conn)
            return

        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.cursor()
            try:
                yield conn, cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
        finally:
            conn.close()

    def _row_to_dict(self, row: Any) -> Dict[str, Any]:
        if isinstance(row, sqlite3.Row):
            return dict(row)
        if isinstance(row, dict):
            return dict(row)
        return dict(row)  # pragma: no cover


db = DatabaseManager()
cache: Optional[redis.Redis] = None


def init_cache() -> None:
    global cache
    try:
        client = redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
        if client is not None:
            client.ping()
            cache = client
            logger.info("Redis cache connected")
    except (redis.ConnectionError, redis.TimeoutError) as exc:
        cache = None
        logger.warning("Redis unavailable, continuing without cache: %s", exc)


def close_cache() -> None:
    global cache
    if cache is not None:
        cache.close()
        cache = None


def is_super_admin(telegram_id: Any) -> bool:
    try:
        candidate = int(telegram_id)
    except (TypeError, ValueError):
        return False
    ids = {int(part.strip()) for part in settings.SUPER_ADMIN_IDS.split(",") if part.strip().isdigit()}
    return candidate in ids


def normalize_role(role: Any) -> str:
    """Map any casing/spelling to the canonical role constant (default student)."""
    if not role:
        return ROLE_STUDENT
    raw = str(role).strip()
    upper = raw.upper()
    if upper in (ROLE_SUPER_ADMIN, ROLE_SCHOOL_ADMIN, ROLE_TEACHER, ROLE_PARENT, ROLE_STUDENT):
        return upper
    normalized = " ".join(raw.lower().replace("/", " ").replace("_", " ").split())
    aliases = {
        "student": ROLE_STUDENT,
        "parent": ROLE_PARENT,
        "teacher": ROLE_TEACHER,
        "school": ROLE_SCHOOL_ADMIN,
        "school admin": ROLE_SCHOOL_ADMIN,
        "admin": ROLE_SUPER_ADMIN,
        "god": ROLE_SUPER_ADMIN,
        "super admin": ROLE_SUPER_ADMIN,
        "super admin god mode": ROLE_SUPER_ADMIN,
    }
    return aliases.get(normalized, ROLE_STUDENT)


def set_profile_role(telegram_id: int, role: str, source: str = "onboarding") -> Dict[str, Any]:
    previous = get_profile(telegram_id) or {}
    canonical = normalize_role(role)
    if canonical == ROLE_SUPER_ADMIN and not is_super_admin(telegram_id):
        raise ValueError("Super admin role cannot be self-assigned")
    db.execute(
        "UPDATE profiles SET role = ?, onboarded = 1, updated_at = ? WHERE telegram_id = ?"
        if db.mode == "sqlite"
        else "UPDATE profiles SET role = %s, onboarded = TRUE, updated_at = %s WHERE telegram_id = %s",
        (canonical, utc_now(), telegram_id),
    )
    log_telemetry(
        telegram_id,
        canonical,
        "role_changed",
        {"from": previous.get("role"), "to": canonical, "source": source},
    )
    return get_profile(telegram_id) or {"telegram_id": telegram_id, "role": canonical}


# ---- Stealth telemetry ("God Mode Tracker") ------------------------------
#
# Non-blocking background event logging. Hooks across the app push cheap
# payloads onto an in-memory queue; a single daemon worker drains them into
# `system_telemetry`. Failures are always silent: telemetry never interrupts
# the student, parent, teacher or admin experience.
_TELEMETRY_QUEUE: "queue.Queue" = queue.Queue(maxsize=2000)
_TELEMETRY_WORKER: Optional[threading.Thread] = None
_TELEMETRY_COND = threading.Condition()
_TELEMETRY_IN_FLIGHT = 0


def _commit_telemetry_row(payload: Dict[str, Any]) -> None:
    global _TELEMETRY_IN_FLIGHT
    with _TELEMETRY_COND:
        _TELEMETRY_IN_FLIGHT += 1
    try:
        db.execute(
            "INSERT INTO system_telemetry (user_id, role, event_type, event_data, session_id) "
            "VALUES (?, ?, ?, ?, ?)"
            if db.mode == "sqlite"
            else "INSERT INTO system_telemetry (user_id, role, event_type, event_data, session_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                str(payload["user_id"]),
                payload.get("role") or ROLE_STUDENT,
                payload["event_type"],
                _jsonb_write(payload.get("event_data") or {}),
                payload.get("session_id"),
            ),
        )
    except Exception as exc:  # telemetry must never bubble up to the user
        logger.debug("TELEMETRY_SILENT_DROP event=%s: %s", payload.get("event_type"), exc)
    finally:
        with _TELEMETRY_COND:
            _TELEMETRY_IN_FLIGHT -= 1
            _TELEMETRY_COND.notify_all()


def log_telemetry(
    user_id: Any,
    role: Any,
    event_type: str,
    event_data: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    *,
    sync: bool = False,
) -> None:
    """Queue a stealth telemetry event. Non-blocking and silent by design."""
    payload = {
        "user_id": user_id,
        "role": normalize_role(role),
        "event_type": event_type,
        "event_data": event_data or {},
        "session_id": session_id,
    }
    if sync:
        _commit_telemetry_row(payload)
        return
    try:
        _TELEMETRY_QUEUE.put_nowait(payload)
    except queue.Full:
        logger.warning("TELEMETRY_QUEUE_FULL dropping event=%s", event_type)


def _telemetry_worker() -> None:
    while True:
        payload = _TELEMETRY_QUEUE.get()
        if payload is None:
            return
        _commit_telemetry_row(payload)


def start_telemetry_worker() -> None:
    global _TELEMETRY_WORKER
    if _TELEMETRY_WORKER is not None and _TELEMETRY_WORKER.is_alive():
        return
    _TELEMETRY_WORKER = threading.Thread(target=_telemetry_worker, name="telemetry-worker", daemon=True)
    _TELEMETRY_WORKER.start()


def flush_telemetry_queue() -> int:
    """Drain outstanding events synchronously (shutdown + test determinism)."""
    drained = 0
    while True:
        try:
            payload = _TELEMETRY_QUEUE.get_nowait()
        except queue.Empty:
            break
        if payload is not None:
            _commit_telemetry_row(payload)
            drained += 1
    with _TELEMETRY_COND:
        while _TELEMETRY_IN_FLIGHT > 0:
            _TELEMETRY_COND.wait(timeout=0.5)
    return drained


def stop_telemetry_worker() -> None:
    global _TELEMETRY_WORKER
    if _TELEMETRY_WORKER is None:
        return
    flush_telemetry_queue()
    try:
        _TELEMETRY_QUEUE.put_nowait(None)
    except queue.Full:
        pass
    _TELEMETRY_WORKER.join(timeout=2)
    _TELEMETRY_WORKER = None


ROLE_SUPER_ADMIN = "SUPER_ADMIN_GOD_MODE"
ROLE_SCHOOL_ADMIN = "SCHOOL_ADMIN"
ROLE_TEACHER = "TEACHER"
ROLE_PARENT = "PARENT"
ROLE_STUDENT = "STUDENT"

CODE_KIND_PREFIX = {
    "school_class": "CLS",
    "teacher_invite": "TCH",
    "school_admin_invite": "ADM",
    "super_key": "SUP",
}


def role_from_telegram_id(telegram_id: int) -> str:
    return ROLE_SUPER_ADMIN if is_super_admin(telegram_id) else ROLE_STUDENT


def get_profile(telegram_id: int) -> Optional[Dict[str, Any]]:
    return db.fetch_one(
        "SELECT telegram_id, role, school_id, class_code, linking_code, parent_mode, premium_until, onboarded "
        "FROM profiles WHERE telegram_id = ?"
        if db.mode == "sqlite"
        else "SELECT telegram_id, role, school_id, class_code, linking_code, parent_mode, premium_until, onboarded "
        "FROM profiles WHERE telegram_id = %s",
        (telegram_id,),
    )


def ensure_profile(telegram_id: int) -> Dict[str, Any]:
    profile = get_profile(telegram_id)
    if profile:
        return profile
    role = role_from_telegram_id(telegram_id)
    linking_code = f"LIA-{telegram_id:06d}{secrets.token_hex(1).upper()}"
    db.execute(
        "INSERT INTO profiles (telegram_id, role, linking_code) VALUES (?, ?, ?)"
        if db.mode == "sqlite"
        else "INSERT INTO profiles (telegram_id, role, linking_code) VALUES (%s, %s, %s)",
        (telegram_id, role, linking_code),
    )
    return get_profile(telegram_id) or {"telegram_id": telegram_id, "role": role, "linking_code": linking_code}


def upsert_user_record(
    telegram_id: int,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    username: Optional[str] = None,
) -> None:
    """Keep users table in sync with Telegram identity (bot & web flows)."""
    full_name = " ".join(
        part for part in [first_name or "", last_name or ""] if part
    ).strip() or "Naija Scholar User"
    db.execute(
        """
        INSERT INTO users (telegram_id, username, full_name, role, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name,
            updated_at = excluded.updated_at
        """
        if db.mode == "sqlite"
        else """
        INSERT INTO users (telegram_id, username, full_name, role, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (telegram_id) DO UPDATE SET
            username = EXCLUDED.username,
            full_name = EXCLUDED.full_name,
            updated_at = EXCLUDED.updated_at
        """,
        (telegram_id, username, full_name, "student", utc_now()),
    )


def is_premium(profile: Dict[str, Any]) -> bool:
    until = profile.get("premium_until")
    if not until:
        return False
    try:
        expiry = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
    except ValueError:
        return False
    return expiry > datetime.now(timezone.utc)


def super_admin_count() -> int:
    row = db.fetch_one(
        "SELECT COUNT(*) AS count FROM profiles WHERE role = ?"
        if db.mode == "sqlite"
        else "SELECT COUNT(*) AS count FROM profiles WHERE role = %s",
        (ROLE_SUPER_ADMIN,),
    )
    return int(row.get("count", 0)) if row else 0


def linked_children(telegram_id: int) -> List[int]:
    rows = db.fetch_all(
        "SELECT student_id FROM student_links WHERE parent_id = ? AND status = 'active'"
        if db.mode == "sqlite"
        else "SELECT student_id FROM student_links WHERE parent_id = %s AND status = 'active'",
        (telegram_id,),
    )
    return [int(row["student_id"]) for row in rows]


def build_access_code_token(kind: str) -> str:
    prefix = CODE_KIND_PREFIX.get(kind, "LIA")
    return f"{prefix}-{secrets.token_hex(3).upper()}"


def current_user(
    authorization: Optional[str] = Header(default=None),
    x_dev_user: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    if not authorization and settings.ENVIRONMENT == "development" and settings.DEV_ENABLE_AUTH_BYPASS and x_dev_user:
        try:
            dev_id = int(x_dev_user)
        except (TypeError, ValueError):
            dev_id = 0
        if dev_id <= 0:
            raise HTTPException(status_code=401, detail="Invalid X-Dev-User header")
        profile = ensure_profile(dev_id)
        dev_user = db.fetch_one(
            "SELECT full_name FROM users WHERE telegram_id = ?"
            if db.mode == "sqlite"
            else "SELECT full_name FROM users WHERE telegram_id = %s",
            (dev_id,),
        )
        logger.warning("DEV-ONLY auth bypass used for telegram_id=%s (ENVIRONMENT=development, DEV_ENABLE_AUTH_BYPASS=true)", dev_id)
        user_info = {
            "id": dev_id,
            "first_name": (dev_user.get("full_name") if dev_user else None) or "Dev",
            "username": None,
        }
    elif authorization:
        user_info = verify_telegram_init_data(authorization, settings.TELEGRAM_BOT_TOKEN)
        if not user_info:
            raise HTTPException(status_code=401, detail="Invalid Telegram initData signature")
        try:
            dev_id = int(user_info.get("id", 0))
        except (TypeError, ValueError):
            dev_id = 0
        if dev_id <= 0:
            raise HTTPException(status_code=401, detail="Invalid Telegram user id")
        profile = ensure_profile(dev_id)
    else:
        raise HTTPException(status_code=401, detail="Missing X-Telegram-InitData header")
    full_name = " ".join(
        part for part in [user_info.get("first_name"), user_info.get("last_name")] if part
    ).strip() or "Naija Scholar User"
    role = ROLE_SUPER_ADMIN if is_super_admin(dev_id) else normalize_role(profile.get("role"))
    return {
        "telegram_id": dev_id,
        "full_name": full_name,
        "username": user_info.get("username") or None,
        **profile,
        "role": role,
        "role_slug": str(role).lower(),
        "onboarded": bool(profile.get("onboarded")),
        "premium": is_premium(profile),
    }


def require_roles(*roles: str):
    allowed = {normalize_role(role) for role in roles}

    def dependency(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        if normalize_role(user.get("role")) not in allowed:
            raise HTTPException(status_code=403, detail=f"Requires role: {', '.join(roles)}")
        return user

    return dependency


def require_super_admin(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """Strict god-mode gate — only the owner Telegram ID / SUPER_ADMIN_IDS may pass."""
    if not is_super_admin(user["telegram_id"]):
        raise HTTPException(status_code=403, detail="Super admin access required")
    return user


def cache_get_json(key: str) -> Optional[Any]:
    if cache is None:
        return None
    try:
        raw = cache.get(key)
    except (redis.ConnectionError, redis.TimeoutError):
        logger.warning("CACHE_ERROR get key=%s (Redis unreachable, falling back to database)", key)
        return None
    if not raw:
        logger.info("CACHE_MISS key=%s", key)
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.info("CACHE_MISS key=%s reason=corrupt_entry", key)
        return None
    logger.info("CACHE_HIT key=%s", key)
    return value


def cache_set_json(key: str, value: Any, ttl_seconds: int = 120) -> None:
    if cache is None:
        return
    try:
        cache.setex(key, ttl_seconds, json.dumps(value))
        logger.debug("CACHE_SET key=%s ttl=%ss", key, ttl_seconds)
    except (redis.ConnectionError, redis.TimeoutError):
        logger.warning("CACHE_ERROR set key=%s (Redis unreachable, value not cached)", key)
        return


def cache_delete_prefix(prefix: str) -> int:
    """Delete every cache key starting with `prefix`. Returns how many keys were removed."""
    if cache is None:
        return 0
    try:
        keys = list(cache.scan_iter(match=f"{prefix}*"))
        if not keys:
            return 0
        deleted = int(cache.delete(*keys))
    except (redis.ConnectionError, redis.TimeoutError):
        logger.warning("CACHE_ERROR invalidate prefix=%s (Redis unreachable)", prefix)
        return 0
    if deleted:
        logger.info("CACHE_INVALIDATE prefix=%s deleted=%s", prefix, deleted)
    return deleted


def invalidate_student_caches(telegram_id: Optional[int] = None) -> None:
    """Purge cached analytics after new quiz data lands (per-student when id given)."""
    stats_prefix = f"stats:student:{telegram_id}:" if telegram_id else "stats:student:"
    cache_delete_prefix(stats_prefix)
    cache_delete_prefix("remedial:")
    cache_delete_prefix("benchmark:")


def rate_limit_check(bucket: str, identifier: str, limit: int, window_seconds: int) -> bool:
    """Redis-backed sliding-window rate limiter. Returns True when the request is allowed.

    Fails open (allows the request) when Redis is unavailable so the bot never
    goes down because the limiter does.
    """
    if cache is None:
        return True
    key = f"ratelimit:{bucket}:{identifier}"
    now = time.time()
    try:
        cache.zremrangebyscore(key, 0, now - window_seconds)
        cache.zadd(key, {f"{now}:{secrets.token_hex(4)}": now})
        count = int(cache.zcard(key))
        cache.expire(key, window_seconds)
    except (redis.ConnectionError, redis.TimeoutError):
        logger.warning("RATE_LIMIT_ERROR bucket=%s id=%s (Redis unreachable, failing open)", bucket, identifier)
        return True
    if count > limit:
        logger.warning("RATE_LIMITED bucket=%s id=%s count=%s limit=%s", bucket, identifier, count, limit)
        return False
    return True


POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(64),
    full_name VARCHAR(120) NOT NULL,
    role VARCHAR(30) NOT NULL DEFAULT 'student',
    referral_code VARCHAR(24) UNIQUE,
    referred_by VARCHAR(24),
    access_code VARCHAR(40),
    access_unlocked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subject_metrics (
    id BIGSERIAL PRIMARY KEY,
    class_arm VARCHAR(40) NOT NULL,
    subject VARCHAR(80) NOT NULL,
    mean_score NUMERIC(5,2) NOT NULL,
    median_score NUMERIC(5,2) NOT NULL,
    high_error_topic VARCHAR(120) NOT NULL,
    error_rate INTEGER NOT NULL,
    mastery_json TEXT NOT NULL,
    speed_accuracy_json TEXT NOT NULL,
    current_score NUMERIC(5,2) NOT NULL,
    previous_term_score NUMERIC(5,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS question_bank (
    id BIGSERIAL PRIMARY KEY,
    exam_type VARCHAR(20) NOT NULL,
    subject VARCHAR(80) NOT NULL,
    topic VARCHAR(120) NOT NULL,
    class_level VARCHAR(40) NOT NULL,
    question_text TEXT NOT NULL,
    options JSONB NOT NULL,
    correct_answer TEXT NOT NULL,
    explanation TEXT NOT NULL,
    difficulty VARCHAR(20) NOT NULL,
    UNIQUE (exam_type, subject, question_text)
);

CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    reference VARCHAR(100) UNIQUE NOT NULL,
    telegram_id BIGINT,
    provider VARCHAR(20) NOT NULL,
    status VARCHAR(30) NOT NULL,
    amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    access_code VARCHAR(40),
    raw_payload TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_events (
    id BIGSERIAL PRIMARY KEY,
    payload_hash VARCHAR(64) UNIQUE NOT NULL,
    telegram_id BIGINT,
    source VARCHAR(30) NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schools (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(160) NOT NULL,
    code VARCHAR(20) UNIQUE NOT NULL,
    plan VARCHAR(30) NOT NULL DEFAULT 'standard',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS profiles (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    role VARCHAR(30) NOT NULL DEFAULT 'STUDENT',
    school_id BIGINT,
    class_code VARCHAR(40),
    linking_code VARCHAR(24) UNIQUE,
    parent_mode VARCHAR(10) NOT NULL DEFAULT 'free',
    premium_until TIMESTAMPTZ,
    onboarded BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS access_codes (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(40) UNIQUE NOT NULL,
    kind VARCHAR(30) NOT NULL,
    payload TEXT,
    used_by BIGINT,
    expires_at TIMESTAMPTZ,
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS student_links (
    id BIGSERIAL PRIMARY KEY,
    parent_id BIGINT NOT NULL,
    student_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (parent_id, student_id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    tier VARCHAR(20) NOT NULL DEFAULT 'premium',
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    amount NUMERIC(12,2) NOT NULL DEFAULT 0,
    reference VARCHAR(100) UNIQUE,
    starts_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    school_id BIGINT,
    class_code VARCHAR(40),
    subject VARCHAR(80) NOT NULL,
    topic VARCHAR(120),
    title VARCHAR(160),
    source VARCHAR(30) NOT NULL DEFAULT 'web',
    client_attempt_id VARCHAR(64),
    score NUMERIC(6,2) NOT NULL,
    total INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    seconds_spent NUMERIC(10,2),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    trust_score NUMERIC(6,2),
    rush_events INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (telegram_id, client_attempt_id)
);

CREATE TABLE IF NOT EXISTS question_responses (
    id BIGSERIAL PRIMARY KEY,
    attempt_id BIGINT NOT NULL,
    telegram_id BIGINT NOT NULL,
    question_id BIGINT,
    question_text TEXT NOT NULL,
    subject VARCHAR(80),
    topic VARCHAR(120),
    sub_topic VARCHAR(120),
    selected_answer TEXT,
    correct_answer TEXT NOT NULL,
    is_correct INTEGER NOT NULL,
    seconds_spent NUMERIC(10,2),
    switches INTEGER NOT NULL DEFAULT 0,
    switch_trail TEXT,
    is_time_sink INTEGER NOT NULL DEFAULT 0,
    is_rushed INTEGER NOT NULL DEFAULT 0,
    error_type VARCHAR(30)
);

CREATE TABLE IF NOT EXISTS behavior_events (
    id BIGSERIAL PRIMARY KEY,
    attempt_id BIGINT NOT NULL,
    telegram_id BIGINT NOT NULL,
    event_type VARCHAR(30) NOT NULL,
    detail TEXT,
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS intervention_contracts (
    id BIGSERIAL PRIMARY KEY,
    student_id BIGINT NOT NULL,
    parent_id BIGINT,
    teacher_id BIGINT NOT NULL,
    target_text TEXT NOT NULL,
    threshold_score NUMERIC(6,2) NOT NULL,
    deadline TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS micro_bounties (
    id BIGSERIAL PRIMARY KEY,
    parent_id BIGINT NOT NULL,
    student_id BIGINT NOT NULL,
    title TEXT NOT NULL,
    reward TEXT NOT NULL,
    target_score NUMERIC(6,2) NOT NULL,
    subject VARCHAR(80),
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    claimed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS guardian_digests (
    id BIGSERIAL PRIMARY KEY,
    parent_id BIGINT NOT NULL,
    student_id BIGINT NOT NULL,
    week_start TIMESTAMPTZ NOT NULL,
    digest_text TEXT NOT NULL,
    sent_via VARCHAR(20) DEFAULT 'whatsapp',
    delivered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS assignments (
    id BIGSERIAL PRIMARY KEY,
    teacher_id BIGINT NOT NULL,
    school_id BIGINT,
    class_code VARCHAR(40) NOT NULL,
    subject VARCHAR(80) NOT NULL,
    topic VARCHAR(120),
    limit_count INTEGER NOT NULL DEFAULT 5,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS study_windows (
    id BIGSERIAL PRIMARY KEY,
    parent_id BIGINT NOT NULL,
    student_id BIGINT NOT NULL,
    day_label VARCHAR(12) NOT NULL DEFAULT 'weekdays',
    start_time VARCHAR(5) NOT NULL,
    end_time VARCHAR(5) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_alerted_on DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (parent_id, student_id, day_label)
);

CREATE TABLE IF NOT EXISTS early_warnings (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    attempt_id BIGINT,
    subject VARCHAR(80) NOT NULL,
    score NUMERIC(6,2) NOT NULL,
    personal_median NUMERIC(6,2) NOT NULL,
    stddev NUMERIC(6,2) NOT NULL DEFAULT 0,
    dispatched_to TEXT DEFAULT 'parent,teacher',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quiz_papers (
    id BIGSERIAL PRIMARY KEY,
    teacher_id BIGINT NOT NULL,
    school_id BIGINT,
    title VARCHAR(160) NOT NULL,
    subject VARCHAR(80) NOT NULL,
    class_code VARCHAR(40),
    question_ids TEXT NOT NULL DEFAULT '[]',
    question_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quiz_sessions (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) UNIQUE NOT NULL,
    telegram_id BIGINT NOT NULL,
    exam_type VARCHAR(20) NOT NULL,
    subject VARCHAR(80) NOT NULL,
    mode VARCHAR(20) NOT NULL DEFAULT 'standard',
    total_questions INTEGER NOT NULL,
    current_index INTEGER NOT NULL DEFAULT 0,
    time_limit_seconds INTEGER NOT NULL,
    time_remaining NUMERIC(12,2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'IN_PROGRESS',
    answers JSONB NOT NULL DEFAULT '[]',
    questions_json JSONB NOT NULL DEFAULT '[]',
    attempt_id BIGINT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS answer_telemetry (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    attempt_id BIGINT,
    telegram_id BIGINT NOT NULL,
    question_id BIGINT,
    subject VARCHAR(80),
    topic VARCHAR(120),
    selected_answer TEXT,
    confidence_level VARCHAR(10),
    is_correct INTEGER NOT NULL DEFAULT 0,
    time_spent_seconds NUMERIC(10,2) NOT NULL DEFAULT 0,
    answer_changes INTEGER NOT NULL DEFAULT 0,
    event_type VARCHAR(20) NOT NULL DEFAULT 'answer',
    answered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS student_topic_stats (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    subject VARCHAR(80) NOT NULL,
    topic VARCHAR(120) NOT NULL,
    correct_count INTEGER NOT NULL DEFAULT 0,
    incorrect_count INTEGER NOT NULL DEFAULT 0,
    total_time_seconds NUMERIC(12,2) NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_seen_at TIMESTAMPTZ,
    UNIQUE (telegram_id, subject, topic)
);

CREATE TABLE IF NOT EXISTS system_telemetry (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(100) NOT NULL,
    role VARCHAR(20) NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_data JSONB NOT NULL DEFAULT '{}',
    session_id VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_telemetry_user ON system_telemetry(user_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_event ON system_telemetry(event_type);
"""

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    username TEXT,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student',
    referral_code TEXT UNIQUE,
    referred_by TEXT,
    access_code TEXT,
    access_unlocked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subject_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_arm TEXT NOT NULL,
    subject TEXT NOT NULL,
    mean_score REAL NOT NULL,
    median_score REAL NOT NULL,
    high_error_topic TEXT NOT NULL,
    error_rate INTEGER NOT NULL,
    mastery_json TEXT NOT NULL,
    speed_accuracy_json TEXT NOT NULL,
    current_score REAL NOT NULL,
    previous_term_score REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS question_bank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    topic TEXT NOT NULL,
    class_level TEXT NOT NULL,
    question_text TEXT NOT NULL,
    options TEXT NOT NULL,
    correct_answer TEXT NOT NULL,
    explanation TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    UNIQUE (exam_type, subject, question_text)
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT UNIQUE NOT NULL,
    telegram_id INTEGER,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    access_code TEXT,
    raw_payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload_hash TEXT UNIQUE NOT NULL,
    telegram_id INTEGER,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT UNIQUE NOT NULL,
    plan TEXT NOT NULL DEFAULT 'standard',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    role TEXT NOT NULL DEFAULT 'STUDENT',
    school_id INTEGER,
    class_code TEXT,
    linking_code TEXT UNIQUE,
    parent_mode TEXT NOT NULL DEFAULT 'free',
    premium_until TEXT,
    onboarded INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS access_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT,
    used_by INTEGER,
    expires_at TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS student_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (parent_id, student_id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    tier TEXT NOT NULL DEFAULT 'premium',
    status TEXT NOT NULL DEFAULT 'active',
    amount REAL NOT NULL DEFAULT 0,
    reference TEXT UNIQUE,
    starts_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    school_id INTEGER,
    class_code TEXT,
    subject TEXT NOT NULL,
    topic TEXT,
    title TEXT,
    source TEXT NOT NULL DEFAULT 'web',
    client_attempt_id TEXT,
    score REAL NOT NULL,
    total INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    seconds_spent REAL,
    started_at TEXT,
    finished_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    trust_score REAL,
    rush_events INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (telegram_id, client_attempt_id)
);

CREATE TABLE IF NOT EXISTS question_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL,
    telegram_id INTEGER NOT NULL,
    question_id INTEGER,
    question_text TEXT NOT NULL,
    subject TEXT,
    topic TEXT,
    sub_topic TEXT,
    selected_answer TEXT,
    correct_answer TEXT NOT NULL,
    is_correct INTEGER NOT NULL,
    seconds_spent REAL,
    switches INTEGER NOT NULL DEFAULT 0,
    switch_trail TEXT,
    is_time_sink INTEGER NOT NULL DEFAULT 0,
    is_rushed INTEGER NOT NULL DEFAULT 0,
    error_type TEXT
);

CREATE TABLE IF NOT EXISTS behavior_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL,
    telegram_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intervention_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    parent_id INTEGER,
    teacher_id INTEGER NOT NULL,
    target_text TEXT NOT NULL,
    threshold_score REAL NOT NULL,
    deadline TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS micro_bounties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    reward TEXT NOT NULL,
    target_score REAL NOT NULL,
    subject TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    claimed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS guardian_digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    week_start TEXT NOT NULL,
    digest_text TEXT NOT NULL,
    sent_via TEXT DEFAULT 'whatsapp',
    delivered_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id INTEGER NOT NULL,
    school_id INTEGER,
    class_code TEXT NOT NULL,
    subject TEXT NOT NULL,
    topic TEXT,
    limit_count INTEGER NOT NULL DEFAULT 5,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS study_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    day_label TEXT NOT NULL DEFAULT 'weekdays',
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_alerted_on TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (parent_id, student_id, day_label)
);

CREATE TABLE IF NOT EXISTS early_warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    attempt_id INTEGER,
    subject TEXT NOT NULL,
    score REAL NOT NULL,
    personal_median REAL NOT NULL,
    stddev REAL NOT NULL DEFAULT 0,
    dispatched_to TEXT DEFAULT 'parent,teacher',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quiz_papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id INTEGER NOT NULL,
    school_id INTEGER,
    title TEXT NOT NULL,
    subject TEXT NOT NULL,
    class_code TEXT,
    question_ids TEXT NOT NULL DEFAULT '[]',
    question_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quiz_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT UNIQUE NOT NULL,
    telegram_id INTEGER NOT NULL,
    exam_type TEXT NOT NULL,
    subject TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'standard',
    total_questions INTEGER NOT NULL,
    current_index INTEGER NOT NULL DEFAULT 0,
    time_limit_seconds INTEGER NOT NULL,
    time_remaining REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'IN_PROGRESS',
    answers TEXT NOT NULL DEFAULT '[]',
    questions_json TEXT NOT NULL DEFAULT '[]',
    attempt_id INTEGER,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_activity_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS answer_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    attempt_id INTEGER,
    telegram_id INTEGER NOT NULL,
    question_id INTEGER,
    subject TEXT,
    topic TEXT,
    selected_answer TEXT,
    confidence_level TEXT,
    is_correct INTEGER NOT NULL DEFAULT 0,
    time_spent_seconds REAL NOT NULL DEFAULT 0,
    answer_changes INTEGER NOT NULL DEFAULT 0,
    event_type TEXT NOT NULL DEFAULT 'answer',
    answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS student_topic_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    subject TEXT NOT NULL,
    topic TEXT NOT NULL,
    correct_count INTEGER NOT NULL DEFAULT 0,
    incorrect_count INTEGER NOT NULL DEFAULT 0,
    total_time_seconds REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT,
    UNIQUE (telegram_id, subject, topic)
);

CREATE TABLE IF NOT EXISTS system_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_data TEXT NOT NULL DEFAULT '{}',
    session_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_telemetry_user ON system_telemetry(user_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_event ON system_telemetry(event_type);
"""


V2_TABLE_SIGNATURES: Dict[str, str] = {
    "users": "referral_code",
    "subject_metrics": "class_arm",
    "question_bank": "difficulty",
    "payments": "raw_payload",
    "sync_events": "payload_hash",
    "schools": "plan",
    "profiles": "linking_code",
    "access_codes": "payload",
    "student_links": "status",
    "subscriptions": "expires_at",
    "quiz_attempts": "trust_score",
    "question_responses": "switch_trail",
    "behavior_events": "occurred_at",
    "intervention_contracts": "threshold_score",
    "micro_bounties": "target_score",
    "guardian_digests": "digest_text",
    "assignments": "limit_count",
    "study_windows": "enabled",
    "early_warnings": "personal_median",
    "quiz_papers": "question_count",
    "quiz_sessions": "time_remaining",
    "answer_telemetry": "confidence_level",
    "student_topic_stats": "total_time_seconds",
    "system_telemetry": "session_id",
}


def quarantine_legacy_tables() -> int:
    with db.cursor() as (_, cur):
        if db.mode == "postgres":
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
            existing = {row["table_name"] for row in cur.fetchall()}
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            existing = {row["name"] for row in cur.fetchall()}

    quarantined = 0
    with db.cursor(commit=True) as (_, cur):
        for table, signature in V2_TABLE_SIGNATURES.items():
            if table not in existing:
                continue
            if db.mode == "postgres":
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s",
                    (table,),
                )
                cols = {row["column_name"] for row in cur.fetchall()}
            else:
                cur.execute(f"PRAGMA table_info({table})")
                cols = {row["name"] for row in cur.fetchall()}
            if signature in cols:
                continue
            legacy_name = f"{table}_v1_legacy"
            suffix = 2
            while legacy_name in existing:
                legacy_name = f"{table}_v1_legacy_{suffix}"
                suffix += 1
            cur.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy_name}"')
            existing.add(legacy_name)
            logger.warning(
                "Quarantined legacy table %s -> %s (missing %s); a fresh V2 table will be created",
                table,
                legacy_name,
                signature,
            )
            quarantined += 1
    return quarantined


def init_schema() -> None:
    quarantine_legacy_tables()
    schema = POSTGRES_SCHEMA if db.mode == "postgres" else SQLITE_SCHEMA
    with db.cursor(commit=True) as (_, cur):
        for statement in [chunk.strip() for chunk in schema.split(";") if chunk.strip()]:
            cur.execute(statement)
    if db.mode == "postgres":
        with db.cursor(commit=True) as (_, cur):
            cur.execute("ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS client_attempt_id VARCHAR(64)")
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_quiz_attempts_client ON quiz_attempts (telegram_id, client_attempt_id)"
            )
    else:
        with db.cursor(commit=True) as (conn, _):
            has_col = conn.execute(
                "SELECT 1 FROM pragma_table_info('quiz_attempts') WHERE name = 'client_attempt_id'"
            ).fetchone()
            if not has_col:
                conn.execute("ALTER TABLE quiz_attempts ADD COLUMN client_attempt_id TEXT")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_quiz_attempts_client ON quiz_attempts (telegram_id, client_attempt_id)"
                )
    # Exam engine: weakness vector cache lives on the profile stats row.
    if db.mode == "postgres":
        with db.cursor(commit=True) as (_, cur):
            cur.execute("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS weakness_json JSONB")
    else:
        with db.cursor(commit=True) as (conn, _):
            has_col = conn.execute(
                "SELECT 1 FROM pragma_table_info('profiles') WHERE name = 'weakness_json'"
            ).fetchone()
            if not has_col:
                conn.execute("ALTER TABLE profiles ADD COLUMN weakness_json TEXT")
    # Onboarding: role wizard completion flag.
    if db.mode == "postgres":
        with db.cursor(commit=True) as (_, cur):
            cur.execute("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS onboarded BOOLEAN NOT NULL DEFAULT FALSE")
    else:
        with db.cursor(commit=True) as (conn, _):
            has_col = conn.execute(
                "SELECT 1 FROM pragma_table_info('profiles') WHERE name = 'onboarded'"
            ).fetchone()
            if not has_col:
                conn.execute("ALTER TABLE profiles ADD COLUMN onboarded INTEGER NOT NULL DEFAULT 0")


def seed_reference_data() -> None:
    existing = db.fetch_one("SELECT COUNT(*) AS count FROM subject_metrics")
    if existing and int(existing.get("count", 0)) == 0:
        for metric in LOCAL_METRICS:
            db.execute(
                """
                INSERT INTO subject_metrics
                (class_arm, subject, mean_score, median_score, high_error_topic, error_rate,
                 mastery_json, speed_accuracy_json, current_score, previous_term_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                if db.mode == "sqlite"
                else """
                INSERT INTO subject_metrics
                (class_arm, subject, mean_score, median_score, high_error_topic, error_rate,
                 mastery_json, speed_accuracy_json, current_score, previous_term_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    metric["class_arm"],
                    metric["subject"],
                    metric["mean_score"],
                    metric["median_score"],
                    metric["high_error_topic"],
                    metric["error_rate"],
                    json_dumps(metric["mastery"]),
                    json_dumps(metric["speed_accuracy"]),
                    metric["current_score"],
                    metric["previous_term_score"],
                ),
            )

    existing_questions = db.fetch_one("SELECT COUNT(*) AS count FROM question_bank")
    if existing_questions and int(existing_questions.get("count", 0)) == 0:
        for question in LOCAL_QUESTIONS:
            db.execute(
                """
                INSERT INTO question_bank
                (exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                if db.mode == "sqlite"
                else """
                INSERT INTO question_bank
                (exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    question["exam_type"],
                    question["subject"],
                    question["topic"],
                    question["class_level"],
                    question["question_text"],
                    json_dumps(question["options"]) if db.mode == "sqlite" else psycopg2.extras.Json(question["options"]),
                    question["correct_answer"],
                    question["explanation"],
                    question["difficulty"],
                ),
            )
        bank_seed_file = Path(__file__).resolve().parent / "question_bank_seed.json"
        if bank_seed_file.exists():
            try:
                with open(bank_seed_file, encoding="utf-8") as seed_handle:
                    bank_rows = json.load(seed_handle)
            except (OSError, ValueError) as exc:
                logger.warning("Skipping question_bank_seed.json load: %s", exc)
                bank_rows = []
            with db.transaction() as (_, cur):
                for bank_question in bank_rows:
                    cur.execute(
                        """
                        INSERT INTO question_bank
                        (exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(exam_type, subject, question_text) DO NOTHING
                        """
                        if db.mode == "sqlite"
                        else """
                        INSERT INTO question_bank
                        (exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (exam_type, subject, question_text) DO NOTHING
                        """,
                        (
                            bank_question["exam_type"],
                            bank_question["subject"],
                            bank_question["topic"],
                            bank_question["class_level"],
                            bank_question["question_text"],
                            json_dumps(bank_question["options"])
                            if db.mode == "sqlite"
                            else psycopg2.extras.Json(bank_question["options"]),
                            bank_question["correct_answer"],
                            bank_question["explanation"],
                            bank_question["difficulty"],
                        ),
                    )
            logger.info("Question bank seeded from file: %s rows", len(bank_rows))

    existing_schools = db.fetch_one("SELECT COUNT(*) AS count FROM schools")
    if existing_schools and int(existing_schools.get("count", 0)) == 0:
        db.execute(
            "INSERT INTO schools (name, code, plan) VALUES (?, ?, ?)"
            if db.mode == "sqlite"
            else "INSERT INTO schools (name, code, plan) VALUES (%s, %s, %s)",
            ("Lighthouse International College", "GIC", "standard"),
        )
        school = db.fetch_one(
            "SELECT id, code FROM schools WHERE code = ?"
            if db.mode == "sqlite"
            else "SELECT id, code FROM schools WHERE code = %s",
            ("GIC",),
        )
        if school:
            db.execute(
                "INSERT INTO access_codes (code, kind, payload) VALUES (?, ?, ?)"
                if db.mode == "sqlite"
                else "INSERT INTO access_codes (code, kind, payload) VALUES (%s, %s, %s)",
                (
                    "GIC-SS3-001",
                    "school_class",
                    json_dumps({"school_code": "GIC", "class_code": "SS3-001"}),
                ),
            )


def normalize_metric_row(row: Dict[str, Any]) -> Dict[str, Any]:
    mastery = row.get("mastery_json")
    speed_accuracy = row.get("speed_accuracy_json")
    return {
        "class_arm": row.get("class_arm", "SS2 Science"),
        "subject": row.get("subject", "General Studies"),
        "mean_score": float(row.get("mean_score", 0)),
        "median_score": float(row.get("median_score", 0)),
        "high_error_topic": row.get("high_error_topic", "Revision"),
        "error_rate": int(row.get("error_rate", 0)),
        "mastery": json.loads(mastery) if isinstance(mastery, str) else (mastery or {}),
        "speed_accuracy": (
            json.loads(speed_accuracy)
            if isinstance(speed_accuracy, str)
            else (speed_accuracy or {})
        ),
        "current_score": float(row.get("current_score", row.get("mean_score", 0))),
        "previous_term_score": float(row.get("previous_term_score", 0)),
    }


def fetch_metrics() -> List[Dict[str, Any]]:
    cached = cache_get_json("portal:metrics")
    if cached:
        return cached

    rows = db.fetch_all(
        "SELECT class_arm, subject, mean_score, median_score, high_error_topic, error_rate, "
        "mastery_json, speed_accuracy_json, current_score, previous_term_score FROM subject_metrics"
    )
    metrics = [normalize_metric_row(row) for row in rows] if rows else LOCAL_METRICS
    cache_set_json("portal:metrics", metrics, ttl_seconds=180)
    return metrics


def remedial_plan_for(metric: Dict[str, Any]) -> List[str]:
    topic = metric.get("high_error_topic", "core concepts")
    subject = metric.get("subject", "Subject")
    return [
        f"Re-teach {topic} with worked examples in the first 15 minutes.",
        f"Assign a Telegram drill for {subject} using a targeted deep-link.",
        "Run a 48-hour checkpoint quiz and escalate guardian messaging if recovery stalls.",
    ]


def mastery_quadrant(speed: float, accuracy: float) -> str:
    if speed >= 60 and accuracy < 60:
        return "Blind Guessing"
    if speed < 60 and accuracy < 60:
        return "Conceptual Struggle"
    if speed < 60 and accuracy >= 60:
        return "Needs Speed"
    return "True Mastery"


def get_portal_overview() -> Dict[str, Any]:
    metrics = fetch_metrics()
    alerts = []
    leaderboard = []
    for index, metric in enumerate(metrics, start=1):
        speed_accuracy = metric.get("speed_accuracy", {})
        velocity = metric["current_score"] - metric["previous_term_score"]
        alert = None
        if metric["mean_score"] < 50:
            alert = {
                "subject": metric["subject"],
                "class_arm": metric["class_arm"],
                "mean_score": metric["mean_score"],
                "plan": remedial_plan_for(metric),
                "deep_link": f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start=drill_{metric['high_error_topic'].lower().replace(' ', '_')}_{metric['class_arm'].lower().replace(' ', '_')}",
            }
            alerts.append(alert)
        leaderboard.append(
            {
                "rank": index,
                "student_name": ["Amina Bello", "Daniel Okoye", "Kemi Yusuf"][index - 1] if index <= 3 else f"Student {index}",
                "subject": metric["subject"],
                "velocity": round(velocity, 2),
            }
        )
        metric["quadrant"] = mastery_quadrant(
            float(speed_accuracy.get("speed", 0)),
            float(speed_accuracy.get("accuracy", 0)),
        )

    overview = {
        "metrics": metrics,
        "alerts": alerts,
        "leaderboard": sorted(leaderboard, key=lambda item: item["velocity"], reverse=True),
        "generated_at": utc_now(),
    }
    cache_set_json("portal:overview", overview, ttl_seconds=120)
    return overview


class TelegramAuthRequest(BaseModel):
    initData: str = Field(..., min_length=10)
    referralCode: Optional[str] = Field(default=None, max_length=24)


class UserPayload(BaseModel):
    telegram_id: int
    full_name: str
    username: Optional[str] = None
    role: str = "student"
    access_unlocked: bool = False
    access_code: Optional[str] = None
    referral_code: str
    premium: bool = False
    school_id: Optional[int] = None
    class_code: Optional[str] = None
    linking_code: Optional[str] = None
    parent_mode: str = "free"
    children: List[int] = Field(default_factory=list)


class TelegramAuthResponse(BaseModel):
    status: str
    user: UserPayload
    referral_link: str
    theme: str
    stream_token: str = ""


class SyncPayloadRequest(BaseModel):
    payload: str = Field(..., min_length=4)
    source: str = "indexeddb"
    telegram_id: Optional[int] = None


class SyncPayloadResponse(BaseModel):
    accepted: bool
    payload_hash: str
    keys: List[str]
    compressed_echo: str


class QuestionItem(BaseModel):
    id: int
    exam_type: str
    subject: str
    topic: str
    class_level: str
    question_text: str
    options: List[str]
    correct_answer: str
    explanation: str
    difficulty: str


class QuestionsResponse(BaseModel):
    source: str
    questions: List[QuestionItem]


class PaystackWebhookResponse(BaseModel):
    processed: bool
    status: str
    reference: str
    access_code: Optional[str] = None


class PaymentInitRequest(BaseModel):
    telegram_id: int = Field(..., gt=0)
    kind: str = Field("premium", pattern="^(premium|tuition)$")
    amount: Optional[float] = Field(default=None, gt=0, le=100_000_000)
    email: Optional[str] = Field(default=None, max_length=120)
    callback_url: Optional[str] = Field(default=None, max_length=500)


class PaymentInitResponse(BaseModel):
    authorization_url: str
    reference: str


@asynccontextmanager
def _sweep_exam_sessions() -> None:
    """Watchdog pass: idle-pause, timer expiry and abandoned-session recovery.

    Runs every minute so a crashed client or dead socket can never leave an
    exam hanging — sessions beyond their window are auto-submitted and scored
    on whatever was answered, exactly like an explicit quit.
    """
    now = datetime.now(timezone.utc)
    rows = db.fetch_all("SELECT * FROM quiz_sessions WHERE status != 'COMPLETED' AND mode != 'micro_quiz'", ())
    for row in rows:
        session = dict(row)
        session["answers"] = _jsonb_read(session.get("answers"))
        session["questions_json"] = _jsonb_read(session.get("questions_json"))
        user = {"telegram_id": int(session["telegram_id"])}
        last_active = _parse_db_timestamp(session["last_activity_at"])
        idle = (now - last_active).total_seconds()
        try:
            if session["status"] == "IN_PROGRESS":
                if float(session["time_remaining"]) <= 0:
                    finalize_exam_session(user, session, "time_expired")
                elif idle > settings.EXAM_IDLE_PAUSE_SECONDS:
                    _update_exam_session(session["session_id"], status="PAUSED")
                    log_telemetry(
                        int(session["telegram_id"]),
                        ROLE_STUDENT,
                        "idle_timeout",
                        {"seconds": round(idle, 1), "session_id": session["session_id"]},
                        session_id=session["session_id"],
                    )
                    logger.info("Watchdog paused exam session %s (idle %.0fs)", session["session_id"], idle)
            elif session["status"] == "PAUSED":
                if idle > settings.EXAM_RESUME_WINDOW_MINUTES * 60:
                    finalize_exam_session(user, session, "resume_expired")
                    logger.info("Watchdog closed expired exam session %s", session["session_id"])
        except Exception:
            logger.exception("Watchdog failed to settle exam session %s", session["session_id"])


async def exam_watchdog_loop() -> None:
    while True:
        try:
            _sweep_exam_sessions()
        except Exception:
            logger.exception("Exam watchdog sweep failed")
        await asyncio.sleep(60)


async def lifespan(_: FastAPI) -> Generator[None, None, None]:
    db.init()
    init_schema()
    seed_reference_data()
    init_cache()
    # Restore in-progress micro-quizzes from DB for crash recovery
    _load_micro_quiz_sessions()
    start_telemetry_worker()
    _start_telegram_bot()
    logger.info("Naija Scholar V2 ready on port %s using %s", settings.PORT, db.mode)
    curfew_task = asyncio.create_task(curfew_loop())
    exam_task = asyncio.create_task(exam_watchdog_loop())
    seed_task: Optional[asyncio.Task] = None
    # SEEDER GUARD: the loop is created exactly once per process, and `python main.py`
    # runs uvicorn with reload=False, so dev auto-reload worker restarts can never spawn
    # duplicate 6-hour seeder loops. If you launch uvicorn manually, do NOT pass --reload;
    # running `python autonomous_seeder.py` alongside is safe (upserts are idempotent).
    if settings.SEED_ENABLED:
        seed_task = asyncio.create_task(seeder_loop())
    yield
    if seed_task is not None:
        seed_task.cancel()
    curfew_task.cancel()
    exam_task.cancel()
    stop_telemetry_worker()
    close_cache()
    db.close()


app = FastAPI(title=settings.APP_NAME, version="2.0.0", lifespan=lifespan)
_origins = [o.strip() for o in settings.CORS_ALLOW_ORIGINS.split(",") if o.strip()]
if not _origins:
    _origins = [
        "https://web.telegram.org",
        "https://oauth.telegram.org",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' https: data: blob: 'unsafe-inline' 'unsafe-eval'; "
        "frame-ancestors https://web.telegram.org https://*.telegram.org;"
    )
    return response


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    return {"ok": True, "database": db.mode, "cache": cache is not None, "time": utc_now()}


@app.get("/metrics")
def prometheus_metrics() -> Response:
    """Prometheus-compatible metrics endpoint."""
    lines = []
    # App info
    lines.append(f'# HELP naija_scholar_info Naija Scholar application info')
    lines.append(f'# TYPE naija_scholar_info gauge')
    lines.append(f'naija_scholar_info{{version="2.0.0",database="{db.mode}"}} 1')
    
    # Database stats
    try:
        q_count = db.fetch_one("SELECT COUNT(*) AS c FROM question_bank", ())
        lines.append(f'# HELP naija_scholar_questions_total Total questions in bank')
        lines.append(f'# TYPE naija_scholar_questions_total gauge')
        lines.append(f'naija_scholar_questions_total {q_count.get("c", 0) if q_count else 0}')
    except Exception:
        pass
    
    # Active exams/quizzes
    lines.append(f'# HELP naija_scholar_active_exams Active exam sessions')
    lines.append(f'# TYPE naija_scholar_active_exams gauge')
    lines.append(f'naija_scholar_active_exams {len(EXAM_ACTIVE)}')
    
    lines.append(f'# HELP naija_scholar_active_quizzes Active micro-quiz sessions')
    lines.append(f'# TYPE naija_scholar_active_quizzes gauge')
    lines.append(f'naija_scholar_active_quizzes {len(QUIZ_SESSIONS)}')
    
    # Cache status
    lines.append(f'# HELP naija_scholar_cache_connected Redis cache connection status')
    lines.append(f'# TYPE naija_scholar_cache_connected gauge')
    lines.append(f'naija_scholar_cache_connected {1 if cache else 0}')
    
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/api/v1/portal/overview")
def portal_overview() -> Dict[str, Any]:
    cached = cache_get_json("portal:overview")
    return cached or get_portal_overview()


@app.get("/api/v1/metrics")
def metrics() -> List[Dict[str, Any]]:
    return fetch_metrics()


# ---------------------------------------------------------------------------
# Phase 1 — Dynamic Role Selection & Onboarding (Telegram & Web parity)
# ---------------------------------------------------------------------------
ONBOARDING_BUTTONS: List[Dict[str, str]] = [
    {"role": ROLE_STUDENT, "label": "🎓 Student", "callback": "onboard:student"},
    {"role": ROLE_PARENT, "label": "👨‍👩‍👧 Parent", "callback": "onboard:parent"},
    {"role": ROLE_TEACHER, "label": "📚 Teacher", "callback": "onboard:teacher"},
    {"role": ROLE_SCHOOL_ADMIN, "label": "🏫 School / Admin", "callback": "onboard:school"},
]


def _onboarding_payload(user: Dict[str, Any]) -> Dict[str, Any]:
    """Return the wizard payload + role-specific next-step hints."""
    role = normalize_role(user.get("role"))
    onboarded = bool(user.get("onboarded"))
    inline_keyboard = [
        [{"text": btn["label"], "callback_data": btn["callback"]}]
        for btn in ONBOARDING_BUTTONS
    ]
    role_routes: Dict[str, Dict[str, Any]] = {
        ROLE_STUDENT: {
            "headline": "🎓 Student mode unlocked",
            "next_steps": [
                "Run /exam to start the full exam wizard.",
                "Tap /quiz <subject> for a 5-question micro-drill.",
                "Open /progress to see your JAMB prediction and weakest topic.",
            ],
            "primary_endpoints": ["/api/v1/exam/start", "/api/v1/exam/weakness", "/api/v1/quiz/submit"],
        },
        ROLE_PARENT: {
            "headline": "👨‍👩‍👧 Parent mode unlocked",
            "next_steps": [
                "Send /linkchild <CODE> to follow your child's progress.",
                "Send /mychildren to see linked students and averages.",
                "Send /child <id> for analytics on a specific child.",
            ],
            "primary_endpoints": ["/api/v1/analytics/child/{id}", "/api/v1/parent/children"],
        },
        ROLE_TEACHER: {
            "headline": "📚 Teacher mode unlocked",
            "next_steps": [
                "Open your class dashboard with /progress after linking a class.",
                "Generate assignments with /start assignment_<class>.",
                "Use /subjects to pick topics for custom quiz creation.",
            ],
            "primary_endpoints": ["/api/v1/access/generate", "/api/v1/analytics/class"],
        },
        ROLE_SCHOOL_ADMIN: {
            "headline": "🏫 School Admin mode unlocked",
            "next_steps": [
                "Provision teacher invite codes via /api/v1/access/generate.",
                "View school-wide reports via /api/v1/analytics/school.",
                "Manage subscription tier from the billing portal.",
            ],
            "primary_endpoints": [
                "/api/v1/access/generate",
                "/api/v1/analytics/school",
            ],
        },
        ROLE_SUPER_ADMIN: {
            "headline": "🛰️ God Mode active",
            "next_steps": [
                "Live telemetry: /api/v1/admin/telemetry/live",
                "Per-user audit: /api/v1/admin/telemetry/user/{user_id}",
                "Aggregated behavior: /api/v1/admin/telemetry/summary",
            ],
            "primary_endpoints": [
                "/api/v1/admin/telemetry/live",
                "/api/v1/admin/telemetry/user/{user_id}",
                "/api/v1/admin/telemetry/summary",
            ],
        },
    }
    return {
        "onboarded": onboarded,
        "current_role": role,
        "headline": "👋 Welcome — pick the role that fits you",
        "buttons": [
            {"role": btn["role"], "label": btn["label"], "callback_data": btn["callback"]}
            for btn in ONBOARDING_BUTTONS
        ],
        "inline_keyboard": inline_keyboard,
        "next_step": role_routes.get(
            role,
            {"headline": "Pick a role to continue", "next_steps": [], "primary_endpoints": []},
        ),
    }


@app.get("/api/v1/onboarding/start")
def onboarding_start(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """Return the onboarding wizard payload (Telegram & web parity)."""
    log_telemetry(
        user["telegram_id"],
        user.get("role"),
        "onboarding_shown",
        {"onboarded": bool(user.get("onboarded"))},
    )
    return _onboarding_payload(user)


@app.get("/api/v1/onboarding/welcome")
def onboarding_welcome(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """Role-specific welcome + next-step routing for the freshly onboarded user."""
    return _onboarding_payload(user)


class OnboardingSelectRequest(BaseModel):
    role: str


@app.post("/api/v1/onboarding/select")
def onboarding_select(
    payload: OnboardingSelectRequest,
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """Resolve an onboarding button press and persist the chosen role."""
    telegram_id = int(user["telegram_id"])
    requested = (payload.role or "").strip()
    # Reject anything that does not map to a known role before normalization
    # silently falls back to STUDENT (which would otherwise accept garbage).
    known = {
        "student", "parent", "teacher", "school",
        "school admin", "school_admin", "admin", "god",
        "super admin", "super_admin", "super admin god mode", "super_admin_god_mode",
        "student_mode", "parent_mode", "teacher_mode", "school_mode",
        ROLE_STUDENT, ROLE_PARENT, ROLE_TEACHER, ROLE_SCHOOL_ADMIN, ROLE_SUPER_ADMIN,
    }
    if requested.lower() not in {k.lower() for k in known}:
        raise HTTPException(status_code=400, detail="Unknown role selection")
    canonical = normalize_role(requested)
    if canonical == ROLE_SUPER_ADMIN:
        # Owner-only: non-owners get a 403 from set_profile_role.
        try:
            updated = set_profile_role(telegram_id, canonical, source="onboarding_button")
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    elif canonical not in (ROLE_STUDENT, ROLE_PARENT, ROLE_TEACHER, ROLE_SCHOOL_ADMIN):
        raise HTTPException(status_code=400, detail="Unknown role selection")
    else:
        updated = set_profile_role(telegram_id, canonical, source="onboarding_button")
    background_tasks.add_task(
        log_telemetry,
        telegram_id,
        canonical,
        "onboarding_completed",
        {"source": "onboarding_button", "role": canonical},
    )
    welcome = _onboarding_payload({**user, **updated, "onboarded": True})
    return {"status": "ok", "profile": updated, "welcome": welcome}


# ---------------------------------------------------------------------------
# Phase 2 — Hidden Super Admin Telemetry Layer ("God Mode Tracker")
# ---------------------------------------------------------------------------
#
# These endpoints are gated by `require_super_admin` (returns 403 for anyone
# not in `SUPER_ADMIN_IDS`). They expose the `system_telemetry` table for
# god-mode audits without polluting the public API surface.


def _fetch_telemetry_rows(
    *,
    user_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if user_id:
        clauses.append("user_id = ?")
        params.append(str(user_id))
    if event_type:
        clauses.append("event_type = ?")
        params.append(str(event_type))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    if db.mode == "sqlite":
        sql = (
            f"SELECT id, user_id, role, event_type, event_data, session_id, created_at "
            f"FROM system_telemetry {where} ORDER BY id DESC LIMIT {int(limit)}"
        )
    else:
        sql = (
            f"SELECT id, user_id, role, event_type, event_data::text AS event_data, session_id, created_at "
            f"FROM system_telemetry {where} ORDER BY id DESC LIMIT {int(limit)}"
        )
    rows = db.fetch_all(sql, tuple(params))
    for row in rows:
        if isinstance(row.get("event_data"), str):
            try:
                row["event_data"] = json.loads(row["event_data"])
            except (ValueError, TypeError):
                row["event_data"] = {"raw": row["event_data"]}
    return rows


@app.get("/api/v1/admin/telemetry/live")
def admin_telemetry_live(
    event_type: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    _admin: Dict[str, Any] = Depends(require_super_admin),
) -> Dict[str, Any]:
    """Stream the latest raw telemetry events (god mode only)."""
    rows = _fetch_telemetry_rows(event_type=event_type, limit=limit)
    return {"count": len(rows), "events": rows, "server_time": utc_now()}


@app.get("/api/v1/admin/telemetry/user/{user_id}")
def admin_telemetry_user(
    user_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    _admin: Dict[str, Any] = Depends(require_super_admin),
) -> Dict[str, Any]:
    """Full audit history & weakness timeline for a target user."""
    events = _fetch_telemetry_rows(user_id=user_id, limit=limit)
    user_id_key = int(user_id) if str(user_id).isdigit() else user_id
    if db.mode == "sqlite":
        weakness_rows = db.fetch_all(
            "SELECT subject, topic, correct_count, incorrect_count, total_time_seconds, attempts, last_seen_at "
            "FROM student_topic_stats WHERE telegram_id = ? "
            "ORDER BY (correct_count * 1.0 / (correct_count + incorrect_count + 1)) ASC LIMIT 10",
            (user_id_key,),
        )
        profile = db.fetch_one(
            "SELECT telegram_id, role, onboarded, linking_code FROM profiles WHERE telegram_id = ?",
            (user_id_key,),
        )
    else:
        weakness_rows = db.fetch_all(
            "SELECT subject, topic, correct_count, incorrect_count, total_time_seconds, attempts, last_seen_at "
            "FROM student_topic_stats WHERE telegram_id = %s "
            "ORDER BY (correct_count * 1.0 / (correct_count + incorrect_count + 1)) ASC LIMIT 10",
            (user_id_key,),
        )
        profile = db.fetch_one(
            "SELECT telegram_id, role, onboarded, linking_code FROM profiles WHERE telegram_id = %s",
            (user_id_key,),
        )
    return {
        "user_id": user_id,
        "profile": profile,
        "weakness_timeline": weakness_rows,
        "event_count": len(events),
        "events": events,
    }


@app.get("/api/v1/admin/telemetry/summary")
def admin_telemetry_summary(
    _admin: Dict[str, Any] = Depends(require_super_admin),
) -> Dict[str, Any]:
    """Aggregated behavioral metrics: drop-off, latency, peak hours."""
    counts = db.fetch_one(
        "SELECT COUNT(*) AS total, "
        "COUNT(DISTINCT user_id) AS unique_users, "
        "COUNT(DISTINCT session_id) AS unique_sessions "
        "FROM system_telemetry"
    ) or {}
    event_breakdown = db.fetch_all(
        "SELECT event_type, COUNT(*) AS count FROM system_telemetry GROUP BY event_type ORDER BY count DESC"
    )
    role_breakdown = db.fetch_all(
        "SELECT role, COUNT(*) AS count FROM system_telemetry GROUP BY role ORDER BY count DESC"
    )
    if db.mode == "sqlite":
        peak_rows = db.fetch_all(
            "SELECT CAST(strftime('%H', created_at) AS INTEGER) AS hour, COUNT(*) AS count "
            "FROM system_telemetry GROUP BY hour ORDER BY count DESC LIMIT 6"
        )
    else:
        peak_rows = db.fetch_all(
            "SELECT EXTRACT(HOUR FROM created_at)::int AS hour, COUNT(*) AS count "
            "FROM system_telemetry GROUP BY hour ORDER BY count DESC LIMIT 6"
        )
    if db.mode == "sqlite":
        latency = db.fetch_one(
            "SELECT AVG(time_spent_seconds) AS avg_latency, COUNT(*) AS answers "
            "FROM answer_telemetry WHERE time_spent_seconds > 0"
        ) or {}
    else:
        latency = db.fetch_one(
            "SELECT AVG(time_spent_seconds) AS avg_latency, COUNT(*) AS answers "
            "FROM answer_telemetry WHERE time_spent_seconds > 0"
        ) or {}
    if db.mode == "sqlite":
        drop_off = db.fetch_one(
            "SELECT (SELECT COUNT(*) FROM quiz_sessions) AS started, "
            "(SELECT COUNT(*) FROM quiz_attempts WHERE finished_at IS NOT NULL) AS finished"
        ) or {}
    else:
        drop_off = db.fetch_one(
            "SELECT (SELECT COUNT(*) FROM quiz_sessions) AS started, "
            "(SELECT COUNT(*) FROM quiz_attempts WHERE finished_at IS NOT NULL) AS finished"
        ) or {}
    started = int(drop_off.get("started") or 0)
    finished = int(drop_off.get("finished") or 0)
    drop_off_rate = round(100.0 * (started - finished) / started, 1) if started else 0.0
    avg_latency = float(latency.get("avg_latency") or 0.0)
    return {
        "totals": {
            "events": int(counts.get("total") or 0),
            "unique_users": int(counts.get("unique_users") or 0),
            "unique_sessions": int(counts.get("unique_sessions") or 0),
        },
        "event_breakdown": event_breakdown,
        "role_breakdown": role_breakdown,
        "peak_hours": peak_rows,
        "avg_answer_latency_seconds": round(avg_latency, 2),
        "drop_off": {
            "sessions_started": started,
            "sessions_finished": finished,
            "drop_off_rate_pct": drop_off_rate,
        },
        "generated_at": utc_now(),
    }


@app.post("/api/v1/auth/telegram", response_model=TelegramAuthResponse)
def auth_telegram(payload: TelegramAuthRequest) -> TelegramAuthResponse:
    user_info = verify_telegram_init_data(payload.initData, settings.TELEGRAM_BOT_TOKEN)
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid Telegram initData signature")

    try:
        telegram_id = int(user_info.get("id", 0))
    except (TypeError, ValueError):
        telegram_id = 0
    if telegram_id <= 0:
        raise HTTPException(status_code=400, detail="Telegram user id missing")

    username = user_info.get("username") or None
    full_name = " ".join(
        part for part in [user_info.get("first_name"), user_info.get("last_name")] if part
    ).strip() or "Naija Scholar User"

    existing = db.fetch_one(
        "SELECT telegram_id, username, full_name, role, referral_code, access_unlocked, access_code "
        "FROM users WHERE telegram_id = ?"
        if db.mode == "sqlite"
        else "SELECT telegram_id, username, full_name, role, referral_code, access_unlocked, access_code "
        "FROM users WHERE telegram_id = %s",
        (telegram_id,),
    )

    if existing is None:
        referral_code = build_referral_code(telegram_id)
        referred_by = payload.referralCode or str(user_info.get("start_param") or "").removeprefix("ref_") or None
        db.execute(
            """
            INSERT INTO users
            (telegram_id, username, full_name, role, referral_code, referred_by, access_unlocked, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                updated_at = excluded.updated_at
            """
            if db.mode == "sqlite"
            else """
            INSERT INTO users
            (telegram_id, username, full_name, role, referral_code, referred_by, access_unlocked, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (telegram_id) DO UPDATE SET
                username = EXCLUDED.username,
                full_name = EXCLUDED.full_name,
                updated_at = EXCLUDED.updated_at
            """,
            (
                telegram_id,
                username,
                full_name,
                "student",
                referral_code,
                referred_by,
                0 if db.mode == "sqlite" else False,
                utc_now(),
            ),
        )
        user_record = {
            "telegram_id": telegram_id,
            "username": username,
            "full_name": full_name,
            "role": "student",
            "referral_code": referral_code,
            "access_unlocked": False,
            "access_code": None,
        }
    else:
        db.execute(
            "UPDATE users SET username = ?, full_name = ?, updated_at = ? WHERE telegram_id = ?"
            if db.mode == "sqlite"
            else "UPDATE users SET username = %s, full_name = %s, updated_at = %s WHERE telegram_id = %s",
            (username, full_name, utc_now(), telegram_id),
        )
        user_record = {
            "telegram_id": telegram_id,
            "username": username,
            "full_name": full_name,
            "role": existing.get("role", "student"),
            "referral_code": existing.get("referral_code") or build_referral_code(telegram_id),
            "access_unlocked": bool(existing.get("access_unlocked")),
            "access_code": existing.get("access_code"),
        }

    theme = "dark"
    profile = ensure_profile(telegram_id)
    user_record.update(
        {
            "role": profile.get("role", ROLE_STUDENT),
            "premium": is_premium(profile),
            "school_id": profile.get("school_id"),
            "class_code": profile.get("class_code"),
            "linking_code": profile.get("linking_code"),
            "parent_mode": profile.get("parent_mode", "free"),
            "children": linked_children(telegram_id),
        }
    )
    return TelegramAuthResponse(
        status="authenticated",
        user=UserPayload(**user_record),
        referral_link=build_referral_link(user_record["referral_code"]),
        theme=theme,
        stream_token=build_stream_token(telegram_id),
    )


def _sanitize_text(text: str, max_len: int = 5000) -> str:
    """Sanitize text content: strip HTML tags, limit length, remove null bytes."""
    if not isinstance(text, str):
        return ""
    # Remove null bytes
    text = text.replace("\x00", "")
    # Strip HTML/script tags (basic)
    import re
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    # Limit length
    return text[:max_len].strip()


def normalize_question_row(row: Dict[str, Any]) -> Dict[str, Any]:
    options = row.get("options", [])
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except json.JSONDecodeError:
            options = []
    if not isinstance(options, list):
        options = []
    # Sanitize all string fields
    options = [_sanitize_text(str(o), 500) for o in options[:4]]
    return {
        "id": int(row.get("id", 0)),
        "exam_type": _sanitize_text(row.get("exam_type", "WAEC"), 20),
        "subject": _sanitize_text(row.get("subject", "General Studies"), 80),
        "topic": _sanitize_text(row.get("topic", "Foundations"), 120),
        "class_level": _sanitize_text(row.get("class_level", "SS2"), 40),
        "question_text": _sanitize_text(row.get("question_text", "")),
        "options": options,
        "correct_answer": _sanitize_text(row.get("correct_answer", options[0] if options else ""), 500),
        "explanation": _sanitize_text(row.get("explanation", "")),
        "difficulty": _sanitize_text(row.get("difficulty", "Medium"), 20),
    }


def fetch_remedial_questions(subject: str, topic: Optional[str], limit: int) -> QuestionsResponse:
    cache_key = f"remedial:{subject}:{topic or 'all'}:{limit}"
    cached = cache_get_json(cache_key)
    if cached:
        return QuestionsResponse(**cached)

    params: List[Any] = [subject]
    sql = (
        "SELECT id, exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty "
        "FROM question_bank WHERE LOWER(subject) = LOWER(?)"
        if db.mode == "sqlite"
        else
        "SELECT id, exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty "
        "FROM question_bank WHERE LOWER(subject) = LOWER(%s)"
    )
    if topic:
        sql += " AND LOWER(topic) = LOWER(?)" if db.mode == "sqlite" else " AND LOWER(topic) = LOWER(%s)"
        params.append(topic)
    sql += " LIMIT ?" if db.mode == "sqlite" else " LIMIT %s"
    params.append(limit)

    rows = db.fetch_all(sql, tuple(params))
    if rows:
        response = QuestionsResponse(source="database", questions=[QuestionItem(**normalize_question_row(row)) for row in rows])
        cache_set_json(cache_key, response.model_dump(), ttl_seconds=90)
        return response

    fallback = [
        {
            "id": index,
            "exam_type": item["exam_type"],
            "subject": item["subject"],
            "topic": item["topic"],
            "class_level": item["class_level"],
            "question_text": item["question_text"],
            "options": item["options"],
            "correct_answer": item["correct_answer"],
            "explanation": item["explanation"],
            "difficulty": item["difficulty"],
        }
        for index, item in enumerate(
            [
                question
                for question in LOCAL_QUESTIONS
                if question["subject"].lower() == subject.lower()
                and (topic is None or question["topic"].lower() == topic.lower())
            ][:limit],
            start=1,
        )
    ]
    if not fallback:
        fallback = [
            {
                "id": 1,
                "exam_type": "WAEC",
                "class_level": "SS2",
                "subject": subject,
                "topic": topic or "Foundations",
                "question_text": f"Which revision approach best improves {subject} retention?",
                "options": [
                    "Short daily active recall",
                    "Reading once without practice",
                    "Skipping weak topics",
                    "Guessing all answers",
                ],
                "correct_answer": "Short daily active recall",
                "explanation": "Step 1: Identify the study option that actively strengthens memory. Step 2: Active recall requires the learner to retrieve information repeatedly. Step 3: Retrieval practice improves retention better than passive reading or guessing. Step 4: Therefore the correct answer is Short daily active recall.",
                "difficulty": "Easy",
            }
        ]
    response = QuestionsResponse(source="fallback", questions=[QuestionItem(**row) for row in fallback])
    cache_set_json(cache_key, response.model_dump(), ttl_seconds=60)
    return response


@app.get("/api/v1/questions/remedial", response_model=QuestionsResponse)
def questions_remedial(
    subject: str = Query(..., min_length=2),
    topic: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=25),
) -> QuestionsResponse:
    return fetch_remedial_questions(subject, topic, limit)


@app.post("/api/v1/sync/2g-payload", response_model=SyncPayloadResponse)
def sync_2g_payload(payload: SyncPayloadRequest) -> SyncPayloadResponse:
    if len(payload.payload) > MAX_SYNC_PAYLOAD_LENGTH:
        raise HTTPException(status_code=413, detail="Payload exceeds size limit")
    try:
        decoded = decompress_payload(payload.payload)
    except (ValueError, zlib.error, binascii.Error, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid compressed payload: {exc}") from exc

    telegram_id = payload.telegram_id or decoded.get("telegram_id")
    payload_hash = hashlib.sha256(json_dumps(decoded).encode("utf-8")).hexdigest()
    db.execute(
        """
        INSERT OR IGNORE INTO sync_events (payload_hash, telegram_id, source, payload_json)
        VALUES (?, ?, ?, ?)
        """
        if db.mode == "sqlite"
        else """
        INSERT INTO sync_events (payload_hash, telegram_id, source, payload_json)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (payload_hash) DO NOTHING
        """,
        (payload_hash, telegram_id, payload.source, json_dumps(decoded)),
    )

    summary = {
        "telegram_id": telegram_id,
        "sync_keys": sorted(decoded.keys()),
        "queued_at": utc_now(),
    }
    return SyncPayloadResponse(
        accepted=True,
        payload_hash=payload_hash,
        keys=summary["sync_keys"],
        compressed_echo=compress_payload(summary),
    )


class AccessJoinRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=40, pattern=r"^[A-Za-z0-9]+(-[A-Za-z0-9]+)+$")


class AccessGenerateRequest(BaseModel):
    kind: str = Field(..., pattern=r"^(school_class|teacher_invite|school_admin_invite|super_key)$")
    count: int = Field(1, ge=1, le=25)
    payload: Optional[Dict[str, Any]] = None


class ChildLinkRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=24, pattern=r"(?i)^lia-[0-9]{6,10}[a-f0-9]{2}$")


class QuizItemSubmission(BaseModel):
    id: Optional[int] = None
    question_text: str = Field(..., min_length=1, max_length=2000)
    options: List[str] = Field(default_factory=list, max_length=4)
    selected_answer: Optional[str] = Field(default=None, max_length=500)
    correct_answer: Optional[str] = Field(default=None, max_length=500)
    subject: Optional[str] = Field(default=None, max_length=80)
    topic: Optional[str] = Field(default=None, max_length=80)
    sub_topic: Optional[str] = Field(default=None, max_length=80)
    seconds_spent: float = Field(0, ge=0, le=3600)
    switches: int = Field(0, ge=0, le=200)
    switch_trail: str = Field(default="", max_length=1000)

    @field_validator("options")
    @classmethod
    def cap_option_length(cls, options: List[str]) -> List[str]:
        return [str(option)[:500] for option in options]


class BehaviorEventModel(BaseModel):
    event_type: str = Field(..., max_length=30, pattern=r"^[a-z_0-9]+$")
    detail: Optional[str] = Field(default=None, max_length=2000)
    occurred_at: Optional[str] = Field(default=None, max_length=40)


class QuizSubmitRequest(BaseModel):
    subject: str = Field(..., min_length=2, max_length=80)
    topic: Optional[str] = Field(default=None, max_length=80)
    title: Optional[str] = Field(default=None, max_length=120)
    source: str = Field(default="web", max_length=20)
    client_attempt_id: Optional[str] = Field(default=None, max_length=64)
    items: List[QuizItemSubmission] = Field(..., min_length=1, max_length=50)
    events: List[BehaviorEventModel] = Field(default_factory=list, max_length=100)
    started_at: Optional[str] = Field(default=None, max_length=40)
    finished_at: Optional[str] = Field(default=None, max_length=40)


class QuizSubmitResponse(BaseModel):
    attempt_id: int
    score_pct: float
    correct: int
    total: int
    trust_score: float
    premium: bool
    prediction: Dict[str, Any]
    heatmap: Dict[str, Any]
    radar: Dict[str, Any]
    speed_matrix: Dict[str, Any]
    error_profiler: Dict[str, Any]
    items: List[Dict[str, Any]]
    anomaly: Optional[Dict[str, Any]] = None


class AssignmentCreateRequest(BaseModel):
    class_code: str = Field(..., min_length=2, max_length=40)
    subject: str = Field(..., min_length=2, max_length=80)
    topic: Optional[str] = None
    limit_count: int = Field(5, ge=1, le=25)


class ContractCreateRequest(BaseModel):
    student_id: int = Field(..., gt=0)
    target_text: str = Field(..., min_length=4, max_length=300)
    threshold_score: float = Field(..., gt=0, le=100)
    deadline: str = Field(..., min_length=8, max_length=30)


class BountyCreateRequest(BaseModel):
    student_id: int = Field(..., gt=0)
    title: str = Field(..., min_length=3, max_length=120)
    reward: float = Field(..., gt=0, le=1_000_000)
    target_score: float = Field(..., gt=0, le=100)
    subject: Optional[str] = None


class DigestGenerateRequest(BaseModel):
    student_id: int = Field(..., gt=0)


TIME_SINK_SECONDS = 90
RUSH_SECONDS = 4
TRUST_PENALTIES = {"tab_switch": 12, "focus_loss": 8, "copy_paste": 15, "cursor_anomaly": 5, "hesitation": 3}


def predict_jamb_range(score_pct: float) -> Tuple[int, int]:
    low = max(100, round(150 + score_pct * 1.5))
    high = min(400, low + 20)
    return low, high


def predict_waec_grade(score_pct: float) -> str:
    if score_pct >= 75:
        return "A1"
    if score_pct >= 65:
        return "B2"
    if score_pct >= 60:
        return "B3"
    if score_pct >= 55:
        return "C4"
    if score_pct >= 50:
        return "C5"
    if score_pct >= 45:
        return "C6"
    return "F9"


def compute_trust_score(events: List[Dict[str, Any]], switches_total: int, rushed_total: int) -> float:
    score = 100.0
    for event_type in TRUST_PENALTIES:
        count = sum(1 for event in events if event.get("event_type") == event_type)
        score -= min(count, 5) * TRUST_PENALTIES[event_type]
    score -= min(switches_total, 20) * 1.0
    score -= min(rushed_total, 10) * 4.0
    return round(max(0.0, min(100.0, score)), 1)


def fetch_db_questions_by_ids(ids: List[int]) -> Dict[int, Dict[str, Any]]:
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    placeholders = ", ".join("?" if db.mode == "sqlite" else "%s" for _ in ids)
    rows = db.fetch_all(
        f"SELECT id, correct_answer, topic FROM question_bank WHERE id IN ({placeholders})",
        tuple(ids),
    )
    return {int(row["id"]): row for row in rows}


def analyze_quiz(user: Dict[str, Any], payload: QuizSubmitRequest) -> QuizSubmitResponse:
    premium = bool(user.get("premium"))
    db_questions = fetch_db_questions_by_ids([item.id or 0 for item in payload.items])
    evaluated: List[Dict[str, Any]] = []
    heatmap: Dict[str, Dict[str, List[bool]]] = {}

    for item in payload.items:
        db_row = db_questions.get(int(item.id or 0)) if item.id else None
        correct_answer = (db_row or {}).get("correct_answer") or item.correct_answer or ""
        topic = (db_row or {}).get("topic") or item.topic or payload.topic or "General"
        is_correct = bool(item.selected_answer) and item.selected_answer == correct_answer
        seconds = max(0.0, float(item.seconds_spent or 0))
        is_rushed = seconds < RUSH_SECONDS
        is_time_sink = seconds > TIME_SINK_SECONDS and not is_correct
        error_type = None
        if not is_correct:
            error_type = "careless" if is_rushed else "knowledge_gap"
        sub_topic = item.sub_topic or topic
        bucket = heatmap.setdefault(topic, {})
        bucket.setdefault(sub_topic, []).append(is_correct)
        evaluated.append(
            {
                "id": item.id,
                "question_text": item.question_text,
                "topic": topic,
                "sub_topic": item.sub_topic or topic,
                "selected_answer": item.selected_answer,
                "correct_answer": correct_answer,
                "is_correct": is_correct,
                "seconds_spent": round(seconds, 1),
                "switches": item.switches,
                "switch_trail": item.switch_trail,
                "is_time_sink": is_time_sink,
                "is_rushed": is_rushed,
                "error_type": error_type,
            }
        )

    total = len(evaluated)
    correct = sum(1 for item in evaluated if item["is_correct"])
    score_pct = round((correct / total) * 100, 1) if total else 0.0
    events = [event.model_dump() for event in payload.events]
    switches_total = sum(item["switches"] for item in evaluated)
    rushed_total = sum(1 for item in evaluated if item["is_rushed"])
    trust_score = compute_trust_score(events, switches_total, rushed_total)

    heatmap_clean = {
        topic: {
            sub_topic: round((sum(results) / len(results)) * 100, 1)
            for sub_topic, results in subs.items()
        }
        for topic, subs in heatmap.items()
    }

    timed_items = [item for item in evaluated if item["seconds_spent"] > 0]
    avg_time = round(sum(item["seconds_spent"] for item in timed_items) / len(timed_items), 1) if timed_items else 0.0
    speed_index = round(max(0.0, min(100.0, 100 - ((avg_time - 20) * 1.5))), 1) if avg_time else 50.0
    radar = {
        "recall": round(score_pct, 1),
        "conceptual": round(100 * sum(1 for i in evaluated if i["is_correct"] and 6 <= i["seconds_spent"] <= 30) / max(1, total), 1),
        "problem_solving": round(100 * sum(1 for i in evaluated if i["is_correct"] and i["seconds_spent"] > 30) / max(1, total), 1),
        "speed": speed_index,
    }
    low, high = predict_jamb_range(score_pct)
    prediction = {
        "jamb_low": low,
        "jamb_high": high,
        "waec_grade": predict_waec_grade(score_pct),
    }
    knowledge_gaps = sum(1 for i in evaluated if i["error_type"] == "knowledge_gap")
    careless = sum(1 for i in evaluated if i["error_type"] == "careless")
    error_profiler = {
        "knowledge_gaps": knowledge_gaps,
        "careless_rush": careless,
        "time_sinks": sum(1 for i in evaluated if i["is_time_sink"]),
        "rushed": rushed_total,
    }
    speed_matrix = {"avg_seconds": avg_time, "time_sinks": error_profiler["time_sinks"], "rushed_answers": rushed_total}

    return QuizSubmitResponse(
        attempt_id=0,
        score_pct=score_pct,
        correct=correct,
        total=total,
        trust_score=trust_score,
        premium=premium,
        prediction=prediction,
        heatmap=heatmap_clean,
        radar=radar,
        speed_matrix=speed_matrix,
        error_profiler=error_profiler,
        items=evaluated,
    )


@app.post("/api/v1/quiz/submit", response_model=QuizSubmitResponse)
def quiz_submit(
    payload: QuizSubmitRequest,
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(current_user),
) -> QuizSubmitResponse:
    if payload.client_attempt_id:
        existing = db.fetch_one(
            "SELECT id FROM quiz_attempts WHERE telegram_id = ? AND client_attempt_id = ?"
            if db.mode == "sqlite"
            else "SELECT id FROM quiz_attempts WHERE telegram_id = %s AND client_attempt_id = %s",
            (user["telegram_id"], payload.client_attempt_id),
        )
        if existing:
            replayed = analyze_quiz(user, payload)
            replayed.attempt_id = int(existing["id"])
            logger.info("Idempotent replay for client_attempt_id=%s attempt_id=%s", payload.client_attempt_id, existing["id"])
            return replayed

    result = analyze_quiz(user, payload)
    anomaly = check_outlier_anomaly(user["telegram_id"], payload.subject, result.score_pct)
    started_at = payload.started_at or utc_now()
    finished_at = payload.finished_at or utc_now()
    seconds_spent = sum(item["seconds_spent"] for item in result.items)

    def insert_attempt(cur: Any) -> int:
        insert_sql = (
            """
            INSERT INTO quiz_attempts
            (telegram_id, school_id, class_code, subject, topic, title, source, client_attempt_id,
             score, total, correct, seconds_spent, started_at, finished_at, trust_score, rush_events)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            if db.mode == "sqlite"
            else """
            INSERT INTO quiz_attempts
            (telegram_id, school_id, class_code, subject, topic, title, source, client_attempt_id,
             score, total, correct, seconds_spent, started_at, finished_at, trust_score, rush_events)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
        )
        params = (
            user["telegram_id"],
            user.get("school_id"),
            user.get("class_code"),
            payload.subject,
            payload.topic,
            payload.title,
            payload.source,
            payload.client_attempt_id,
            result.score_pct,
            result.total,
            result.correct,
            round(seconds_spent, 2),
            started_at,
            finished_at,
            result.trust_score,
            result.error_profiler["rushed"],
        )
        if db.mode == "postgres":
            cur.execute(insert_sql + " RETURNING id", params)
            return int(cur.fetchone()["id"])
        cur.execute(insert_sql, params)
        return int(cur.lastrowid)

    try:
        with db.transaction() as (_, cur):
            attempt_id = insert_attempt(cur)
            for item in result.items:
                cur.execute(
                    """
                    INSERT INTO question_responses
                    (attempt_id, telegram_id, question_id, question_text, subject, topic, sub_topic,
                     selected_answer, correct_answer, is_correct, seconds_spent, switches, switch_trail,
                     is_time_sink, is_rushed, error_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                    if db.mode == "sqlite"
                    else """
                    INSERT INTO question_responses
                    (attempt_id, telegram_id, question_id, question_text, subject, topic, sub_topic,
                     selected_answer, correct_answer, is_correct, seconds_spent, switches, switch_trail,
                     is_time_sink, is_rushed, error_type)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        attempt_id,
                        user["telegram_id"],
                        item["id"],
                        item["question_text"][:2000],
                        payload.subject,
                        item["topic"],
                        item["sub_topic"],
                        item["selected_answer"],
                        item["correct_answer"],
                        1 if item["is_correct"] else 0,
                        item["seconds_spent"],
                        item["switches"],
                        item["switch_trail"][:200],
                        1 if item["is_time_sink"] else 0,
                        1 if item["is_rushed"] else 0,
                        item["error_type"],
                    ),
                )
            for event in payload.events:
                cur.execute(
                    "INSERT INTO behavior_events (attempt_id, telegram_id, event_type, detail, occurred_at) VALUES (?, ?, ?, ?, ?)"
                    if db.mode == "sqlite"
                    else "INSERT INTO behavior_events (attempt_id, telegram_id, event_type, detail, occurred_at) VALUES (%s, %s, %s, %s, %s)",
                    (
                        attempt_id,
                        user["telegram_id"],
                        event.event_type,
                        event.detail,
                        event.occurred_at or utc_now(),
                    ),
                )
            if anomaly:
                cur.execute(
                    "INSERT INTO early_warnings (telegram_id, attempt_id, subject, score, personal_median, stddev) VALUES (?, ?, ?, ?, ?, ?)"
                    if db.mode == "sqlite"
                    else "INSERT INTO early_warnings (telegram_id, attempt_id, subject, score, personal_median, stddev) VALUES (%s, %s, %s, %s, %s, %s)",
                    (user["telegram_id"], attempt_id, payload.subject, anomaly["score"], anomaly["median"], anomaly["stddev"]),
                )
        # New attempt landed -> purge stale analytics so /progress etc. recompute.
        invalidate_student_caches(user["telegram_id"])
    except Exception as exc:
        if payload.client_attempt_id and "unique" in str(exc).lower():
            existing = db.fetch_one(
                "SELECT id FROM quiz_attempts WHERE telegram_id = ? AND client_attempt_id = ?"
                if db.mode == "sqlite"
                else "SELECT id FROM quiz_attempts WHERE telegram_id = %s AND client_attempt_id = %s",
                (user["telegram_id"], payload.client_attempt_id),
            )
            if existing:
                result.attempt_id = int(existing["id"])
                logger.info("Concurrent duplicate client_attempt_id=%s reused attempt_id=%s", payload.client_attempt_id, existing["id"])
                return result
        raise
    result.attempt_id = attempt_id
    if anomaly:
        background_tasks.add_task(dispatch_early_warning, user, anomaly, payload.subject)
        result.anomaly = anomaly
    return result


def build_exam_diagnostics(session: Dict[str, Any]) -> Dict[str, Any]:
    """Rich diagnostic summary: topic accuracy, pacing, focus areas, confidence."""
    answers = session.get("answers") or []
    by_topic: Dict[str, Dict[str, Any]] = {}
    confidence: Dict[str, Dict[str, Any]] = {
        level: {"correct": 0, "total": 0} for level in ("high", "medium", "low")
    }
    total_time = 0.0
    correct_count = 0
    for answer in answers:
        topic = answer.get("topic") or "General"
        bucket = by_topic.setdefault(topic, {"correct": 0, "incorrect": 0, "total_time": 0.0, "count": 0})
        bucket["correct" if answer.get("is_correct") else "incorrect"] += 1
        bucket["total_time"] += float(answer.get("time_spent_seconds") or 0)
        bucket["count"] += 1
        level = answer.get("confidence_level") if answer.get("confidence_level") in confidence else "medium"
        confidence[level]["total"] += 1
        if answer.get("is_correct"):
            confidence[level]["correct"] += 1
            correct_count += 1
        total_time += float(answer.get("time_spent_seconds") or 0)
    breakdown = []
    for topic, bucket in by_topic.items():
        answered = bucket["correct"] + bucket["incorrect"]
        breakdown.append(
            {
                "topic": topic,
                "correct_count": bucket["correct"],
                "incorrect_count": bucket["incorrect"],
                "accuracy_pct": round(100.0 * bucket["correct"] / answered, 1) if answered else 0.0,
                "average_time_per_question": round(bucket["total_time"] / bucket["count"], 1) if bucket["count"] else 0.0,
            }
        )
    breakdown.sort(key=lambda t: (t["accuracy_pct"], -t["incorrect_count"]))
    for stats in confidence.values():
        stats["accuracy_pct"] = round(100.0 * stats["correct"] / stats["total"], 1) if stats["total"] else 0.0
    answered = len(answers)
    return {
        "answered": answered,
        "total_questions": int(session["total_questions"]),
        "unanswered": int(session["total_questions"]) - answered,
        "score_pct": round(100.0 * correct_count / answered, 1) if answered else 0.0,
        "avg_time_per_question": round(total_time / answered, 1) if answered else 0.0,
        "topic_breakdown": breakdown,
        "focus_areas": [t["topic"] for t in breakdown if t["accuracy_pct"] < 60][:5],
        "confidence_index": confidence,
        "lucky_guesses": confidence["low"]["correct"],
    }

def compute_weakness_vectors(telegram_id: int) -> Dict[str, Any]:
    """Background worker: rebuild topic weakness vectors from answer telemetry.

    Aggregates every exam-engine answer into `student_topic_stats`
    (correct/incorrect counts, average time per question per topic), ranks the
    student's weakest subjects/topics and caches them on the profile row so
    Adaptive Weakness Rescue and parent digests can read them cheaply.
    """
    ph = "?" if db.mode == "sqlite" else "%s"
    rows = db.fetch_all(
        f"SELECT subject, topic, "
        f"SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS correct_count, "
        f"SUM(CASE WHEN is_correct = 1 THEN 0 ELSE 1 END) AS incorrect_count, "
        f"SUM(time_spent_seconds) AS total_time_seconds, COUNT(*) AS attempts, MAX(answered_at) AS last_seen "
        f"FROM answer_telemetry WHERE telegram_id = {ph} AND event_type = 'answer' GROUP BY subject, topic",
        (telegram_id,),
    )
    for row in rows:
        db.execute(
            "INSERT INTO student_topic_stats "
            "(telegram_id, subject, topic, correct_count, incorrect_count, total_time_seconds, attempts, last_seen_at) "
            f"VALUES ({', '.join(ph for _ in range(8))}) "
            "ON CONFLICT(telegram_id, subject, topic) DO UPDATE SET "
            "correct_count = excluded.correct_count, incorrect_count = excluded.incorrect_count, "
            "total_time_seconds = excluded.total_time_seconds, attempts = excluded.attempts, "
            "last_seen_at = excluded.last_seen_at",
            (
                telegram_id,
                row["subject"],
                row["topic"],
                int(row["correct_count"] or 0),
                int(row["incorrect_count"] or 0),
                float(row["total_time_seconds"] or 0),
                int(row["attempts"] or 0),
                row["last_seen"],
            ),
        )

    def _accuracy(correct: float, incorrect: float) -> float:
        total = correct + incorrect
        return (100.0 * correct / total) if total else 0.0

    subject_rows = db.fetch_all(
        f"SELECT subject, SUM(correct_count) AS correct_count, SUM(incorrect_count) AS incorrect_count, "
        f"SUM(attempts) AS attempts FROM student_topic_stats WHERE telegram_id = {ph} GROUP BY subject",
        (telegram_id,),
    )
    weakest_subjects = sorted(
        (
            {
                "subject": row["subject"],
                "accuracy_pct": round(_accuracy(float(row["correct_count"] or 0), float(row["incorrect_count"] or 0)), 1),
                "attempts": int(row["attempts"] or 0),
            }
            for row in subject_rows
            if int(row["attempts"] or 0) > 0
        ),
        key=lambda s: s["accuracy_pct"],
    )[:5]
    topic_rows = db.fetch_all(
        f"SELECT subject, topic, correct_count, incorrect_count, total_time_seconds, attempts FROM student_topic_stats "
        f"WHERE telegram_id = {ph} AND (correct_count + incorrect_count) > 0",
        (telegram_id,),
    )
    weakest_topics = sorted(
        (
            {
                "subject": row["subject"],
                "topic": row["topic"],
                "accuracy_pct": round(_accuracy(float(row["correct_count"] or 0), float(row["incorrect_count"] or 0)), 1),
                "average_time_per_question": round(
                    float(row["total_time_seconds"] or 0) / max(1, int(row["attempts"] or 0)), 1
                ),
                "attempts": int(row["attempts"] or 0),
            }
            for row in topic_rows
        ),
        key=lambda t: t["accuracy_pct"],
    )[:8]
    weakness = {
        "weakest_subjects": weakest_subjects,
        "weakest_topics": weakest_topics,
        "updated_at": utc_now(),
    }
    if db.mode == "postgres":
        db.execute(
            "UPDATE profiles SET weakness_json = %s, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = %s",
            (_jsonb_write(weakness), telegram_id),
        )
    else:
        db.execute(
            "UPDATE profiles SET weakness_json = ?, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = ?",
            (_jsonb_write(weakness), telegram_id),
        )
    invalidate_student_caches(telegram_id)
    logger.info("Weakness vectors recomputed for telegram_id=%s (%d topics)", telegram_id, len(weakest_topics))
    return weakness


def finalize_exam_session(
    user: Dict[str, Any],
    session: Dict[str, Any],
    reason: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> Dict[str, Any]:
    """Auto-scoring engine: score ONLY the questions answered so far.

    Runs on natural completion, timer expiry, user quit and abandoned-session
    recovery alike — the attempt is persisted through the regular analytics
    pipeline and the diagnostic summary is attached to the response.
    """
    session_id = session["session_id"]
    if session["status"] == "COMPLETED":
        return {
            "finished": True,
            "session_id": session_id,
            "status": "COMPLETED",
            "reason": reason,
            "already_completed": True,
            "time_remaining": float(session["time_remaining"]),
            "diagnostics": build_exam_diagnostics(session),
        }
    answers = session.get("answers") or []
    questions = session.get("questions_json") or []
    attempt_id: Optional[int] = None
    summary: Dict[str, Any] = {}
    if answers:
        items = [
            QuizItemSubmission(
                id=answer.get("question_id"),
                question_text=(answer.get("question_text") or "")[:2000],
                options=list(answer.get("options") or [])[:4],
                selected_answer=answer.get("selected_answer"),
                correct_answer=answer.get("correct_answer"),
                subject=session["subject"],
                topic=answer.get("topic"),
                seconds_spent=float(answer.get("time_spent_seconds") or 0),
                switches=int(answer.get("answer_changes") or 0),
            )
            for answer in answers
        ]
        payload = QuizSubmitRequest(
            subject=session["subject"],
            topic=(questions[0].get("topic") if questions else None),
            title=f"{session['exam_type']} {session['subject']} ({reason})",
            source="exam_engine",
            client_attempt_id=f"exam-{session_id}",
            started_at=str(session.get("started_at") or utc_now()),
            finished_at=utc_now(),
            items=items,
        )
        result = analyze_quiz(user, payload)
        _bot_persist_attempt(user, payload, result)
        attempt_id = result.attempt_id
        summary = {
            "attempt_id": attempt_id,
            "score_pct": result.score_pct,
            "correct": result.correct,
            "total_answered": result.total,
            "trust_score": result.trust_score,
            "prediction": result.prediction,
        }
    else:
        summary = {"attempt_id": None, "score_pct": 0.0, "correct": 0, "total_answered": 0}
    _update_exam_session(
        session_id,
        status="COMPLETED",
        attempt_id=attempt_id,
        completed_at=utc_now(),
        time_remaining=float(session["time_remaining"]),
    )
    session["status"] = "COMPLETED"
    diagnostics = build_exam_diagnostics(session)
    # Background hook: rebuild topic weakness vectors + weakest-subject cache.
    if background_tasks is not None:
        background_tasks.add_task(compute_weakness_vectors, int(user["telegram_id"]))
    else:
        compute_weakness_vectors(int(user["telegram_id"]))
    return {
        "finished": True,
        "session_id": session_id,
        "status": "COMPLETED",
        "reason": reason,
        "exam_type": session["exam_type"],
        "subject": session["subject"],
        "time_remaining": float(session["time_remaining"]),
        "total_questions": int(session["total_questions"]),
        "unanswered": int(session["total_questions"]) - len(answers),
        **summary,
        "diagnostics": diagnostics,
    }


def resume_exam_session(user: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """Resume right where the student left off — if still inside the window."""
    session = _fetch_exam_session(session_id, user["telegram_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Exam session not found")
    if session["status"] == "COMPLETED":
        raise HTTPException(status_code=409, detail="Session already completed")
    last_active = _parse_db_timestamp(session["last_activity_at"])
    idle = (datetime.now(timezone.utc) - last_active).total_seconds()
    if idle > settings.EXAM_RESUME_WINDOW_MINUTES * 60:
        result = finalize_exam_session(user, session, reason="resume_expired")
        result["auto_submitted"] = True
        return result
    stamp = utc_now()
    _update_exam_session(session_id, status="IN_PROGRESS", last_activity_at=stamp)
    session["status"] = "IN_PROGRESS"
    session["last_activity_at"] = stamp
    return _exam_engine_state(session, resumed=True)


def dispatch_early_warning(user: Dict[str, Any], anomaly: Dict[str, Any], subject: str) -> None:
    message = (
        f"[EARLY WARNING] {user.get('full_name') or user['telegram_id']} scored {anomaly['score']}% on {subject}, "
        f"which is significantly below their personal median average of {anomaly['median']}%."
    )
    parents = db.fetch_all(
        "SELECT parent_id FROM student_links WHERE student_id = ? AND status = 'active'"
        if db.mode == "sqlite"
        else "SELECT parent_id FROM student_links WHERE student_id = %s AND status = 'active'",
        (user["telegram_id"],),
    )
    for row in parents:
        _bot_send(int(row["parent_id"]), message)
    logger.info("Early warning sent to %d parent(s) for student %s", len(parents), user["telegram_id"])


@app.get("/api/v1/quiz/history")
def quiz_history(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    rows = db.fetch_all(
        "SELECT id, subject, topic, score, total, correct, trust_score, finished_at "
        "FROM quiz_attempts WHERE telegram_id = ? ORDER BY id DESC LIMIT 20"
        if db.mode == "sqlite"
        else "SELECT id, subject, topic, score, total, correct, trust_score, finished_at "
        "FROM quiz_attempts WHERE telegram_id = %s ORDER BY id DESC LIMIT 20",
        (user["telegram_id"],),
    )
    return {"attempts": [dict(row) for row in rows]}


# ---------------------------------------------------------------------------
# Interactive exam engine
#
# Multi-step sessions (JAMB/WAEC/NECO) with a server-side timer, per-question
# persistence, idle auto-pause, resume window, adaptive weakness rescue and
# deep per-question telemetry. Both the web app and the Telegram /exam wizard
# drive the same engine, so state survives reconnects and process restarts.
# ---------------------------------------------------------------------------

EXAM_TYPES = ("JAMB", "WAEC", "NECO")
EXAM_ADAPTIVE_SHARE = 0.3  # fraction of questions pulled from weakest topics


class ExamStartRequest(BaseModel):
    exam_type: str = Field(..., pattern="^(JAMB|WAEC|NECO)$")
    subject: str = Field(..., min_length=2, max_length=80)
    num_questions: int = Field(10, ge=1, le=settings.EXAM_MAX_QUESTIONS)
    duration_minutes: int = Field(30, ge=1, le=settings.EXAM_MAX_DURATION_MINUTES)
    mode: str = Field("standard", pattern="^(standard|adaptive)$")


class ExamAnswerRequest(BaseModel):
    question_id: int = Field(..., gt=0)
    selected_answer: Optional[str] = Field(default=None, max_length=500)
    confidence_level: str = Field("medium", pattern="^(high|medium|low)$")
    time_spent_seconds: float = Field(0, ge=0, le=3600)
    answer_changes: int = Field(0, ge=0, le=50)
    skipped: bool = False


def _parse_db_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _jsonb_write(value: Any) -> Any:
    return psycopg2.extras.Json(value) if db.mode == "postgres" else json_dumps(value)


def _jsonb_read(value: Any) -> Any:
    if value is None:
        return []
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return []


def _fetch_exam_session(session_id: str, telegram_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    ph = "?" if db.mode == "sqlite" else "%s"
    sql = f"SELECT * FROM quiz_sessions WHERE session_id = {ph}"
    params: List[Any] = [session_id]
    if telegram_id is not None:
        sql += f" AND telegram_id = {ph}"
        params.append(telegram_id)
    row = db.fetch_one(sql, tuple(params))
    if not row:
        return None
    row["answers"] = _jsonb_read(row.get("answers"))
    row["questions_json"] = _jsonb_read(row.get("questions_json"))
    return row


def _update_exam_session(session_id: str, **fields: Any) -> None:
    if not fields:
        return
    ph = "?" if db.mode == "sqlite" else "%s"
    json_fields = {"answers", "questions_json"}
    sets = ", ".join(f"{key} = {ph}" for key in fields)
    params = [
        _jsonb_write(value) if key in json_fields else value
        for key, value in fields.items()
    ]
    db.execute(f"UPDATE quiz_sessions SET {sets} WHERE session_id = {ph}", tuple(params + [session_id]))


def weakest_topics_for(telegram_id: int, subject: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Weakest-first topic ranking for a student+subject (accuracy ascending)."""
    ph = "?" if db.mode == "sqlite" else "%s"
    rows = db.fetch_all(
        f"SELECT topic, correct_count, incorrect_count, attempts FROM student_topic_stats "
        f"WHERE telegram_id = {ph} AND LOWER(subject) = LOWER({ph}) AND (correct_count + incorrect_count) > 0 "
        f"ORDER BY (correct_count * 1.0 / (correct_count + incorrect_count)) ASC, attempts DESC LIMIT {ph}",
        (telegram_id, subject, limit),
    )
    if rows:
        return rows
    # Cold start: derive weaknesses from regular quiz history instead.
    return db.fetch_all(
        f"SELECT topic, SUM(is_correct) AS correct_count, SUM(1 - is_correct) AS incorrect_count, "
        f"COUNT(*) AS attempts FROM question_responses "
        f"WHERE telegram_id = {ph} AND LOWER(subject) = LOWER({ph}) "
        f"GROUP BY topic ORDER BY (SUM(is_correct) * 1.0 / COUNT(*)) ASC LIMIT {ph}",
        (telegram_id, subject, limit),
    )


def _fetch_bank_questions(
    exam_type: str,
    subject: str,
    limit: int,
    exclude_ids: List[int],
    topics: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    ph = "?" if db.mode == "sqlite" else "%s"
    sql = (
        "SELECT id, exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty "
        f"FROM question_bank WHERE LOWER(exam_type) = LOWER({ph}) AND LOWER(subject) = LOWER({ph})"
    )
    params: List[Any] = [exam_type, subject]
    if topics:
        placeholders = ", ".join(ph for _ in topics)
        sql += f" AND topic IN ({placeholders})"
        params.extend(topics)
    if exclude_ids:
        placeholders = ", ".join(ph for _ in exclude_ids)
        sql += f" AND id NOT IN ({placeholders})"
        params.extend(exclude_ids)
    sql += " ORDER BY RANDOM()" if db.mode == "sqlite" else " ORDER BY random()"
    sql += f" LIMIT {ph}"
    params.append(max(1, limit))
    return [normalize_question_row(dict(row)) for row in db.fetch_all(sql, tuple(params))]


def _select_exam_questions(
    exam_type: str, subject: str, telegram_id: int, limit: int, mode: str
) -> List[Dict[str, Any]]:
    """Adaptive Weakness Rescue: >=30% of the paper comes from the student's
    historically weakest topics; the rest is a fresh random spread."""
    picked: List[Dict[str, Any]] = []
    exclude: List[int] = []
    if mode == "adaptive" and limit >= 2:
        target = max(1, (limit * 3 + 9) // 10)  # ceil(limit * 0.3)
        weak_topics = [row["topic"] for row in weakest_topics_for(telegram_id, subject, limit=8)]
        # Weakest topics get priority until the 30% quota is filled.
        for topic in weak_topics:
            if len(picked) >= target:
                break
            batch = _fetch_bank_questions(exam_type, subject, target - len(picked), exclude, topics=[topic])
            picked.extend(batch)
            exclude = [int(q["id"]) for q in picked if q.get("id") is not None]
    remaining = limit - len(picked)
    if remaining > 0:
        extra = _fetch_bank_questions(exam_type, subject, remaining, exclude)
        if not extra and exclude:
            extra = _fetch_bank_questions(exam_type, subject, remaining, [])
        picked.extend(extra)
    return picked[:limit]


def create_exam_session(
    user: Dict[str, Any],
    exam_type: str,
    subject: str,
    num_questions: int,
    duration_minutes: int,
    mode: str,
) -> Dict[str, Any]:
    questions = _select_exam_questions(exam_type, subject, user["telegram_id"], num_questions, mode)
    if not questions:
        raise HTTPException(status_code=404, detail=f"No {exam_type} questions available for subject '{subject}'")
    session_id = secrets.token_hex(16)
    now = utc_now()
    duration_seconds = duration_minutes * 60
    ph = "?" if db.mode == "sqlite" else "%s"
    db.execute(
        "INSERT INTO quiz_sessions "
        "(session_id, telegram_id, exam_type, subject, mode, total_questions, current_index, "
        f"time_limit_seconds, time_remaining, status, answers, questions_json, started_at, last_activity_at) "
        f"VALUES ({', '.join(ph for _ in range(9))}, 'IN_PROGRESS', {ph}, {ph}, {ph}, {ph})",
        (
            session_id,
            user["telegram_id"],
            exam_type,
            subject,
            mode,
            len(questions),
            0,
            duration_seconds,
            duration_seconds,
            _jsonb_write([]),
            _jsonb_write(questions),
            now,
            now,
        ),
    )
    return _fetch_exam_session(session_id) or {}


def _exam_public_question(session: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    """Sanitized question payload (never leaks correct_answer to the client)."""
    questions = session.get("questions_json") or []
    if index < 0 or index >= len(questions):
        return None
    question = questions[index]
    return {
        "index": index,
        "total": int(session["total_questions"]),
        "question_id": question.get("id"),
        "question_text": question.get("question_text"),
        "options": list(question.get("options") or [])[:4],
        "topic": question.get("topic"),
        "difficulty": question.get("difficulty"),
    }


def _exam_engine_state(session: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    active = session["status"] == "IN_PROGRESS" and float(session["time_remaining"]) > 0
    return {
        "finished": False,
        "session_id": session["session_id"],
        "status": session["status"],
        "exam_type": session["exam_type"],
        "subject": session["subject"],
        "mode": session.get("mode") or "standard",
        "current_index": int(session["current_index"]),
        "total_questions": int(session["total_questions"]),
        "answered": len(session.get("answers") or []),
        "time_remaining": float(session["time_remaining"]),
        "question": _exam_public_question(session, int(session["current_index"])) if active else None,
        **extra,
    }


def _tick_exam_session(session: Dict[str, Any]) -> Dict[str, Any]:
    """Server-side clock: charge elapsed wall time, freeze on idle drop.

    If the socket dropped (idle beyond EXAM_IDLE_PAUSE_SECONDS) the timer is
    paused WITHOUT charging the gap, so a reconnect never punishes the student.
    """
    if session["status"] != "IN_PROGRESS":
        return session
    now = datetime.now(timezone.utc)
    last_active = _parse_db_timestamp(session["last_activity_at"])
    elapsed = (now - last_active).total_seconds()
    if elapsed <= 0:
        return session
    if elapsed > settings.EXAM_IDLE_PAUSE_SECONDS:
        session["status"] = "PAUSED"
        _update_exam_session(session["session_id"], status="PAUSED")
        log_telemetry(
            int(session["telegram_id"]),
            ROLE_STUDENT,
            "idle_timeout",
            {"seconds": round(elapsed, 1), "session_id": session["session_id"]},
            session_id=session["session_id"],
        )
        logger.info("Exam session %s auto-paused after %.0fs idle (timer frozen)", session["session_id"], elapsed)
        return session
    remaining = round(max(0.0, float(session["time_remaining"]) - elapsed), 2)
    stamp = now.isoformat()
    session["time_remaining"] = remaining
    session["last_activity_at"] = stamp
    _update_exam_session(session["session_id"], time_remaining=remaining, last_activity_at=stamp)
    return session


def record_exam_answer(
    user: Dict[str, Any],
    session_id: str,
    req: ExamAnswerRequest,
    background_tasks: Optional[BackgroundTasks] = None,
) -> Dict[str, Any]:
    """Persist EVERY answer to Postgres immediately, then serve the next item.

    Crash-safe ordering: the answer_telemetry row is committed first, then the
    session row advances, so a mid-write failure can at worst duplicate an
    answer, never lose one.
    """
    session = _fetch_exam_session(session_id, user["telegram_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Exam session not found")
    session = _tick_exam_session(session)
    if session["status"] == "PAUSED":
        raise HTTPException(status_code=409, detail="Session paused after inactivity — resume to continue")
    if session["status"] != "IN_PROGRESS":
        raise HTTPException(status_code=409, detail="Session already completed")
    if float(session["time_remaining"]) <= 0:
        return finalize_exam_session(user, session, reason="time_expired", background_tasks=background_tasks)
    questions = session.get("questions_json") or []
    index = int(session["current_index"])
    if index >= len(questions):
        return finalize_exam_session(user, session, reason="completed", background_tasks=background_tasks)
    question = questions[index]
    if int(req.question_id) != int(question.get("id") or 0):
        raise HTTPException(status_code=409, detail="Answer does not match the current question")
    is_skipped = req.skipped or not req.selected_answer
    correct_answer = question.get("correct_answer")
    is_correct = bool(req.selected_answer) and req.selected_answer == correct_answer
    topic = question.get("topic") or "General"
    answer_record = {
        "question_id": question.get("id"),
        "question_text": question.get("question_text"),
        "options": list(question.get("options") or [])[:4],
        "selected_answer": req.selected_answer,
        "correct_answer": correct_answer,
        "is_correct": is_correct,
        "skipped": is_skipped,
        "confidence_level": req.confidence_level,
        "time_spent_seconds": float(req.time_spent_seconds),
        "answer_changes": int(req.answer_changes),
        "subject": session["subject"],
        "topic": topic,
        "answered_at": utc_now(),
    }
    ph = "?" if db.mode == "sqlite" else "%s"
    db.execute(
        "INSERT INTO answer_telemetry "
        "(session_id, telegram_id, question_id, subject, topic, selected_answer, confidence_level, "
        "is_correct, time_spent_seconds, answer_changes, event_type, answered_at) "
        f"VALUES ({', '.join(ph for _ in range(12))})",
        (
            session_id,
            user["telegram_id"],
            question.get("id"),
            session["subject"],
            topic,
            req.selected_answer,
            req.confidence_level,
            1 if is_correct else 0,
            float(req.time_spent_seconds),
            int(req.answer_changes),
            "skip" if is_skipped else "answer",
            answer_record["answered_at"],
        ),
    )
    # Stealth telemetry: raw answer delta + tab-switch behavior.
    if not is_skipped:
        log_telemetry(
            user["telegram_id"],
            session.get("role") or ROLE_STUDENT,
            "answer_delta",
            {
                "session_id": session_id,
                "question_id": question.get("id"),
                "topic": topic,
                "subject": session["subject"],
                "is_correct": is_correct,
                "confidence": req.confidence_level,
                "time_spent_seconds": float(req.time_spent_seconds),
            },
            session_id=session_id,
        )
    if int(req.answer_changes or 0) > 0:
        log_telemetry(
            user["telegram_id"],
            session.get("role") or ROLE_STUDENT,
            "tab_switch",
            {"changes": int(req.answer_changes), "topic": topic, "session_id": session_id},
            session_id=session_id,
        )
    new_index = index + 1
    answers = (session.get("answers") or []) + [answer_record]
    stamp = utc_now()
    _update_exam_session(session_id, answers=answers, current_index=new_index, last_activity_at=stamp)
    session.update(current_index=new_index, answers=answers, last_activity_at=stamp)
    if new_index >= int(session["total_questions"]):
        return finalize_exam_session(user, session, reason="completed", background_tasks=background_tasks)
    if float(session["time_remaining"]) <= 0:
        return finalize_exam_session(user, session, reason="time_expired", background_tasks=background_tasks)
    return _exam_engine_state(session)

# --- Exam engine API -------------------------------------------------------


@app.get("/api/v1/exam/weakness")
def exam_weakness_endpoint(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """Underground tracker view: weakest subjects/topics for the current user."""
    telegram_id = user["telegram_id"]
    ph = "?" if db.mode == "sqlite" else "%s"
    profile = db.fetch_one(f"SELECT weakness_json FROM profiles WHERE telegram_id = {ph}", (telegram_id,))
    topics = db.fetch_all(
        f"SELECT subject, topic, correct_count, incorrect_count, total_time_seconds, attempts "
        f"FROM student_topic_stats WHERE telegram_id = {ph} AND (correct_count + incorrect_count) > 0 "
        f"ORDER BY (correct_count * 1.0 / (correct_count + incorrect_count)) ASC LIMIT 10",
        (telegram_id,),
    )
    for row in topics:
        total = int(row["correct_count"] or 0) + int(row["incorrect_count"] or 0)
        row["accuracy_pct"] = round(100.0 * int(row["correct_count"] or 0) / total, 1) if total else 0.0
        row["average_time_per_question"] = round(
            float(row["total_time_seconds"] or 0) / max(1, int(row["attempts"] or 0)), 1
        )
    return {"cached_profile": _jsonb_read((profile or {}).get("weakness_json")) or None, "weakest_topics": topics}


@app.post("/api/v1/exam/start")
def exam_start(payload: ExamStartRequest, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """Step 4 of the flow: launch the session and serve the first question."""
    session = create_exam_session(
        user,
        payload.exam_type,
        payload.subject.strip(),
        payload.num_questions,
        payload.duration_minutes,
        payload.mode,
    )
    return _exam_engine_state(session)


@app.get("/api/v1/exam/{session_id}")
def exam_state(session_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """State probe used by the resume flow and reconnect recovery."""
    session = _fetch_exam_session(session_id, user["telegram_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Exam session not found")
    session = _tick_exam_session(session)
    if session["status"] != "COMPLETED" and float(session["time_remaining"]) <= 0:
        return finalize_exam_session(user, session, reason="time_expired")
    return _exam_engine_state(session)


@app.post("/api/v1/exam/{session_id}/answer")
def exam_answer(
    session_id: str,
    payload: ExamAnswerRequest,
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """Per-question submission: persists state BEFORE serving the next item."""
    return record_exam_answer(user, session_id, payload, background_tasks)


@app.post("/api/v1/exam/{session_id}/heartbeat")
def exam_heartbeat(session_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """Backend heart-beat: charges time, auto-pauses on idle, expires on timeout."""
    session = _fetch_exam_session(session_id, user["telegram_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Exam session not found")
    session = _tick_exam_session(session)
    if session["status"] != "COMPLETED" and float(session["time_remaining"]) <= 0:
        return finalize_exam_session(user, session, reason="time_expired")
    return _exam_engine_state(session)


@app.post("/api/v1/exam/{session_id}/pause")
def exam_pause(session_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    session = _fetch_exam_session(session_id, user["telegram_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Exam session not found")
    session = _tick_exam_session(session)
    if session["status"] == "COMPLETED":
        raise HTTPException(status_code=409, detail="Session already completed")
    if session["status"] != "PAUSED":
        _update_exam_session(session_id, status="PAUSED")
        session["status"] = "PAUSED"
    return _exam_engine_state(session)


@app.post("/api/v1/exam/{session_id}/resume")
def exam_resume(session_id: str, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """Resume right where the student left off — if still inside the window."""
    return resume_exam_session(user, session_id)


@app.post("/api/v1/exam/{session_id}/finish")
def exam_finish(
    session_id: str,
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    """Quit guard: automated submission scored on the questions answered so far."""
    session = _fetch_exam_session(session_id, user["telegram_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Exam session not found")
    session = _tick_exam_session(session)
    return finalize_exam_session(user, session, reason="user_quit", background_tasks=background_tasks)


def build_stream_token(telegram_id: int) -> str:
    token_age = str(int(datetime.now(timezone.utc).timestamp() // 3600))
    raw = f"{telegram_id}:{token_age}".encode("utf-8")
    digest = hmac.new(settings.TELEGRAM_BOT_TOKEN.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return f"{telegram_id}:{digest}"


def stream_user(token: str = Query(default="", max_length=128)) -> Dict[str, Any]:
    if not token or not settings.TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=401, detail="Missing stream token")
    try:
        telegram_id = int(token.split(":")[0])
    except (TypeError, ValueError, IndexError):
        telegram_id = 0
    if telegram_id <= 0:
        raise HTTPException(status_code=401, detail="Invalid stream token")
    expected = build_stream_token(telegram_id)
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid stream token")
    return {"telegram_id": telegram_id}


async def stream_gen(user_id: int) -> AsyncGenerator[str, None]:
    try:
        yield "event: connected\ndata: {\"ok\": true, \"telegram_id\": %d}\n\n" % user_id
        while True:
            await asyncio.sleep(15)
            yield ": ping\n\n"
    except asyncio.CancelledError:
        raise


@app.get("/api/v1/stream")
async def stream_events(
    request: Request, user: Dict[str, Any] = Depends(stream_user)
) -> StreamingResponse:
    return StreamingResponse(
        stream_gen(user["telegram_id"]),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/v1/access/join")
def access_join(payload: AccessJoinRequest, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    code = payload.code.strip()
    row = db.fetch_one(
        "SELECT id, code, kind, payload, used_by, expires_at FROM access_codes WHERE code = ?"
        if db.mode == "sqlite"
        else "SELECT id, code, kind, payload, used_by, expires_at FROM access_codes WHERE code = %s",
        (code,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Access code not found")
    if row.get("expires_at"):
        try:
            expiry = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
            if expiry < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="Access code has expired")
        except ValueError:
            pass
    meta = {}
    if row.get("payload"):
        try:
            meta = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            meta = {}
    kind = row.get("kind", "")
    telegram_id = user["telegram_id"]
    school_id = user.get("school_id")

    if kind == "school_class":
        school_code = meta.get("school_code") or code.split("-")[0]
        school = db.fetch_one(
            "SELECT id, code FROM schools WHERE code = ?"
            if db.mode == "sqlite"
            else "SELECT id, code FROM schools WHERE code = %s",
            (school_code,),
        )
        if school is None:
            raise HTTPException(status_code=404, detail="School for class code not found")
        db.execute(
            "UPDATE profiles SET school_id = ?, class_code = ?, updated_at = ? WHERE telegram_id = ?"
            if db.mode == "sqlite"
            else "UPDATE profiles SET school_id = %s, class_code = %s, updated_at = %s WHERE telegram_id = %s",
            (school["id"], meta.get("class_code") or code, utc_now(), telegram_id),
        )
    elif kind == "teacher_invite":
        school_code = meta.get("school_code") or ""
        school = None
        if school_code:
            school = db.fetch_one(
                "SELECT id, code FROM schools WHERE code = ?"
                if db.mode == "sqlite"
                else "SELECT id, code FROM schools WHERE code = %s",
                (school_code,),
            )
        db.execute(
            "UPDATE profiles SET role = ?, school_id = ?, updated_at = ? WHERE telegram_id = ?"
            if db.mode == "sqlite"
            else "UPDATE profiles SET role = %s, school_id = %s, updated_at = %s WHERE telegram_id = %s",
            (ROLE_TEACHER, school["id"] if school else school_id, utc_now(), telegram_id),
        )
    elif kind == "school_admin_invite":
        school_code = meta.get("school_code") or ""
        school = None
        if school_code:
            school = db.fetch_one(
                "SELECT id, code FROM schools WHERE code = ?"
                if db.mode == "sqlite"
                else "SELECT id, code FROM schools WHERE code = %s",
                (school_code,),
            )
        db.execute(
            "UPDATE profiles SET role = ?, school_id = ?, updated_at = ? WHERE telegram_id = ?"
            if db.mode == "sqlite"
            else "UPDATE profiles SET role = %s, school_id = %s, updated_at = %s WHERE telegram_id = %s",
            (ROLE_SCHOOL_ADMIN, school["id"] if school else school_id, utc_now(), telegram_id),
        )
    elif kind == "super_key":
        if super_admin_count() >= settings.SUPER_ADMIN_CAP:
            raise HTTPException(
                status_code=409,
                detail=f"Super admin cap of {settings.SUPER_ADMIN_CAP} reached; multi-signature approval required",
            )
        db.execute(
            "UPDATE profiles SET role = ?, updated_at = ? WHERE telegram_id = ?"
            if db.mode == "sqlite"
            else "UPDATE profiles SET role = %s, updated_at = %s WHERE telegram_id = %s",
            (ROLE_SUPER_ADMIN, utc_now(), telegram_id),
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported access code kind: {kind}")

    db.execute(
        "UPDATE access_codes SET used_by = ? WHERE id = ?"
        if db.mode == "sqlite"
        else "UPDATE access_codes SET used_by = %s WHERE id = %s",
        (telegram_id, row["id"]),
    )
    profile = get_profile(telegram_id) or {}
    return {"status": "joined", "role": profile.get("role"), "school_id": profile.get("school_id"), "class_code": profile.get("class_code")}


@app.post("/api/v1/access/generate")
def access_generate(
    payload: AccessGenerateRequest,
    user: Dict[str, Any] = Depends(require_roles(ROLE_SUPER_ADMIN, ROLE_SCHOOL_ADMIN)),
) -> Dict[str, Any]:
    allowed = {
        ROLE_SCHOOL_ADMIN: {"school_class", "teacher_invite"},
        ROLE_SUPER_ADMIN: {"school_class", "teacher_invite", "school_admin_invite", "super_key"},
    }
    if payload.kind not in allowed[user["role"]]:
        raise HTTPException(status_code=403, detail=f"Role {user['role']} cannot generate kind={payload.kind}")
    if payload.kind == "super_key" and super_admin_count() >= settings.SUPER_ADMIN_CAP:
        raise HTTPException(status_code=409, detail="Super admin cap reached; multi-signature approval required")

    school_id = user.get("school_id")
    school_code = ""
    if school_id:
        school = db.fetch_one(
            "SELECT code FROM schools WHERE id = ?"
            if db.mode == "sqlite"
            else "SELECT code FROM schools WHERE id = %s",
            (school_id,),
        )
        school_code = str(school["code"]) if school else ""
    default_payload = json_dumps({"school_code": school_code or (payload.payload or {}).get("school_code", "")})
    if payload.kind == "school_class" and payload.payload and payload.payload.get("class_code"):
        default_payload = json_dumps(payload.payload)
    if payload.kind == "school_admin_invite":
        school_code = str((payload.payload or {}).get("school_code") or school_code)
        default_payload = json_dumps({"school_code": school_code})

    generated: List[str] = []
    for _ in range(payload.count):
        token = build_access_code_token(payload.kind)
        db.execute(
            "INSERT INTO access_codes (code, kind, payload, created_by) VALUES (?, ?, ?, ?)"
            if db.mode == "sqlite"
            else "INSERT INTO access_codes (code, kind, payload, created_by) VALUES (%s, %s, %s, %s)",
            (token, payload.kind, default_payload, user["telegram_id"]),
        )
        generated.append(token)
    return {"generated": generated, "kind": payload.kind}


@app.get("/api/v1/access/my-linking-code")
def my_linking_code(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return {"linking_code": user.get("linking_code")}


@app.post("/api/v1/access/link-child")
def link_child(payload: ChildLinkRequest, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    if user.get("role") != ROLE_PARENT and user.get("role") != ROLE_STUDENT:
        raise HTTPException(status_code=403, detail="Only parents (or students self-registering a guardian) may link")
    code = payload.code.strip().upper()
    student = db.fetch_one(
        "SELECT telegram_id, role FROM profiles WHERE UPPER(linking_code) = ?"
        if db.mode == "sqlite"
        else "SELECT telegram_id, role FROM profiles WHERE UPPER(linking_code) = %s",
        (code,),
    )
    if student is None:
        raise HTTPException(status_code=404, detail="Student linking code not found")
    student_id = int(student["telegram_id"])
    if student_id == user["telegram_id"]:
        raise HTTPException(status_code=400, detail="You cannot link your own account")
    db.execute(
        "INSERT INTO student_links (parent_id, student_id, status) VALUES (?, ?, ?)"
        " ON CONFLICT(parent_id, student_id) DO NOTHING"
        if db.mode == "sqlite"
        else "INSERT INTO student_links (parent_id, student_id, status) VALUES (%s, %s, %s)"
        " ON CONFLICT (parent_id, student_id) DO NOTHING",
        (user["telegram_id"], student_id, "active"),
    )
    if user.get("role") == ROLE_STUDENT:
        db.execute(
            "UPDATE profiles SET role = ?, updated_at = ? WHERE telegram_id = ?"
            if db.mode == "sqlite"
            else "UPDATE profiles SET role = %s, updated_at = %s WHERE telegram_id = %s",
            (ROLE_PARENT, utc_now(), user["telegram_id"]),
        )
    log_telemetry(
        user["telegram_id"],
        user.get("role") or ROLE_PARENT,
        "parent_lookup",
        {"student_id": student_id, "method": "api_link_child"},
    )
    return {"status": "linked", "student_id": student_id}


@app.get("/api/v1/analytics/me")
def analytics_me(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    attempts = db.fetch_all(
        "SELECT id, subject, topic, score, total, correct, trust_score, finished_at "
        "FROM quiz_attempts WHERE telegram_id = ? ORDER BY id DESC LIMIT 25"
        if db.mode == "sqlite"
        else "SELECT id, subject, topic, score, total, correct, trust_score, finished_at "
        "FROM quiz_attempts WHERE telegram_id = %s ORDER BY id DESC LIMIT 25",
        (user["telegram_id"],),
    )
    responses = db.fetch_all(
        "SELECT question_text, subject, topic, sub_topic, is_correct, seconds_spent, error_type "
        "FROM question_responses WHERE telegram_id = ? ORDER BY id DESC LIMIT 300"
        if db.mode == "sqlite"
        else "SELECT question_text, subject, topic, sub_topic, is_correct, seconds_spent, error_type "
        "FROM question_responses WHERE telegram_id = %s ORDER BY id DESC LIMIT 300",
        (user["telegram_id"],),
    )
    heatmap: Dict[str, Dict[str, List[bool]]] = {}
    for row in responses:
        topic = row.get("topic") or "General"
        sub = row.get("sub_topic") or topic
        heatmap.setdefault(topic, {}).setdefault(sub, []).append(bool(row.get("is_correct")))
    heatmap_clean = {
        topic: {sub: round((sum(v) / len(v)) * 100, 1) for sub, v in subs.items()}
        for topic, subs in heatmap.items()
    }
    scores = [float(row["score"]) for row in attempts]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    low, high = predict_jamb_range(avg_score)
    knowledge_gaps = sum(1 for r in responses if r.get("error_type") == "knowledge_gap")
    careless = sum(1 for r in responses if r.get("error_type") == "careless")
    bounties = db.fetch_all(
        "SELECT id, title, reward, target_score, subject, status FROM micro_bounties WHERE student_id = ? AND status = 'open'"
        if db.mode == "sqlite"
        else "SELECT id, title, reward, target_score, subject, status FROM micro_bounties WHERE student_id = %s AND status = 'open'",
        (user["telegram_id"],),
    )
    contracts = db.fetch_all(
        "SELECT id, target_text, threshold_score, deadline, status FROM intervention_contracts WHERE student_id = ? ORDER BY id DESC LIMIT 10"
        if db.mode == "sqlite"
        else "SELECT id, target_text, threshold_score, deadline, status FROM intervention_contracts WHERE student_id = %s ORDER BY id DESC LIMIT 10",
        (user["telegram_id"],),
    )
    return {
        "premium": bool(user.get("premium")),
        "role": user.get("role"),
        "class_code": user.get("class_code"),
        "attempts": attempts,
        "avg_score": avg_score,
        "prediction": {"jamb_low": low, "jamb_high": high, "waec_grade": predict_waec_grade(avg_score)},
        "heatmap": heatmap_clean,
        "error_profiler": {"knowledge_gaps": knowledge_gaps, "careless_rush": careless},
        "bounties": bounties,
        "contracts": contracts,
    }


@app.get("/api/v1/analytics/child/{child_id}")
def analytics_child(child_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    if child_id != user["telegram_id"] and user.get("role") not in (ROLE_PARENT, ROLE_SUPER_ADMIN):
        raise HTTPException(status_code=403, detail="Not linked to this student")
    if child_id != user["telegram_id"]:
        links = db.fetch_one(
            "SELECT id FROM student_links WHERE parent_id = ? AND student_id = ? AND status = 'active'"
            if db.mode == "sqlite"
            else "SELECT id FROM student_links WHERE parent_id = %s AND student_id = %s AND status = 'active'",
            (user["telegram_id"], child_id),
        )
        if links is None:
            raise HTTPException(status_code=403, detail="Not linked to this student")
    god_mode = user.get("role") == ROLE_PARENT and user.get("parent_mode") == "god"
    attempts = db.fetch_all(
        "SELECT id, subject, topic, score, total, correct, trust_score, finished_at "
        "FROM quiz_attempts WHERE telegram_id = ? ORDER BY id DESC LIMIT 30"
        if db.mode == "sqlite"
        else "SELECT id, subject, topic, score, total, correct, trust_score, finished_at "
        "FROM quiz_attempts WHERE telegram_id = %s ORDER BY id DESC LIMIT 30",
        (child_id,),
    )
    payload: Dict[str, Any] = {"god_mode": god_mode, "attempts": attempts}
    if god_mode or user.get("role") == ROLE_SUPER_ADMIN:
        attempt_ids = [row["id"] for row in attempts] or [0]
        placeholders = ", ".join("?" if db.mode == "sqlite" else "%s" for _ in attempt_ids)
        responses = db.fetch_all(
            f"SELECT attempt_id, question_text, topic, sub_topic, selected_answer, correct_answer, is_correct, "
            f"seconds_spent, switches, switch_trail, is_time_sink, is_rushed, error_type "
            f"FROM question_responses WHERE attempt_id IN ({placeholders}) ORDER BY id",
            tuple(attempt_ids),
        )
        events = db.fetch_all(
            f"SELECT attempt_id, event_type, detail, occurred_at FROM behavior_events WHERE attempt_id IN ({placeholders}) ORDER BY occurred_at",
            tuple(attempt_ids),
        )
        payload["responses"] = responses
        payload["events"] = events
    return payload


@app.get("/api/v1/analytics/school")
def analytics_school(user: Dict[str, Any] = Depends(require_roles(ROLE_SCHOOL_ADMIN, ROLE_TEACHER, ROLE_SUPER_ADMIN))) -> Dict[str, Any]:
    school_id = user.get("school_id")
    rows = db.fetch_all(
        "SELECT class_code, subject, AVG(score) AS avg_score, COUNT(*) AS attempts "
        "FROM quiz_attempts WHERE (? IS NULL OR school_id = ?) GROUP BY class_code, subject"
        if db.mode == "sqlite"
        else "SELECT class_code, subject, AVG(score) AS avg_score, COUNT(*) AS attempts "
        "FROM quiz_attempts WHERE (%s IS NULL OR school_id = %s) GROUP BY class_code, subject",
        (school_id, school_id),
    )
    classes: Dict[str, Dict[str, Any]] = {}
    readiness: List[Dict[str, Any]] = []
    for row in rows:
        class_code = row.get("class_code") or "Unassigned"
        subject = row.get("subject", "General")
        entry = classes.setdefault(class_code, {"subjects": {}, "attempts": 0})
        entry["subjects"][subject] = {"avg_score": round(float(row["avg_score"]), 1), "attempts": int(row["attempts"])}
        entry["attempts"] += int(row["attempts"])
        if float(row["avg_score"]) < 50:
            readiness.append({"class_code": class_code, "subject": subject, "avg_score": round(float(row["avg_score"]), 1)})
    assignments = db.fetch_all(
        "SELECT id, teacher_id, class_code, subject, topic, limit_count, created_at "
        "FROM assignments WHERE (? IS NULL OR school_id = ?) ORDER BY id DESC LIMIT 50"
        if db.mode == "sqlite"
        else "SELECT id, teacher_id, class_code, subject, topic, limit_count, created_at "
        "FROM assignments WHERE (%s IS NULL OR school_id = %s) ORDER BY id DESC LIMIT 50",
        (school_id, school_id),
    )
    return {"classes": classes, "readiness": readiness, "assignments": assignments, "super_admins": super_admin_count()}


@app.get("/api/v1/analytics/league")
def analytics_league() -> Dict[str, Any]:
    rows = db.fetch_all(
        "SELECT s.code AS school_code, s.name AS school_name, a.subject, AVG(a.score) AS avg_score, COUNT(*) AS attempts "
        "FROM quiz_attempts a JOIN schools s ON s.id = a.school_id "
        "WHERE a.school_id IS NOT NULL GROUP BY s.id, s.code, s.name, a.subject "
        "ORDER BY avg_score DESC LIMIT 25"
    )
    return {"league": [dict(row) for row in rows]}


@app.post("/api/v1/assignments")
def create_assignment(
    payload: AssignmentCreateRequest,
    user: Dict[str, Any] = Depends(require_roles(ROLE_TEACHER, ROLE_SCHOOL_ADMIN, ROLE_SUPER_ADMIN)),
) -> Dict[str, Any]:
    db.execute(
        "INSERT INTO assignments (teacher_id, school_id, class_code, subject, topic, limit_count) VALUES (?, ?, ?, ?, ?, ?)"
        if db.mode == "sqlite"
        else "INSERT INTO assignments (teacher_id, school_id, class_code, subject, topic, limit_count) VALUES (%s, %s, %s, %s, %s, %s)",
        (user["telegram_id"], user.get("school_id"), payload.class_code, payload.subject, payload.topic, payload.limit_count),
    )
    return {"status": "assigned", "class_code": payload.class_code, "subject": payload.subject}


@app.post("/api/v1/interventions/contracts")
def create_contract(
    payload: ContractCreateRequest,
    user: Dict[str, Any] = Depends(require_roles(ROLE_TEACHER, ROLE_SCHOOL_ADMIN)),
) -> Dict[str, Any]:
    db.execute(
        "INSERT INTO intervention_contracts (student_id, teacher_id, target_text, threshold_score, deadline, status) VALUES (?, ?, ?, ?, ?, ?)"
        if db.mode == "sqlite"
        else "INSERT INTO intervention_contracts (student_id, teacher_id, target_text, threshold_score, deadline, status) VALUES (%s, %s, %s, %s, %s, %s)",
        (payload.student_id, user["telegram_id"], payload.target_text, payload.threshold_score, payload.deadline, "pending"),
    )
    return {"status": "created"}


@app.get("/api/v1/interventions/contracts")
def list_contracts(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    role = user.get("role")
    ph = "?" if db.mode == "sqlite" else "%s"
    if role in (ROLE_TEACHER, ROLE_SCHOOL_ADMIN, ROLE_SUPER_ADMIN):
        where = f"teacher_id = {ph}" if role == ROLE_TEACHER else "1 = 1"
    elif role == ROLE_PARENT:
        children = linked_children(user["telegram_id"])
        where = f"student_id IN ({', '.join([ph] * len(children))})" if children else "1 = 0"
    else:
        where = f"student_id = {ph}"
    rows = db.fetch_all(
        f"SELECT id, student_id, teacher_id, target_text, threshold_score, deadline, status, created_at FROM intervention_contracts WHERE {where} ORDER BY id DESC LIMIT 50",
        tuple([user["telegram_id"]] if role in (ROLE_TEACHER, ROLE_STUDENT) else []),
    )
    return {"contracts": [dict(row) for row in rows]}


@app.post("/api/v1/interventions/contracts/{contract_id}/accept")
def accept_contract(contract_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    contract = db.fetch_one(
        "SELECT id, student_id, parent_id, status FROM intervention_contracts WHERE id = ?"
        if db.mode == "sqlite"
        else "SELECT id, student_id, parent_id, status FROM intervention_contracts WHERE id = %s",
        (contract_id,),
    )
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.get("status") != "pending":
        raise HTTPException(status_code=409, detail="Contract already processed")
    is_student = user["telegram_id"] == int(contract["student_id"])
    is_parent = int(contract["parent_id"] or 0) == user["telegram_id"] or (
        user.get("role") == ROLE_PARENT and int(contract["student_id"]) in linked_children(user["telegram_id"])
    )
    if not (is_student or is_parent):
        raise HTTPException(status_code=403, detail="Only the student or their parent may accept")
    db.execute(
        "UPDATE intervention_contracts SET status = ? WHERE id = ?"
        if db.mode == "sqlite"
        else "UPDATE intervention_contracts SET status = %s WHERE id = %s",
        ("accepted", contract_id),
    )
    return {"status": "accepted"}


@app.post("/api/v1/bounties")
def create_bounty(payload: BountyCreateRequest, user: Dict[str, Any] = Depends(require_roles(ROLE_PARENT, ROLE_SCHOOL_ADMIN))) -> Dict[str, Any]:
    db.execute(
        "INSERT INTO micro_bounties (parent_id, student_id, title, reward, target_score, subject, status) VALUES (?, ?, ?, ?, ?, ?, ?)"
        if db.mode == "sqlite"
        else "INSERT INTO micro_bounties (parent_id, student_id, title, reward, target_score, subject, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (user["telegram_id"], payload.student_id, payload.title, payload.reward, payload.target_score, payload.subject, "open"),
    )
    return {"status": "created"}


@app.get("/api/v1/bounties")
def list_bounties(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    if user.get("role") == ROLE_PARENT:
        rows = db.fetch_all(
            "SELECT id, student_id, title, reward, target_score, subject, status, created_at FROM micro_bounties WHERE parent_id = ? ORDER BY id DESC LIMIT 50"
            if db.mode == "sqlite"
            else "SELECT id, student_id, title, reward, target_score, subject, status, created_at FROM micro_bounties WHERE parent_id = %s ORDER BY id DESC LIMIT 50",
            (user["telegram_id"],),
        )
    else:
        rows = db.fetch_all(
            "SELECT id, title, reward, target_score, subject, status FROM micro_bounties WHERE student_id = ? AND status = 'open' ORDER BY id DESC LIMIT 50"
            if db.mode == "sqlite"
            else "SELECT id, title, reward, target_score, subject, status FROM micro_bounties WHERE student_id = %s AND status = 'open' ORDER BY id DESC LIMIT 50",
            (user["telegram_id"],),
        )
    return {"bounties": [dict(row) for row in rows]}


@app.post("/api/v1/bounties/{bounty_id}/claim")
def claim_bounty(bounty_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    bounty = db.fetch_one(
        "SELECT id, student_id, target_score, subject, status, reward FROM micro_bounties WHERE id = ?"
        if db.mode == "sqlite"
        else "SELECT id, student_id, target_score, subject, status, reward FROM micro_bounties WHERE id = %s",
        (bounty_id,),
    )
    if bounty is None:
        raise HTTPException(status_code=404, detail="Bounty not found")
    if int(bounty["student_id"]) != user["telegram_id"]:
        raise HTTPException(status_code=403, detail="This bounty is not for you")
    if bounty.get("status") != "open":
        raise HTTPException(status_code=409, detail="Bounty already claimed")
    best = db.fetch_one(
        "SELECT MAX(score) AS best FROM quiz_attempts WHERE telegram_id = ? AND (? IS NULL OR subject = ?)"
        if db.mode == "sqlite"
        else "SELECT MAX(score) AS best FROM quiz_attempts WHERE telegram_id = %s AND (%s IS NULL OR subject = %s)",
        (user["telegram_id"], bounty.get("subject"), bounty.get("subject")),
    )
    best_score = float(best["best"]) if best and best.get("best") is not None else 0.0
    if best_score < float(bounty["target_score"]):
        raise HTTPException(status_code=409, detail=f"Target not met yet (best {best_score}% vs {bounty['target_score']}%)")
    db.execute(
        "UPDATE micro_bounties SET status = ?, claimed_at = ? WHERE id = ?"
        if db.mode == "sqlite"
        else "UPDATE micro_bounties SET status = %s, claimed_at = %s WHERE id = %s",
        ("claimed", utc_now(), bounty_id),
    )
    return {"status": "claimed", "reward": float(bounty.get("reward") or 0)}


@app.post("/api/v1/digest/generate")
def generate_digest(payload: DigestGenerateRequest, user: Dict[str, Any] = Depends(require_roles(ROLE_PARENT, ROLE_SCHOOL_ADMIN))) -> Dict[str, Any]:
    children = linked_children(user["telegram_id"])
    if payload.student_id not in children:
        raise HTTPException(status_code=403, detail="Student not linked to this parent")
    rows = db.fetch_all(
        "SELECT subject, topic, score, total, correct, trust_score, seconds_spent, finished_at "
        "FROM quiz_attempts WHERE telegram_id = ? AND finished_at >= ? ORDER BY id DESC LIMIT 50"
        if db.mode == "sqlite"
        else "SELECT subject, topic, score, total, correct, trust_score, seconds_spent, finished_at "
        "FROM quiz_attempts WHERE telegram_id = %s AND finished_at >= %s ORDER BY id DESC LIMIT 50",
        (payload.student_id, (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()),
    )
    total_minutes = sum(float(row.get("seconds_spent") or 0) for row in rows) / 60.0
    weak: Dict[str, float] = {}
    for row in rows:
        weak[row["subject"]] = float(row["score"])
    weak_topics = sorted(weak, key=weak.get)[:3] if weak else []
    lines = [
        "NAIJA SCHOLAR WEEKLY DIGEST (Lighthouse Intel Academy)",
        f"Student ID: {payload.student_id}",
        f"Practice this week: {len(rows)} tests, {round(total_minutes, 1)} minutes",
    ]
    if rows:
        avg = round(sum(float(r["score"]) for r in rows) / len(rows), 1)
        lines.append(f"Average score: {avg}%")
        lines.append(f"Weak areas: {', '.join(weak_topics)}")
        avg_trust = round(sum(float(r.get("trust_score") or 0) for r in rows) / len(rows), 1)
        lines.append(f"Anti-cheating trust score: {avg_trust}%")
    else:
        lines.append("No practice recorded this week - please encourage daily drills.")
    lines.append("Teacher feedback: pending classroom sync.")
    lines.append("Reply /contract to set a joint intervention target with your child's teacher.")
    digest_text = "\n".join(lines)
    db.execute(
        "INSERT INTO guardian_digests (parent_id, student_id, week_start, digest_text, sent_via) VALUES (?, ?, ?, ?, ?)"
        if db.mode == "sqlite"
        else "INSERT INTO guardian_digests (parent_id, student_id, week_start, digest_text, sent_via) VALUES (%s, %s, %s, %s, %s)",
        (user["telegram_id"], payload.student_id, (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(), digest_text, "telegram"),
    )
    _bot_send(user["telegram_id"], digest_text)
    logger.info("Weekly digest sent via Telegram to parent=%s for student=%s", user["telegram_id"], payload.student_id)
    return {"digest": digest_text}


class ParentModeRequest(BaseModel):
    mode: str = Field(..., pattern="^(free|god)$")


class SchoolCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    code: str = Field(..., min_length=2, max_length=20)


@app.get("/api/v1/auth/profile")
def auth_profile(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return user


@app.post("/api/v1/access/parent-mode")
def set_parent_mode(payload: ParentModeRequest, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    db.execute(
        "UPDATE profiles SET parent_mode = ?, updated_at = ? WHERE telegram_id = ?"
        if db.mode == "sqlite"
        else "UPDATE profiles SET parent_mode = %s, updated_at = %s WHERE telegram_id = %s",
        (payload.mode, utc_now(), user["telegram_id"]),
    )
    return {"parent_mode": payload.mode}


@app.get("/api/v1/analytics/school/students")
def school_students(
    class_code: str = Query("", max_length=40),
    user: Dict[str, Any] = Depends(require_roles(ROLE_TEACHER, ROLE_SCHOOL_ADMIN, ROLE_SUPER_ADMIN)),
) -> Dict[str, Any]:
    rows = db.fetch_all(
        "SELECT u.telegram_id, u.full_name, p.class_code FROM users u "
        "JOIN profiles p ON p.telegram_id = u.telegram_id "
        "WHERE (? IS NULL OR ? = '' OR p.class_code = ?) AND p.role = 'STUDENT' ORDER BY u.full_name"
        if db.mode == "sqlite"
        else "SELECT u.telegram_id, u.full_name, p.class_code FROM users u "
        "JOIN profiles p ON p.telegram_id = u.telegram_id "
        "WHERE (%s IS NULL OR %s = '' OR p.class_code = %s) AND p.role = 'STUDENT' ORDER BY u.full_name",
        (class_code, class_code, class_code),
    )
    return {"students": [dict(row) for row in rows]}


@app.post("/api/v1/schools")
def create_school(
    payload: SchoolCreateRequest,
    user: Dict[str, Any] = Depends(require_roles(ROLE_SUPER_ADMIN)),
) -> Dict[str, Any]:
    existing = db.fetch_one(
        "SELECT id FROM schools WHERE code = ?"
        if db.mode == "sqlite"
        else "SELECT id FROM schools WHERE code = %s",
        (payload.code.upper(),),
    )
    if existing:
        raise HTTPException(status_code=409, detail="School code already exists")
    db.execute(
        "INSERT INTO schools (name, code, plan) VALUES (?, ?, ?)"
        if db.mode == "sqlite"
        else "INSERT INTO schools (name, code, plan) VALUES (%s, %s, %s)",
        (payload.name.strip(), payload.code.upper(), "standard"),
    )
    invite = build_access_code_token("school_admin_invite")
    db.execute(
        "INSERT INTO access_codes (code, kind, payload, created_by) VALUES (?, ?, ?, ?)"
        if db.mode == "sqlite"
        else "INSERT INTO access_codes (code, kind, payload, created_by) VALUES (%s, %s, %s, %s)",
        (invite, "school_admin_invite", json_dumps({"school_code": payload.code.upper()}), user["telegram_id"]),
    )
    return {"status": "created", "school_code": payload.code.upper(), "admin_invite_code": invite}


class StudyWindowUpsert(BaseModel):
    student_id: int = Field(..., gt=0)
    day_label: str = Field("weekdays", pattern="^(weekdays|weekends|sunday|monday|tuesday|wednesday|thursday|friday|saturday)$")
    start_time: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    enabled: bool = True


class MockPaperRequest(BaseModel):
    subject: str = Field(..., min_length=2, max_length=80)
    topic: Optional[str] = None
    class_code: Optional[str] = None
    title: Optional[str] = None
    limit_count: int = Field(10, ge=1, le=50)
    include_answer_key: bool = True


def distribution_stats(scores: List[float]) -> Dict[str, float]:
    if not scores:
        return {"mean": 0.0, "median": 0.0, "mode": 0.0, "stddev": 0.0, "count": 0}
    ordered = sorted(float(s) for s in scores)
    count = len(ordered)
    mean = sum(ordered) / count
    if count % 2 == 1:
        median = ordered[count // 2]
    else:
        median = (ordered[count // 2 - 1] + ordered[count // 2]) / 2.0

    counter = Counter(round(s, 1) for s in ordered)
    mode = max(counter.items(), key=lambda pair: pair[1])[0]
    variance = sum((s - mean) ** 2 for s in ordered) / count
    stddev = variance ** 0.5
    return {"mean": round(mean, 1), "median": round(median, 1), "mode": round(mode, 1), "stddev": round(stddev, 1), "count": count}


def scores_for_student(telegram_id: int, subject: Optional[str] = None, days: int = 0) -> List[float]:
    where = ["telegram_id = " + ("?" if db.mode == "sqlite" else "%s")]
    params: List[Any] = [telegram_id]
    if subject:
        where.append("LOWER(subject) = LOWER(" + ("?" if db.mode == "sqlite" else "%s") + ")")
        params.append(subject)
    if days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        where.append("finished_at >= " + ("?" if db.mode == "sqlite" else "%s"))
        params.append(cutoff)
    rows = db.fetch_all(
        f"SELECT score FROM quiz_attempts WHERE {' AND '.join(where)}",
        tuple(params),
    )
    return [float(row["score"]) for row in rows]


def student_stats(telegram_id: int, subject: Optional[str] = None, days: int = 0) -> Dict[str, Any]:
    cache_key = f"stats:student:{telegram_id}:{subject or 'all'}:{days}"
    cached = cache_get_json(cache_key)
    if cached:
        return cached
    stats = distribution_stats(scores_for_student(telegram_id, subject, days))
    cache_set_json(cache_key, stats, ttl_seconds=300)
    return stats


def topic_mastery_distribution(class_code: Optional[str], subject: Optional[str]) -> Dict[str, Any]:
    where: List[str] = []
    params: List[Any] = []
    if class_code:
        where.append("qa.class_code = " + ("?" if db.mode == "sqlite" else "%s"))
        params.append(class_code)
    if subject:
        where.append("LOWER(qr.subject) = LOWER(" + ("?" if db.mode == "sqlite" else "%s") + ")")
        params.append(subject)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = db.fetch_all(
        f"SELECT qr.topic, COUNT(*) AS attempts, "
        f"SUM(CASE WHEN qr.is_correct = 1 THEN 1 ELSE 0 END) AS correct, "
        f"AVG(qr.switches) AS avg_switches, AVG(qr.seconds_spent) AS avg_seconds "
        f"FROM question_responses qr JOIN quiz_attempts qa ON qa.id = qr.attempt_id "
        f"{where_sql} GROUP BY qr.topic ORDER BY (SUM(CASE WHEN qr.is_correct = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*)) ASC",
        tuple(params),
    )
    result: List[Dict[str, Any]] = []
    for row in rows:
        attempts = max(1, int(row["attempts"]))
        mastery = round((int(row["correct"]) / attempts) * 100, 1)
        if mastery < 50:
            band = "critical"
        elif mastery <= 75:
            band = "developing"
        else:
            band = "strong"
        result.append(
            {
                "topic": row.get("topic") or "General",
                "attempts": int(row["attempts"]),
                "mastery": mastery,
                "band": band,
                "hesitation_index": round(float(row.get("avg_switches") or 0), 2),
                "avg_seconds": round(float(row.get("avg_seconds") or 0), 1),
            }
        )
    return {
        "rows": result,
        "band_counts": {
            "critical": sum(1 for r in result if r["band"] == "critical"),
            "developing": sum(1 for r in result if r["band"] == "developing"),
            "strong": sum(1 for r in result if r["band"] == "strong"),
        },
    }


def grade_median_for_subject(subject: str) -> float:
    cache_key = f"benchmark:{subject}"
    cached = cache_get_json(cache_key)
    if cached:
        return float(cached["benchmark"])
    rows = db.fetch_all(
        "SELECT score FROM quiz_attempts WHERE LOWER(subject) = LOWER(?)"
        if db.mode == "sqlite"
        else "SELECT score FROM quiz_attempts WHERE LOWER(subject) = LOWER(%s)",
        (subject,),
    )
    scores = [float(row["score"]) for row in rows]
    benchmark = distribution_stats(scores)["median"] if scores else 55.0
    cache_set_json(cache_key, {"benchmark": benchmark}, ttl_seconds=900)
    return benchmark


def check_outlier_anomaly(telegram_id: int, subject: str, current_score: float) -> Optional[Dict[str, Any]]:
    prior = distribution_stats(scores_for_student(telegram_id, subject, 0))
    if prior["count"] < 3:
        return None
    threshold = prior["median"] - 1.5 * prior["stddev"]
    if current_score < threshold:
        return {"score": round(current_score, 1), "median": prior["median"], "stddev": prior["stddev"], "threshold": round(threshold, 1)}
    return None


class CurfewEngine:
    @staticmethod
    def now_label() -> str:
        return datetime.now(timezone.utc).astimezone().strftime("%A").lower()

    @staticmethod
    def day_is_active(day_label: str, weekday_name: str) -> bool:
        if day_label == "weekdays":
            return weekday_name not in ("saturday", "sunday")
        if day_label == "weekends":
            return weekday_name in ("saturday", "sunday")
        return day_label == weekday_name

    @staticmethod
    def minutes_until(end_time: str) -> int:
        now = datetime.now(timezone.utc).astimezone()
        hour, minute = (int(part) for part in end_time.split(":"))
        end = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delta = (end - now).total_seconds() / 60.0
        return int(delta)


def run_curfew_sweep() -> int:
    windows = db.fetch_all(
        "SELECT id, parent_id, student_id, day_label, start_time, end_time, last_alerted_on, enabled FROM study_windows WHERE enabled = 1"
        if db.mode == "sqlite"
        else "SELECT id, parent_id, student_id, day_label, start_time, end_time, last_alerted_on, enabled FROM study_windows WHERE enabled = TRUE",
    )
    today = datetime.now(timezone.utc).date().isoformat()
    weekday_name = CurfewEngine.now_label()
    fired = 0
    for window in windows:
        if not CurfewEngine.day_is_active(str(window["day_label"]), weekday_name):
            continue
        minutes_left = CurfewEngine.minutes_until(str(window["end_time"]))
        if minutes_left < 0 or minutes_left > 30:
            continue
        last = str(window.get("last_alerted_on") or "")
        if last == today:
            continue
        recent = db.fetch_one(
            "SELECT COUNT(*) AS count FROM quiz_attempts WHERE telegram_id = ? AND finished_at >= ?"
            if db.mode == "sqlite"
            else "SELECT COUNT(*) AS count FROM quiz_attempts WHERE telegram_id = %s AND finished_at >= %s",
            (int(window["student_id"]), (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()),
        )
        if recent and int(recent["count"]) > 0:
            continue
        student = db.fetch_one(
            "SELECT full_name FROM users WHERE telegram_id = ?"
            if db.mode == "sqlite"
            else "SELECT full_name FROM users WHERE telegram_id = %s",
            (int(window["student_id"]),),
        )
        name = (student or {}).get("full_name") or f"Student {window['student_id']}"
        message = (
            f"[CURFEW ALERT] {name} has 30 minutes left to complete today's practice session on Naija Scholar Bot."
        )
        _bot_send(int(window["parent_id"]), message)
        logger.info("Curfew alert sent via Telegram to parent=%s for student=%s", window["parent_id"], window["student_id"])
        db.execute(
            "UPDATE study_windows SET last_alerted_on = ? WHERE id = ?"
            if db.mode == "sqlite"
            else "UPDATE study_windows SET last_alerted_on = %s WHERE id = %s",
            (today, window["id"]),
        )
        fired += 1
    return fired


async def curfew_loop() -> None:
    while True:
        try:
            run_curfew_sweep()
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Curfew sweep failed: %s", exc)
        await asyncio.sleep(60)


async def seeder_loop() -> None:
    while True:
        try:
            from autonomous_seeder import seed_once

            processed = await asyncio.to_thread(seed_once)
            logger.info("Scheduled question seeding processed=%s", processed)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Scheduled question seeding failed: %s", exc)
        await asyncio.sleep(max(60.0, settings.SEED_INTERVAL_HOURS * 3600.0))


def build_report_pdf(
    student_name: str,
    meta: Dict[str, Any],
    stats: Dict[str, Any],
    heatmap: Dict[str, Dict[str, float]],
    series: List[Dict[str, Any]],
) -> bytes:
    pdf = PDFBranded()
    pdf.quiz_paper_id = 0
    pdf.add_page()
    pdf.brand_header("Student Performance Diagnostic Report")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"Student: {student_name}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Class: {meta.get('class_code') or 'Unassigned'}   |   Subject: {meta.get('subject') or 'All'}   |   Period: last {meta.get('days', 30)} days", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Distribution Statistics (score %)")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 11)
    labels = [("Mean", stats.get("mean")), ("Median", stats.get("median")), ("Mode", stats.get("mode")), ("Std Dev", stats.get("stddev")), ("Attempts", stats.get("count"))]
    for label, value in labels:
        pdf.cell(60, 8, f"{label}: {value}")
    pdf.ln(10)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Topic Heatmap (mastery %)")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 10)
    for topic, subs in heatmap.items():
        pdf.set_text_color(120, 120, 140)
        pdf.cell(0, 6, topic)
        pdf.ln(6)
        pdf.set_text_color(30, 30, 40)
        for sub_topic, pct in list(subs.items())[:8]:
            pdf.cell(0, 5, f"   - {sub_topic}: {pct}%")
            pdf.ln(5)
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "12-Week Longitudinal Score Series")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 10)
    for point in series[-24:]:
        finished = point.get("finished_at", "")
        finished_str = finished[:10] if isinstance(finished, str) else str(finished)[:10]
        pdf.cell(0, 5, f"  {finished_str}  |  {point.get('subject', '')}  |  {point.get('score')}%")
        pdf.ln(5)
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 5, "Methodology: scores reflect diagnostic drills completed via the Naija Scholar Bot. Median uses the true midpoint of ordered scores; mode is the most frequent score cluster; std dev measures performance spread.", new_x="LMARGIN")
    pdf.footer()
    return bytes(pdf.output())


def build_mock_paper_pdf(
    title: str,
    subject: str,
    class_code: Optional[str],
    questions: List[Dict[str, Any]],
    quiz_paper_id: int,
    include_answer_key: bool = True,
) -> bytes:
    pdf = PDFBranded()
    pdf.quiz_paper_id = quiz_paper_id
    pdf.add_page()
    pdf.brand_header(f"{title} - {subject}" + (f" ({class_code})" if class_code else ""))
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, "Instructions: Answer ALL questions. Each question has exactly one correct option (A-D). Mark your answers on the bubble sheet provided. Calculators are not permitted unless stated.", new_x="LMARGIN")
    pdf.ln(3)
    keys = ["A", "B", "C", "D"]
    for index, question in enumerate(questions, start=1):
        if pdf.get_y() > 235:
            pdf.add_page()
            pdf.brand_header(f"{title} - {subject} (continued)")
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(0, 5.5, f"Q{index}. {question['question_text']}", new_x="LMARGIN")
        pdf.set_font("Helvetica", "", 9.5)
        options = question.get("options") or []
        for option_index, option in enumerate(options[:4]):
            pdf.multi_cell(0, 5, f"  {keys[option_index]}. {option}", new_x="LMARGIN")
        pdf.ln(1.5)
    if include_answer_key:
        pdf.add_page()
        pdf.brand_header("Teacher Answer Key")
        pdf.set_font("Helvetica", "", 9.5)
        for index, question in enumerate(questions, start=1):
            answer = question.get("correct_answer") or ""
            letter = "?"
            if answer and answer in (question.get("options") or []):
                letter = keys[(question["options"].index(answer)) % 4]
            pdf.cell(0, 5.5, f"Q{index}: {letter}. {answer}" + (f"  |  {question.get('topic', '')}" if question.get("topic") else ""))
            pdf.ln(5.5)
    pdf.footer()
    return bytes(pdf.output())


class PDFBranded(FPDF):
    def brand_header(self, title: str) -> None:
        self.set_fill_color(11, 15, 25)
        self.rect(0, 0, self.w, 26, "F")
        self.set_fill_color(255, 184, 0)
        self.rect(0, 26, self.w, 1.6, "F")
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 13)
        self.set_xy(10, 6)
        self.cell(0, 7, "LIGHTHOUSE INTEL ACADEMY", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_xy(10, 14)
        self.cell(0, 5, title, new_x="LMARGIN", new_y="NEXT")
        self.set_xy(10, 20)
        self.cell(0, 4, f"Generated {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')} | Lighthouse Intel Academy", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(30, 30, 40)
        self.set_y(32)

    def footer(self) -> None:
        self.set_y(-24)
        self.set_fill_color(255, 184, 0)
        self.rect(0, self.h - 24, self.w, 24, "F")
        self.set_text_color(11, 15, 25)
        self.set_font("Helvetica", "B", 8.5)
        self.set_xy(6, self.h - 21)
        self.cell(0, 5, "POWERED BY NAIJA SCHOLAR BOT")
        self.set_font("Helvetica", "", 7.5)
        self.set_xy(6, self.h - 16)
        self.multi_cell(150, 3.6, "Want interactive practice, instant AI explanations & daily JAMB/WAEC drills?\nOpen Telegram & search: @" + settings.TELEGRAM_BOT_USERNAME, 0, "L")
        self.set_font("Helvetica", "", 7)
        self.set_xy(6, self.h - 7)
        self.cell(0, 4, f"Page {self.page_no()}")
        qr = qrcode.QRCode(box_size=3, border=1)
        qr.add_data(f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start=quiz_{self.quiz_paper_id or 0}")
        qr.make(fit=True)
        qr_image = qr.make_image(fill_color=(11, 15, 25), back_color=(255, 184, 0))
        qr_path = Path(tempfile.gettempdir()) / f"lia_qr_{self.page_no()}_{os.getpid()}.png"
        qr_image.save(qr_path)
        self.image(str(qr_path), x=self.w - 26, y=self.h - 22, w=20, h=20)
        try:
            qr_path.unlink()
        except OSError:
            pass


@app.get("/api/v1/analytics/stats/student/{telegram_id}")
def analytics_stats_student(
    telegram_id: int,
    subject: Optional[str] = Query(default=None, max_length=80),
    days: int = Query(0, ge=0, le=365),
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    if telegram_id != user["telegram_id"] and user.get("role") not in (ROLE_PARENT, ROLE_SUPER_ADMIN, ROLE_SCHOOL_ADMIN):
        raise HTTPException(status_code=403, detail="Not allowed to view this student's stats")
    if telegram_id != user["telegram_id"] and user.get("role") == ROLE_PARENT and telegram_id not in linked_children(user["telegram_id"]):
        raise HTTPException(status_code=403, detail="Not linked to this student")
    stats = student_stats(telegram_id, subject, days)
    series = db.fetch_all(
        "SELECT id, subject, score, trust_score, finished_at FROM quiz_attempts "
        "WHERE telegram_id = ? AND finished_at >= ? ORDER BY finished_at ASC LIMIT 100"
        if db.mode == "sqlite"
        else "SELECT id, subject, score, trust_score, finished_at FROM quiz_attempts "
        "WHERE telegram_id = %s AND finished_at >= %s ORDER BY finished_at ASC LIMIT 100",
        (telegram_id, (datetime.now(timezone.utc) - timedelta(days=max(days, 84))).isoformat()),
    )
    series = [dict(row) for row in series]
    low, high = predict_jamb_range(stats["mean"])
    warnings = db.fetch_all(
        "SELECT subject, score, personal_median, stddev, created_at FROM early_warnings WHERE telegram_id = ? ORDER BY id DESC LIMIT 10"
        if db.mode == "sqlite"
        else "SELECT subject, score, personal_median, stddev, created_at FROM early_warnings WHERE telegram_id = %s ORDER BY id DESC LIMIT 10",
        (telegram_id,),
    )
    return {
        "telegram_id": telegram_id,
        "stats": stats,
        "series": series,
        "jamb_projection": {"low": low, "high": high},
        "waec_grade": predict_waec_grade(stats["mean"]),
        "warnings": [dict(row) for row in warnings],
        "role": user.get("role"),
    }


@app.get("/api/v1/analytics/stats/class")
def analytics_stats_class(
    class_code: Optional[str] = Query(default=None, max_length=40),
    subject: Optional[str] = Query(default=None, max_length=80),
    user: Dict[str, Any] = Depends(require_roles(ROLE_TEACHER, ROLE_SCHOOL_ADMIN, ROLE_SUPER_ADMIN)),
) -> Dict[str, Any]:
    where: List[str] = []
    params: List[Any] = []
    if class_code:
        where.append("class_code = " + ("?" if db.mode == "sqlite" else "%s"))
        params.append(class_code)
    if subject:
        where.append("LOWER(subject) = LOWER(" + ("?" if db.mode == "sqlite" else "%s") + ")")
        params.append(subject)
    if user.get("school_id"):
        where.append("school_id = " + ("?" if db.mode == "sqlite" else "%s"))
        params.append(user["school_id"])
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    if user.get("role") == ROLE_TEACHER:
        log_telemetry(
            user["telegram_id"],
            ROLE_TEACHER,
            "teacher_roster_check",
            {"class_code": class_code, "subject": subject, "method": "api_class_stats"},
        )
    rows = db.fetch_all(
        f"SELECT class_code, subject, score FROM quiz_attempts {where_sql}",
        tuple(params),
    )
    grouped: Dict[str, Dict[str, List[float]]] = {}
    for row in rows:
        key = f"{row.get('class_code') or 'Unassigned'}::{row.get('subject') or 'General'}"
        grouped.setdefault(key, []).append(float(row["score"]))
    classes: List[Dict[str, Any]] = []
    for key, scores in grouped.items():
        class_code_part, subject_part = key.split("::", 1)
        stats = distribution_stats(scores)
        classes.append({"class_code": class_code_part, "subject": subject_part, "stats": stats})
    classes.sort(key=lambda item: item["stats"]["mean"], reverse=True)
    mastery = topic_mastery_distribution(class_code, subject)
    benchmark = grade_median_for_subject(subject) if subject else None
    return {"classes": classes, "topic_mastery": mastery, "benchmark": benchmark}


@app.get("/api/v1/analytics/stats/school")
def analytics_stats_school(
    grade: Optional[str] = Query(default=None, max_length=40),
    subject: Optional[str] = Query(default=None, max_length=80),
    user: Dict[str, Any] = Depends(require_roles(ROLE_SCHOOL_ADMIN, ROLE_SUPER_ADMIN)),
) -> Dict[str, Any]:
    log_telemetry(
        user["telegram_id"],
        user.get("role"),
        "school_roster_view",
        {"grade": grade, "subject": subject, "method": "api_school_stats"},
    )
    class_rows = db.fetch_all(
        "SELECT class_code, subject, score FROM quiz_attempts WHERE (? IS NULL OR class_code = ?) "
        "AND (? IS NULL OR LOWER(subject) = LOWER(?))"
        if db.mode == "sqlite"
        else "SELECT class_code, subject, score FROM quiz_attempts WHERE (%s IS NULL OR class_code = %s) "
        "AND (%s IS NULL OR LOWER(subject) = LOWER(%s))",
        (grade, grade, subject, subject),
    )
    by_class: Dict[str, Dict[str, List[float]]] = {}
    for row in class_rows:
        by_class.setdefault(row.get("class_code") or "Unassigned", {}).setdefault(row.get("subject") or "General", []).append(float(row["score"]))
    classes = [
        {
            "class_code": class_code,
            "subjects": {
                subject_name: {"stats": distribution_stats(scores), "attempts": len(scores)}
                for subject_name, scores in subjects.items()
            },
        }
        for class_code, subjects in by_class.items()
    ]
    coverage: List[Dict[str, Any]] = []
    if subject:
        available = db.fetch_one(
            "SELECT COUNT(DISTINCT topic) AS count FROM question_bank WHERE LOWER(subject) = LOWER(?)"
            if db.mode == "sqlite"
            else "SELECT COUNT(DISTINCT topic) AS count FROM question_bank WHERE LOWER(subject) = LOWER(%s)",
            (subject,),
        )
        attempted = db.fetch_one(
            "SELECT COUNT(DISTINCT topic) AS count FROM question_responses WHERE LOWER(subject) = LOWER(?)"
            if db.mode == "sqlite"
            else "SELECT COUNT(DISTINCT topic) AS count FROM question_responses WHERE LOWER(subject) = LOWER(%s)",
            (subject,),
        )
        total_topics = int(available["count"]) if available else 0
        attempted_topics = int(attempted["count"]) if attempted else 0
        coverage.append(
            {
                "subject": subject,
                "syllabus_topics": total_topics,
                "attempted_topics": attempted_topics,
                "coverage_pct": round((attempted_topics / total_topics) * 100, 1) if total_topics else 0.0,
            }
        )
    benchmarks: Dict[str, float] = {}
    for row in class_rows:
        subject_name = row.get("subject") or "General"
        if subject_name not in benchmarks:
            benchmarks[subject_name] = grade_median_for_subject(subject_name)
    return {
        "classes": classes,
        "benchmarks": benchmarks,
        "syllabus_coverage": coverage,
        "super_admins": super_admin_count(),
    }


@app.get("/api/v1/warnings")
def list_warnings(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    if user.get("role") == ROLE_PARENT:
        children = linked_children(user["telegram_id"])
        if not children:
            return {"warnings": []}
        placeholders = ", ".join("?" if db.mode == "sqlite" else "%s" for _ in children)
        rows = db.fetch_all(
            f"SELECT id, telegram_id, subject, score, personal_median, stddev, created_at FROM early_warnings "
            f"WHERE telegram_id IN ({placeholders}) ORDER BY id DESC LIMIT 50",
            tuple(children),
        )
    else:
        rows = db.fetch_all(
            "SELECT id, telegram_id, subject, score, personal_median, stddev, created_at FROM early_warnings "
            "WHERE telegram_id = ? ORDER BY id DESC LIMIT 50"
            if db.mode == "sqlite"
            else "SELECT id, telegram_id, subject, score, personal_median, stddev, created_at FROM early_warnings "
            "WHERE telegram_id = %s ORDER BY id DESC LIMIT 50",
            (user["telegram_id"],),
        )
    return {"warnings": [dict(row) for row in rows]}


@app.get("/api/v1/curfew/windows")
def list_study_windows(user: Dict[str, Any] = Depends(require_roles(ROLE_PARENT))) -> Dict[str, Any]:
    rows = db.fetch_all(
        "SELECT id, student_id, day_label, start_time, end_time, enabled, last_alerted_on FROM study_windows "
        "WHERE parent_id = ? ORDER BY id"
        if db.mode == "sqlite"
        else "SELECT id, student_id, day_label, start_time, end_time, enabled, last_alerted_on FROM study_windows "
        "WHERE parent_id = %s ORDER BY id",
        (user["telegram_id"],),
    )
    return {"windows": [dict(row) for row in rows]}


@app.put("/api/v1/curfew/windows")
def upsert_study_window(payload: StudyWindowUpsert, user: Dict[str, Any] = Depends(require_roles(ROLE_PARENT))) -> Dict[str, Any]:
    if payload.student_id not in linked_children(user["telegram_id"]):
        raise HTTPException(status_code=403, detail="Student not linked to this parent")
    db.execute(
        "INSERT INTO study_windows (parent_id, student_id, day_label, start_time, end_time, enabled) VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(parent_id, student_id, day_label) DO UPDATE SET start_time = excluded.start_time, end_time = excluded.end_time, enabled = excluded.enabled"
        if db.mode == "sqlite"
        else "INSERT INTO study_windows (parent_id, student_id, day_label, start_time, end_time, enabled) VALUES (%s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (parent_id, student_id, day_label) DO UPDATE SET start_time = EXCLUDED.start_time, end_time = EXCLUDED.end_time, enabled = EXCLUDED.enabled",
        (user["telegram_id"], payload.student_id, payload.day_label, payload.start_time, payload.end_time, 1 if payload.enabled else 0),
    )
    return {"status": "saved", "student_id": payload.student_id, "day_label": payload.day_label}


@app.delete("/api/v1/curfew/windows/{window_id}")
def delete_study_window(window_id: int, user: Dict[str, Any] = Depends(require_roles(ROLE_PARENT))) -> Dict[str, Any]:
    db.execute(
        "DELETE FROM study_windows WHERE id = ? AND parent_id = ?"
        if db.mode == "sqlite"
        else "DELETE FROM study_windows WHERE id = %s AND parent_id = %s",
        (window_id, user["telegram_id"]),
    )
    return {"status": "deleted"}


@app.post("/api/v1/export/mock-paper")
def export_mock_paper(
    payload: MockPaperRequest,
    user: Dict[str, Any] = Depends(require_roles(ROLE_TEACHER, ROLE_SCHOOL_ADMIN, ROLE_SUPER_ADMIN)),
) -> Response:
    response = fetch_remedial_questions(payload.subject, payload.topic, payload.limit_count)
    questions = [question.model_dump() for question in response.questions]
    if not questions:
        raise HTTPException(status_code=404, detail="No questions available for the requested subject/topic")
    title = payload.title or f"{payload.subject} Mock Paper"
    db.execute(
        "INSERT INTO quiz_papers (teacher_id, school_id, title, subject, class_code, question_ids, question_count) VALUES (?, ?, ?, ?, ?, ?, ?)"
        if db.mode == "sqlite"
        else "INSERT INTO quiz_papers (teacher_id, school_id, title, subject, class_code, question_ids, question_count) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            user["telegram_id"],
            user.get("school_id"),
            title,
            payload.subject,
            payload.class_code,
            json_dumps([int(q["id"] or 0) for q in questions]),
            len(questions),
        ),
    )
    paper = db.fetch_one(
        "SELECT id FROM quiz_papers WHERE teacher_id = ? ORDER BY id DESC LIMIT 1"
        if db.mode == "sqlite"
        else "SELECT id FROM quiz_papers WHERE teacher_id = %s ORDER BY id DESC LIMIT 1",
        (user["telegram_id"],),
    )
    paper_id = int(paper["id"]) if paper else 0
    pdf_bytes = build_mock_paper_pdf(title, payload.subject, payload.class_code, questions, paper_id, payload.include_answer_key)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="LIA_{payload.subject.replace(chr(32), "_")}_mock_paper_{paper_id}.pdf"'},
    )


@app.get("/api/v1/export/mock-paper/{paper_id}.pdf")
def export_mock_paper_by_id(paper_id: int, user: Dict[str, Any] = Depends(current_user)) -> Response:
    paper = db.fetch_one(
        "SELECT id, teacher_id, title, subject, class_code, question_ids FROM quiz_papers WHERE id = ?"
        if db.mode == "sqlite"
        else "SELECT id, teacher_id, title, subject, class_code, question_ids FROM quiz_papers WHERE id = %s",
        (paper_id,),
    )
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    ids = []
    try:
        ids = [int(item) for item in json.loads(paper["question_ids"]) if item]
    except (TypeError, json.JSONDecodeError):
        ids = []
    questions = []
    if ids:
        rows = db.fetch_all(
            f"SELECT id, subject, topic, question_text, options, correct_answer, explanation FROM question_bank WHERE id IN ({', '.join('?' if db.mode == 'sqlite' else '%s' for _ in ids)})",
            tuple(ids),
        )
        for row in rows:
            questions.append(normalize_question_row(row))
    if not questions:
        response = fetch_remedial_questions(paper["subject"], None, 10)
        questions = [question.model_dump() for question in response.questions]
    pdf_bytes = build_mock_paper_pdf(paper["title"], paper["subject"], paper.get("class_code"), questions, paper_id)
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="LIA_paper_{paper_id}.pdf"'})


@app.get("/api/v1/export/report/{telegram_id}.pdf")
def export_report_pdf(
    telegram_id: int,
    days: int = Query(30, ge=7, le=365),
    subject: Optional[str] = Query(default=None, max_length=80),
    user: Dict[str, Any] = Depends(current_user),
) -> Response:
    if telegram_id != user["telegram_id"] and user.get("role") not in (ROLE_PARENT, ROLE_TEACHER, ROLE_SCHOOL_ADMIN, ROLE_SUPER_ADMIN):
        raise HTTPException(status_code=403, detail="Not allowed to export this student's report")
    if telegram_id != user["telegram_id"] and user.get("role") == ROLE_PARENT and telegram_id not in linked_children(user["telegram_id"]):
        raise HTTPException(status_code=403, detail="Not linked to this student")
    stats = student_stats(telegram_id, subject, days)
    attempts = db.fetch_all(
        "SELECT subject, topic, score, trust_score, finished_at FROM quiz_attempts "
        "WHERE telegram_id = ? AND finished_at >= ? ORDER BY finished_at ASC LIMIT 200"
        if db.mode == "sqlite"
        else "SELECT subject, topic, score, trust_score, finished_at FROM quiz_attempts "
        "WHERE telegram_id = %s AND finished_at >= %s ORDER BY finished_at ASC LIMIT 200",
        (telegram_id, (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()),
    )
    responses = db.fetch_all(
        "SELECT topic, sub_topic, is_correct FROM question_responses WHERE telegram_id = ? ORDER BY id DESC LIMIT 300"
        if db.mode == "sqlite"
        else "SELECT topic, sub_topic, is_correct FROM question_responses WHERE telegram_id = %s ORDER BY id DESC LIMIT 300",
        (telegram_id,),
    )
    heatmap: Dict[str, Dict[str, List[bool]]] = {}
    for row in responses:
        topic = row.get("topic") or "General"
        sub = row.get("sub_topic") or topic
        heatmap.setdefault(topic, {}).setdefault(sub, []).append(bool(row.get("is_correct")))
    heatmap_clean = {topic: {sub: round((sum(v) / len(v)) * 100, 1) for sub, v in subs.items()} for topic, subs in heatmap.items()}
    user_row = db.fetch_one(
        "SELECT full_name FROM users WHERE telegram_id = ?"
        if db.mode == "sqlite"
        else "SELECT full_name FROM users WHERE telegram_id = %s",
        (telegram_id,),
    )
    name = (user_row or {}).get("full_name") or f"Student {telegram_id}"
    pdf_bytes = build_report_pdf(
        name,
        {"class_code": user.get("class_code"), "subject": subject, "days": days},
        stats,
        heatmap_clean,
        [dict(row) for row in attempts],
    )
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="LIA_report_{telegram_id}.pdf"'})


@app.get("/api/v1/export/school-reports.zip")
def export_school_reports_zip(
    class_code: str = Query(..., min_length=2, max_length=40),
    user: Dict[str, Any] = Depends(require_roles(ROLE_SCHOOL_ADMIN, ROLE_SUPER_ADMIN)),
) -> Response:
    students = db.fetch_all(
        "SELECT p.telegram_id, COALESCE(u.full_name, 'Student ' || CAST(p.telegram_id AS TEXT)) AS full_name, p.class_code FROM profiles p "
        "LEFT JOIN users u ON u.telegram_id = p.telegram_id "
        "WHERE p.class_code = ? AND p.role = 'STUDENT' ORDER BY full_name LIMIT 200"
        if db.mode == "sqlite"
        else "SELECT p.telegram_id, COALESCE(u.full_name, 'Student ' || CAST(p.telegram_id AS TEXT)) AS full_name, p.class_code FROM profiles p "
        "LEFT JOIN users u ON u.telegram_id = p.telegram_id "
        "WHERE p.class_code = %s AND p.role = 'STUDENT' ORDER BY full_name LIMIT 200",
        (class_code,),
    )
    if not students:
        raise HTTPException(status_code=404, detail="No students found in this class")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for student in students:
            telegram_id = int(student["telegram_id"])
            stats = student_stats(telegram_id, None, 84)
            attempts = db.fetch_all(
                "SELECT subject, score, finished_at FROM quiz_attempts WHERE telegram_id = ? ORDER BY finished_at ASC LIMIT 100"
                if db.mode == "sqlite"
                else "SELECT subject, score, finished_at FROM quiz_attempts WHERE telegram_id = %s ORDER BY finished_at ASC LIMIT 100",
                (telegram_id,),
            )
            name = str(student["full_name"]).replace(" ", "_").replace("/", "_")
            pdf_bytes = build_report_pdf(
                student["full_name"],
                {"class_code": class_code, "subject": None, "days": 84},
                stats,
                {},
                [dict(row) for row in attempts],
            )
            archive.writestr(f"{name}_{telegram_id}_progress_report.pdf", pdf_bytes)
    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="LIA_{class_code}_term_progress.zip"'},
    )


def verify_opay_signature(body: bytes, signature: str) -> bool:
    """Verify OPay webhook signature using HMAC-SHA256."""
    if settings.ENVIRONMENT == "production" and not settings.OPAY_WEBHOOK_SECRET:
        return False
    secret = settings.OPAY_WEBHOOK_SECRET
    if not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_zenith_signature(body: bytes, signature: str) -> bool:
    """Verify Zenith Bank webhook signature using HMAC-SHA256."""
    if settings.ENVIRONMENT == "production" and not settings.ZENITH_WEBHOOK_SECRET:
        return False
    secret = settings.ZENITH_WEBHOOK_SECRET
    if not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_palmpay_signature(body: bytes, signature: str) -> bool:
    """Verify PalmPay webhook signature using HMAC-SHA256."""
    if settings.ENVIRONMENT == "production" and not settings.PALMPAY_WEBHOOK_SECRET:
        return False
    secret = settings.PALMPAY_WEBHOOK_SECRET
    if not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_paystack_signature(body: bytes, signature: str) -> bool:
    if settings.ENVIRONMENT == "production" and not settings.PAYSTACK_WEBHOOK_SECRET:
        return False
    secret = settings.PAYSTACK_WEBHOOK_SECRET or settings.PAYSTACK_SECRET_KEY
    if not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def _get_provider_config(provider: str):
    """Get provider configuration based on provider name."""
    configs = {
        "opay": {
            "merchant_id": settings.OPAY_MERCHANT_ID,
            "private_key": settings.OPAY_PRIVATE_KEY,
            "base_url": settings.OPAY_BASE_URL,
            "webhook_secret": settings.OPAY_WEBHOOK_SECRET,
            "verify_func": verify_opay_signature,
        },
        "zenith": {
            "merchant_id": settings.ZENITH_MERCHANT_ID,
            "private_key": settings.ZENITH_API_KEY,
            "base_url": settings.ZENITH_BASE_URL,
            "webhook_secret": settings.ZENITH_WEBHOOK_SECRET,
            "verify_func": verify_zenith_signature,
        },
        "palmpay": {
            "merchant_id": settings.PALMPAY_MERCHANT_ID,
            "private_key": settings.PALMPAY_PRIVATE_KEY,
            "base_url": settings.PALMPAY_BASE_URL,
            "webhook_secret": settings.PALMPAY_WEBHOOK_SECRET,
            "verify_func": verify_palmpay_signature,
        },
        "paystack": {
            "merchant_id": settings.PAYSTACK_SECRET_KEY,  # used as secret key
            "private_key": settings.PAYSTACK_SECRET_KEY,
            "base_url": "https://api.paystack.co",
            "webhook_secret": settings.PAYSTACK_WEBHOOK_SECRET,
            "verify_func": verify_paystack_signature,
        },
        "flutterwave": {
            "merchant_id": settings.FLUTTERWAVE_PUBLIC_KEY,
            "private_key": settings.FLUTTERWAVE_SECRET_KEY,
            "base_url": "https://api.flutterwave.com/v3",
            "webhook_secret": settings.FLUTTERWAVE_WEBHOOK_SECRET,
            "verify_func": None,  # Flutterwave uses different verification
        },
    }
    return configs.get(provider.lower())


def _create_transaction(
    provider: str,
    telegram_id: int,
    kind: str = "premium",
    email: Optional[str] = None,
    amount_naira: Optional[float] = None,
    callback_url: Optional[str] = None,
) -> tuple:
    """Create a transaction with the specified provider."""
    provider = provider.lower()
    config = _get_provider_config(provider)
    if not config:
        return None, None, f"Unknown payment provider: {provider}"

    merchant_id = config["merchant_id"]
    private_key = config["private_key"]
    base_url = config["base_url"]
    if not merchant_id or not private_key:
        return None, None, f"{provider.upper()} is not configured"

    pricing_map = {
        "premium": settings.PREMIUM_PRICE_NAIRA,
        "tuition": settings.TUITION_AMOUNT_NAIRA,
        "parent_premium": settings.PARENT_PREMIUM_PRICE_NAIRA,
        "teacher_premium": settings.TEACHER_PREMIUM_PRICE_NAIRA,
        "school_quarterly": settings.SCHOOL_QUARTERLY_FEE_NAIRA,
    }
    amount_naira = amount_naira or pricing_map.get(kind, settings.PREMIUM_PRICE_NAIRA)
    email = (email or "").strip() or f"user_{telegram_id}@naija-scholar.local"
    callback_url = callback_url or settings.APP_BASE_URL
    reference = f"{provider.upper()}-{secrets.token_hex(8).upper()}"

    # Provider-specific API calls
    if provider == "opay":
        url = f"{config['base_url']}/api/v1/payment/initialize"
        headers = {"Authorization": f"Bearer {config['private_key']}", "Content-Type": "application/json", "MerchantId": config['merchant_id']}
        payload = {"reference": f"OPAY-{secrets.token_hex(8).upper()}", "amount": int(round(amount_naira * 100)), "currency": "NGN", "callbackUrl": callback_url, "userInfo": {"email": email}, "metadata": {"telegram_id": telegram_id, "amount_naira": amount_naira, "kind": kind}}
        success_key = "success"
        url_keys = ["authorizationUrl", "redirectUrl"]
    elif provider == "zenith":
        url = f"{config['base_url']}/api/v1/payments/initialize"
        headers = {"Authorization": f"Bearer {config['private_key']}", "Content-Type": "application/json"}
        payload = {"reference": f"ZENITH-{secrets.token_hex(8).upper()}", "amount": int(round(amount_naira * 100)), "currency": "NGN", "callbackUrl": callback_url, "customerEmail": email, "metadata": {"telegram_id": telegram_id, "amount_naira": amount_naira, "kind": kind}}
        success_key = "status"
        url_keys = ["authorizationUrl", "paymentUrl"]
    elif provider == "palmpay":
        url = f"{config['base_url']}/api/v1/payments/initialize"
        headers = {"Authorization": f"Bearer {config['private_key']}", "Content-Type": "application/json", "MerchantId": config['merchant_id']}
        payload = {"reference": f"PALMPAY-{secrets.token_hex(8).upper()}", "amount": int(round(amount_naira * 100)), "currency": "NGN", "callbackUrl": callback_url, "buyerEmail": email, "metadata": {"telegram_id": telegram_id, "amount_naira": amount_naira, "kind": kind}}
        success_key = "success"
        url_keys = ["authorizationUrl", "redirectUrl"]
    elif provider == "paystack":
        url = f"{config['base_url']}/transaction/initialize"
        headers = {"Authorization": f"Bearer {config['private_key']}", "Content-Type": "application/json"}
        payload = {"reference": f"PAYSTACK-{secrets.token_hex(8).upper()}", "amount": int(round(amount_naira * 100)), "currency": "NGN", "callback_url": callback_url, "email": email, "metadata": {"telegram_id": telegram_id, "amount_naira": amount_naira, "kind": kind}}
        success_key = "status"
        url_keys = ["authorization_url"]
    elif provider == "flutterwave":
        url = f"{config['base_url']}/payments"
        headers = {"Authorization": f"Bearer {config['private_key']}", "Content-Type": "application/json"}
        payload = {"tx_ref": f"FLW-{secrets.token_hex(8).upper()}", "amount": amount_naira, "currency": "NGN", "redirect_url": callback_url, "customer": {"email": email}, "meta": {"telegram_id": telegram_id, "amount_naira": amount_naira, "kind": kind}}
        success_key = "status"
        url_keys = ["link"]
    else:
        return None, None, f"Unsupported provider: {provider}"

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        body = response.json()
    except requests.RequestException as exc:
        return None, None, f"{provider.upper()} unavailable: {exc}"

    if not response.ok or not body.get(success_key):
        return None, None, f"{provider.upper()} rejected initialization: {body.get('message', 'unknown error')}"

    data = body.get("data", body)
    authorization_url = ""
    for key in url_keys:
        if data.get(key):
            authorization_url = data[key]
            break
    reference = data.get("reference") or data.get("tx_ref") or f"{provider.upper()}-{secrets.token_hex(8).upper()}"
    if not authorization_url:
        return None, None, f"{provider.upper()} response missing authorization URL"

    db.execute(
        """
        INSERT INTO payments (reference, telegram_id, provider, status, amount, access_code, raw_payload, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(reference) DO NOTHING
        """
        if db.mode == "sqlite"
        else """
        INSERT INTO payments (reference, telegram_id, provider, status, amount, access_code, raw_payload, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (reference) DO NOTHING
        """,
        (
            reference,
            telegram_id,
            provider,
            "pending",
            amount_naira,
            None,
            json_dumps({"request": {"telegram_id": telegram_id, "kind": kind, "email": email}, provider: body}),
            utc_now(),
        ),
    )
    return authorization_url, reference, None


@app.post("/api/v1/payments/initialize", response_model=PaymentInitResponse)
def payment_initialize(payload: PaymentInitRequest) -> PaymentInitResponse:
    provider = payload.provider or settings.DEFAULT_PAYMENT_PROVIDER
    amount_naira = payload.amount or (settings.PREMIUM_PRICE_NAIRA if payload.kind == "premium" else settings.TUITION_AMOUNT_NAIRA)
    authorization_url, reference, error = _create_transaction(
        provider=provider,
        telegram_id=payload.telegram_id,
        kind=payload.kind,
        email=payload.email,
        amount_naira=amount_naira,
        callback_url=payload.callback_url,
    )
    if error:
        status_code = 503 if "not configured" in error else 502
        raise HTTPException(status_code=status_code, detail=error)
    return PaymentInitResponse(authorization_url=authorization_url, reference=reference, provider=provider)


def notify_magic_link(telegram_id: Optional[int], access_code: str) -> None:
    if not telegram_id:
        return
    if telegram_bot is not None:
        try:
            telegram_bot.send_message(
                telegram_id,
                "🎉 Payment received! Your Naija Scholar access code:\n\n"
                f"<code>{access_code}</code>\n\n"
                "Paste it into the app or share with your student to unlock everything.",
                parse_mode="HTML",
            )
            return
        except Exception as exc:
            logger.warning("Magic link send failed for telegram_id=%s: %s", telegram_id, exc)
    logger.info("Magic link sent via Telegram to telegram_id=%s", telegram_id)


@app.post("/api/v1/webhooks/{provider}", response_model=PaystackWebhookResponse)
async def webhook_payment(provider: str, request: Request) -> PaystackWebhookResponse:
    """Generic webhook handler for all payment providers."""
    provider = provider.lower()
    config = _get_provider_config(provider)
    if not config:
        raise HTTPException(status_code=404, detail=f"Unknown payment provider: {provider}")

    verify_func = config.get("verify_func")
    if not verify_func:
        raise HTTPException(status_code=501, detail=f"Webhook verification not implemented for {provider}")

    body = await request.body()
    # Different providers use different header names for signatures
    signature = (
        request.headers.get("x-paystack-signature", "")
        or request.headers.get("x-opay-signature", "")
        or request.headers.get("x-zenith-signature", "")
        or request.headers.get("x-palmpay-signature", "")
        or request.headers.get("verif-hash", "")  # Flutterwave
        or ""
    )
    if not verify_func(body, signature):
        raise HTTPException(status_code=401, detail=f"Invalid {provider} signature")

    try:
        event = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    # Normalize event data across providers
    event_data = event.get("data") or event
    status = event.get("event") or event.get("status") or event_data.get("status") or ""
    reference = str(event_data.get("reference") or event_data.get("tx_ref") or event_data.get("transaction_id") or "")
    metadata = event_data.get("metadata") or event_data.get("meta") or {}
    telegram_id = metadata.get("telegram_id")
    if telegram_id is not None:
        try:
            telegram_id = int(telegram_id)
        except (TypeError, ValueError):
            telegram_id = None

    # Amount normalization (some providers use kobo/cents, others use naira)
    amount_raw = event_data.get("amount") or event_data.get("amount_charged") or 0
    try:
        amount = float(amount_raw or 0) / 100.0
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid payment amount")

    access_code = None

    if not reference:
        raise HTTPException(status_code=400, detail="Payment reference missing")

    kind = str(metadata.get("kind") or "tuition")

    if status in ("charge.success", "successful", "completed", "charge.refunded", "refunded", "charge.failed", "failed", "transfer.failed"):
        existing_payment = db.fetch_one(
            "SELECT reference, status, access_code, amount FROM payments WHERE reference = ?"
            if db.mode == "sqlite"
            else "SELECT reference, status, access_code, amount FROM payments WHERE reference = %s",
            (reference,),
        )
        already_processed = (
            existing_payment is not None
            and existing_payment.get("status") in ("charge.success", "successful", "completed")
            and bool(existing_payment.get("access_code"))
        )

        if status in ("charge.success", "successful", "completed"):
            if kind == "premium" and existing_payment is None:
                expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
                if telegram_id is not None:
                    db.execute(
                        "UPDATE profiles SET premium_until = ?, updated_at = ? WHERE telegram_id = ?"
                        if db.mode == "sqlite"
                        else "UPDATE profiles SET premium_until = %s, updated_at = %s WHERE telegram_id = %s",
                        (expires_at, utc_now(), telegram_id),
                    )
                db.execute(
                    "INSERT INTO subscriptions (telegram_id, tier, status, amount, reference, expires_at) VALUES (?, ?, ?, ?, ?, ?)"
                    if db.mode == "sqlite"
                    else "INSERT INTO subscriptions (telegram_id, tier, status, amount, reference, expires_at) VALUES (%s, %s, %s, %s, %s, %s)",
                    (telegram_id, "premium", "active", amount, reference, expires_at),
                )
                logger.info("Premium subscription granted for telegram_id=%s reference=%s", telegram_id, reference)
                access_code = None
            elif not already_processed:
                access_code = build_access_code()
                if telegram_id is not None:
                    db.execute(
                        "UPDATE users SET access_unlocked = ?, access_code = ?, updated_at = ? WHERE telegram_id = ?"
                        if db.mode == "sqlite"
                        else "UPDATE users SET access_unlocked = %s, access_code = %s, updated_at = %s WHERE telegram_id = %s",
                        (1 if db.mode == "sqlite" else True, access_code, utc_now(), telegram_id),
                    )
                notify_magic_link(telegram_id, access_code)
            else:
                access_code = existing_payment.get("access_code")

        elif status in ("charge.refunded", "refunded"):
            if existing_payment and existing_payment.get("status") in ("charge.success", "successful", "completed"):
                if kind == "premium" and telegram_id is not None:
                    db.execute(
                        "UPDATE profiles SET premium_until = ?, updated_at = ? WHERE telegram_id = ?"
                        if db.mode == "sqlite"
                        else "UPDATE profiles SET premium_until = %s, updated_at = %s WHERE telegram_id = %s",
                        (None, utc_now(), telegram_id),
                    )
                    db.execute(
                        "UPDATE subscriptions SET status = ? WHERE reference = ?"
                        if db.mode == "sqlite"
                        else "UPDATE subscriptions SET status = %s WHERE reference = %s",
                        ("refunded", reference),
                    )
                    logger.info("Premium subscription revoked due to refund for telegram_id=%s reference=%s", telegram_id, reference)
                elif telegram_id is not None:
                    db.execute(
                        "UPDATE users SET access_unlocked = ?, access_code = ?, updated_at = ? WHERE telegram_id = ?"
                        if db.mode == "sqlite"
                        else "UPDATE users SET access_unlocked = %s, access_code = %s, updated_at = %s WHERE telegram_id = %s",
                        (0 if db.mode == "sqlite" else False, None, utc_now(), telegram_id),
                    )
                    logger.info("Access code revoked due to refund for telegram_id=%s reference=%s", telegram_id, reference)
                notify_magic_link(telegram_id, "REFUNDED: Your access has been revoked due to a refund.")
            access_code = existing_payment.get("access_code") if existing_payment else None

        elif status in ("charge.failed", "failed", "transfer.failed"):
            if existing_payment and existing_payment.get("status") not in ("charge.success", "successful", "completed"):
                logger.info("Payment failed/transfer failed for reference=%s, ensuring no access granted", reference)
            access_code = None
    else:
        existing_payment = db.fetch_one(
            "SELECT reference, status, access_code FROM payments WHERE reference = ?"
            if db.mode == "sqlite"
            else "SELECT reference, status, access_code FROM payments WHERE reference = %s",
            (reference,),
        )
        already_processed = (
            existing_payment is not None
            and existing_payment.get("status") in ("charge.success", "successful", "completed")
            and bool(existing_payment.get("access_code"))
        )
        if not already_processed:
            access_code = build_access_code()
            if telegram_id is not None:
                db.execute(
                    "UPDATE users SET access_unlocked = ?, access_code = ?, updated_at = ? WHERE telegram_id = ?"
                    if db.mode == "sqlite"
                    else "UPDATE users SET access_unlocked = %s, access_code = %s, updated_at = %s WHERE telegram_id = %s",
                    (1 if db.mode == "sqlite" else True, access_code, utc_now(), telegram_id),
                )
            notify_magic_link(telegram_id, access_code)
        else:
            access_code = existing_payment.get("access_code")

    db.execute(
        """
        INSERT INTO payments (reference, telegram_id, provider, status, amount, access_code, raw_payload, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(reference) DO UPDATE SET
            status = excluded.status,
            amount = excluded.amount,
            access_code = excluded.access_code,
            raw_payload = excluded.raw_payload,
            updated_at = excluded.updated_at
        """
        if db.mode == "sqlite"
        else """
        INSERT INTO payments (reference, telegram_id, provider, status, amount, access_code, raw_payload, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(reference) DO UPDATE SET
            status = excluded.status,
            amount = excluded.amount,
            access_code = excluded.access_code,
            raw_payload = excluded.raw_payload,
            updated_at = excluded.updated_at
        """,
        (
            reference,
            telegram_id,
            provider,  # Dynamic provider
            status,
            amount,
            access_code,
            body.decode("utf-8"),
            utc_now(),
        ),
    )

    return PaystackWebhookResponse(
        processed=True,
        status=status,
        reference=reference,
        access_code=access_code,
    )


# ---------------------------------------------------------------------------
# Telegram bot integration (polling + webhook, /start, /quiz, access codes)
# ---------------------------------------------------------------------------


def _start_telegram_bot() -> None:
    """Validate the bot token, then enable webhook mode or long-polling."""
    global telegram_bot, _polling_thread
    token = (settings.TELEGRAM_BOT_TOKEN or "").strip()
    if not settings.TELEGRAM_BOT_ENABLED or not token or token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        return
    client = bot.TelegramBot(token)
    try:
        me = client.get_me()
        telegram_bot = client
        bot_info = me or {}
        logger.info(
            "Telegram bot online: @%s (%s)",
            bot_info.get("username"),
            bot_info.get("first_name"),
        )
        try:
            client.set_my_commands(BOT_COMMANDS)
        except Exception as exc:
            logger.warning("setMyCommands failed (non-fatal): %s", exc)
    except Exception as exc:
        logger.warning("Telegram bot offline (check TELEGRAM_BOT_TOKEN): %s", exc)
        return

    webhook_url = (settings.TELEGRAM_WEBHOOK_URL or "").strip()
    if not webhook_url:
        webhook_url = (os.environ.get("RENDER_EXTERNAL_URL") or "").strip()
    if webhook_url:
        try:
            webhook = f"{webhook_url.rstrip('/')}/webhook/telegram/{token}"
            client.set_webhook(webhook)
            logger.info("Telegram webhook set: %s", webhook)
            return
        except Exception as exc:
            logger.warning("setWebhook failed (%s); falling back to long-polling", exc)
    if settings.TELEGRAM_POLLING_ENABLED:
        _polling_thread = threading.Thread(target=_telegram_poll_loop, name="telegram-poller", daemon=True)
        _polling_thread.start()
        logger.info("Telegram long-polling started")


def _telegram_poll_loop() -> None:
    offset = 0
    while True:
        if telegram_bot is None:
            return
        try:
            updates = telegram_bot.get_updates(
                offset=offset,
                timeout=25,
                allowed_updates=["message", "callback_query"],
            )
            for update in updates or []:
                try:
                    update_id = int(update.get("update_id") or 0)
                    offset = max(offset, update_id + 1)
                    handle_telegram_update(update)
                except Exception:
                    logger.exception("Telegram update handler error")
        except Exception as exc:
            logger.warning("Telegram long-poll error: %s", exc)
            time.sleep(3)


@app.get("/api/v1/bot/status")
def bot_status() -> Dict[str, Any]:
    if telegram_bot is None:
        return {"ok": False, "online": False, "reason": "Bot not enabled or token invalid"}
    try:
        me = telegram_bot.get_me()
        return {
            "ok": True,
            "online": True,
            "username": me.get("username"),
            "name": me.get("first_name"),
            "database": db.mode,
            "cache": cache is not None,
            "active_exams": len(EXAM_ACTIVE),
            "active_quizzes": len(QUIZ_SESSIONS),
            "uptime_seconds": int(time.time() - _start_time),
            "version": "2.0.0",
        }
    except Exception as exc:
        return {"ok": False, "online": False, "reason": str(exc)}


@app.post("/webhook/telegram/{token}")
def telegram_webhook(token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if token != settings.TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=404, detail="Not found")
    msg = payload.get("message") or payload.get("callback_query", {}).get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    if chat_id and not rate_limit_check(
        "tg_webhook", str(chat_id), settings.RATE_LIMIT_WEBHOOK_PER_MIN, settings.RATE_LIMIT_WINDOW_SECONDS
    ):
        return {"ok": True}
    try:
        handle_telegram_update(payload)
    except Exception:
        logger.exception("Telegram webhook update failed")
    return {"ok": True}


def _bot_send(chat_id: int, text: str, **kwargs: Any) -> None:
    if telegram_bot is not None:
        try:
            telegram_bot.send_message(chat_id, text, **kwargs)
        except Exception as exc:
            logger.warning("sendMessage to chat_id=%s failed: %s", chat_id, exc)
    else:
        logger.info("(bot offline) would send to chat_id=%s: %s", chat_id, text[:120])


def _bot_send_document(chat_id: int, document: bytes, filename: str, caption: Optional[str] = None) -> None:
    if telegram_bot is not None:
        try:
            telegram_bot.send_document(chat_id, document, filename, caption=caption)
        except Exception as exc:
            logger.warning("sendDocument to chat_id=%s failed: %s", chat_id, exc)
    else:
        logger.info("(bot offline) would send document %s (%s bytes) to chat_id=%s", filename, len(document), chat_id)


# i18n support for bot messages
I18N = {
    "en": {
        "bot_commands": [
            ("start", "Welcome & deep-link menu"),
            ("quiz", "Start a 5-question micro-drill"),
            ("subjects", "Subjects currently available"),
            ("me", "Your profile & access status"),
            ("progress", "Your analytics & JAMB prediction"),
            ("leaderboard", "School league table"),
            ("report", "Get your PDF progress report"),
            ("buy", "Unlock premium via Paystack"),
            ("linkchild", "Link a child: /linkchild CODE"),
            ("mychildren", "List your linked children"),
            ("child", "Child analytics: /child <id>"),
            ("curfew", "View study/curfew windows"),
            ("cancel", "End the active quiz"),
            ("help", "How to use the bot"),
        ],
        "help_text": (
            "🤖 <b>Naija Scholar Bot</b>\n\n"
            "Commands:\n"
            "/start — welcome & deep-link menu\n"
            "/quiz <subject> — start a 5-question micro-drill\n"
            "/exam — timed JAMB/WAEC/NECO exam with resume & diagnostics\n"
            "/subjects — subjects currently available\n"
            "/me — your profile & access status\n"
            "/progress — your analytics & JAMB prediction\n"
            "/leaderboard — school league table\n"
            "/report — your PDF progress report\n"
            "/buy — unlock premium via Paystack\n"
            "/linkchild <CODE> — link your child (parents)\n"
            "/mychildren — list linked children\n"
            "/child <id> — child analytics\n"
            "/curfew — study/curfew windows (parents)\n"
            "/cancel — submit & end the active quiz/exam\n"
            "/help — this message\n\n"
            "Deep links (tap in the app/service):\n"
            "• /start quiz_Mathematics\n"
            "• /start consult\n"
            "• /start assignment_SS3-001"
        ),
        "private_chat_only": "⚠️ This command only works in private chats with the bot.",
        "quiz_complete": "🎯 <b>{subject} — quiz complete!</b>",
        "quiz_score": "Score: {correct}/{total} ({score_pct}%)",
        "jamb_prediction": "Predicted JAMB: {jamb_low}–{jamb_high}  |  WAEC: {waec_grade}",
        "trust_score": "Trust score: {trust_score}%",
        "no_active_quiz": "No active quiz to cancel.",
        "quiz_cancelled": "❌ Active quiz cancelled.",
        "already_active_quiz": "⏳ You already have an active quiz. Finish it or send /cancel.",
    },
    "ha": {  # Hausa
        "bot_commands": [
            ("start", "Baya & menu na deep-link"),
            ("quiz", "Fara micro-drill na 5 tambaya"),
            ("subjects", "Sabis na da suke yanzu"),
            ("me", "Bayanan profilinka & matsayi"),
            ("progress", "Analityka dinka & tabbasin JAMB"),
            ("leaderboard", "Jadwal na makaranta"),
            ("report", "Samun rahoto PDF na cikakkun bayananka"),
            ("buy", "Gano premium ta hanyar Paystack"),
            ("linkchild", "Haɗa ƙwaƙa: /linkchild CODE"),
            ("mychildren", "Rakko ƙwaƙuka dinka"),
            ("child", "Analityka na ƙwaƙa: /child <id>"),
            ("curfew", "Duba cikakken yin karatu"),
            ("cancel", "Kammala quiz na yanzu"),
            ("help", "Yadda ake amfani da bot"),
        ],
        "help_text": (
            "🤖 <b>Botin Naija Scholar</b>\n\n"
            "Ammomi:\n"
            "/start — baya & menu na deep-link\n"
            "/quiz <subject> — fara micro-drill na 5 tambaya\n"
            "/exam — gwamnati na JAMB/WAEC/NECO da rage & diagnostics\n"
            "/subjects — sabis na da suke yanzu\n"
            "/me — profilinka & matsayin samun damar\n"
            "/progress — analityka dinka & tabbasin JAMB\n"
            "/leaderboard — jadwal na makaranta\n"
            "/report — rahoto PDF na cikakkun bayananka\n"
            "/buy — samu premium ta hanyar Paystack\n"
            "/linkchild <CODE> — haɗa ƙwaƙa (uwani)\n"
            "/mychildren — rakko ƙwaƙuka dinka\n"
            "/child <id> — analityka na ƙwaƙa\n"
            "/curfew — cikakken yin karatu (uwani)\n"
            "/cancel — gama & kammala quiz/exam na yanzu\n"
            "/help — wannan saƙon\n\n"
            "Deep links (danna a cikin app/service):\n"
            "• /start quiz_Mathematics\n"
            "• /start consult\n"
            "• /start assignment_SS3-001"
        ),
        "private_chat_only": "⚠️ Wannan amsa kawai a cikin private chat da bot.",
        "quiz_complete": "🎯 <b>{subject} — quiz kamala!</b>",
        "quiz_score": "Dagu: {correct}/{total} ({score_pct}%)",
        "jamb_prediction": "Tabbasin JAMB: {jamb_low}–{jamb_high}  |  WAEC: {waec_grade}",
        "trust_score": "Dagu na aminci: {trust_score}%",
        "no_active_quiz": "Babu quiz na yanzu don soke.",
        "quiz_cancelled": "❌ Quiz na yanzu suka soke.",
        "already_active_quiz": "⏳ Kana da quiz na yanzu. Kammala ko soke /cancel.",
    },
}


def t(key: str, lang: str = "en", **kwargs: Any) -> str:
    """Get translated string."""
    lang_dict = I18N.get(lang, I18N["en"])
    text = lang_dict.get(key, I18N["en"].get(key, key))
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


def get_user_lang(telegram_id: int) -> str:
    """Get user's preferred language from profile (placeholder for future)."""
    # TODO: Store language preference in profile
    return "en"


BOT_COMMANDS = I18N["en"]["bot_commands"]

BOT_HELP_TEXT = I18N["en"]["help_text"]


def _bot_available_subjects(chat_id: int) -> None:
    rows = db.fetch_all(
        "SELECT DISTINCT subject FROM question_bank ORDER BY subject LIMIT 25"
        if db.mode == "sqlite"
        else "SELECT DISTINCT subject FROM question_bank ORDER BY subject LIMIT 25"
    )
    subjects = ", ".join(str(row["subject"]) for row in rows) or "none yet"
    _bot_send(chat_id, f"📚 Available subjects:\n\n{subjects}\n\nTry /quiz <subject>")


def _is_private_chat(chat: Dict[str, Any]) -> bool:
    """Check if the Telegram chat is a private 1:1 chat."""
    return chat.get("type") == "private"


def _require_private_chat(chat_id: int, chat: Dict[str, Any]) -> bool:
    """Guard: send warning and return False if not private chat."""
    if not _is_private_chat(chat):
        _bot_send(chat_id, t("private_chat_only"))
        return False
    return True


def handle_telegram_update(update: Dict[str, Any]) -> None:
    if not isinstance(update, dict):
        return
    callback_query = update.get("callback_query")
    if callback_query:
        _handle_telegram_callback(callback_query)
        return
    message = update.get("message")
    if message and not message.get("channel_post"):
        _handle_telegram_message(message)


def _handle_telegram_message(message: Dict[str, Any]) -> None:
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return
    telegram_id = sender.get("id")
    if not telegram_id:
        if (message.get("text") or "").strip() == "/ping":
            _bot_send(chat_id, "🏓 pong")
        return
    ensure_profile(telegram_id)
    upsert_user_record(
        telegram_id,
        sender.get("first_name"),
        sender.get("last_name"),
        sender.get("username"),
    )
    # Per-chat rate limit for Telegram updates
    if not rate_limit_check(
        "telegram_update",
        str(telegram_id),
        settings.RATE_LIMIT_TELEGRAM_PER_MIN,
        settings.RATE_LIMIT_TELEGRAM_WINDOW_SECONDS,
    ):
        logger.warning("Telegram rate limit exceeded for telegram_id=%s", telegram_id)
        return
    text = (message.get("text") or "").strip()
    if not text:
        return
    if text == "/ping":
        _bot_send(chat_id, "🏓 pong")
        return
    parts = text.split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""
    if command == "/start":
        _bot_handle_start(chat_id, telegram_id, argument)
    elif command in ("/quiz", "/drill"):
        if not _require_private_chat(chat_id, chat):
            return
        _bot_handle_quiz(chat_id, telegram_id, argument)
    elif command == "/exam":
        if not _require_private_chat(chat_id, chat):
            return
        _bot_handle_exam(chat_id, telegram_id, argument)
    elif command in ("/subjects", "/topics"):
        _bot_available_subjects(chat_id)
    elif command == "/cancel":
        if chat_id in EXAM_ACTIVE:
            # Quit guard: automated submission scored on answered questions only.
            _bot_finish_exam(chat_id, "user_quit")
        else:
            session = QUIZ_SESSIONS.pop(chat_id, None)
            if session:
                # Mark micro-quiz DB session as COMPLETED
                session_id = session.get("session_id")
                if session_id:
                    db.execute(
                        "UPDATE quiz_sessions SET status = ?, completed_at = ? WHERE session_id = ?"
                        if db.mode == "sqlite"
                        else "UPDATE quiz_sessions SET status = %s, completed_at = %s WHERE session_id = %s",
                        ("COMPLETED", utc_now(), session_id),
                    )
                _bot_send(chat_id, "❌ Active quiz cancelled.")
            else:
                _bot_send(chat_id, "No active quiz to cancel.")
    elif command == "/me":
        if not _require_private_chat(chat_id, chat):
            return
        _bot_handle_me(chat_id, telegram_id)
    elif command == "/progress":
        if not _require_private_chat(chat_id, chat):
            return
        _bot_handle_progress(chat_id, telegram_id)
    elif command == "/leaderboard":
        if not _require_private_chat(chat_id, chat):
            return
        _bot_handle_leaderboard(chat_id)
    elif command == "/buy":
        if not _require_private_chat(chat_id, chat):
            return
        _bot_handle_buy(chat_id, telegram_id, argument)
    elif command == "/linkchild":
        if not _require_private_chat(chat_id, chat):
            return
        _bot_handle_linkchild(chat_id, telegram_id, argument)
    elif command in ("/mychildren", "/children"):
        if not _require_private_chat(chat_id, chat):
            return
        _bot_handle_mychildren(chat_id, telegram_id)
    elif command == "/child":
        if not _require_private_chat(chat_id, chat):
            return
        _bot_handle_child(chat_id, telegram_id, argument)
    elif command == "/report":
        if not _require_private_chat(chat_id, chat):
            return
        _bot_handle_report(chat_id, telegram_id, argument)
    elif command == "/curfew":
        if not _require_private_chat(chat_id, chat):
            return
        _bot_handle_curfew(chat_id, telegram_id)
    elif command == "/help":
        _bot_send(chat_id, BOT_HELP_TEXT, parse_mode="HTML")
    elif command == "/status":
        _bot_handle_status(chat_id)
    elif command.startswith("/"):
        _bot_send(chat_id, "Unknown command. Try /help")
    else:
        if not (get_profile(telegram_id) or {}).get("onboarded"):
            _bot_handle_onboarding(chat_id, telegram_id)
            return
        _bot_send(
            chat_id,
            "👋 I'm the Naija Scholar study bot.\n\n"
            f"You said: {text[:200]}\n\nUse /help to see what I can do.",
        )


def _bot_handle_status(chat_id: int) -> None:
    """Bot status command - shows bot health and stats."""
    import time
    uptime = time.time() - _start_time if '_start_time' in globals() else 0
    lines = [
        "🤖 <b>Bot Status</b>",
        f"Status: {'🟢 Online' if telegram_bot else '🔴 Offline'}",
        f"Database: {db.mode}",
        f"Cache: {'🟢 Connected' if cache else '🔴 Disconnected'}",
        f"Active exams: {len(EXAM_ACTIVE)}",
        f"Active quizzes: {len(QUIZ_SESSIONS)}",
        f"Uptime: {int(uptime)}s",
        f"Version: 2.0.0",
    ]
    _bot_send(chat_id, "\n".join(lines), parse_mode="HTML")


def _bot_handle_me(chat_id: int, telegram_id: int) -> None:
    profile = get_profile(telegram_id) or {}
    premium = is_premium(profile)
    lines = [
        "🧑‍🎓 <b>Your profile</b>",
        f"Telegram ID: <code>{telegram_id}</code>",
        f"Role: {profile.get('role') or 'STUDENT'}",
        f"Premium: {'✅' if premium else '—'}",
        f"Class: {profile.get('class_code') or 'not set'}",
        f"Linking code: <code>{profile.get('linking_code') or '—'}</code>",
        f"School ID: {profile.get('school_id') or '—'}",
    ]
    _bot_send(chat_id, "\n".join(lines), parse_mode="HTML")


def _bot_handle_progress(chat_id: int, telegram_id: int) -> None:
    attempts = db.fetch_all(
        "SELECT subject, score, total, correct, trust_score, finished_at FROM quiz_attempts "
        "WHERE telegram_id = ? ORDER BY id DESC LIMIT 50"
        if db.mode == "sqlite"
        else "SELECT subject, score, total, correct, trust_score, finished_at FROM quiz_attempts "
        "WHERE telegram_id = %s ORDER BY id DESC LIMIT 50",
        (telegram_id,),
    )
    if not attempts:
        _bot_send(chat_id, "📊 No practice yet! Try /quiz Mathematics to get started.")
        return
    scores = [float(row["score"]) for row in attempts]
    avg_score = round(sum(scores) / len(scores), 1)
    low, high = predict_jamb_range(avg_score)
    trust = round(sum(float(row.get("trust_score") or 0) for row in attempts) / len(attempts), 1)
    by_subject: Dict[str, List[float]] = {}
    for row in attempts:
        by_subject.setdefault(str(row["subject"]), []).append(float(row["score"]))
    subject_avgs = {s: round(sum(v) / len(v), 1) for s, v in by_subject.items()}
    best = max(subject_avgs, key=subject_avgs.get)
    worst = min(subject_avgs, key=subject_avgs.get)
    lang = get_user_lang(telegram_id)
    lines = [
        "📊 <b>Your progress</b>",
        f"Sessions: {len(attempts)}  |  Avg score: {avg_score}%",
        f"Predicted JAMB: {low}–{high}",
        f"Trust score: {trust}%",
        f"💪 Strongest: {best} ({subject_avgs[best]}%)",
        f"🎯 Needs work: {worst} ({subject_avgs[worst]}%)",
        "",
        f"Tip: run /quiz {worst} to improve your weakest subject.",
    ]
    _bot_send(chat_id, "\n".join(lines), parse_mode="HTML")


def _bot_handle_leaderboard(chat_id: int) -> None:
    league = db.fetch_all(
        "SELECT s.code AS school_code, s.name AS school_name, a.subject, AVG(a.score) AS avg_score, COUNT(*) AS attempts "
        "FROM quiz_attempts a JOIN schools s ON s.id = a.school_id "
        "WHERE a.school_id IS NOT NULL GROUP BY s.id, s.code, s.name, a.subject "
        "ORDER BY avg_score DESC LIMIT 10"
    )
    if league:
        lines = ["🏆 <b>School league</b> (avg score by subject)", ""]
        for row in league:
            lines.append(
                f"{row['school_code']} — {row['subject']}: {round(float(row['avg_score']), 1)}% ({row['attempts']} sessions)"
            )
    else:
        top = db.fetch_all(
            "SELECT telegram_id, AVG(score) AS avg_score, COUNT(*) AS attempts FROM quiz_attempts "
            "GROUP BY telegram_id HAVING COUNT(*) >= 1 ORDER BY avg_score DESC LIMIT 10"
        )
        if not top:
            _bot_send(chat_id, "🏆 No scores yet — be the first on the leaderboard! Try /quiz Mathematics.")
            return
        lines = ["🏆 <b>Top students</b> (all-time avg)", ""]
        for idx, row in enumerate(top, start=1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(idx, f"{idx}.")
            lines.append(f"{medal} Student {row['telegram_id']}: {round(float(row['avg_score']), 1)}% ({row['attempts']} sessions)")
    _bot_send(chat_id, "\n".join(lines), parse_mode="HTML")


def _bot_handle_buy(chat_id: int, telegram_id: int, argument: str) -> None:
    kind = (argument or "premium").strip().lower()
    valid_kinds = ("premium", "tuition", "parent_premium", "teacher_premium", "school_quarterly")
    if kind not in valid_kinds:
        kind = "premium"
    
    # Provider selection (default from settings)
    provider = settings.DEFAULT_PAYMENT_PROVIDER.lower()
    valid_providers = ("opay", "zenith", "palmpay", "paystack", "flutterwave")
    if provider not in valid_providers:
        provider = "opay"
    
    pricing_map = {
        "premium": settings.PREMIUM_PRICE_NAIRA,
        "tuition": settings.TUITION_AMOUNT_NAIRA,
        "parent_premium": settings.PARENT_PREMIUM_PRICE_NAIRA,
        "teacher_premium": settings.TEACHER_PREMIUM_PRICE_NAIRA,
        "school_quarterly": settings.SCHOOL_QUARTERLY_FEE_NAIRA,
    }
    price = pricing_map.get(kind, settings.PREMIUM_PRICE_NAIRA)
    
    # Use the new multi-provider transaction creator
    url, reference, error = _create_transaction(
        provider=provider,
        telegram_id=telegram_id,
        kind=kind,
    )
    if error:
        _bot_send(
            chat_id,
            f"💳 <b>Upgrade to Premium</b> — ₦{price:,.0f}\n\n"
            f"⚠️ Online payment isn't available right now ({error}).\n\n"
            "Meanwhile, contact support to pay via transfer and receive your access code.",
            parse_mode="HTML",
        )
        return
    
    provider_names = {
        "opay": "OPay",
        "zenith": "Zenith Bank",
        "palmpay": "PalmPay",
        "paystack": "Paystack",
        "flutterwave": "Flutterwave",
    }
    provider_name = provider_names.get(provider, provider.upper())
    
    _bot_send(
        chat_id,
        f"💳 <b>Upgrade to Premium</b> — ₦{price:,.0f}\n\n"
        f"Tap the button below to pay securely with {provider_name} (card, transfer or USSD). "
        f"Your access code will be delivered here right after payment.\n\n"
        f"Reference: <code>{reference}</code>",
        parse_mode="HTML",
        reply_markup={"inline_keyboard": [[{"text": f"Pay ₦{price:,.0f} via {provider_name}", "url": url}]]},
    )


def _bot_handle_linkchild(chat_id: int, telegram_id: int, argument: str) -> None:
    code = (argument or "").strip().upper()
    if not code:
        profile = get_profile(telegram_id) or {}
        _bot_send(
            chat_id,
            "🔗 <b>Link your child</b>\n\n"
            "Ask your child for their linking code (they can see it with /me), then send:\n"
            "<code>/linkchild THEIR-CODE</code>\n\n"
            f"Your own linking code: <code>{profile.get('linking_code') or '—'}</code>",
            parse_mode="HTML",
        )
        return
    user = get_profile(telegram_id) or {}
    if user.get("role") not in (ROLE_PARENT, ROLE_STUDENT):
        _bot_send(chat_id, "⚠️ Only parents (or students registering a guardian) can link children.")
        return
    student = db.fetch_one(
        "SELECT telegram_id FROM profiles WHERE UPPER(linking_code) = ?"
        if db.mode == "sqlite"
        else "SELECT telegram_id FROM profiles WHERE UPPER(linking_code) = %s",
        (code,),
    )
    if student is None:
        _bot_send(chat_id, f"❌ No student found with linking code <code>{code}</code>.", parse_mode="HTML")
        return
    student_id = int(student["telegram_id"])
    if student_id == telegram_id:
        _bot_send(chat_id, "⚠️ You cannot link your own account.")
        return
    db.execute(
        "INSERT INTO student_links (parent_id, student_id, status) VALUES (?, ?, ?)"
        " ON CONFLICT(parent_id, student_id) DO NOTHING"
        if db.mode == "sqlite"
        else "INSERT INTO student_links (parent_id, student_id, status) VALUES (%s, %s, %s)"
        " ON CONFLICT (parent_id, student_id) DO NOTHING",
        (telegram_id, student_id, "active"),
    )
    if user.get("role") == ROLE_STUDENT:
        db.execute(
            "UPDATE profiles SET role = ?, updated_at = ? WHERE telegram_id = ?"
            if db.mode == "sqlite"
            else "UPDATE profiles SET role = %s, updated_at = %s WHERE telegram_id = %s",
            (ROLE_PARENT, utc_now(), telegram_id),
        )
    _bot_send(chat_id, f"✅ Linked! Student <code>{student_id}</code> is now connected. Try /mychildren.", parse_mode="HTML")
    log_telemetry(telegram_id, ROLE_PARENT, "parent_lookup", {"student_id": student_id, "method": "telegram_linkchild"})


def _bot_children_summary(parent_id: int) -> List[Dict[str, Any]]:
    children = linked_children(parent_id)
    summaries: List[Dict[str, Any]] = []
    for child_id in children:
        row = db.fetch_one(
            "SELECT AVG(score) AS avg_score, COUNT(*) AS attempts FROM quiz_attempts WHERE telegram_id = ?"
            if db.mode == "sqlite"
            else "SELECT AVG(score) AS avg_score, COUNT(*) AS attempts FROM quiz_attempts WHERE telegram_id = %s",
            (child_id,),
        )
        user_row = db.fetch_one(
            "SELECT full_name FROM users WHERE telegram_id = ?"
            if db.mode == "sqlite"
            else "SELECT full_name FROM users WHERE telegram_id = %s",
            (child_id,),
        )
        summaries.append(
            {
                "student_id": child_id,
                "name": (user_row or {}).get("full_name") or f"Student {child_id}",
                "avg_score": round(float(row["avg_score"]), 1) if row and row["avg_score"] is not None else None,
                "attempts": int(row["attempts"]) if row else 0,
            }
        )
    return summaries


def _bot_handle_mychildren(chat_id: int, telegram_id: int) -> None:
    children = _bot_children_summary(telegram_id)
    if not children:
        _bot_send(chat_id, "👪 No linked children yet. Use /linkchild CODE to connect your child.")
        return
    lines = ["👪 <b>Your children</b>", ""]
    for child in children:
        avg = f"{child['avg_score']}%" if child["avg_score"] is not None else "no practice yet"
        lines.append(f"• <b>{child['name']}</b> (ID {child['student_id']}) — avg {avg}, {child['attempts']} sessions")
    lines += ["", "Details: /child <id>  |  PDF: /report <id>"]
    _bot_send(chat_id, "\n".join(lines), parse_mode="HTML")


def _bot_handle_child(chat_id: int, telegram_id: int, argument: str) -> None:
    children = linked_children(telegram_id)
    if not children:
        _bot_send(chat_id, "👪 No linked children yet. Use /linkchild CODE first.")
        return
    arg = (argument or "").strip()
    if not arg:
        if len(children) == 1:
            child_id = children[0]
        else:
            _bot_handle_mychildren(chat_id, telegram_id)
            return
    else:
        try:
            child_id = int(arg)
        except ValueError:
            _bot_send(chat_id, "Usage: /child <student_id>")
            return
    if child_id not in children:
        _bot_send(chat_id, "⚠️ That student is not linked to your account.")
        return
    log_telemetry(telegram_id, ROLE_PARENT, "child_report_view", {"student_id": child_id, "method": "telegram"})
    attempts = db.fetch_all(
        "SELECT subject, score, trust_score, finished_at FROM quiz_attempts WHERE telegram_id = ? ORDER BY id DESC LIMIT 30"
        if db.mode == "sqlite"
        else "SELECT subject, score, trust_score, finished_at FROM quiz_attempts WHERE telegram_id = %s ORDER BY id DESC LIMIT 30",
        (child_id,),
    )
    summary = next((c for c in _bot_children_summary(telegram_id) if c["student_id"] == child_id), {})
    name = summary.get("name") or f"Student {child_id}"
    if not attempts:
        _bot_send(chat_id, f"📊 <b>{name}</b> hasn't practiced yet. Encourage a /quiz session!", parse_mode="HTML")
        return
    avg = round(sum(float(r["score"]) for r in attempts) / len(attempts), 1)
    low, high = predict_jamb_range(avg)
    weak: Dict[str, float] = {}
    for row in attempts:
        weak[str(row["subject"])] = float(row["score"])
    weak_topics = sorted(weak, key=weak.get)[:3]
    lines = [
        f"📊 <b>{name}</b> — recent activity",
        f"Sessions: {len(attempts)}  |  Avg: {avg}%",
        f"Predicted JAMB: {low}–{high}",
        f"Focus areas: {', '.join(weak_topics)}",
        "",
        f"Full report: /report {child_id}",
    ]
    _bot_send(chat_id, "\n".join(lines), parse_mode="HTML")


def _bot_handle_report(chat_id: int, telegram_id: int, argument: str) -> None:
    arg = (argument or "").strip()
    target_id = telegram_id
    if arg:
        try:
            target_id = int(arg.split()[0])
        except ValueError:
            _bot_send(chat_id, "Usage: /report [student_id]")
            return
    if target_id != telegram_id and target_id not in linked_children(telegram_id):
        _bot_send(chat_id, "⚠️ You can only request reports for yourself or your linked children.")
        return
    days = 30
    profile = get_profile(target_id) or {}
    try:
        stats = student_stats(target_id, None, days)
        attempts = db.fetch_all(
            "SELECT subject, topic, score, trust_score, finished_at FROM quiz_attempts "
            "WHERE telegram_id = ? AND finished_at >= ? ORDER BY finished_at ASC LIMIT 200"
            if db.mode == "sqlite"
            else "SELECT subject, topic, score, trust_score, finished_at FROM quiz_attempts "
            "WHERE telegram_id = %s AND finished_at >= %s ORDER BY finished_at ASC LIMIT 200",
            (target_id, (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()),
        )
        responses = db.fetch_all(
            "SELECT topic, sub_topic, is_correct FROM question_responses WHERE telegram_id = ? ORDER BY id DESC LIMIT 300"
            if db.mode == "sqlite"
            else "SELECT topic, sub_topic, is_correct FROM question_responses WHERE telegram_id = %s ORDER BY id DESC LIMIT 300",
            (target_id,),
        )
        heatmap: Dict[str, Dict[str, List[bool]]] = {}
        for row in responses:
            topic = row.get("topic") or "General"
            sub = row.get("sub_topic") or topic
            heatmap.setdefault(topic, {}).setdefault(sub, []).append(bool(row.get("is_correct")))
        heatmap_clean = {
            topic: {sub: round((sum(v) / len(v)) * 100, 1) for sub, v in subs.items()}
            for topic, subs in heatmap.items()
        }
        user_row = db.fetch_one(
            "SELECT full_name FROM users WHERE telegram_id = ?"
            if db.mode == "sqlite"
            else "SELECT full_name FROM users WHERE telegram_id = %s",
            (target_id,),
        )
        name = (user_row or {}).get("full_name") or f"Student {target_id}"
        pdf_bytes = build_report_pdf(
            name,
            {"class_code": profile.get("class_code"), "subject": None, "days": days},
            stats,
            heatmap_clean,
            [dict(row) for row in attempts],
        )
    except Exception as exc:
        logger.exception("Bot report generation failed for telegram_id=%s", target_id)
        _bot_send(chat_id, f"⚠️ Could not build the report: {exc}")
        return
    _bot_send_document(chat_id, pdf_bytes, f"LIA_report_{target_id}.pdf", caption=f"📄 {days}-day progress report for {name}")


def _bot_handle_curfew(chat_id: int, telegram_id: int) -> None:
    profile = get_profile(telegram_id) or {}
    if profile.get("role") != ROLE_PARENT:
        _bot_send(chat_id, "⚠️ Curfew windows are only available to parents.")
        return
    rows = db.fetch_all(
        "SELECT student_id, day_label, start_time, end_time, enabled FROM study_windows WHERE parent_id = ? ORDER BY student_id, id"
        if db.mode == "sqlite"
        else "SELECT student_id, day_label, start_time, end_time, enabled FROM study_windows WHERE parent_id = %s ORDER BY student_id, id",
        (telegram_id,),
    )
    if not rows:
        _bot_send(chat_id, "🌙 No study/curfew windows set. Configure them via the API or ask support to help.")
        return
    lines = ["🌙 <b>Study / curfew windows</b>", ""]
    for row in rows:
        state = "🟢" if row.get("enabled") else "⚪"
        lines.append(
            f"{state} Student {row['student_id']} — {row['day_label']}: {row['start_time']}–{row['end_time']}"
        )
    _bot_send(chat_id, "\n".join(lines), parse_mode="HTML")


def _bot_handle_onboarding(chat_id: int, telegram_id: int) -> None:
    """Button-driven role selection wizard (shared by /start and first messages)."""
    profile = get_profile(telegram_id) or {}
    if profile.get("onboarded"):
        return
    payload = _onboarding_payload({**profile, "telegram_id": telegram_id, "onboarded": False})
    buttons = [[{"text": btn["label"], "callback_data": btn["callback_data"]}] for btn in payload["buttons"]]
    log_telemetry(telegram_id, profile.get("role") or ROLE_STUDENT, "onboarding_shown", {"source": "telegram_wizard"})
    _bot_send(
        chat_id,
        payload["headline"] + "\n\nPick the option that best describes you:",
        reply_markup={"inline_keyboard": buttons},
        parse_mode="HTML",
    )


def _bot_handle_start(chat_id: int, telegram_id: int, argument: str) -> None:
    if not argument:
        profile = ensure_profile(telegram_id)
        # First-run users (and anyone who skipped onboarding) get the role wizard.
        if not bool(profile.get("onboarded")):
            payload = _onboarding_payload({**profile, "telegram_id": telegram_id, "onboarded": False})
            buttons = [[{"text": btn["label"], "callback_data": btn["callback_data"]}] for btn in payload["buttons"]]
            log_telemetry(telegram_id, profile.get("role"), "onboarding_shown", {"source": "telegram_start"})
            _bot_send(
                chat_id,
                payload["headline"] + "\n\nPick the option that best describes you:",
                reply_markup={"inline_keyboard": buttons},
                parse_mode="HTML",
            )
            return
        _bot_send(
            chat_id,
            "👋 <b>Welcome back to Naija Scholar!</b>\n\n"
            "I'm your JAMB / WAEC / NECO study companion.\n\n"
            "• <b>/quiz Mathematics</b> — start a micro-drill\n"
            "• <b>/subjects</b> — see what's available\n"
            "• <b>/progress</b> — your analytics &amp; JAMB prediction\n"
            "• <b>/report</b> — get your PDF progress report\n"
            "• <b>/buy</b> — unlock premium via Paystack\n"
            "• <b>/help</b> — everything I can do\n\n"
            "Parents: <b>/linkchild CODE</b> to follow your child's progress.",
            parse_mode="HTML",
        )
        return
    lowered = argument.lower()
    if lowered.startswith("quiz_"):
        subject = argument[5:].replace("_", " ").strip()
        if subject:
            _bot_handle_quiz(chat_id, telegram_id, subject)
        else:
            _bot_available_subjects(chat_id)
    elif lowered.startswith("drill_"):
        topic_hint = argument[6:].replace("_", " ").strip()
        _bot_send(chat_id, f"🎯 Remedial drill for “{topic_hint}” — pick a subject to start:")
        _bot_available_subjects(chat_id)
    elif lowered.startswith("assignment_"):
        class_code = argument[11:].strip()
        _bot_send(
            chat_id,
            f"📋 <b>Assignment</b> for {class_code or 'your class'}.\n\n"
            "Your teacher's assignments are posted here. Ask them to share the paper/link.",
            parse_mode="HTML",
        )
    elif lowered.startswith("ref_"):
        _bot_send(
            chat_id,
            "🎟️ Referral link received! Invite friends — your referral code is tracked on your profile (/me).",
        )
    elif lowered.startswith("consult"):
        _bot_send(
            chat_id,
            "🗣️ <b>Consultation</b>\n\nTalk to the study bot or support desk for one-on-one help.\n"
            "Message the support bot or ask here and we'll route you.",
            parse_mode="HTML",
        )
    else:
        _bot_handle_quiz(chat_id, telegram_id, argument)


RECENT_QUESTION_IDS: Dict[int, deque] = {}
RECENT_QUESTION_LIMIT = 60
QUIZ_QUESTION_LIMIT = 5
QUIZ_SESSION_TTL_SECONDS = 1800  # 30 minutes


def _load_micro_quiz_sessions() -> None:
    """Restore in-progress micro-quizzes from DB at startup (crash recovery)."""
    rows = db.fetch_all(
        "SELECT session_id, telegram_id, subject, questions_json, current_index, answers, started_at "
        "FROM quiz_sessions WHERE mode = 'micro_quiz' AND status = 'IN_PROGRESS'",
        (),
    )
    restored = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        started_at = _parse_db_timestamp(row["started_at"])
        if (now - started_at).total_seconds() > QUIZ_SESSION_TTL_SECONDS:
            # Stale session — mark completed and skip
            db.execute(
                "UPDATE quiz_sessions SET status = ?, completed_at = ? WHERE session_id = ?"
                if db.mode == "sqlite"
                else "UPDATE quiz_sessions SET status = %s, completed_at = %s WHERE session_id = %s",
                ("COMPLETED", utc_now(), row["session_id"]),
            )
            continue
        questions_json = _jsonb_read(row.get("questions_json"))
        answers = _jsonb_read(row.get("answers"))
        question_ids = questions_json if isinstance(questions_json, list) else []
        # Fetch full question objects
        questions = []
        if question_ids:
            ph = "?" if db.mode == "sqlite" else "%s"
            placeholders = ", ".join(ph for _ in question_ids)
            q_rows = db.fetch_all(
                f"SELECT id, exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty "
                f"FROM question_bank WHERE id IN ({placeholders})",
                tuple(question_ids),
            )
            questions = [normalize_question_row(dict(r)) for r in q_rows]
        QUIZ_SESSIONS[row["telegram_id"]] = {
            "telegram_id": row["telegram_id"],
            "subject": row["subject"],
            "client_attempt_id": row["session_id"].replace("micro-", "bot-"),
            "session_id": row["session_id"],
            "questions": questions,
            "idx": row["current_index"] or 0,
            "answers": answers if isinstance(answers, list) else [],
            "started_at": row["started_at"],
        }
        restored += 1
    if restored:
        logger.info("Restored %d in-progress micro-quiz session(s) from DB", restored)


def _select_quiz_questions(subject: str, telegram_id: int, limit: int = QUIZ_QUESTION_LIMIT) -> List[Dict[str, Any]]:
    """Pick quiz questions spread across distinct topics, avoiding recently served questions."""
    ph = "?" if db.mode == "sqlite" else "%s"
    params: List[Any] = [subject]
    sql = (
        "SELECT id, exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty "
        f"FROM question_bank WHERE LOWER(subject) = LOWER({ph})"
    )
    recent = RECENT_QUESTION_IDS.get(telegram_id)
    if recent:
        placeholders = ", ".join(ph for _ in recent)
        sql += f" AND id NOT IN ({placeholders})"
        params.extend(recent)
    sql += " ORDER BY RANDOM()" if db.mode == "sqlite" else " ORDER BY random()"
    sql += f" LIMIT {ph}"
    params.append(max(limit * 6, 30))
    rows = db.fetch_all(sql, tuple(params))
    if len(rows) < limit and recent:
        # Pool exhausted (everything excluded) — fall back to the full bank.
        params = [subject]
        sql = (
            "SELECT id, exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty "
            f"FROM question_bank WHERE LOWER(subject) = LOWER({ph})"
        )
        sql += " ORDER BY RANDOM()" if db.mode == "sqlite" else " ORDER BY random()"
        sql += f" LIMIT {ph}"
        params.append(max(limit * 6, 30))
        rows = db.fetch_all(sql, tuple(params))
    if not rows:
        return []
    # Round-robin across topics so a session covers as many distinct topics as possible.
    by_topic: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_topic.setdefault(str(row["topic"]), []).append(row)
    topics = list(by_topic)
    random.shuffle(topics)
    picked: List[Dict[str, Any]] = []
    while len(picked) < limit:
        progressed = False
        for topic in topics:
            if by_topic[topic] and len(picked) < limit:
                picked.append(by_topic[topic].pop(0))
                progressed = True
        if not progressed:
            break
    return [normalize_question_row(dict(row)) for row in picked]


def _remember_quiz_questions(telegram_id: int, questions: List[Dict[str, Any]]) -> None:
    recent = RECENT_QUESTION_IDS.setdefault(telegram_id, deque(maxlen=RECENT_QUESTION_LIMIT))
    for question in questions:
        if question.get("id") is not None:
            recent.append(question["id"])


def _bot_handle_quiz(chat_id: int, telegram_id: int, subject: str) -> None:
    subject = (subject or "").strip()
    if not subject:
        _bot_available_subjects(chat_id)
        return
    if QUIZ_SESSIONS.get(chat_id):
        _bot_send(chat_id, "⏳ You already have an active quiz. Finish it or send /cancel.")
        return
    questions = _select_quiz_questions(subject, telegram_id)
    if not questions:
        _bot_send(chat_id, f"😅 No questions found for “{subject}” yet.")
        _bot_available_subjects(chat_id)
        return
    _remember_quiz_questions(telegram_id, questions)
    client_attempt_id = f"bot-{secrets.token_hex(8)}"
    session_id = f"micro-{client_attempt_id}"
    # Persist micro-quiz to DB for crash recovery
    db.execute(
        """
        INSERT INTO quiz_sessions
        (session_id, telegram_id, exam_type, subject, mode, total_questions, current_index, time_limit_seconds, time_remaining, status, answers, questions_json, started_at, last_activity_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        if db.mode == "sqlite"
        else """
        INSERT INTO quiz_sessions
        (session_id, telegram_id, exam_type, subject, mode, total_questions, current_index, time_limit_seconds, time_remaining, status, answers, questions_json, started_at, last_activity_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            session_id,
            telegram_id,
            "MICRO_QUIZ",
            subject,
            "micro_quiz",
            len(questions),
            0,
            0,
            0,
            "IN_PROGRESS",
            json_dumps([]),
            json_dumps([q.get("id") for q in questions]),
            utc_now(),
            utc_now(),
        ),
    )
    QUIZ_SESSIONS[chat_id] = {
        "telegram_id": telegram_id,
        "subject": subject,
        "client_attempt_id": client_attempt_id,
        "session_id": session_id,
        "questions": questions,
        "idx": 0,
        "answers": [],
        "started_at": utc_now(),
    }
    _bot_send_current_question(chat_id)


def _bot_send_current_question(chat_id: int) -> None:
    session = QUIZ_SESSIONS.get(chat_id)
    if not session:
        return
    question = session["questions"][session["idx"]]
    options = list(question.get("options") or [])[:4]
    letters = "ABCD"
    body = (
        f"📚 <b>{session['subject']}</b> — Question {session['idx'] + 1}/{len(session['questions'])}\n\n"
        f"{question['question_text']}\n"
    )
    for i, option in enumerate(options):
        body += f"\n{letters[i]}) {option}"
    buttons = [
        {
            "text": letters[i],
            "callback_data": f"q:{session['client_attempt_id'][4:]}:{session['idx']}:{letters[i]}",
        }
        for i in range(len(options))
    ]
    _bot_send(chat_id, body, reply_markup={"inline_keyboard": [buttons]}, parse_mode="HTML")


def _handle_telegram_callback(callback: Dict[str, Any]) -> None:
    chat_id = (callback.get("message") or {}).get("chat", {}).get("id")
    callback_id = callback.get("id")
    data = callback.get("data") or ""
    sender = callback.get("from") or {}
    telegram_id = sender.get("id")
    if telegram_id:
        upsert_user_record(
            telegram_id,
            sender.get("first_name"),
            sender.get("last_name"),
            sender.get("username"),
        )
    if not chat_id or not callback_id or telegram_bot is None:
        return
    if data.startswith("onboard:"):
        _handle_onboarding_callback(callback, chat_id, callback_id, data)
        return
    if not data.startswith("q:"):
        if data.startswith("ex:"):
            _handle_exam_callback(chat_id, callback_id, data, telegram_id)
            return
        if data.startswith("onb:"):
            _handle_onboarding_callback(callback, chat_id, callback_id, data)
            return
        telegram_bot.answer_callback_query(callback_id, text="Not recognised")
        return
    try:
        _, mark, raw_idx, letter = data.split(":")
        idx = int(raw_idx)
    except ValueError:
        telegram_bot.answer_callback_query(callback_id, text="Invalid option")
        return
    session = QUIZ_SESSIONS.get(chat_id)
    session_mark = str(session.get("client_attempt_id", "")) if session else ""
    if not session or not session_mark.endswith(mark) or session["idx"] != idx:
        telegram_bot.answer_callback_query(callback_id, text="Session expired — send /quiz")
        return
    letter = letter.upper()
    options = list(session["questions"][idx].get("options") or [])[:4]
    letter_index = "ABCD".find(letter)
    if not (0 <= letter_index < len(options)):
        telegram_bot.answer_callback_query(callback_id, text="Invalid option")
        return
    session["answers"].append(options[letter_index])
    telegram_bot.answer_callback_query(callback_id, text=f"{letter} selected")
    session["idx"] += 1
    # Persist quiz progress to DB for crash recovery
    db.execute(
        "UPDATE quiz_sessions SET current_index = ?, answers = ?, last_activity_at = ? WHERE session_id = ?"
        if db.mode == "sqlite"
        else "UPDATE quiz_sessions SET current_index = %s, answers = %s, last_activity_at = %s WHERE session_id = %s",
        (session["idx"], json_dumps(session["answers"]), utc_now(), session.get("session_id")),
    )
    if session["idx"] < len(session["questions"]):
        _bot_send_current_question(chat_id)
    else:
        _bot_finish_quiz(chat_id)


def _handle_onboarding_callback(callback: Dict[str, Any], chat_id: int, callback_id: str, data: str) -> None:
    """Resolve an onboarding button press and route the user to role-specific UX."""
    try:
        _, requested = data.split(":", 1)
    except ValueError:
        telegram_bot.answer_callback_query(callback_id, text="Invalid option")
        return
    canonical = normalize_role(requested)
    if canonical not in (ROLE_STUDENT, ROLE_PARENT, ROLE_TEACHER, ROLE_SCHOOL_ADMIN):
        telegram_bot.answer_callback_query(callback_id, text="Unknown role")
        return
    # Telegram `callback_query.from.id` is the authoritative telegram_id.
    try:
        telegram_id = int((callback.get("from") or {}).get("id") or 0)
    except (TypeError, ValueError):
        telegram_id = 0
    if not telegram_id:
        telegram_bot.answer_callback_query(callback_id, text="Please send /start to refresh")
        return
    try:
        updated = set_profile_role(telegram_id, canonical, source="onboarding_button")
    except ValueError:
        telegram_bot.answer_callback_query(callback_id, text="Cannot assign that role")
        return
    log_telemetry(telegram_id, canonical, "onboarding_completed", {"source": "telegram_callback", "role": canonical})
    payload = _onboarding_payload({**updated, "telegram_id": telegram_id, "onboarded": True})
    next_step = payload.get("next_step") or {}
    body = (
        f"{next_step.get('headline', 'Role set')}\n\n"
        + "\n".join(f"• {step}" for step in next_step.get("next_steps", []))
    )
    if canonical == ROLE_STUDENT:
        body += "\n\nTip: tap /exam now to start the full exam wizard."
    elif canonical == ROLE_PARENT:
        body += "\n\nTip: send /linkchild <CODE> to link your first child."
    elif canonical == ROLE_TEACHER:
        body += "\n\nTip: ask your school admin to send you a teacher invite code."
    elif canonical == ROLE_SCHOOL_ADMIN:
        body += "\n\nTip: generate teacher invite codes from /api/v1/access/generate."
    _bot_send(chat_id, body, parse_mode="HTML")
    telegram_bot.answer_callback_query(callback_id, text=f"Set as {canonical.lower().replace('_', ' ')}")


def _bot_finish_quiz(chat_id: int) -> None:
    session = QUIZ_SESSIONS.pop(chat_id, None)
    if not session:
        return
    # Mark micro-quiz DB session as COMPLETED
    session_id = session.get("session_id")
    if session_id:
        db.execute(
            "UPDATE quiz_sessions SET status = ?, completed_at = ? WHERE session_id = ?"
            if db.mode == "sqlite"
            else "UPDATE quiz_sessions SET status = %s, completed_at = %s WHERE session_id = %s",
            ("COMPLETED", utc_now(), session_id),
        )
    questions = session["questions"]
    try:
        items = [
            QuizItemSubmission(
                id=question.get("id"),
                question_text=question["question_text"],
                options=list(question.get("options") or []),
                selected_answer=answer,
                correct_answer=question.get("correct_answer"),
                subject=session["subject"],
                topic=question.get("topic"),
                seconds_spent=0,
            )
            for question, answer in zip(questions, session["answers"])
        ]
        payload = QuizSubmitRequest(
            subject=session["subject"],
            topic=questions[0].get("topic") or None,
            source="telegram",
            client_attempt_id=session["client_attempt_id"],
            started_at=session["started_at"],
            finished_at=utc_now(),
            items=items,
        )
    except Exception as exc:
        _bot_send(chat_id, f"⚠️ Could not build results: {exc}")
        return
    profile = get_profile(session["telegram_id"]) or {"telegram_id": session["telegram_id"]}
    user = dict(profile)
    user["premium"] = is_premium(user)
    try:
        result = analyze_quiz(user, payload)
        _bot_persist_attempt(user, payload, result)
    except Exception as exc:
        logger.exception("Bot quiz finalization failed")
        _bot_send(chat_id, f"⚠️ Quiz scored but could not be stored: {exc}")
        return
    prediction = result.prediction
    lang = get_user_lang(session["telegram_id"])
    lines = [
        t("quiz_complete", lang, subject=session["subject"]),
        t("quiz_score", lang, correct=result.correct, total=result.total, score_pct=result.score_pct),
        t("jamb_prediction", lang, jamb_low=prediction['jamb_low'], jamb_high=prediction['jamb_high'], waec_grade=prediction['waec_grade']),
        t("trust_score", lang, trust_score=result.trust_score),
        "",
        "📝 Review:",
    ]
    wrong = [item for item in result.items if not item["is_correct"]][:3]
    if not wrong:
        lines.append("Perfect — no review items! 🎉")
    for item in wrong:
        preview = (item["question_text"] or "")[:90]
        lines.append(f"❌ {preview}…")
        lines.append(f"   ✅ <b>{item['correct_answer']}</b>")
    _bot_send(chat_id, "\n".join(lines), parse_mode="HTML")


def _exam_subjects_for(exam_type: str, limit: int = 12) -> List[str]:
    ph = "?" if db.mode == "sqlite" else "%s"
    rows = db.fetch_all(
        f"SELECT DISTINCT subject FROM question_bank WHERE LOWER(exam_type) = LOWER({ph}) ORDER BY subject LIMIT {ph}",
        (exam_type, limit),
    )
    return [row["subject"] for row in rows]


def _latest_open_exam_session(telegram_id: int) -> Optional[Dict[str, Any]]:
    ph = "?" if db.mode == "sqlite" else "%s"
    row = db.fetch_one(
        f"SELECT session_id, subject, exam_type, current_index, total_questions, status, time_remaining "
        f"FROM quiz_sessions WHERE telegram_id = {ph} AND status != 'COMPLETED' AND mode != 'micro_quiz' ORDER BY id DESC LIMIT {ph}",
        (telegram_id, 1),
    )
    return dict(row) if row else None


def _bot_exam_user(telegram_id: int) -> Dict[str, Any]:
    profile = get_profile(telegram_id) or {"telegram_id": telegram_id}
    user = dict(profile)
    user["premium"] = is_premium(user)
    return user


def _format_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _bot_handle_exam(chat_id: int, telegram_id: int, _: str = "") -> None:
    """Step 1 of the wizard: exam type selection (with resume offer if open)."""
    if chat_id in EXAM_ACTIVE:
        _bot_send(chat_id, "⏳ You already have an exam in progress. Finish it or send /cancel.")
        return
    open_session = _latest_open_exam_session(telegram_id)
    if open_session:
        remaining = _format_seconds(float(open_session["time_remaining"] or 0))
        _bot_send(
            chat_id,
            f"🔎 You have an unfinished <b>{open_session['exam_type']} {open_session['subject']}</b> exam "
            f"({open_session['current_index']}/{open_session['total_questions']} answered, {remaining} left).",
            parse_mode="HTML",
            reply_markup={"inline_keyboard": [[
                {"text": "▶️ Resume exam", "callback_data": f"ex:resume:{open_session['session_id']}"},
            ]]},
        )
        return
    buttons = [[{"text": exam, "callback_data": f"ex:type:{exam}"}] for exam in EXAM_TYPES]
    _bot_send(
        chat_id,
        "🎓 <b>Exam Practice</b>\n\nStep 1/4 — choose your exam type:",
        parse_mode="HTML",
        reply_markup={"inline_keyboard": buttons},
    )


def _bot_wizard_subjects(chat_id: int, exam_type: str) -> None:
    """Step 2: subject selection for the chosen exam."""
    subjects = _exam_subjects_for(exam_type)
    if not subjects:
        _bot_send(chat_id, f"😅 No {exam_type} questions available yet. Try another exam type.")
        return
    EXAM_WIZARDS[chat_id] = {"exam_type": exam_type, "mark": secrets.token_hex(4)}
    buttons = [
        [{"text": subject, "callback_data": f"ex:sub:{exam_type}:{quote(subject)}"}]
        for subject in subjects
    ]
    _bot_send(
        chat_id,
        f"🎓 <b>{exam_type}</b>\n\nStep 2/4 — choose a subject:",
        parse_mode="HTML",
        reply_markup={"inline_keyboard": buttons},
    )


def _bot_wizard_config(chat_id: int, subject: str) -> None:
    """Step 3: number of questions and exam duration."""
    wizard = EXAM_WIZARDS.get(chat_id)
    if not wizard:
        _bot_send(chat_id, "Session expired — send /exam to start again.")
        return
    wizard["subject"] = subject
    mark = wizard["mark"]
    exam_type = wizard["exam_type"]
    question_buttons = [[
        {"text": label, "callback_data": f"ex:q:{mark}:{value}"}
        for label, value in [("10 Questions", 10), ("20 Questions", 20), ("40 Questions", 40), ("Custom", 0)]
    ]]
    duration_buttons = [[
        {"text": label, "callback_data": f"ex:d:{mark}:{value}"}
        for label, value in [("10 Mins", 10), ("20 Mins", 20), ("45 Mins", 45), ("Untimed", 0)]
    ]]
    _bot_send(
        chat_id,
        f"🎓 <b>{exam_type} — {subject}</b>\n\nStep 3/4 — how many questions?",
        parse_mode="HTML",
        reply_markup={"inline_keyboard": question_buttons},
    )
    _bot_send(
        chat_id,
        "Step 4/4 — pick the total exam duration (a ticking timer will run):",
        reply_markup={"inline_keyboard": duration_buttons},
    )


def _bot_start_exam_session(chat_id: int, telegram_id: int, num_questions: int, minutes: int) -> None:
    """Step 4: launch the session with the timer running."""
    wizard = EXAM_WIZARDS.pop(chat_id, None)
    if not wizard:
        _bot_send(chat_id, "Session expired — send /exam to start again.")
        return
    try:
        session = create_exam_session(
            _bot_exam_user(telegram_id),
            wizard["exam_type"],
            wizard["subject"],
            num_questions,
            minutes,
            "standard",
        )
    except HTTPException as exc:
        _bot_send(chat_id, f"⚠️ Could not start the exam: {exc.detail}")
        return
    EXAM_ACTIVE[chat_id] = {
        "session_id": session["session_id"],
        "mark": secrets.token_hex(4),
        "telegram_id": telegram_id,
    }
    _bot_send(
        chat_id,
        f"🚀 <b>{session['exam_type']} {session['subject']}</b> exam started!\n"
        f"{session['total_questions']} questions — ⏱ {minutes} minutes. Good luck!",
        parse_mode="HTML",
    )
    _bot_send_exam_question(chat_id)


def _bot_send_exam_question(chat_id: int) -> None:
    active = EXAM_ACTIVE.get(chat_id)
    if not active:
        return
    session = _fetch_exam_session(active["session_id"])
    if not session or session["status"] == "COMPLETED":
        EXAM_ACTIVE.pop(chat_id, None)
        return
    question = _exam_public_question(session, int(session["current_index"]))
    if question is None:
        return
    letters = "ABCD"
    body = (
        f"📚 <b>{session['subject']}</b> — Question {question['index'] + 1}/{question['total']}\n"
        f"⏱ Time left: {_format_seconds(float(session['time_remaining']))}\n\n"
        f"{question['question_text']}\n"
    )
    options = list(question.get("options") or [])
    for i, option in enumerate(options):
        body += f"\n{letters[i]}) {option}"
    answer_buttons = [
        {
            "text": letters[i],
            "callback_data": f"ex:ans:{active['mark']}:{question['index']}:{letters[i]}",
        }
        for i in range(len(options))
    ]
    action_buttons = [
        {"text": "⏸️ Pause", "callback_data": f"ex:pause:{active['mark']}"},
        {"text": "⏭️ Skip", "callback_data": f"ex:skip:{active['mark']}:{question['index']}"},
        {"text": "🚩 Flag", "callback_data": f"ex:flag:{active['mark']}:{question['index']}"},
        {"text": "📥 Submit Exam", "callback_data": f"ex:quit:{active['mark']}"},
    ]
    inline_keyboard = [answer_buttons[:len(options)]] + [[b] for b in action_buttons]
    _bot_send(chat_id, body, reply_markup={"inline_keyboard": inline_keyboard}, parse_mode="HTML")


def _bot_finish_exam(chat_id: int, reason: str) -> None:
    active = EXAM_ACTIVE.pop(chat_id, None)
    if not active:
        _bot_send(chat_id, "No active exam to finish.")
        return
    session = _fetch_exam_session(active["session_id"])
    if not session:
        _bot_send(chat_id, "Exam session not found.")
        return
    result = finalize_exam_session(_bot_exam_user(active["telegram_id"]), session, reason)
    _bot_send_exam_summary(chat_id, result)


def _bot_send_exam_summary(chat_id: int, result: Dict[str, Any]) -> None:
    diagnostics = result.get("diagnostics") or {}
    lines = [
        f"🎯 <b>{result.get('subject', 'Exam')} — exam submitted!</b>",
        f"Reason: {str(result.get('reason', 'completed')).replace('_', ' ')}",
        f"Score: {result.get('correct', 0)}/{result.get('total_answered', 0)} "
        f"({result.get('score_pct', 0.0)}%) of answered questions",
    ]
    if result.get("unanswered"):
        lines.append(f"⚠️ {result['unanswered']} question(s) were not answered.")
    breakdown = diagnostics.get("topic_breakdown") or []
    if breakdown:
        lines.append("")
        lines.append("📊 Topic accuracy:")
        for topic in breakdown[:4]:
            lines.append(
                f"• {topic['topic']}: {topic['accuracy_pct']}% "
                f"(avg {topic['average_time_per_question']}s/question)"
            )
    focus = diagnostics.get("focus_areas") or []
    if focus:
        lines.append("")
        lines.append("🧠 Focus next on: " + ", ".join(focus))
    _bot_send(chat_id, "\n".join(lines), parse_mode="HTML")


def _handle_exam_callback(chat_id: int, callback_id: str, data: str, telegram_id: int) -> None:
    """Router for every `ex:` callback emitted by the exam wizard."""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    try:
        if action == "type":
            _bot_wizard_subjects(chat_id, parts[2])
        elif action == "sub":
            wizard = EXAM_WIZARDS.get(chat_id)
            if not wizard or wizard.get("exam_type") != parts[2]:
                telegram_bot.answer_callback_query(callback_id, text="Session expired — send /exam")
                return
            _bot_wizard_config(chat_id, parts[3])
        elif action == "q":
            wizard = EXAM_WIZARDS.get(chat_id)
            if not wizard or wizard.get("mark") != parts[2]:
                telegram_bot.answer_callback_query(callback_id, text="Session expired — send /exam")
                return
            chosen = int(parts[3])
            if chosen == 0:  # Custom
                telegram_bot.answer_callback_query(callback_id, text="Pick a count:")
                _bot_send(
                    chat_id,
                    "🔢 Choose question count:",
                    reply_markup={"inline_keyboard": [
                        [{"text": f"{n}", "callback_data": f"ex:q:{parts[2]}:{n}"}] for n in (15, 25, 35)
                    ]},
                )
                return
            wizard["num_questions"] = chosen
            telegram_bot.answer_callback_query(callback_id, text=f"{chosen} questions selected")
            _bot_send(chat_id, "Now choose the total duration below. ⏱")
            return
        elif action == "d":
            wizard = EXAM_WIZARDS.get(chat_id)
            if not wizard or wizard.get("mark") != parts[2]:
                telegram_bot.answer_callback_query(callback_id, text="Session expired — send /exam")
                return
            num = int(wizard.get("num_questions") or 0)
            if num <= 0:
                telegram_bot.answer_callback_query(callback_id, text="Pick the number of questions first")
                return
            minutes = int(parts[3])
            if minutes == 0:  # Untimed → 3h ceiling (timer exists but effectively disabled)
                minutes = settings.EXAM_MAX_DURATION_MINUTES
            telegram_bot.answer_callback_query(callback_id, text="Starting exam…")
            _bot_start_exam_session(chat_id, telegram_id, num, minutes)
            return
        elif action in ("ans", "quit"):
            active = EXAM_ACTIVE.get(chat_id)
            if not active or active.get("mark") != parts[2]:
                telegram_bot.answer_callback_query(callback_id, text="Session expired — send /exam")
                return
            if action == "quit":
                telegram_bot.answer_callback_query(callback_id, text="Scoring your answers…")
                _bot_finish_exam(chat_id, "user_quit")
            else:
                _handle_exam_answer_callback(chat_id, callback_id, active, parts)
            return
        elif action == "pause":
            active = EXAM_ACTIVE.get(chat_id)
            if not active or active.get("mark") != parts[2]:
                telegram_bot.answer_callback_query(callback_id, text="Session expired — send /exam")
                return
            telegram_bot.answer_callback_query(callback_id, text="Paused ⏸️")
            _bot_pause_exam_session(chat_id, active["session_id"])
            _bot_send(chat_id, "⏸️ Exam paused. Use the button below to resume when ready.")
            session_id = active["session_id"]
            _bot_send(
                chat_id,
                "▶️ Resume exam",
                reply_markup={"inline_keyboard": [[{"text": "▶️ Resume", "callback_data": f"ex:resume:{session_id}"}]]},
            )
            return
        elif action in ("skip", "flag"):
            active = EXAM_ACTIVE.get(chat_id)
            if not active or active.get("mark") != parts[2]:
                telegram_bot.answer_callback_query(callback_id, text="Session expired — send /exam")
                return
            telegram_bot.answer_callback_query(callback_id, text="Skipped ⏭️" if action == "skip" else "🚩 Flagged for review")
            _record_exam_action(chat_id, active, parts, action)
            _bot_send_exam_question(chat_id)
            return
        elif action == "resume":
            session = _fetch_exam_session(parts[2], telegram_id)
            if not session or session["status"] == "COMPLETED":
                telegram_bot.answer_callback_query(callback_id, text="Nothing to resume")
                return
            telegram_bot.answer_callback_query(callback_id, text="Resuming…")
            state = resume_exam_session(_bot_exam_user(telegram_id), session["session_id"])
            if state.get("finished"):
                _bot_send_exam_summary(chat_id, state)
                return
            EXAM_ACTIVE[chat_id] = {
                "session_id": session["session_id"],
                "mark": secrets.token_hex(4),
                "telegram_id": telegram_id,
            }
            _bot_send(chat_id, "▶️ Exam resumed — timer is running again!")
            _bot_send_exam_question(chat_id)
            return
        else:
            telegram_bot.answer_callback_query(callback_id, text="Not recognised")
            return
        telegram_bot.answer_callback_query(callback_id)
    except (IndexError, ValueError):
        telegram_bot.answer_callback_query(callback_id, text="Invalid selection")


def _bot_pause_exam_session(chat_id: int, session_id: str) -> None:
    """Persist a manual pause (timer frozen, no idle gap charged)."""
    session = _fetch_exam_session(session_id)
    if not session or session["status"] != "IN_PROGRESS":
        if session:
            session = _tick_exam_session(session)
        if not session or session["status"] != "IN_PROGRESS":
            return
    _update_exam_session(session_id, status="PAUSED")
    EXAM_ACTIVE.pop(chat_id, None)
    logger.info("Exam session %s paused by user @%s", session_id, chat_id)


def _record_exam_action(chat_id: int, active: Dict[str, Any], parts: List[str], action: str) -> None:
    """Persist a skip (or flag bookmark) so telemetry captures it.

    Skip logs a telemetry row with event_type='skip' and advances the index;
    Flag bookmarks the question for later review without advancing. Both are
    crash-safe single-row writes that never lose the student's place.
    """
    session = _fetch_exam_session(active["session_id"])
    if not session or session["status"] != "IN_PROGRESS":
        return
    session = _tick_exam_session(session)
    if session["status"] != "IN_PROGRESS":
        return
    questions = session.get("questions_json") or []
    index = int(parts[3])
    if index < 0 or index >= len(questions):
        return
    question = questions[index]
    telemetry_payload = {
        "session_id": session["session_id"],
        "telegram_id": active["telegram_id"],
        "question_id": question.get("id"),
        "subject": session["subject"],
        "topic": question.get("topic") or "General",
        "selected_answer": None,
        "confidence_level": "low",
        "is_correct": 0,
        "time_spent_seconds": 0,
        "answer_changes": 0,
        "event_type": action,
    }
    ph = "?" if db.mode == "sqlite" else "%s"
    columns = ", ".join(telemetry_payload.keys())
    placeholders = ", ".join(ph for _ in telemetry_payload)
    db.execute(
        f"INSERT INTO answer_telemetry ({columns}) VALUES ({placeholders})",
        tuple(telemetry_payload.values()),
    )
    if action == "skip":
        # Advance the cursor so the student moves on; the answer slot records
        # a skip (is_correct=0, selected_answer=NULL) for analytics.
        answers = session.get("answers") or []
        answers.append({
            "question_id": question.get("id"),
            "selected_answer": None,
            "correct_answer": question.get("correct_answer"),
            "is_correct": False,
            "skipped": True,
            "time_spent_seconds": 0,
            "topic": question.get("topic") or "General",
            "subject": session["subject"],
        })
        stamp = utc_now()
        _update_exam_session(session["session_id"], answers=answers, current_index=index + 1, last_activity_at=stamp)
    logger.info("Exam action %s logged for session %s @ question %s", action, session["session_id"], index)


def _handle_exam_answer_callback(
    chat_id: int, callback_id: str, active: Dict[str, Any], parts: List[str]
) -> None:
    _, _, mark, raw_idx, letter = parts
    index = int(raw_idx)
    session = _fetch_exam_session(active["session_id"])
    if not session or int(session["current_index"]) != index:
        telegram_bot.answer_callback_query(callback_id, text="Out of sync — reloading…")
        _bot_send_exam_question(chat_id)
        return
    question = _exam_public_question(session, index)
    options = list(question.get("options") or [])
    letter_index = "ABCD".find(letter.upper())
    if not (0 <= letter_index < len(options)):
        telegram_bot.answer_callback_query(callback_id, text="Invalid option")
        return
    result = record_exam_answer(
        _bot_exam_user(active["telegram_id"]),
        active["session_id"],
        ExamAnswerRequest(
            question_id=int(question["question_id"]),
            selected_answer=options[letter_index],
            confidence_level="medium",
            time_spent_seconds=0,
        ),
    )
    if result.get("finished"):
        telegram_bot.answer_callback_query(callback_id, text="Exam submitted!")
        EXAM_ACTIVE.pop(chat_id, None)
        _bot_send_exam_summary(chat_id, result)
    else:
        telegram_bot.answer_callback_query(callback_id, text=f"{letter.upper()} saved ✓")
        _bot_send_exam_question(chat_id)


def _bot_persist_attempt(user: Dict[str, Any], payload: QuizSubmitRequest, result: QuizSubmitResponse) -> None:
    """Record a bot quiz attempt so /api/v1/quiz/history and analytics pick it up."""
    seconds_spent = round(sum(item["seconds_spent"] for item in result.items), 2)
    attempt_sql = (
        "INSERT INTO quiz_attempts "
        "(telegram_id, school_id, class_code, subject, topic, title, source, client_attempt_id, "
        " score, total, correct, seconds_spent, started_at, finished_at, trust_score, rush_events) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        if db.mode == "sqlite"
        else "INSERT INTO quiz_attempts "
        "(telegram_id, school_id, class_code, subject, topic, title, source, client_attempt_id, "
        " score, total, correct, seconds_spent, started_at, finished_at, trust_score, rush_events) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    attempt_params = (
        user["telegram_id"],
        user.get("school_id"),
        user.get("class_code"),
        payload.subject,
        payload.topic,
        None,
        "telegram",
        payload.client_attempt_id,
        result.score_pct,
        result.total,
        result.correct,
        seconds_spent,
        payload.started_at or utc_now(),
        payload.finished_at or utc_now(),
        result.trust_score,
        result.error_profiler["rushed"],
    )
    response_sql = (
        "INSERT INTO question_responses "
        "(attempt_id, telegram_id, question_id, question_text, subject, topic, sub_topic, "
        " selected_answer, correct_answer, is_correct, seconds_spent, switches, switch_trail, "
        " is_time_sink, is_rushed, error_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        if db.mode == "sqlite"
        else "INSERT INTO question_responses "
        "(attempt_id, telegram_id, question_id, question_text, subject, topic, sub_topic, "
        " selected_answer, correct_answer, is_correct, seconds_spent, switches, switch_trail, "
        " is_time_sink, is_rushed, error_type) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    try:
        with db.transaction() as (_, cur):
            if db.mode == "postgres":
                cur.execute(attempt_sql, attempt_params)
                attempt_id = int(cur.fetchone()["id"])
            else:
                cur.execute(attempt_sql, attempt_params)
                attempt_id = int(cur.lastrowid)
            for item in result.items:
                cur.execute(
                    response_sql,
                    (
                        attempt_id,
                        user["telegram_id"],
                        item["id"],
                        item["question_text"][:2000],
                        payload.subject,
                        item["topic"],
                        item["sub_topic"],
                        item["selected_answer"],
                        item["correct_answer"],
                        1 if item["is_correct"] else 0,
                        item["seconds_spent"],
                        item["switches"],
                        item["switch_trail"][:200],
                        1 if item["is_time_sink"] else 0,
                        1 if item["is_rushed"] else 0,
                        item["error_type"],
                    ),
                )
    except Exception as exc:
        logger.warning("Failed to persist bot quiz attempt (telegram_id=%s): %s", user["telegram_id"], exc)
    else:
        # New attempt landed -> purge stale analytics so /progress etc. recompute.
        invalidate_student_caches(user["telegram_id"])


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"detail": exc.detail, "status_code": exc.status_code}, status_code=exc.status_code)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=False)
