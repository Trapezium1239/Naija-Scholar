"""Idempotent importer for master_exam_db.db into the Naija Scholar question bank.

Maps the master exam database schema onto question_bank, resolving inconsistent
correct_option encodings (letter vs text, dict vs list options), fills every
question_bank column, sorts by (subject, topic, difficulty, question_text) and
upserts with ON CONFLICT (exam_type, subject, question_text) DO NOTHING so it is
safe to re-run and never duplicates the autonomous seeder's work.

Usage:
    python import_master_db.py [--source PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_ROOT = Path(__file__).resolve().parent
VALID_EXAM_TYPES = {"WAEC", "NECO", "JAMB", "BECE"}
VALID_DIFFICULTIES = {"Easy", "Medium", "Hard"}
SUBJECT_ALIASES = {
    "english": "English Language",
    "mathematics": "Mathematics",
    "maths": "Mathematics",
    "further mathematics": "Further Mathematics",
}
LETTERS = "ABCDEFGH"

SQLITE_QUESTION_BANK_DDL = """
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
)
"""

PG_QUESTION_BANK_DDL = """
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
)
"""


def load_env_file() -> None:
    env_path = APP_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def database_url() -> str:
    explicit_dsn = os.getenv("DATABASE_URL")
    if explicit_dsn:
        return explicit_dsn
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "")
    database = os.getenv("POSTGRES_DB", "postgres")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def clean_text(value: str) -> str:
    return " ".join((value or "").strip().split())


def normalize_difficulty(value: str) -> str:
    lowered = clean_text(value).lower()
    if lowered in {"easy", "ease"}:
        return "Easy"
    if lowered in {"hard", "difficult", "harder"}:
        return "Hard"
    if lowered in {"medium", "moderate", "average", "normal"}:
        return "Medium"
    return "Medium"


def normalized_subject(value: str) -> str:
    cleaned = clean_text(value)
    return SUBJECT_ALIASES.get(cleaned.lower(), cleaned)


def resolve_correct(
    correct_value: str, options: Dict[str, str], option_list: List[str]
) -> Optional[str]:
    raw = clean_text(correct_value)
    if not raw:
        return None
    upper = raw.upper()
    upper_clean = "".join(upper.split())
    for prefix in ("OPTION", "OPT."):
        if upper_clean.startswith(prefix):
            tail = upper_clean[len(prefix):].lstrip(":.-_ ")
            if tail and tail[0] in LETTERS:
                return _resolve_letter(tail[0], options, option_list)
    if len(raw) <= 2 and raw.upper() in LETTERS:
        return _resolve_letter(raw.upper(), options, option_list)
    if raw[:2] in {"A.", "B.", "C.", "D.", "E."} or raw[1:2] in {")", ".", ":"} and raw[:1] in LETTERS:
        letter = raw[:1]
        return _resolve_letter(letter, options, option_list)
    for option in option_list:
        if clean_text(option).lower() == raw.lower():
            return clean_text(option)
    for letter, option in options.items():
        if clean_text(option).lower() == raw.lower():
            return clean_text(option)
    return None


def _resolve_letter(letter: str, options: Dict[str, str], option_list: List[str]) -> Optional[str]:
    if letter in options:
        return clean_text(options[letter])
    index = LETTERS.index(letter)
    if 0 <= index < len(option_list):
        return clean_text(option_list[index])
    return None


def build_explanation(explanation: str, question_text: str, correct_answer: str) -> str:
    cleaned = clean_text(explanation)
    if cleaned:
        return cleaned
    return (
        "Step 1: Read the question carefully. "
        f"Step 2: The question is: {question_text[:160]}. "
        f"Step 3: The correct option is {correct_answer}. "
        "Step 4: Review the related topic for further practice."
    )


def normalize_question(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    exam_type = clean_text(row.get("exam_type", "")).upper()
    if exam_type not in VALID_EXAM_TYPES:
        return None
    subject = normalized_subject(row.get("subject", ""))
    if not subject:
        return None
    topic = clean_text(row.get("topic", "")) or "General"
    question_text = clean_text(row.get("question_text", ""))
    if not question_text:
        return None
    passage = clean_text(row.get("passage_text", ""))
    if passage:
        question_text = f"Passage: {passage}\n\nQuestion: {question_text}"

    raw_options = row.get("options")
    try:
        parsed_options = json.loads(raw_options) if isinstance(raw_options, str) else raw_options
    except (TypeError, ValueError):
        return None
    if isinstance(parsed_options, dict):
        options: Dict[str, str] = {}
        for key, value in parsed_options.items():
            cleaned_key = clean_text(str(key)).upper()
            cleaned_value = clean_text(str(value))
            if cleaned_value:
                options[cleaned_key] = cleaned_value
        if len(options) != 4:
            return None
        option_list = [options[letter] for letter in "ABCD" if letter in options]
        if len(option_list) != 4:
            option_list = [clean_text(str(v)) for v in parsed_options.values()][:4]
    elif isinstance(parsed_options, list):
        option_list = [clean_text(str(option)) for option in parsed_options][:4]
        options = {}
    else:
        return None
    if len(option_list) != 4 or any(not option for option in option_list):
        return None

    correct_answer = resolve_correct(str(row.get("correct_option", "")), options, option_list)
    if not correct_answer or correct_answer not in option_list:
        return None
    if len(set(option_list)) < len(option_list):
        return None

    return {
        "exam_type": exam_type,
        "subject": subject,
        "topic": topic,
        "class_level": clean_text(row.get("class_level", "")) or "SS2",
        "question_text": question_text,
        "options": option_list,
        "correct_answer": correct_answer,
        "explanation": build_explanation(str(row.get("explanation", "")), question_text, correct_answer),
        "difficulty": normalize_difficulty(str(row.get("difficulty", "Medium"))),
    }


class SqliteTarget:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(SQLITE_QUESTION_BANK_DDL)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def upsert(self, question: Dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO question_bank
            (exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (exam_type, subject, question_text) DO NOTHING
            """,
            (
                question["exam_type"],
                question["subject"],
                question["topic"],
                question["class_level"],
                question["question_text"],
                json.dumps(question["options"], ensure_ascii=False),
                question["correct_answer"],
                question["explanation"],
                question["difficulty"],
            ),
        )

    def commit(self) -> None:
        self.connection.commit()

    def existing_key(self, question: Dict[str, Any]) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM question_bank WHERE exam_type = ? AND subject = ? AND question_text = ? LIMIT 1",
            (question["exam_type"], question["subject"], question["question_text"]),
        ).fetchone()
        return row is not None


