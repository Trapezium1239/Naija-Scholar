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
import tempfile
import time
import unittest
import zlib
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

    def send_message(self, chat_id: int, text: str, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return {"message_id": len(self.messages)}

    def answer_callback_query(self, callback_query_id: str, text: str | None = None, show_alert: bool = False):
        self.callbacks.append((callback_query_id, text))
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


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NaijaScholarContracts)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
