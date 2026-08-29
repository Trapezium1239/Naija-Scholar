#!/usr/bin/env python3
"""
Executable verification suite for Naija Scholar V2.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parent
TMP_DB = Path(tempfile.gettempdir()) / "naija_scholar_v2_test.sqlite3"

if TMP_DB.exists():
    TMP_DB.unlink()

os.environ["ENABLE_SQLITE_FALLBACK"] = "true"
os.environ["SQLITE_PATH"] = str(TMP_DB)
os.environ["POSTGRES_PORT"] = "59999"
os.environ["SEED_ENABLED"] = "false"
os.environ["TELEGRAM_BOT_ENABLED"] = "false"
os.environ["TELEGRAM_POLLING_ENABLED"] = "false"
os.environ["TELEGRAM_BOT_TOKEN"] = "telegram_test_token"
os.environ["PAYSTACK_SECRET_KEY"] = "paystack_test_secret"
os.environ["PAYSTACK_WEBHOOK_SECRET"] = "paystack_test_secret"

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def build_init_data(user: dict, auth_date: str | None = None) -> str:
    payload = {
        "auth_date": auth_date or str(int(time.time())),
        "query_id": "AAEAAAE",
        "user": json.dumps(user, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(payload.items()))
    secret_key = hmac.new(b"WebAppData", main.settings.TELEGRAM_BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    signature = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    query = "&".join(f"{key}={quote(value, safe='{}\":,')}" for key, value in payload.items())
    return f"{query}&hash={signature}"


def compress_payload(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")


def compress_raw_deflate(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    return base64.urlsafe_b64encode(compressor.compress(raw) + compressor.flush()).decode("ascii").rstrip("=")


class FakeTelegramBot:
    """Captures outbound messages/callbacks so tests never hit the Bot API."""

    def __init__(self) -> None:
        self.messages: list = []
        self.callbacks: list = []
        self.documents: list = []
        self.command_registrations: list = []

    def send_message(self, chat_id: int, text: str, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return {"message_id": len(self.messages)}

    def send_document(self, chat_id: int, document: bytes, filename: str, caption: str | None = None, parse_mode: str | None = None):
        self.documents.append((chat_id, document, filename, caption))
        return {"document": {"file_name": filename}}

    def answer_callback_query(self, callback_query_id: str, text: str | None = None, show_alert: bool = False):
        self.callbacks.append((callback_query_id, text))
        return True

    def set_my_commands(self, commands):
        self.command_registrations.append(list(commands))
        return True

    def get_me(self):
        return {"id": 1, "username": "test_bot", "first_name": "Test Bot"}


def seed_test_question(index: int) -> int:
    main.db.execute(
        "INSERT INTO question_bank "
        "(exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "JAMB",
            "Mathematics",
            "Algebra",
            "SS3",
            f"Sample mathematics question number {index}: what is {index} plus 2?",
            json.dumps(["wrong one", "wrong two", str(index + 2), "wrong four"]),
            str(index + 2),
            "Reasonable explanation.",
            "Easy",
        ),
    )
    row = main.db.fetch_one(
        "SELECT id FROM question_bank WHERE question_text = ? ORDER BY id DESC LIMIT 1",
        (f"Sample mathematics question number {index}: what is {index} plus 2?",),
    )
    return int(row["id"])


class NaijaScholarContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._client_context = TestClient(main.app)
        cls.client = cls._client_context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._client_context.__exit__(None, None, None)

    def test_root_no_frontend_served(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 404)
        self.assertFalse((main.APP_ROOT / "index.html").exists())

    def test_healthz(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn(body["database"], {"sqlite", "postgres"})

    def test_telegram_auth_auto_provisions_user(self) -> None:
        init_data = build_init_data(
            {
                "id": 99001,
                "first_name": "Amina",
                "last_name": "Bello",
                "username": "amina_bello",
            }
        )
        response = self.client.post("/api/v1/auth/telegram", json={"initData": init_data})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "authenticated")
        self.assertEqual(body["user"]["telegram_id"], 99001)
        self.assertTrue(body["referral_link"].endswith(body["user"]["referral_code"]))

    def test_remedial_route_returns_fallback_questions(self) -> None:
        response = self.client.get("/api/v1/questions/remedial", params={"subject": "Government", "limit": 2})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn(body["source"], {"database", "fallback"})
        self.assertGreaterEqual(len(body["questions"]), 1)
        self.assertEqual(body["questions"][0]["subject"], "Government")
        missing_subject = self.client.get(
            "/api/v1/questions/remedial", params={"subject": "Cosmic Cartography", "limit": 2}
        )
        self.assertEqual(missing_subject.status_code, 200)
        self.assertEqual(missing_subject.json()["source"], "fallback")

    def test_sync_payload_round_trip(self) -> None:
        payload = compress_payload({"telegram_id": 99001, "mastery": {"recall": 62}, "alerts": 1})
        response = self.client.post(
            "/api/v1/sync/2g-payload",
            json={"payload": payload, "source": "test-suite", "telegram_id": 99001},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["accepted"])
        self.assertIn("mastery", body["keys"])

    def test_sync_accepts_raw_deflate_payload(self) -> None:
        payload = compress_raw_deflate({"telegram_id": 99001, "raw_deflate": True})
        response = self.client.post(
            "/api/v1/sync/2g-payload",
            json={"payload": payload, "source": "test-raw-deflate", "telegram_id": 99001},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["accepted"])

    def test_sync_rejects_oversized_payload(self) -> None:
        payload = compress_payload({"blob": "x" * 1_000_000})
        response = self.client.post(
            "/api/v1/sync/2g-payload",
            json={"payload": payload, "source": "test-oversized"},
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_sync_rejects_zip_bomb(self) -> None:
        bomb = compress_payload({"blob": "y" * 50_000_000})
        response = self.client.post(
            "/api/v1/sync/2g-payload",
            json={"payload": bomb, "source": "test-bomb"},
        )
        self.assertIn(response.status_code, (400, 413), response.text)

    def test_auth_rejects_expired_init_data(self) -> None:
        stale = build_init_data(
            {
                "id": 99123,
                "first_name": "Stale",
                "last_name": "User",
            },
            auth_date=str(int(time.time()) - 90_000),
        )
        response = self.client.post("/api/v1/auth/telegram", json={"initData": stale})
        self.assertEqual(response.status_code, 401, response.text)

    def test_paystack_webhook_unlocks_access(self) -> None:
        init_data = build_init_data(
            {
                "id": 44110,
                "first_name": "Daniel",
                "last_name": "Okoye",
                "username": "daniel_okoye",
            }
        )
        self.client.post("/api/v1/auth/telegram", json={"initData": init_data})

        event = {
            "event": "charge.success",
            "data": {
                "reference": "ref_001",
                "amount": 250000,
                "metadata": {"telegram_id": 44110},
            },
        }
        raw = json.dumps(event).encode("utf-8")
        signature = hmac.new(
            main.settings.PAYSTACK_SECRET_KEY.encode("utf-8"),
            raw,
            hashlib.sha512,
        ).hexdigest()
        response = self.client.post(
            "/api/v1/webhooks/paystack",
            data=raw,
            headers={"x-paystack-signature": signature, "content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["processed"])
        self.assertEqual(body["status"], "charge.success")
        self.assertTrue(body["access_code"].startswith("NS-"))

    def test_paystack_webhook_is_idempotent(self) -> None:
        init_data = build_init_data(
            {
                "id": 44220,
                "first_name": "Idem",
                "last_name": "User",
            }
        )
        self.client.post("/api/v1/auth/telegram", json={"initData": init_data})

        event = {
            "event": "charge.success",
            "data": {
                "reference": "ref_idem_001",
                "amount": 250000,
                "metadata": {"telegram_id": 44220},
            },
        }
        raw = json.dumps(event).encode("utf-8")
        signature = hmac.new(
            main.settings.PAYSTACK_SECRET_KEY.encode("utf-8"),
            raw,
            hashlib.sha512,
        ).hexdigest()
        headers = {"x-paystack-signature": signature, "content-type": "application/json"}

        first = self.client.post("/api/v1/webhooks/paystack", data=raw, headers=headers)
        second = self.client.post("/api/v1/webhooks/paystack", data=raw, headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["access_code"], second.json()["access_code"])

    def test_portal_overview_contains_intelligence_modules(self) -> None:
        response = self.client.get("/api/v1/portal/overview")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("metrics", body)
        self.assertIn("alerts", body)
        self.assertIn("leaderboard", body)
        self.assertGreaterEqual(len(body["metrics"]), 1)

    def dev_auth(self, telegram_id: int) -> dict:
        response = self.client.get("/api/v1/auth/profile", headers={"X-Dev-User": str(telegram_id)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_lia_rbac_student_isolation(self) -> None:
        self.dev_auth(88101)
        response = self.client.get("/api/v1/analytics/child/99001", headers={"X-Dev-User": "88101"})
        self.assertEqual(response.status_code, 403, response.text)
        response = self.client.get("/api/v1/analytics/school", headers={"X-Dev-User": "88101"})
        self.assertEqual(response.status_code, 403, response.text)
        response = self.client.get("/api/v1/access/generate", headers={"X-Dev-User": "88101"})
        self.assertIn(response.status_code, (403, 405), response.text)

    def test_lia_access_join_school_class(self) -> None:
        profile = self.dev_auth(88201)
        self.assertEqual(profile["role"], "STUDENT")
        response = self.client.post(
            "/api/v1/access/join",
            json={"code": "GIC-SS3-001"},
            headers={"X-Dev-User": "88201"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "joined")
        self.assertEqual(body["class_code"], "SS3-001")
        refreshed = self.dev_auth(88201)
        self.assertEqual(refreshed["class_code"], "SS3-001")
        self.assertIsNotNone(refreshed["school_id"])

    def test_lia_access_generate_role_gate(self) -> None:
        self.dev_auth(88301)
        main.db.execute(
            "UPDATE profiles SET role = ? WHERE telegram_id = ?",
            ("SCHOOL_ADMIN", 88301),
        )
        response = self.client.post(
            "/api/v1/access/generate",
            json={"kind": "school_class", "count": 2, "payload": {"class_code": "SS3-002"}},
            headers={"X-Dev-User": "88301"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        codes = response.json()["generated"]
        self.assertEqual(len(codes), 2)
        self.assertTrue(all(code.startswith("CLS-") for code in codes))
        response = self.client.post(
            "/api/v1/access/generate",
            json={"kind": "school_admin_invite", "count": 1, "payload": {}},
            headers={"X-Dev-User": "88301"},
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_lia_quiz_submit_round_trip(self) -> None:
        self.dev_auth(88401)
        payload = {
            "subject": "Mathematics",
            "topic": "Algebra",
            "title": "Unit test drill",
            "source": "test-suite",
            "items": [
                {
                    "id": None,
                    "question_text": "What is 2 + 2?",
                    "options": ["3", "4", "5", "6"],
                    "selected_answer": "4",
                    "correct_answer": "4",
                    "seconds_spent": 8,
                    "switches": 1,
                    "switch_trail": "3 → 4",
                },
                {
                    "id": None,
                    "question_text": "What is 2 x 2?",
                    "options": ["3", "4", "5", "6"],
                    "selected_answer": "5",
                    "correct_answer": "4",
                    "seconds_spent": 2,
                    "switches": 0,
                },
            ],
            "events": [
                {"event_type": "tab_switch", "occurred_at": "2026-08-01T10:00:00Z"},
                {"event_type": "focus_loss", "occurred_at": "2026-08-01T10:00:02Z"},
            ],
            "started_at": "2026-08-01T10:00:00Z",
            "finished_at": "2026-08-01T10:05:00Z",
        }
        response = self.client.post(
            "/api/v1/quiz/submit", json=payload, headers={"X-Dev-User": "88401"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertGreater(body["attempt_id"], 0)
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["correct"], 1)
        self.assertAlmostEqual(body["score_pct"], 50.0)
        self.assertLess(body["trust_score"], 100)
        self.assertFalse(body["premium"])
        self.assertEqual(body["error_profiler"]["careless_rush"], 1)
        self.assertEqual(body["error_profiler"]["knowledge_gaps"], 0)
        self.assertIn("jamb_low", body["prediction"])
        self.assertIn("waec_grade", body["prediction"])
        history = self.client.get("/api/v1/quiz/history", headers={"X-Dev-User": "88401"})
        self.assertEqual(history.status_code, 200)
        self.assertTrue(any(a["id"] == body["attempt_id"] for a in history.json()["attempts"]))

    def test_quiz_submit_is_idempotent_per_client_attempt(self) -> None:
        self.dev_auth(88402)
        payload = {
            "subject": "Mathematics",
            "topic": "Idempotency",
            "title": "Retry-safe drill",
            "source": "test-suite",
            "client_attempt_id": "unit-retry-88402-1",
            "items": [
                {
                    "id": None,
                    "question_text": "What is 1 + 1?",
                    "options": ["2", "3"],
                    "selected_answer": "2",
                    "correct_answer": "2",
                    "seconds_spent": 6,
                }
            ],
        }
        first = self.client.post(
            "/api/v1/quiz/submit", json=payload, headers={"X-Dev-User": "88402"}
        )
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post(
            "/api/v1/quiz/submit", json=payload, headers={"X-Dev-User": "88402"}
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["attempt_id"], first.json()["attempt_id"])
        history = self.client.get("/api/v1/quiz/history", headers={"X-Dev-User": "88402"})
        matches = [
            a
            for a in history.json()["attempts"]
            if a["id"] == first.json()["attempt_id"] and a["topic"] == "Idempotency"
        ]
        self.assertEqual(len(matches), 1)

    def test_strict_validation_rejects_malformed_codes(self) -> None:
        self.dev_auth(88403)
        for bad_code in ["", "abc", "x!@#", "a b-c"]:
            response = self.client.post(
                "/api/v1/access/join", json={"code": bad_code}, headers={"X-Dev-User": "88403"}
            )
            self.assertEqual(response.status_code, 422, f"code={bad_code!r} -> {response.text}")
        main.db.execute(
            "UPDATE profiles SET role = ? WHERE telegram_id = ?",
            ("SCHOOL_ADMIN", 88403),
        )
        self.dev_auth(88403)
        for bad_kind in ["banana", "SCHOOL_CLASS", "Class"]:
            response = self.client.post(
                "/api/v1/access/generate",
                json={"kind": bad_kind, "count": 1},
                headers={"X-Dev-User": "88403"},
            )
            self.assertEqual(response.status_code, 422, f"kind={bad_kind!r} -> {response.text}")

    def test_sse_stream_emits_heartbeat(self) -> None:
        token = main.build_stream_token(88404)
        self.assertTrue(token)

        async def consume() -> str:
            gen = main.stream_gen(88404)
            first = await gen.__anext__()
            await gen.aclose()
            return first

        chunk = asyncio.run(consume())
        self.assertIn("connected", chunk)
        self.assertIn("88404", chunk)

    def test_sse_stream_rejects_bad_token(self) -> None:
        user = main.stream_user(main.build_stream_token(88405))
        self.assertEqual(user["telegram_id"], 88405)
        with self.assertRaises(Exception):
            main.stream_user("88405:deadbeef")
        with self.assertRaises(Exception):
            main.stream_user("")

    def test_parent_god_mode_gating(self) -> None:
        student_profile = self.dev_auth(88501)
        linking_code = student_profile["linking_code"]
        self.assertTrue(linking_code.startswith("LIA-"))
        response = self.client.post(
            "/api/v1/access/link-child",
            json={"code": linking_code},
            headers={"X-Dev-User": "88502"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        free = self.client.get("/api/v1/analytics/child/88501", headers={"X-Dev-User": "88502"})
        self.assertEqual(free.status_code, 200, free.text)
        self.assertFalse(free.json()["god_mode"])
        self.assertNotIn("responses", free.json())
        god = self.client.post(
            "/api/v1/access/parent-mode",
            json={"mode": "god"},
            headers={"X-Dev-User": "88502"},
        )
        self.assertEqual(god.status_code, 200, god.text)
        locked = self.client.get("/api/v1/analytics/child/88501", headers={"X-Dev-User": "88502"})
        self.assertEqual(locked.status_code, 200)
        self.assertTrue(locked.json()["god_mode"])
        self.assertIn("responses", locked.json())
        self.assertIn("events", locked.json())
        stranger = self.client.get("/api/v1/analytics/child/88501", headers={"X-Dev-User": "88699"})
        self.assertEqual(stranger.status_code, 403, stranger.text)

    def test_lia_bounty_claim_flow(self) -> None:
        student = self.dev_auth(88701)
        parent = self.dev_auth(88702)
        link = self.client.post(
            "/api/v1/access/link-child",
            json={"code": student["linking_code"]},
            headers={"X-Dev-User": "88702"},
        )
        self.assertEqual(link.status_code, 200, link.text)
        created = self.client.post(
            "/api/v1/bounties",
            json={"student_id": 88701, "title": "Algebra mastery", "subject": "Mathematics", "target_score": 100, "reward": 500},
            headers={"X-Dev-User": "88702"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        bounty = self.client.get("/api/v1/bounties", headers={"X-Dev-User": "88701"})
        self.assertEqual(bounty.status_code, 200, bounty.text)
        bounty_id = bounty.json()["bounties"][0]["id"]
        blocked = self.client.post(f"/api/v1/bounties/{bounty_id}/claim", headers={"X-Dev-User": "88701"})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        payload = {
            "subject": "Mathematics",
            "title": "Perfect drill",
            "source": "test-suite",
            "items": [
                {
                    "id": None,
                    "question_text": f"Q{i}",
                    "options": ["A", "B"],
                    "selected_answer": "A",
                    "correct_answer": "A",
                    "seconds_spent": 10,
                    "switches": 0,
                }
                for i in range(3)
            ],
            "events": [],
        }
        perfect = self.client.post("/api/v1/quiz/submit", json=payload, headers={"X-Dev-User": "88701"})
        self.assertEqual(perfect.status_code, 200, perfect.text)
        self.assertAlmostEqual(perfect.json()["score_pct"], 100.0)
        claimed = self.client.post(f"/api/v1/bounties/{bounty_id}/claim", headers={"X-Dev-User": "88701"})
        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertEqual(claimed.json()["status"], "claimed")
        self.assertEqual(claimed.json()["reward"], 500)
        self.assertEqual(parent["role"], "STUDENT")

    def test_lia_digest_and_contract_flow(self) -> None:
        self.dev_auth(88902)
        self.client.post(
            "/api/v1/access/link-child",
            json={"code": self.dev_auth(88901)["linking_code"]},
            headers={"X-Dev-User": "88902"},
        )
        digest = self.client.post(
            "/api/v1/digest/generate",
            json={"student_id": 88901},
            headers={"X-Dev-User": "88902"},
        )
        self.assertEqual(digest.status_code, 200, digest.text)
        self.assertIn("NAIJA SCHOLAR WEEKLY DIGEST", digest.json()["digest"])
        contract = self.client.post(
            "/api/v1/interventions/contracts",
            json={"student_id": 88901, "target_text": "Reach 80% in Mathematics", "threshold_score": 80, "deadline": "2026-09-01"},
            headers={"X-Dev-User": "88903"},
        )
        self.assertEqual(contract.status_code, 403, contract.text)
        main.db.execute(
            "UPDATE profiles SET role = ? WHERE telegram_id = ?",
            ("SCHOOL_ADMIN", 88903),
        )
        contract = self.client.post(
            "/api/v1/interventions/contracts",
            json={"student_id": 88901, "target_text": "Reach 80% in Mathematics", "threshold_score": 80, "deadline": "2026-09-01"},
            headers={"X-Dev-User": "88903"},
        )
        self.assertEqual(contract.status_code, 200, contract.text)
        listed = self.client.get("/api/v1/interventions/contracts", headers={"X-Dev-User": "88901"})
        self.assertEqual(listed.status_code, 200, listed.text)
        contract_id = listed.json()["contracts"][0]["id"]
        accepted = self.client.post(
            f"/api/v1/interventions/contracts/{contract_id}/accept",
            headers={"X-Dev-User": "88902"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["status"], "accepted")


    def test_lia_analytics_stats_endpoints(self) -> None:
        self.dev_auth(89001)
        for correct, wrong in [(2, 3), (4, 1), (3, 2), (4, 2), (2, 0)]:
            items = [
                {
                    "id": None,
                    "question_text": f"c{i}",
                    "options": ["A", "B"],
                    "selected_answer": "A",
                    "correct_answer": "A",
                    "seconds_spent": 10,
                    "switches": 0,
                }
                for i in range(correct)
            ] + [
                {
                    "id": None,
                    "question_text": f"w{i}",
                    "options": ["A", "B"],
                    "selected_answer": "B",
                    "correct_answer": "A",
                    "seconds_spent": 10,
                    "switches": 0,
                }
                for i in range(wrong)
            ]
            response = self.client.post(
                "/api/v1/quiz/submit",
                json={"subject": "Mathematics", "title": "s", "source": "s", "items": items, "events": []},
                headers={"X-Dev-User": "89001"},
            )
            self.assertEqual(response.status_code, 200, response.text)
        stats = self.client.get("/api/v1/analytics/stats/student/89001", headers={"X-Dev-User": "89001"})
        self.assertEqual(stats.status_code, 200, stats.text)
        body = stats.json()
        for key in ("mean", "median", "mode", "stddev", "count"):
            self.assertIn(key, body["stats"])
        self.assertEqual(body["stats"]["count"], 5)
        self.assertIn("series", body)
        self.assertIn("warnings", body)
        stranger = self.client.get("/api/v1/analytics/stats/student/89001", headers={"X-Dev-User": "89099"})
        self.assertEqual(stranger.status_code, 403, stranger.text)
        main.db.execute(
            "UPDATE profiles SET role = ? WHERE telegram_id = ?",
            ("TEACHER", 89001),
        )
        klass = self.client.get(
            "/api/v1/analytics/stats/class?subject=Mathematics", headers={"X-Dev-User": "89001"}
        )
        self.assertEqual(klass.status_code, 200, klass.text)
        school = self.client.get("/api/v1/analytics/stats/school", headers={"X-Dev-User": "89001"})
        self.assertEqual(school.status_code, 403, school.text)

    def test_lia_outlier_early_warning(self) -> None:
        self.dev_auth(89101)
        for correct, wrong in [(2, 1), (3, 1), (4, 1), (2, 0)]:
            items = [
                {
                    "id": None,
                    "question_text": f"c{i}",
                    "options": ["A", "B"],
                    "selected_answer": "A",
                    "correct_answer": "A",
                    "seconds_spent": 10,
                    "switches": 0,
                }
                for i in range(correct)
            ] + [
                {
                    "id": None,
                    "question_text": f"w{i}",
                    "options": ["A", "B"],
                    "selected_answer": "B",
                    "correct_answer": "A",
                    "seconds_spent": 10,
                    "switches": 0,
                }
                for i in range(wrong)
            ]
            self.client.post(
                "/api/v1/quiz/submit",
                json={"subject": "Mathematics", "title": "s", "source": "s", "items": items, "events": []},
                headers={"X-Dev-User": "89101"},
            )
        low = self.client.post(
            "/api/v1/quiz/submit",
            json={
                "subject": "Mathematics",
                "title": "s",
                "source": "s",
                "items": [
                    {
                        "id": None,
                        "question_text": "low",
                        "options": ["A", "B"],
                        "selected_answer": "B",
                        "correct_answer": "A",
                        "seconds_spent": 10,
                        "switches": 0,
                    }
                ],
                "events": [],
            },
            headers={"X-Dev-User": "89101"},
        )
        self.assertEqual(low.status_code, 200, low.text)
        anomaly = low.json().get("anomaly")
        self.assertIsNotNone(anomaly)
        self.assertLess(anomaly["score"], anomaly["median"])
        warnings = self.client.get("/api/v1/warnings", headers={"X-Dev-User": "89101"})
        self.assertEqual(warnings.status_code, 200, warnings.text)
        self.assertGreaterEqual(len(warnings.json()["warnings"]), 1)
        first = warnings.json()["warnings"][0]
        self.assertIn("subject", first)
        self.assertIn("score", first)

    def test_lia_curfew_windows(self) -> None:
        self.dev_auth(89202)
        student = self.dev_auth(89201)
        self.client.post(
            "/api/v1/access/link-child",
            json={"code": student["linking_code"]},
            headers={"X-Dev-User": "89202"},
        )
        blocked = self.client.put(
            "/api/v1/curfew/windows",
            json={"student_id": 89201, "day_label": "weekdays", "start_time": "17:00", "end_time": "19:00", "enabled": True},
            headers={"X-Dev-User": "89299"},
        )
        self.assertEqual(blocked.status_code, 403, blocked.text)
        saved = self.client.put(
            "/api/v1/curfew/windows",
            json={"student_id": 89201, "day_label": "weekdays", "start_time": "17:00", "end_time": "19:00", "enabled": True},
            headers={"X-Dev-User": "89202"},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        updated = self.client.put(
            "/api/v1/curfew/windows",
            json={"student_id": 89201, "day_label": "weekdays", "start_time": "17:30", "end_time": "20:00", "enabled": True},
            headers={"X-Dev-User": "89202"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        listed = self.client.get("/api/v1/curfew/windows", headers={"X-Dev-User": "89202"})
        self.assertEqual(listed.status_code, 200, listed.text)
        windows = listed.json()["windows"]
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["start_time"], "17:30")
        window_id = windows[0]["id"]
        deleted = self.client.delete(f"/api/v1/curfew/windows/{window_id}", headers={"X-Dev-User": "89202"})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        after = self.client.get("/api/v1/curfew/windows", headers={"X-Dev-User": "89202"}).json()["windows"]
        self.assertEqual(len(after), 0)

    def test_lia_pdf_and_zip_exports(self) -> None:
        self.dev_auth(89301)
        main.db.execute(
            "UPDATE profiles SET role = ?, class_code = ? WHERE telegram_id = ?",
            ("TEACHER", "SS3-001", 89301),
        )
        paper = self.client.post(
            "/api/v1/export/mock-paper",
            json={"subject": "Government", "limit_count": 4, "class_code": "SS3-001", "include_answer_key": True},
            headers={"X-Dev-User": "89301"},
        )
        self.assertEqual(paper.status_code, 200, paper.text[:200])
        self.assertEqual(paper.content[:5], b"%PDF-")
        self.assertGreater(len(paper.content), 2000)
        report = self.client.get("/api/v1/export/report/89301.pdf?days=30", headers={"X-Dev-User": "89301"})
        self.assertEqual(report.status_code, 200, report.text[:200])
        self.assertEqual(report.content[:5], b"%PDF-")
        student = self.dev_auth(89302)
        self.assertEqual(student["role"], "STUDENT")
        main.db.execute(
            "UPDATE profiles SET class_code = ? WHERE telegram_id = ?",
            ("SS3-001", 89302),
        )
        main.db.execute(
            "UPDATE profiles SET role = ? WHERE telegram_id = ?",
            ("SCHOOL_ADMIN", 89301),
        )
        archive = self.client.get(
            "/api/v1/export/school-reports.zip?class_code=SS3-001", headers={"X-Dev-User": "89301"}
        )
        self.assertEqual(archive.status_code, 200, archive.text[:200])
        self.assertEqual(archive.content[:2], b"PK")


    def test_bot_webhook_rejects_unknown_token(self) -> None:
        response = self.client.post("/webhook/telegram/not-the-token", json={"update_id": 1})
        self.assertEqual(response.status_code, 404)

    def test_bot_quiz_flow_persists_attempt(self) -> None:
        original = main.telegram_bot
        main.telegram_bot = FakeTelegramBot()
        chat_id = 77701
        try:
            token = main.settings.TELEGRAM_BOT_TOKEN
            for index in range(1, 6):
                seed_test_question(index)
            # /start quiz_Mathematics via deep link
            start = self.client.post(
                f"/webhook/telegram/{token}",
                json={
                    "update_id": 1,
                    "message": {
                        "message_id": 1,
                        "date": int(time.time()),
                        "chat": {"id": chat_id, "type": "private"},
                        "from": {"id": chat_id, "first_name": "BotTester"},
                        "text": "/start quiz_Mathematics",
                    },
                },
            )
            self.assertEqual(start.status_code, 200, start.text)
            sent = [message[1] for message in main.telegram_bot.messages]
            self.assertTrue(any("Question 1/" in text for text in sent), sent)
            session = main.QUIZ_SESSIONS.get(chat_id)
            self.assertIsNotNone(session)
            self.assertEqual(len(session["questions"]), 5)
            # Answer every question correctly via inline buttons.
            for step in range(5):
                active = main.QUIZ_SESSIONS[chat_id]
                question = active["questions"][active["idx"]]
                options = list(question["options"])
                correct = question["correct_answer"]
                self.assertIn(correct, options, question["question_text"])
                letter = "ABCD"[options.index(correct)]
                callback = {
                    "callback_query": {
                        "id": f"cb-{chat_id}-{step}",
                        "from": {"id": chat_id},
                        "message": {"chat": {"id": chat_id}},
                        "data": f"q:{active['client_attempt_id'][4:]}:{step}:{letter}",
                    }
                }
                answered = self.client.post(f"/webhook/telegram/{token}", json=callback)
                self.assertEqual(answered.status_code, 200, answered.text)
            self.assertNotIn(chat_id, main.QUIZ_SESSIONS)
            row = main.db.fetch_one(
                "SELECT id, subject, score FROM quiz_attempts WHERE telegram_id = ? ORDER BY id DESC LIMIT 1",
                (chat_id,),
            )
            self.assertIsNotNone(row, "bot quiz attempt should be persisted")
            self.assertEqual(float(row["score"]), 100.0)
            final_texts = [message[1] for message in main.telegram_bot.messages]
            self.assertTrue(any("quiz complete" in text for text in final_texts), final_texts)
        finally:
            main.telegram_bot = original
            main.QUIZ_SESSIONS.pop(chat_id, None)

    def _send_bot_text(self, chat_id: int, text: str) -> None:
        response = self.client.post(
            f"/webhook/telegram/{main.settings.TELEGRAM_BOT_TOKEN}",
            json={
                "update_id": int(time.time() * 1000) % 1000000,
                "message": {
                    "message_id": int(time.time()),
                    "date": int(time.time()),
                    "chat": {"id": chat_id, "type": "private"},
                    "from": {"id": chat_id, "first_name": "BotTester"},
                    "text": text,
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_neco_questions_are_seeded(self) -> None:
        row = main.db.fetch_one("SELECT COUNT(*) AS count FROM question_bank WHERE exam_type = 'NECO'")
        self.assertIsNotNone(row)
        self.assertGreaterEqual(int(row["count"]), 35, "expected curated NECO questions in the bank")

    def test_quiz_selection_balances_topics_and_avoids_recent(self) -> None:
        main.RECENT_QUESTION_IDS.pop(77750, None)
        try:
            first = main._select_quiz_questions("Mathematics", 77750)
            self.assertEqual(len(first), 5)
            self.assertEqual(len({q["id"] for q in first}), 5, "no duplicate questions within a session")
            self.assertGreaterEqual(
                len({q["topic"] for q in first}),
                4,
                "questions should cover distinct topics when the bank allows",
            )
            main._remember_quiz_questions(77750, first)
            second = main._select_quiz_questions("Mathematics", 77750)
            self.assertEqual(len(second), 5)
            first_ids = {q["id"] for q in first}
            self.assertFalse(
                first_ids.intersection({q["id"] for q in second}),
                "recently served questions must be excluded from the next session",
            )
        finally:
            main.RECENT_QUESTION_IDS.pop(77750, None)

    def test_quiz_selection_falls_back_when_pool_exhausted(self) -> None:
        main.RECENT_QUESTION_IDS.pop(77751, None)
        try:
            ids: list = []
            for _ in range(30):  # exceed the 60-question anti-repeat window with 5 per round
                questions = main._select_quiz_questions("Chemistry", 77751)
                self.assertTrue(questions, "selection must never come back empty for a seeded subject")
                main._remember_quiz_questions(77751, questions)
                ids.extend(q["id"] for q in questions)
            # With the window full, selection must still return questions (fallback path).
            questions = main._select_quiz_questions("Chemistry", 77751)
            self.assertTrue(questions)
        finally:
            main.RECENT_QUESTION_IDS.pop(77751, None)

    def test_bot_buy_reports_unconfigured_paystack(self) -> None:
        original = main.telegram_bot
        main.telegram_bot = FakeTelegramBot()
        chat_id = 77720
        try:
            self._send_bot_text(chat_id, "/buy premium")
            texts = [message[1] for message in main.telegram_bot.messages if message[0] == chat_id]
            self.assertTrue(any("Premium" in text and "not configured" in text for text in texts), texts)
            self.assertTrue(any("₦" in text for text in texts), texts)
        finally:
            main.telegram_bot = original

    def test_bot_parent_link_analytics_report_flow(self) -> None:
        original = main.telegram_bot
        main.telegram_bot = FakeTelegramBot()
        student_id, parent_id = 77730, 77731
        try:
            # Ensure profiles exist.
            self._send_bot_text(student_id, "/start")
            self._send_bot_text(parent_id, "/start")
            code_row = main.db.fetch_one(
                "SELECT linking_code FROM profiles WHERE telegram_id = ?",
                (student_id,),
            )
            self.assertTrue(code_row and code_row["linking_code"], code_row)
            # Parent links the child by code.
            self._send_bot_text(parent_id, f"/linkchild {code_row['linking_code']}")
            texts = [message[1] for message in main.telegram_bot.messages if message[0] == parent_id]
            self.assertTrue(any("Linked!" in text for text in texts), texts)
            parent_role = main.db.fetch_one("SELECT role FROM profiles WHERE telegram_id = ?", (parent_id,))
            self.assertEqual(parent_role["role"], "PARENT")
            # Seed one practice session for the child.
            main.db.execute(
                "INSERT INTO quiz_attempts "
                "(telegram_id, school_id, class_code, subject, topic, title, source, client_attempt_id, "
                " score, total, correct, seconds_spent, started_at, finished_at, trust_score, rush_events) "
                "VALUES (?, NULL, NULL, ?, NULL, NULL, 'telegram', ?, ?, ?, ?, 60, ?, ?, 95, 0)",
                (
                    student_id,
                    "Mathematics",
                    f"bot-test-{int(time.time())}",
                    80.0,
                    5,
                    4,
                    main.utc_now(),
                    main.utc_now(),
                ),
            )
            main.telegram_bot.messages.clear()
            # /mychildren shows the child
            self._send_bot_text(parent_id, "/mychildren")
            texts = [message[1] for message in main.telegram_bot.messages if message[0] == parent_id]
            self.assertTrue(any(str(student_id) in text and "Your children" in text for text in texts), texts)
            # /child gives analytics
            self._send_bot_text(parent_id, f"/child {student_id}")
            texts = [message[1] for message in main.telegram_bot.messages if message[0] == parent_id]
            self.assertTrue(any("Predicted JAMB" in text for text in texts), texts)
            # /progress for the student
            self._send_bot_text(student_id, "/progress")
            texts = [message[1] for message in main.telegram_bot.messages if message[0] == student_id]
            self.assertTrue(any("Your progress" in text and "80.0" in text for text in texts), texts)
            # /leaderboard has data now
            self._send_bot_text(student_id, "/leaderboard")
            texts = [message[1] for message in main.telegram_bot.messages if message[0] == student_id]
            self.assertTrue(any("Top students" in text for text in texts), texts)
            # /report sends a PDF document to the parent about the child
            self._send_bot_text(parent_id, f"/report {student_id}")
            docs = [d for d in main.telegram_bot.documents if d[0] == parent_id]
            self.assertEqual(len(docs), 1, main.telegram_bot.documents)
            self.assertTrue(docs[0][1].startswith(b"%PDF-"))
            self.assertIn(str(student_id), docs[0][2])
            # /curfew for the parent (no windows configured)
            self._send_bot_text(parent_id, "/curfew")
            texts = [message[1] for message in main.telegram_bot.messages if message[0] == parent_id]
            self.assertTrue(any("curfew" in text.lower() for text in texts), texts)
        finally:
            main.telegram_bot = original
            main.db.execute("DELETE FROM student_links WHERE parent_id = ? OR student_id = ?", (parent_id, student_id))
            main.db.execute("DELETE FROM quiz_attempts WHERE telegram_id = ?", (student_id,))


class _DownRedis:
    """Stub that behaves like Redis when the server is unreachable."""

    def get(self, key):
        raise main.redis.ConnectionError("Redis is down")

    def setex(self, key, ttl, value):
        raise main.redis.ConnectionError("Redis is down")

    def scan_iter(self, match=None):
        raise main.redis.ConnectionError("Redis is down")

    def delete(self, *keys):
        raise main.redis.ConnectionError("Redis is down")

    def zremrangebyscore(self, key, lo, hi):
        raise main.redis.ConnectionError("Redis is down")

    def zadd(self, key, mapping):
        raise main.redis.ConnectionError("Redis is down")

    def zcard(self, key):
        raise main.redis.ConnectionError("Redis is down")

    def expire(self, key, ttl):
        raise main.redis.ConnectionError("Redis is down")


class _FakeRedis:
    """Minimal in-memory Redis covering the operations main.py uses."""

    def __init__(self) -> None:
        self.strings: dict = {}
        self.zsets: dict = {}
        self.deleted: list = []

    def get(self, key):
        return self.strings.get(key)

    def setex(self, key, ttl, value):
        self.strings[key] = value

    def scan_iter(self, match=None):
        import fnmatch

        pattern = (match or "*").replace("*", "")
        if match and match.endswith("*"):
            for key in list(self.zsets) + list(self.strings):
                if key.startswith(pattern):
                    yield key
        else:
            yield from [k for k in list(self.zsets) + list(self.strings) if fnmatch.fnmatch(k, match or "*")]

    def delete(self, *keys):
        removed = 0
        for key in keys:
            if self.strings.pop(key, None) is not None or self.zsets.pop(key, None) is not None:
                removed += 1
        self.deleted.extend(keys)
        return removed

    def zremrangebyscore(self, key, lo, hi):
        members = self.zsets.get(key, {})
        for member, score in list(members.items()):
            if score <= hi:
                del members[member]

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def expire(self, key, ttl):
        pass


def seed_exam_question(exam_type: str, subject: str, topic: str, index: int) -> int:
    """Insert an exam-bank question with a unique text and return its id."""
    text = f"Exam engine sample {exam_type} {subject} {topic} number {index}: 2 + {index} = ?"
    main.db.execute(
        "INSERT INTO question_bank "
        "(exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            exam_type,
            subject,
            topic,
            "SS3",
            text,
            json.dumps([str(index), str(index + 2), str(index + 3), str(index + 4)]),
            str(index + 2),
            "Reasonable explanation.",
            "Easy",
        ),
    )
    row = main.db.fetch_one(
        "SELECT id FROM question_bank WHERE question_text = ? ORDER BY id DESC LIMIT 1", (text,)
    )
    return int(row["id"])


def correct_answer_for(question_id: int) -> str:
    row = main.db.fetch_one("SELECT correct_answer FROM question_bank WHERE id = ?", (question_id,))
    return row["correct_answer"]


class ExamEngineTests(unittest.TestCase):
    """Interactive exam engine: flow, persistence, recovery, adaptive mode."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._client_context = TestClient(main.app)
        cls.client = cls._client_context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._client_context.__exit__(None, None, None)

    def _headers(self, telegram_id: int) -> dict:
        return {"X-Dev-User": str(telegram_id)}

    def _start_exam(self, telegram_id: int, subject: str, num: int = 4, minutes: int = 5, mode: str = "standard") -> dict:
        response = self.client.post(
            "/api/v1/exam/start",
            json={
                "exam_type": "JAMB",
                "subject": subject,
                "num_questions": num,
                "duration_minutes": minutes,
                "mode": mode,
            },
            headers=self._headers(telegram_id),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _answer_current(self, telegram_id: int, session_id: str, confidence: str = "high") -> dict:
        state = self.client.get(
            f"/api/v1/exam/{session_id}", headers=self._headers(telegram_id)
        ).json()
        question = state["question"]
        answer = correct_answer_for(question["question_id"])
        response = self.client.post(
            f"/api/v1/exam/{session_id}/answer",
            json={
                "question_id": question["question_id"],
                "selected_answer": answer,
                "confidence_level": confidence,
                "time_spent_seconds": 12.5,
            },
            headers=self._headers(telegram_id),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _backdate(self, session_id: str, seconds: int) -> None:
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
        main.db.execute(
            "UPDATE quiz_sessions SET last_activity_at = ? WHERE session_id = ?", (stamp, session_id)
        )

    def _send_bot_text(self, chat_id: int, text: str) -> None:
        response = self.client.post(
            f"/webhook/telegram/{main.settings.TELEGRAM_BOT_TOKEN}",
            json={
                "update_id": int(time.time() * 1000) % 1000000,
                "message": {
                    "message_id": int(time.time()),
                    "date": int(time.time()),
                    "chat": {"id": chat_id, "type": "private"},
                    "from": {"id": chat_id, "first_name": "BotTester"},
                    "text": text,
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_exam_full_flow_persists_every_answer_and_weakness(self) -> None:
        for i in range(4):
            seed_exam_question("JAMB", "Mathematics", "FlowTopic", i)
        state = self._start_exam(88001, "Mathematics", num=4)
        self.assertEqual(state["total_questions"], 4)
        self.assertIsNotNone(state["question"])
        self.assertNotIn("correct_answer", state["question"])  # never leak answers
        session_id = state["session_id"]
        for step in range(4):
            state = self._answer_current(88001, session_id)
            if step < 3:
                self.assertFalse(state["finished"])
                self.assertEqual(state["current_index"], step + 1)
        self.assertTrue(state["finished"])
        self.assertEqual(state["reason"], "completed")
        self.assertEqual(state["score_pct"], 100.0)
        # Attempt persisted through the shared analytics pipeline.
        attempt = main.db.fetch_one(
            "SELECT id, subject, score, total FROM quiz_attempts WHERE telegram_id = ? ORDER BY id DESC LIMIT 1",
            (88001,),
        )
        self.assertIsNotNone(attempt)
        self.assertEqual(int(attempt["total"]), 4)
        self.assertEqual(float(attempt["score"]), 100.0)
        # Per-question telemetry rows + weakness vectors recomputed.
        telemetry = main.db.fetch_all(
            "SELECT * FROM answer_telemetry WHERE session_id = ?", (session_id,)
        )
        self.assertEqual(len(telemetry), 4)
        # Pool contamination (reference seed + other tests share JAMB/Mathematics)
        # makes random picks non-deterministic, so assert on the session's own topics.
        expected_topics = sorted({q["topic"] for q in self._session_questions(session_id)})
        placeholders = ", ".join("?" for _ in expected_topics)
        stats = main.db.fetch_one(
            f"SELECT SUM(correct_count) AS correct_count FROM student_topic_stats "
            f"WHERE telegram_id = ? AND topic IN ({placeholders})",
            (88001, *expected_topics),
        )
        self.assertIsNotNone(stats)
        self.assertEqual(int(stats["correct_count"]), 4)
        weakness = self.client.get("/api/v1/exam/weakness", headers=self._headers(88001)).json()
        topics = {row["topic"] for row in weakness["weakest_topics"]}
        for topic in expected_topics:
            self.assertIn(topic, topics)

    def test_exam_quit_autoscores_only_answered_questions(self) -> None:
        for i in range(4):
            seed_exam_question("JAMB", "Mathematics", "QuitTopic", i + 10)
        session_id = self._start_exam(88002, "Mathematics", num=4)["session_id"]
        self._answer_current(88002, session_id)
        self._answer_current(88002, session_id)
        result = self.client.post(
            f"/api/v1/exam/{session_id}/finish", headers=self._headers(88002)
        ).json()
        self.assertTrue(result["finished"])
        self.assertEqual(result["reason"], "user_quit")
        self.assertEqual(result["total_answered"], 2)
        self.assertEqual(result["unanswered"], 2)
        self.assertEqual(result["score_pct"], 100.0)
        attempt = main.db.fetch_one(
            "SELECT total, correct FROM quiz_attempts WHERE client_attempt_id = ?",
            (f"exam-{session_id}",),
        )
        self.assertIsNotNone(attempt)
        self.assertEqual(int(attempt["total"]), 2)
        self.assertEqual(int(attempt["correct"]), 2)
        # Double-finish is idempotent.
        again = self.client.post(
            f"/api/v1/exam/{session_id}/finish", headers=self._headers(88002)
        ).json()
        self.assertTrue(again["already_completed"])

    def test_exam_idle_pause_freezes_timer_then_resume_window(self) -> None:
        for i in range(4):
            seed_exam_question("JAMB", "Mathematics", "PauseTopic", i + 20)
        session_id = self._start_exam(88003, "Mathematics", num=4)["session_id"]
        self._answer_current(88003, session_id)
        # Socket drop: idle beyond 180s -> writes rejected, timer frozen.
        self._backdate(session_id, seconds=main.settings.EXAM_IDLE_PAUSE_SECONDS + 20)
        blocked = self.client.post(
            f"/api/v1/exam/{session_id}/answer",
            json={"question_id": 1, "selected_answer": "1"},
            headers=self._headers(88003),
        )
        self.assertEqual(blocked.status_code, 409)
        row = main.db.fetch_one("SELECT status FROM quiz_sessions WHERE session_id = ?", (session_id,))
        self.assertEqual(row["status"], "PAUSED")
        # Resume inside the window continues exactly where the student left off.
        resumed = self.client.post(
            f"/api/v1/exam/{session_id}/resume", headers=self._headers(88003)
        ).json()
        self.assertFalse(resumed["finished"])
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["current_index"], 1)
        self.assertEqual(resumed["answered"], 1)
        # Beyond the resume window the session auto-submits what was answered.
        self._backdate(session_id, seconds=200 * 60)
        expired = self.client.post(
            f"/api/v1/exam/{session_id}/resume", headers=self._headers(88003)
        ).json()
        self.assertTrue(expired["finished"])
        self.assertTrue(expired["auto_submitted"])
        self.assertEqual(expired["total_answered"], 1)

    def test_exam_timer_expiry_autosubmits_on_heartbeat(self) -> None:
        seed_exam_question("JAMB", "Mathematics", "TimerTopic", 30)
        seed_exam_question("JAMB", "Mathematics", "TimerTopic", 31)
        session_id = self._start_exam(88004, "Mathematics", num=2, minutes=1)["session_id"]
        self._answer_current(88004, session_id)
        main.db.execute(
            "UPDATE quiz_sessions SET time_remaining = 0 WHERE session_id = ?", (session_id,)
        )
        result = self.client.post(
            f"/api/v1/exam/{session_id}/heartbeat", headers=self._headers(88004)
        ).json()
        self.assertTrue(result["finished"])
        self.assertEqual(result["reason"], "time_expired")
        self.assertEqual(result["total_answered"], 1)

    def test_exam_adaptive_mode_pulls_weakest_topics(self) -> None:
        # Historical weakness: WeakTopicZ is bad, StrongTopicY is mastered.
        for topic, correct, incorrect in (("WeakTopicZ", 0, 10), ("StrongTopicY", 10, 0)):
            main.db.execute(
                "INSERT OR REPLACE INTO student_topic_stats "
                "(telegram_id, subject, topic, correct_count, incorrect_count, total_time_seconds, attempts, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (88005, "Further Maths", topic, correct, incorrect, 50.0, correct + incorrect),
            )
        for i in range(3):
            seed_exam_question("JAMB", "Further Maths", "WeakTopicZ", i + 40)
            seed_exam_question("JAMB", "Further Maths", "StrongTopicY", i + 50)
        state = self._start_exam(88005, "Further Maths", num=4, mode="adaptive")
        self.assertEqual(state["mode"], "adaptive")
        topics = [q["topic"] for q in self._session_questions(state["session_id"])]
        self.assertGreaterEqual(topics.count("WeakTopicZ"), 2)  # >= 30% of 4
        self.assertLess(topics.count("WeakTopicZ"), 4)  # rest spread over other topics

    def _session_questions(self, session_id: str) -> list:
        row = main.db.fetch_one("SELECT questions_json FROM quiz_sessions WHERE session_id = ?", (session_id,))
        return row["questions_json"] if isinstance(row["questions_json"], list) else json.loads(row["questions_json"])

    def test_exam_confidence_index_in_diagnostics(self) -> None:
        seed_exam_question("JAMB", "Mathematics", "ConfidenceTopic", 60)
        seed_exam_question("JAMB", "Mathematics", "ConfidenceTopic", 61)
        session_id = self._start_exam(88006, "Mathematics", num=2)["session_id"]
        self._answer_current(88006, session_id, confidence="low")  # correct but "guessed"
        questions = self._session_questions(session_id)
        correct_second = questions[1].get("correct_answer")
        wrong_answer = next(opt for opt in questions[1]["options"] if opt != correct_second)
        result = self.client.post(
            f"/api/v1/exam/{session_id}/answer",
            json={
                "question_id": questions[1]["id"],
                "selected_answer": wrong_answer,
                "confidence_level": "low",
                "time_spent_seconds": 5,
            },
            headers=self._headers(88006),
        )
        self.assertEqual(result.status_code, 200, result.text)
        payload = result.json()
        self.assertTrue(payload["finished"])
        diagnostics = payload["diagnostics"]
        self.assertEqual(diagnostics["confidence_index"]["low"]["total"], 2)
        self.assertEqual(diagnostics["lucky_guesses"], 1)  # correct despite low confidence
        self.assertIn("topic_breakdown", diagnostics)
        self.assertIn("avg_time_per_question", diagnostics)

    def _exam_callback(self, chat_id: int, data: str) -> None:
        response = self.client.post(
            f"/webhook/telegram/{main.settings.TELEGRAM_BOT_TOKEN}",
            json={
                "update_id": int(time.time() * 1000) % 1000000,
                "callback_query": {
                    "id": f"cbx-{abs(hash(data)) % 100000}-{int(time.time() * 1000) % 100000}",
                    "from": {"id": chat_id},
                    "message": {"chat": {"id": chat_id}},
                    "data": data,
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_exam_inquiz_action_buttons_pause_skip_flag(self) -> None:
        """Pause freezes the session, Skip advances the cursor, Flag bookmarks telemetry."""
        original = main.telegram_bot
        main.telegram_bot = FakeTelegramBot()
        chat_id = 88701
        try:
            for i in range(2):
                seed_exam_question("WAEC", "Chemistry", "ActionTopic", i + 80)
            self._send_bot_text(chat_id, "/exam")
            self._exam_callback(chat_id, "ex:type:WAEC")
            self._exam_callback(chat_id, "ex:sub:WAEC:Chemistry")
            wizard = main.EXAM_WIZARDS[chat_id]
            self._exam_callback(chat_id, f"ex:q:{wizard['mark']}:2")
            self._exam_callback(chat_id, f"ex:d:{wizard['mark']}:5")
            session_id = main.EXAM_ACTIVE[chat_id]["session_id"]
            mark = main.EXAM_ACTIVE[chat_id]["mark"]
            # Pause button -> session frozen in DB, timer stops, cursor advances to resume offer.
            self._exam_callback(chat_id, f"ex:pause:{mark}")
            self.assertNotIn(chat_id, main.EXAM_ACTIVE)
            row = main.db.fetch_one("SELECT status FROM quiz_sessions WHERE session_id = ?", (session_id,))
            self.assertEqual(row["status"], "PAUSED")
            # Resume from the inline button sent alongside the pause message.
            resume_data = None
            for msg in main.telegram_bot.messages:
                kwargs = msg[2] if isinstance(msg[2], dict) else {}
                keyboard = (kwargs.get("reply_markup") or {}).get("inline_keyboard", [])
                for row in keyboard:
                    for btn in row:
                        cb = btn.get("callback_data", "")
                        if cb.startswith("ex:resume:"):
                            resume_data = cb
            self.assertIsNotNone(resume_data, "pause should offer a Resume button")
            # Reconnect via the resume callback.
            main.EXAM_ACTIVE.pop(chat_id, None)
            active = {"session_id": session_id, "mark": "testmark", "telegram_id": chat_id}
            main.EXAM_ACTIVE[chat_id] = active
            state = main.resume_exam_session(main._bot_exam_user(chat_id), session_id)
            self.assertTrue(state.get("resumed"))
            # Skip this question; cursor advances without an answer recorded.
            idx = state["current_index"]
            self._exam_callback(chat_id, f"ex:skip:{active['mark']}:{idx}")
            updated = main._fetch_exam_session(session_id)
            self.assertEqual(updated["current_index"], idx + 1)
            skip_telemetry = main.db.fetch_all(
                "SELECT event_type FROM answer_telemetry WHERE session_id = ? AND event_type = 'skip'", (session_id,)
            )
            self.assertEqual(len(skip_telemetry), 1)
            # Flag bookmarks a telemetry row without advancing.
            flag_idx = updated["current_index"]
            self._exam_callback(chat_id, f"ex:flag:{active['mark']}:{flag_idx}")
            flagged = main.db.fetch_all(
                "SELECT event_type FROM answer_telemetry WHERE session_id = ? AND event_type = 'flag'", (session_id,)
            )
            self.assertEqual(len(flagged), 1)
            self.assertEqual(updated["current_index"], flag_idx)  # flag does not advance
        finally:
            main.telegram_bot = original
            main.EXAM_ACTIVE.pop(chat_id, None)
            main.EXAM_WIZARDS.pop(chat_id, None)

    def test_bot_exam_wizard_full_flow(self) -> None:
        """The 4-step Telegram wizard drives the same resilient engine."""
        original = main.telegram_bot
        main.telegram_bot = FakeTelegramBot()
        chat_id = 88501
        try:
            for i in range(2):
                seed_exam_question("WAEC", "Biology", "BotTopic", i + 70)
            self._send_bot_text(chat_id, "/exam")
            sent = [m[1] for m in main.telegram_bot.messages]
            self.assertTrue(any("Step 1/4" in text for text in sent), sent)
            self._exam_callback(chat_id, "ex:type:WAEC")
            sent = [m[1] for m in main.telegram_bot.messages]
            self.assertTrue(any("Step 2/4" in text for text in sent), sent)
            self._exam_callback(chat_id, "ex:sub:WAEC:Biology")
            wizard = main.EXAM_WIZARDS.get(chat_id)
            self.assertIsNotNone(wizard)
            self.assertEqual(wizard["exam_type"], "WAEC")
            self._exam_callback(chat_id, f"ex:q:{wizard['mark']}:2")
            self._exam_callback(chat_id, f"ex:d:{wizard['mark']}:5")
            self.assertIn(chat_id, main.EXAM_ACTIVE)
            session_id = main.EXAM_ACTIVE[chat_id]["session_id"]
            active = main.EXAM_ACTIVE[chat_id]
            # Answer both questions through inline buttons; each save persists.
            for step in range(2):
                session = main._fetch_exam_session(session_id)
                question = main._exam_public_question(session, step)
                answer = correct_answer_for(question["question_id"])
                letter = "ABCD"[question["options"].index(answer)]
                self._exam_callback(chat_id, f"ex:ans:{active['mark']}:{step}:{letter}")
            self.assertNotIn(chat_id, main.EXAM_ACTIVE)
            attempt = main.db.fetch_one(
                "SELECT score, total FROM quiz_attempts WHERE client_attempt_id = ?",
                (f"exam-{session_id}",),
            )
            self.assertIsNotNone(attempt, "bot exam attempt should be persisted")
            self.assertEqual(int(attempt["total"]), 2)
            self.assertEqual(float(attempt["score"]), 100.0)
            summary = [m[1] for m in main.telegram_bot.messages]
            self.assertTrue(any("exam submitted" in text for text in summary), summary)
        finally:
            main.telegram_bot = original
            main.EXAM_ACTIVE.pop(chat_id, None)
            main.EXAM_WIZARDS.pop(chat_id, None)


class CacheResilienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_cache = main.cache

    def tearDown(self) -> None:
        main.cache = self._original_cache

    def test_cache_falls_back_when_redis_down(self) -> None:
        """If Redis stops mid-run, cache helpers degrade gracefully instead of 500ing."""
        main.cache = _DownRedis()
        self.assertIsNone(main.cache_get_json("remedial:Mathematics:all:5"))
        main.cache_set_json("remedial:Mathematics:all:5", {"source": "database", "questions": []})
        self.assertEqual(main.cache_delete_prefix("remedial:"), 0)
        # Rate limiter fails OPEN so the bot keeps working without Redis.
        self.assertTrue(main.rate_limit_check("tg_webhook", "123", limit=1, window_seconds=60))

    def test_cache_hit_and_miss_logging(self) -> None:
        main.cache = _FakeRedis()
        with self.assertLogs("naija-scholar-v2", level="INFO") as captured:
            self.assertIsNone(main.cache_get_json("stats:student:1:all:0"))
            main.cache_set_json("stats:student:1:all:0", {"median": 70}, ttl_seconds=60)
            self.assertEqual(main.cache_get_json("stats:student:1:all:0"), {"median": 70})
        joined = "\n".join(captured.output)
        self.assertIn("CACHE_MISS key=stats:student:1:all:0", joined)
        self.assertIn("CACHE_HIT key=stats:student:1:all:0", joined)

    def test_invalidate_student_caches_purges_namespaces(self) -> None:
        fake = _FakeRedis()
        main.cache = fake
        for key in ("stats:student:42:all:0", "stats:student:99:all:0", "remedial:English:all:5", "benchmark:Mathematics"):
            fake.strings[key] = "{}"
        main.invalidate_student_caches(42)
        self.assertNotIn("stats:student:42:all:0", fake.strings)
        self.assertIn("stats:student:99:all:0", fake.strings)  # other students untouched
        self.assertNotIn("remedial:English:all:5", fake.strings)
        self.assertNotIn("benchmark:Mathematics", fake.strings)

    def test_sliding_window_rate_limit(self) -> None:
        fake = _FakeRedis()
        main.cache = fake
        # First two requests pass, third is blocked, and another identity is independent.
        self.assertTrue(main.rate_limit_check("tg_webhook", "777", limit=2, window_seconds=60))
        self.assertTrue(main.rate_limit_check("tg_webhook", "777", limit=2, window_seconds=60))
        self.assertFalse(main.rate_limit_check("tg_webhook", "777", limit=2, window_seconds=60))
        self.assertTrue(main.rate_limit_check("tg_webhook", "888", limit=2, window_seconds=60))
        # Old entries expire out of the window -> allowed again.
        for members in fake.zsets.values():
            for member in list(members):
                members[member] = 0.0
        self.assertTrue(main.rate_limit_check("tg_webhook", "777", limit=2, window_seconds=60))


class OnboardingAndTelemetryTests(unittest.TestCase):
    """Phase 1 (Role Onboarding) + Phase 2 (Hidden God-Mode Telemetry) coverage."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._client_context = TestClient(main.app)
        cls.client = cls._client_context.__enter__()
        cls._original_super_admin_ids = main.settings.SUPER_ADMIN_IDS
        cls._owner_id = 99001
        main.settings.SUPER_ADMIN_IDS = str(cls._owner_id)
        main.start_telemetry_worker()
        try:
            while not main._TELEMETRY_QUEUE.empty():
                main._TELEMETRY_QUEUE.get_nowait()
        except Exception:
            pass
        main.flush_telemetry_queue()

    @classmethod
    def tearDownClass(cls) -> None:
        main.flush_telemetry_queue()
        main.settings.SUPER_ADMIN_IDS = cls._original_super_admin_ids
        cls._client_context.__exit__(None, None, None)

    # ---- Phase 1: Role Onboarding -----------------------------------------

    def test_onboarding_start_returns_buttons_and_records_telemetry(self) -> None:
        before = self.client.get(
            "/api/v1/admin/telemetry/summary", headers={"X-Dev-User": str(self._owner_id)}
        )
        self.assertEqual(before.status_code, 200, before.text)
        baseline_events = before.json()["totals"]["events"]

        student_id = 11001
        self.client.get("/api/v1/auth/profile", headers={"X-Dev-User": str(student_id)})
        response = self.client.get("/api/v1/onboarding/start", headers={"X-Dev-User": str(student_id)})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("buttons", body)
        self.assertEqual(len(body["buttons"]), 4)
        roles = {b["role"] for b in body["buttons"]}
        self.assertEqual(
            roles,
            {main.ROLE_STUDENT, main.ROLE_PARENT, main.ROLE_TEACHER, main.ROLE_SCHOOL_ADMIN},
        )
        self.assertEqual(len(body["inline_keyboard"]), 4)
        for row in body["inline_keyboard"]:
            self.assertIn("callback_data", row[0])
            self.assertTrue(row[0]["callback_data"].startswith("onboard:"))
        self.assertFalse(body["onboarded"])
        self.assertEqual(body["current_role"], main.ROLE_STUDENT)

        main.flush_telemetry_queue()
        after = self.client.get(
            "/api/v1/admin/telemetry/summary", headers={"X-Dev-User": str(self._owner_id)}
        )
        self.assertGreaterEqual(after.json()["totals"]["events"], baseline_events + 1)

    def test_onboarding_select_persists_role_and_emits_event(self) -> None:
        student_id = 11002
        self.client.get("/api/v1/auth/profile", headers={"X-Dev-User": str(student_id)})
        for role in (main.ROLE_PARENT, main.ROLE_TEACHER, main.ROLE_SCHOOL_ADMIN, main.ROLE_STUDENT):
            response = self.client.post(
                "/api/v1/onboarding/select",
                json={"role": role},
                headers={"X-Dev-User": str(student_id)},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["status"], "ok")
            self.assertEqual(body["profile"]["role"], role)
            self.assertEqual(body["welcome"]["current_role"], role)
            headline = body["welcome"]["next_step"]["headline"].lower().replace("_", " ")
            self.assertIn(role.lower().replace("_", " "), headline)
            row = main.db.fetch_one(
                "SELECT role, onboarded FROM profiles WHERE telegram_id = ?",
                (student_id,),
            )
            self.assertEqual(row["role"], role)
            self.assertEqual(int(row["onboarded"]), 1)

    def test_onboarding_select_rejects_super_admin_self_assignment(self) -> None:
        rogue_id = 11003
        self.client.get("/api/v1/auth/profile", headers={"X-Dev-User": str(rogue_id)})
        response = self.client.post(
            "/api/v1/onboarding/select",
            json={"role": main.ROLE_SUPER_ADMIN},
            headers={"X-Dev-User": str(rogue_id)},
        )
        self.assertEqual(response.status_code, 403, response.text)
        row = main.db.fetch_one(
            "SELECT role FROM profiles WHERE telegram_id = ?", (rogue_id,)
        )
        self.assertNotEqual(row["role"], main.ROLE_SUPER_ADMIN)

    def test_onboarding_select_rejects_unknown_role(self) -> None:
        student_id = 11004
        self.client.get("/api/v1/auth/profile", headers={"X-Dev-User": str(student_id)})
        response = self.client.post(
            "/api/v1/onboarding/select",
            json={"role": "WIZARD"},
            headers={"X-Dev-User": str(student_id)},
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_onboarding_welcome_returns_role_specific_routing(self) -> None:
        teacher_id = 11005
        self.client.get("/api/v1/auth/profile", headers={"X-Dev-User": str(teacher_id)})
        self.client.post(
            "/api/v1/onboarding/select",
            json={"role": main.ROLE_TEACHER},
            headers={"X-Dev-User": str(teacher_id)},
        )
        response = self.client.get(
            "/api/v1/onboarding/welcome", headers={"X-Dev-User": str(teacher_id)}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["current_role"], main.ROLE_TEACHER)
        self.assertIn("Teacher mode unlocked", body["next_step"]["headline"])
        self.assertIn("/api/v1/access/generate", body["next_step"]["primary_endpoints"])

    # ---- Phase 2: Silent Telemetry Layer -----------------------------------

    def test_log_telemetry_is_non_blocking_and_silent(self) -> None:
        import time as _time
        main.flush_telemetry_queue()
        start = _time.perf_counter()
        for i in range(50):
            main.log_telemetry(
                11006, main.ROLE_STUDENT, "answer_delta",
                {"question_id": i, "selected": "A", "correct": True, "latency_ms": 100 + i},
            )
        elapsed_ms = (_time.perf_counter() - start) * 1000
        self.assertLess(elapsed_ms, 100, f"log_telemetry blocked for {elapsed_ms:.1f}ms")
        for bad in (None, "", 0, object()):
            try:
                main.log_telemetry(bad, bad, "test_garbage", {"bad": True})
            except Exception as exc:  # pragma: no cover
                self.fail(f"log_telemetry raised on garbage payload: {exc}")
        main.flush_telemetry_queue()
        rows = main.db.fetch_all(
            "SELECT event_type FROM system_telemetry WHERE user_id = ? ORDER BY id DESC LIMIT 5",
            ("11006",),
        )
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "answer_delta")

    def test_telemetry_worker_drains_queue_and_persists_rows(self) -> None:
        main.flush_telemetry_queue()
        marker = "drain_test_marker_xyz"
        for i in range(5):
            main.log_telemetry(11007, main.ROLE_PARENT, "parent_lookup", {"marker": marker, "i": i})
        main.flush_telemetry_queue()
        rows = main.db.fetch_all(
            "SELECT event_data FROM system_telemetry "
            "WHERE event_type = 'parent_lookup' AND user_id = '11007'"
        )
        self.assertEqual(len(rows), 5)
        for row in rows:
            payload = json.loads(row["event_data"]) if isinstance(row["event_data"], str) else row["event_data"]
            self.assertEqual(payload.get("marker"), marker)

    def test_telemetry_records_role_switches_and_teacher_roster_checks(self) -> None:
        main.flush_telemetry_queue()
        main.log_telemetry(11008, main.ROLE_TEACHER, "teacher_roster_check", {"class_code": "SS3-A"})
        main.log_telemetry(11009, main.ROLE_STUDENT, "role_changed", {"from": "STUDENT", "to": "PARENT"})
        main.flush_telemetry_queue()
        rows = main.db.fetch_all(
            "SELECT event_type, user_id FROM system_telemetry "
            "WHERE event_type IN ('teacher_roster_check', 'role_changed') "
            "AND user_id IN ('11008', '11009')"
        )
        events = {(r["user_id"], r["event_type"]) for r in rows}
        self.assertIn(("11008", "teacher_roster_check"), events)
        self.assertIn(("11009", "role_changed"), events)

    # ---- Phase 2: Super Admin Authorization & Gatekeeping ------------------

    def _assert_forbidden_for_role(self, role: str, path: str, method: str = "get", json_body=None) -> None:
        user_id = 13000 + abs(hash((role, path))) % 5000
        self.client.get("/api/v1/auth/profile", headers={"X-Dev-User": str(user_id)})
        main.set_profile_role(user_id, role, source="test_setup")
        headers = {"X-Dev-User": str(user_id)}
        if method == "get":
            response = self.client.get(path, headers=headers)
        else:
            response = self.client.post(path, json=json_body, headers=headers)
        self.assertEqual(
            response.status_code, 403,
            f"{role} should be 403 on {path}, got {response.status_code}: {response.text}",
        )

    def test_admin_telemetry_live_blocks_non_super_admin(self) -> None:
        for role in (main.ROLE_STUDENT, main.ROLE_PARENT, main.ROLE_TEACHER, main.ROLE_SCHOOL_ADMIN):
            self._assert_forbidden_for_role(role, "/api/v1/admin/telemetry/live")

    def test_admin_telemetry_user_blocks_non_super_admin(self) -> None:
        for role in (main.ROLE_STUDENT, main.ROLE_PARENT, main.ROLE_TEACHER, main.ROLE_SCHOOL_ADMIN):
            self._assert_forbidden_for_role(role, "/api/v1/admin/telemetry/user/12345")

    def test_admin_telemetry_summary_blocks_non_super_admin(self) -> None:
        for role in (main.ROLE_STUDENT, main.ROLE_PARENT, main.ROLE_TEACHER, main.ROLE_SCHOOL_ADMIN):
            self._assert_forbidden_for_role(role, "/api/v1/admin/telemetry/summary")

    def test_admin_telemetry_live_allows_super_admin_and_returns_events(self) -> None:
        main.flush_telemetry_queue()
        main.log_telemetry(11010, main.ROLE_STUDENT, "tab_switch", {"tab": "physics"})
        main.flush_telemetry_queue()
        response = self.client.get(
            "/api/v1/admin/telemetry/live?event_type=tab_switch&limit=50",
            headers={"X-Dev-User": str(self._owner_id)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("events", body)
        self.assertGreaterEqual(body["count"], 1)
        self.assertTrue(all(e["event_type"] == "tab_switch" for e in body["events"]))

    def test_admin_telemetry_user_returns_audit_and_weakness(self) -> None:
        main.flush_telemetry_queue()
        target = "11011"
        main.log_telemetry(int(target), main.ROLE_STUDENT, "answer_delta", {"q": 1})
        main.log_telemetry(int(target), main.ROLE_STUDENT, "idle_timeout", {"seconds": 90})
        main.flush_telemetry_queue()
        response = self.client.get(
            f"/api/v1/admin/telemetry/user/{target}",
            headers={"X-Dev-User": str(self._owner_id)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["user_id"], target)
        self.assertGreaterEqual(body["event_count"], 2)
        event_types = {e["event_type"] for e in body["events"]}
        self.assertIn("answer_delta", event_types)
        self.assertIn("idle_timeout", event_types)
        self.assertIn("weakness_timeline", body)
        self.assertIn("profile", body)

    def test_admin_telemetry_summary_returns_aggregated_metrics(self) -> None:
        main.flush_telemetry_queue()
        for i in range(3):
            main.log_telemetry(11012 + i, main.ROLE_STUDENT, "answer_delta", {"i": i})
        main.flush_telemetry_queue()
        response = self.client.get(
            "/api/v1/admin/telemetry/summary",
            headers={"X-Dev-User": str(self._owner_id)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("totals", body)
        self.assertIn("event_breakdown", body)
        self.assertIn("role_breakdown", body)
        self.assertIn("peak_hours", body)
        self.assertIn("drop_off", body)
        self.assertIn("avg_answer_latency_seconds", body)
        self.assertIsInstance(body["totals"]["events"], int)
        self.assertGreaterEqual(body["totals"]["events"], 3)

    def test_telegram_onboarding_callback_persists_role(self) -> None:
        target_id = 11013
        main.ensure_profile(target_id)
        fake_bot = FakeTelegramBot()
        with unittest.mock.patch.object(main, "telegram_bot", fake_bot):
            callback = {
                "id": "cbq_test_001",
                "from": {"id": target_id, "first_name": "Test"},
                "message": {"chat": {"id": 55555}},
                "data": "onboard:teacher",
            }
            main._handle_onboarding_callback(callback, 55555, "cbq_test_001", "onboard:teacher")
        row = main.db.fetch_one(
            "SELECT role, onboarded FROM profiles WHERE telegram_id = ?",
            (target_id,),
        )
        self.assertEqual(row["role"], main.ROLE_TEACHER)
        self.assertEqual(int(row["onboarded"]), 1)
        self.assertTrue(any("teacher" in (t or "").lower() for _, t in fake_bot.callbacks))
        self.assertTrue(any("Teacher mode unlocked" in body for _, body, _ in fake_bot.messages))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