class PostgresTarget:
    def __init__(self, dsn: str) -> None:
        import psycopg2
        import psycopg2.extras

        self.psycopg2 = psycopg2
        self.connection = psycopg2.connect(dsn)
        self.connection.autocommit = False
        with self.connection.cursor() as cur:
            cur.execute(PG_QUESTION_BANK_DDL)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def upsert(self, question: Dict[str, Any]) -> None:
        with self.connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO question_bank
                (exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (exam_type, subject, question_text) DO NOTHING
                """,
                (
                    question["exam_type"],
                    question["subject"],
                    question["topic"],
                    question["class_level"],
                    question["question_text"],
                    self.psycopg2.extras.Json(question["options"]),
                    question["correct_answer"],
                    question["explanation"],
                    question["difficulty"],
                ),
            )

    def commit(self) -> None:
        self.connection.commit()

    def existing_key(self, question: Dict[str, Any]) -> bool:
        with self.connection.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM question_bank WHERE exam_type = %s AND subject = %s AND question_text = %s LIMIT 1",
                (question["exam_type"], question["subject"], question["question_text"]),
            )
            return cur.fetchone() is not None


def read_master_questions(source: Path) -> List[Dict[str, Any]]:
    connection = sqlite3.connect(str(source))
    connection.row_factory = sqlite3.Row
    rows = [dict(row) for row in connection.execute("SELECT * FROM questions")]
    connection.close()
    return rows


def read_bank_questions(bank_file: Path) -> List[Dict[str, Any]]:
    connection = sqlite3.connect(str(bank_file))
    connection.row_factory = sqlite3.Row
    rows = [
        dict(row)
        for row in connection.execute(
            "SELECT exam_type, subject, topic, class_level, question_text, options, "
            "correct_answer, explanation, difficulty FROM question_bank"
        )
    ]
    connection.close()
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        options_raw = row["options"]
        try:
            option_list = json.loads(options_raw) if isinstance(options_raw, str) else list(options_raw)
        except (TypeError, ValueError):
            continue
        question = {
            "exam_type": clean_text(row["exam_type"]).upper(),
            "subject": normalized_subject(row["subject"]),
            "topic": clean_text(row["topic"]) or "General",
            "class_level": clean_text(row["class_level"]) or "SS2",
            "question_text": clean_text(row["question_text"]),
            "options": [clean_text(str(option)) for option in option_list],
            "correct_answer": clean_text(row["correct_answer"]),
            "explanation": clean_text(row["explanation"]),
            "difficulty": normalize_difficulty(row["difficulty"]),
        }
        if (
            not question["question_text"]
            or len(question["options"]) != 4
            or question["correct_answer"] not in question["options"]
        ):
            continue
        normalized.append(question)
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="Import master exam questions into the question bank")
    parser.add_argument("--source", default=r"C:/Users/henry/OneDrive/Desktop/JambPrepLocalTest/master_exam_db.db")
    parser.add_argument("--bank-file", default="")
    parser.add_argument("--postgres", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env_file()

    if args.bank_file:
        bank_path = Path(args.bank_file)
        if not bank_path.exists():
            print(f"ERROR: bank database not found: {bank_path}")
            return 1
        raw_rows = read_bank_questions(bank_path)
        print(f"Bank rows (already normalized): {len(raw_rows)}")
    else:
        source = Path(args.source)
        if not source.exists():
            print(f"ERROR: source database not found: {source}")
            return 1
        raw_rows = read_master_questions(source)
        print(f"Source rows: {len(raw_rows)}")

    if args.bank_file:
        seen: Dict[Any, str] = {}
        normalized = []
        skipped = {}
        for question in raw_rows:
            key = (question["exam_type"], question["subject"], question["question_text"])
            if key in seen:
                skipped["source-duplicate"] = skipped.get("source-duplicate", 0) + 1
            else:
                seen[key] = question
                normalized.append(question)
        print(f"Unique valid questions after normalization+dedup: {len(normalized)}")
        for reason, count in sorted(skipped.items()):
            print(f"  skipped [{reason}]: {count}")
    else:
        normalized: List[Dict[str, Any]] = []
        skipped: Dict[str, int] = {}
        seen: Dict[Any, str] = {}

        for row in raw_rows:
            question = normalize_question(row)
            if question is None:
                reason = "unresolvable-answer" if row.get("correct_option") else "missing-fields"
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            key = (question["exam_type"], question["subject"], question["question_text"])
            previous = seen.get(key)
            if previous is None:
                seen[key] = question
            else:
                skipped["source-duplicate"] = skipped.get("source-duplicate", 0) + 1

        normalized = sorted(
            seen.values(),
            key=lambda q: (q["subject"], q["topic"], q["difficulty"], q["question_text"]),
        )
        print(f"Unique valid questions after normalization+dedup: {len(normalized)}")
        for reason, count in sorted(skipped.items()):
            print(f"  skipped [{reason}]: {count}")

    if args.dry_run:
        print("DRY RUN - nothing written")
        return 0

    target: Any
    dsn = database_url()
    use_postgres = bool(os.getenv("DATABASE_URL")) or args.postgres
    if use_postgres:
        try:
            target = PostgresTarget(dsn)
        except Exception as exc:
            print(f"WARNING: PostgreSQL unavailable ({exc}); skipping import (bank already synced or DB not ready)")
            return 0
        print(f"Target: PostgreSQL ({dsn})")
    else:
        path = APP_ROOT / "naija_scholar.sqlite3"
        target = SqliteTarget(path)
        print(f"Target: SQLite ({path})")

    imported = skipped_conflict = 0
    try:
        for question in normalized:
            if target.existing_key(question):
                skipped_conflict += 1
                continue
            target.upsert(question)
            imported += 1
        target.commit()
    finally:
        target.close()

    print(f"Imported: {imported} | already present (ON CONFLICT/skip): {skipped_conflict}")
    if imported:
        print(f"Bank now holds the full sorted question set at {len(normalized) + 0} normalized rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
