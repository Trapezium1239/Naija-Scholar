"""
Autonomous PostgreSQL seeder for Naija Scholar V2.

This script audits the live `question_bank` schema, migrates legacy columns into the
required structure, generates validated questions with Ollama when available, and
falls back to a local curated bank when the local model is slow or returns invalid JSON.
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import psycopg2
import psycopg2.extras
import requests

APP_ROOT = Path(__file__).resolve().parent
VALID_EXAM_TYPES = {"WAEC", "NECO", "JAMB", "BECE"}
VALID_DIFFICULTIES = {"Easy", "Medium", "Hard"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("naija-scholar-seeder")


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
    candidate = clean_text(value).title()
    return candidate if candidate in VALID_DIFFICULTIES else "Medium"


def ensure_stepwise_explanation(explanation: str, question_text: str, correct_answer: str) -> str:
    cleaned = (explanation or "").strip()
    if cleaned and "step" in cleaned.lower() and len(cleaned) >= 40:
        return cleaned
    return (
        f"Step 1: Read the question carefully and identify what is being asked. "
        f"Step 2: Use the relevant concept or formula needed to solve it. "
        f"Step 3: Compare the computed or inferred result with the answer options. "
        f"Step 4: Select `{correct_answer}` because it is the option that correctly resolves: {question_text}"
    )


def curated_question_bank() -> List[Dict[str, Any]]:
    return [
        {
            "exam_type": "WAEC",
            "subject": "Mathematics",
            "topic": "Quadratic Equations",
            "class_level": "SS2",
            "question_text": "Solve x² - 5x + 6 = 0.",
            "options": ["x = 2 or x = 3", "x = -2 or x = -3", "x = 1 or x = 6", "x = -1 or x = -6"],
            "correct_answer": "x = 2 or x = 3",
            "explanation": "Step 1: Factorise x² - 5x + 6 as (x - 2)(x - 3). Step 2: Set each factor equal to zero. Step 3: From x - 2 = 0, x = 2. From x - 3 = 0, x = 3. Step 4: Therefore the correct answer is x = 2 or x = 3.",
            "difficulty": "Medium",
        },
        {
            "exam_type": "WAEC",
            "subject": "Mathematics",
            "topic": "Quadratic Equations",
            "class_level": "SS2",
            "question_text": "Find the sum of the roots of x² - 7x + 10 = 0.",
            "options": ["-10", "5", "7", "10"],
            "correct_answer": "7",
            "explanation": "Step 1: Compare x² - 7x + 10 = 0 with ax² + bx + c = 0. Step 2: For a quadratic equation, the sum of the roots is -b/a. Step 3: Here a = 1 and b = -7, so -b/a = -(-7)/1. Step 4: Therefore the sum of the roots is 7.",
            "difficulty": "Medium",
        },
        {
            "exam_type": "WAEC",
            "subject": "English Language",
            "topic": "Lexis and Structure",
            "class_level": "SS2",
            "question_text": "Choose the option that best completes the sentence: The principal, together with the teachers, ___ in the hall.",
            "options": ["are", "were", "is", "have been"],
            "correct_answer": "is",
            "explanation": "Step 1: Identify the subject of the sentence. Step 2: The main subject is 'The principal', which is singular. Step 3: The phrase 'together with the teachers' does not change the number of the main subject. Step 4: A singular subject takes a singular verb, so 'is' is correct.",
            "difficulty": "Medium",
        },
        {
            "exam_type": "WAEC",
            "subject": "English Language",
            "topic": "Lexis and Structure",
            "class_level": "SS2",
            "question_text": "Choose the word nearest in meaning to 'diligent'.",
            "options": ["careless", "hardworking", "fearful", "silent"],
            "correct_answer": "hardworking",
            "explanation": "Step 1: Determine the meaning of the word 'diligent'. Step 2: 'Diligent' describes someone who works carefully and persistently. Step 3: Among the options, 'hardworking' matches that meaning best. Step 4: Therefore the correct answer is hardworking.",
            "difficulty": "Easy",
        },
        {
            "exam_type": "WAEC",
            "subject": "Physics",
            "topic": "Newton's Laws",
            "class_level": "SS1",
            "question_text": "A force of 20 N acts on a body of mass 4 kg. What is the acceleration of the body?",
            "options": ["4 m/s²", "5 m/s²", "16 m/s²", "80 m/s²"],
            "correct_answer": "5 m/s²",
            "explanation": "Step 1: Recall Newton's second law, F = ma. Step 2: Rearrange to make a the subject: a = F/m. Step 3: Substitute the values: a = 20/4. Step 4: This gives 5 m/s², so the correct answer is 5 m/s².",
            "difficulty": "Easy",
        },
        {
            "exam_type": "WAEC",
            "subject": "Physics",
            "topic": "Newton's Laws",
            "class_level": "SS1",
            "question_text": "Which law explains why a passenger jerks forward when a moving car stops suddenly?",
            "options": ["Newton's first law", "Newton's second law", "Newton's third law", "Law of gravitation"],
            "correct_answer": "Newton's first law",
            "explanation": "Step 1: Think about inertia, the tendency of a body to remain in its state of motion. Step 2: When the car stops, the passenger's body tends to keep moving forward. Step 3: This tendency is described by Newton's first law of motion. Step 4: Therefore the correct answer is Newton's first law.",
            "difficulty": "Easy",
        },
        {
            "exam_type": "NECO",
            "subject": "Mathematics",
            "topic": "Logarithms",
            "class_level": "SS2",
            "question_text": "Evaluate log₁₀ 1000.",
            "options": ["1", "2", "3", "10"],
            "correct_answer": "3",
            "explanation": "Step 1: Express 1000 as a power of 10. Step 2: 1000 = 10³. Step 3: Therefore log₁₀ 1000 is the exponent of 10 that gives 1000. Step 4: The exponent is 3, so the answer is 3.",
            "difficulty": "Easy",
        },
        {
            "exam_type": "NECO",
            "subject": "Mathematics",
            "topic": "Logarithms",
            "class_level": "SS2",
            "question_text": "Evaluate log₂ 8.",
            "options": ["2", "3", "4", "8"],
            "correct_answer": "3",
            "explanation": "Step 1: Express 8 as a power of 2. Step 2: 8 = 2³. Step 3: The logarithm asks for the exponent of 2 that gives 8. Step 4: Therefore log₂ 8 = 3.",
            "difficulty": "Easy",
        },
        {
            "exam_type": "NECO",
            "subject": "Biology",
            "topic": "Ecology",
            "class_level": "SS2",
            "question_text": "The feeding relationship among organisms in a habitat is best described as a?",
            "options": ["food web", "root system", "respiratory chain", "circulatory loop"],
            "correct_answer": "food web",
            "explanation": "Step 1: Think about the term used for interconnected feeding links in an ecosystem. Step 2: A single path is called a food chain, but multiple linked paths form a food web. Step 3: The question refers to feeding relationships among organisms generally. Step 4: Therefore the best answer is food web.",
            "difficulty": "Easy",
        },
        {
            "exam_type": "NECO",
            "subject": "Biology",
            "topic": "Ecology",
            "class_level": "SS2",
            "question_text": "Organisms that make their own food in an ecosystem are called?",
            "options": ["consumers", "decomposers", "producers", "parasites"],
            "correct_answer": "producers",
            "explanation": "Step 1: Recall that green plants manufacture food by photosynthesis. Step 2: Organisms that produce their own food are known as producers. Step 3: Consumers depend on other organisms for food. Step 4: Therefore the correct answer is producers.",
            "difficulty": "Easy",
        },
        {
            "exam_type": "NECO",
            "subject": "Chemistry",
            "topic": "Atomic Structure",
            "class_level": "SS1",
            "question_text": "What is the number of protons in an atom with atomic number 12?",
            "options": ["6", "12", "14", "24"],
            "correct_answer": "12",
            "explanation": "Step 1: Recall that the atomic number equals the number of protons in an atom. Step 2: The atomic number given is 12. Step 3: Therefore the proton number must also be 12. Step 4: The correct option is 12.",
            "difficulty": "Easy",
        },
        {
            "exam_type": "JAMB",
            "subject": "Mathematics",
            "topic": "Trigonometry",
            "class_level": "SS3",
            "question_text": "Find the value of sin 30°.",
            "options": ["0", "1/2", "√3/2", "1"],
            "correct_answer": "1/2",
            "explanation": "Step 1: Recall the standard trigonometric ratios for special angles. Step 2: For 30°, the sine value is 1/2. Step 3: Compare with the options given. Step 4: Therefore the correct answer is 1/2.",
            "difficulty": "Easy",
        },
        {
            "exam_type": "JAMB",
            "subject": "Chemistry",
            "topic": "Chemical Bonding",
            "class_level": "SS2",
            "question_text": "Which type of bond is formed by transfer of electrons from one atom to another?",
            "options": ["Covalent bond", "Hydrogen bond", "Ionic bond", "Metallic bond"],
            "correct_answer": "Ionic bond",
            "explanation": "Step 1: Recall the basic types of chemical bonding. Step 2: Electron transfer from one atom to another produces oppositely charged ions. Step 3: Attraction between these ions forms an ionic bond. Step 4: Therefore the correct answer is Ionic bond.",
            "difficulty": "Medium",
        },
        {
            "exam_type": "JAMB",
            "subject": "Chemistry",
            "topic": "Chemical Bonding",
            "class_level": "SS2",
            "question_text": "Which particles are shared in a covalent bond?",
            "options": ["protons", "neutrons", "electrons", "ions"],
            "correct_answer": "electrons",
            "explanation": "Step 1: In covalent bonding, atoms achieve stability by sharing rather than transferring particles. Step 2: The particles involved in bonding are electrons in the outermost shell. Step 3: Protons and neutrons remain in the nucleus. Step 4: Therefore the correct answer is electrons.",
            "difficulty": "Easy",
        },
        {
            "exam_type": "JAMB",
            "subject": "Biology",
            "topic": "Cell Structure",
            "class_level": "SS1",
            "question_text": "Which organelle is known as the powerhouse of the cell?",
            "options": ["Nucleus", "Ribosome", "Mitochondrion", "Golgi body"],
            "correct_answer": "Mitochondrion",
            "explanation": "Step 1: Identify the organelle responsible for releasing usable energy during respiration. Step 2: That organelle is the mitochondrion. Step 3: It is called the powerhouse because it generates ATP for cell activities. Step 4: Therefore the correct answer is Mitochondrion.",
            "difficulty": "Easy",
        },
        {
            "exam_type": "BECE",
            "subject": "Mathematics",
            "topic": "Fractions",
            "class_level": "JSS3",
            "question_text": "What is 3/4 + 1/8?",
            "options": ["4/12", "5/8", "7/8", "1"],
            "correct_answer": "7/8",
            "explanation": "Step 1: Convert the fractions to a common denominator of 8. Step 2: 3/4 becomes 6/8. Step 3: Add 6/8 and 1/8 to get 7/8. Step 4: Therefore the answer is 7/8.",
            "difficulty": "Easy",
        },
        {
            "exam_type": "BECE",
            "subject": "English Language",
            "topic": "Parts of Speech",
            "class_level": "JSS2",
            "question_text": "Identify the adjective in the sentence: The diligent pupil passed the test.",
            "options": ["pupil", "passed", "diligent", "test"],
            "correct_answer": "diligent",
            "explanation": "Step 1: An adjective describes or qualifies a noun. Step 2: In the sentence, the word describing the noun 'pupil' is 'diligent'. Step 3: The other options are a noun or a verb. Step 4: Therefore the adjective is diligent.",
            "difficulty": "Easy",
        },
        {
            "exam_type": "WAEC",
            "subject": "Chemistry",
            "topic": "Separation Techniques",
            "class_level": "SS1",
            "question_text": "Which method is most suitable for separating sand from water?",
            "options": ["Distillation", "Filtration", "Chromatography", "Evaporation"],
            "correct_answer": "Filtration",
            "explanation": "Step 1: Sand is an insoluble solid while water is a liquid. Step 2: The standard method for separating an insoluble solid from a liquid is filtration. Step 3: During filtration, sand remains on the filter paper while water passes through. Step 4: Therefore the correct answer is Filtration.",
            "difficulty": "Easy",
        },
    ]


QUESTION_BLUEPRINTS: List[Tuple[str, str, str, str]] = [
    ("WAEC", "Mathematics", "Quadratic Equations", "SS2"),
    ("WAEC", "English Language", "Lexis and Structure", "SS2"),
    ("WAEC", "Physics", "Newton's Laws", "SS1"),
    ("NECO", "Mathematics", "Logarithms", "SS2"),
    ("NECO", "Biology", "Ecology", "SS2"),
    ("JAMB", "Chemistry", "Chemical Bonding", "SS2"),
]


class PostgreSQLSeeder:
    def __init__(self) -> None:
        self.connection: psycopg2.extensions.connection | None = None
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
        preferred_model = os.getenv("OLLAMA_MODEL", "llama3.1")
        self.model_candidates = [
            preferred_model,
            "llama3.2:3b",
            "llama3.1",
            "qwen2.5-coder:14b",
            "qwen2.5-coder",
            "llama3.1",
        ]
        self.randomizer = random.Random(42)

    def connect(self) -> None:
        self.connection = psycopg2.connect(database_url())
        self.connection.autocommit = False
        logger.info("Connected to PostgreSQL using configured environment settings")

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def fetch_all(self, query: str, params: Sequence[Any] | None = None) -> List[Dict[str, Any]]:
        if self.connection is None:
            raise RuntimeError("Database connection is not initialized")
        with self.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params or ())
            return [dict(row) for row in cur.fetchall()]

    def fetch_one(self, query: str, params: Sequence[Any] | None = None) -> Dict[str, Any] | None:
        rows = self.fetch_all(query, params)
        return rows[0] if rows else None

    def execute(self, query: str, params: Sequence[Any] | None = None) -> None:
        if self.connection is None:
            raise RuntimeError("Database connection is not initialized")
        with self.connection.cursor() as cur:
            cur.execute(query, params or ())

    def ensure_question_bank_schema(self) -> None:
        logger.info("Auditing and hardening question_bank schema")
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS question_bank (
                id BIGSERIAL PRIMARY KEY,
                exam_type TEXT NOT NULL,
                subject TEXT NOT NULL,
                topic TEXT NOT NULL,
                class_level TEXT NOT NULL,
                question_text TEXT NOT NULL,
                options JSONB NOT NULL,
                correct_answer TEXT NOT NULL,
                explanation TEXT NOT NULL,
                difficulty TEXT NOT NULL
            )
            """
        )

        columns = {
            row["column_name"]: row["data_type"]
            for row in self.fetch_all(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'question_bank'
                ORDER BY ordinal_position
                """
            )
        }

        required_columns = {
            "exam_type": "TEXT",
            "subject": "TEXT",
            "topic": "TEXT",
            "class_level": "TEXT",
            "question_text": "TEXT",
            "options": "JSONB",
            "correct_answer": "TEXT",
            "explanation": "TEXT",
            "difficulty": "TEXT",
        }
        for column_name, column_type in required_columns.items():
            if column_name not in columns:
                self.execute(f"ALTER TABLE question_bank ADD COLUMN {column_name} {column_type}")
                logger.info("Added missing column: %s", column_name)

        legacy_columns = [
            "class_arm",
            "subtopic",
            "option_a",
            "option_b",
            "option_c",
            "option_d",
            "correct_option",
        ]
        for legacy_column in legacy_columns:
            if legacy_column in columns:
                self.execute(f"ALTER TABLE question_bank ALTER COLUMN {legacy_column} DROP NOT NULL")

        if "class_arm" in columns:
            self.execute(
                """
                UPDATE question_bank
                SET class_level = COALESCE(NULLIF(class_level, ''), NULLIF(class_arm, ''), 'SS2')
                WHERE class_level IS NULL OR class_level = ''
                """
            )
        self.execute(
            """
            UPDATE question_bank
            SET class_level = COALESCE(NULLIF(class_level, ''), 'SS2')
            WHERE class_level IS NULL OR class_level = ''
            """
        )

        if {"option_a", "option_b", "option_c", "option_d"}.issubset(columns):
            self.execute(
                """
                UPDATE question_bank
                SET options = COALESCE(
                    options,
                    jsonb_build_array(option_a, option_b, option_c, option_d)
                )
                WHERE options IS NULL
                """
            )
        self.execute(
            """
            UPDATE question_bank
            SET options = jsonb_build_array('Option A', 'Option B', 'Option C', 'Option D')
            WHERE options IS NULL
            """
        )

        if "correct_option" in columns:
            self.execute(
                """
                UPDATE question_bank
                SET correct_answer = COALESCE(
                    NULLIF(correct_answer, ''),
                    CASE UPPER(correct_option)
                        WHEN 'A' THEN options->>0
                        WHEN 'B' THEN options->>1
                        WHEN 'C' THEN options->>2
                        WHEN 'D' THEN options->>3
                        ELSE correct_option
                    END
                )
                WHERE correct_answer IS NULL OR correct_answer = ''
                """
            )
        self.execute(
            """
            UPDATE question_bank
            SET correct_answer = COALESCE(NULLIF(correct_answer, ''), options->>0)
            WHERE correct_answer IS NULL OR correct_answer = ''
            """
        )

        self.execute(
            """
            UPDATE question_bank
            SET exam_type = UPPER(TRIM(exam_type))
            WHERE exam_type IS NOT NULL
            """
        )
        self.execute(
            """
            UPDATE question_bank
            SET difficulty = INITCAP(TRIM(difficulty))
            WHERE difficulty IS NOT NULL
            """
        )
        self.execute(
            """
            UPDATE question_bank
            SET explanation = %s
            WHERE explanation IS NULL OR LENGTH(TRIM(explanation)) < 20
            """,
            (
                "Step 1: Read the question carefully. Step 2: Apply the relevant concept. "
                "Step 3: Compare the result with the answer options. Step 4: Choose the correct answer.",
            ),
        )

        self.execute(
            """
            UPDATE question_bank
            SET subject = COALESCE(NULLIF(subject, ''), 'General Studies')
            WHERE subject IS NULL OR subject = ''
            """
        )
        self.execute(
            """
            UPDATE question_bank
            SET topic = COALESCE(NULLIF(topic, ''), 'Core Concepts')
            WHERE topic IS NULL OR topic = ''
            """
        )
        self.execute(
            """
            UPDATE question_bank
            SET question_text = COALESCE(NULLIF(question_text, ''), 'Revision practice question')
            WHERE question_text IS NULL OR question_text = ''
            """
        )

        self.execute(
            """
            DELETE FROM question_bank a
            USING question_bank b
            WHERE a.ctid < b.ctid
              AND COALESCE(a.exam_type, '') = COALESCE(b.exam_type, '')
              AND COALESCE(a.subject, '') = COALESCE(b.subject, '')
              AND COALESCE(a.question_text, '') = COALESCE(b.question_text, '')
            """
        )

        for column_name in required_columns:
            self.execute(f"ALTER TABLE question_bank ALTER COLUMN {column_name} SET NOT NULL")

        self.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_question_bank_exam_subject_text
            ON question_bank (exam_type, subject, question_text)
            """
        )
        self.connection.commit()

    def schema_snapshot(self) -> List[Dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'question_bank'
            ORDER BY ordinal_position
            """
        )

    def prompt_for_batch(self, exam_type: str, subject: str, topic: str, class_level: str, batch_size: int) -> str:
        return f"""
Return only valid JSON matching this exact shape:
{{
  "questions": [
    {{
      "exam_type": "WAEC|NECO|JAMB|BECE",
      "subject": "string",
      "topic": "string",
      "class_level": "string",
      "question_text": "string",
      "options": ["opt1", "opt2", "opt3", "opt4"],
      "correct_answer": "must match one option exactly",
      "explanation": "Step 1: ... Step 2: ... Step 3: ... Step 4: ...",
      "difficulty": "Easy|Medium|Hard"
    }}
  ]
}}

Generate {batch_size} curriculum-aligned Nigerian exam multiple-choice questions for:
- exam_type: {exam_type}
- subject: {subject}
- topic: {topic}
- class_level: {class_level}

Rules:
- Make all questions unique.
- Include exactly 4 answer options for each question.
- `correct_answer` must exactly match one option string.
- `exam_type` must remain `{exam_type}`.
- `subject` must remain `{subject}`.
- `topic` must remain `{topic}`.
- `class_level` must remain `{class_level}`.
- `explanation` must be detailed and step-by-step.
- No markdown fences. No commentary. JSON only.
""".strip()

    def generate_with_ollama(
        self,
        exam_type: str,
        subject: str,
        topic: str,
        class_level: str,
        batch_size: int,
    ) -> List[Dict[str, Any]]:
        latency_threshold = int(os.getenv("OLLAMA_LATENCY_THRESHOLD_SECONDS", "300"))
        seen_models: set[str] = set()
        for model_name in self.model_candidates:
            if model_name in seen_models:
                continue
            seen_models.add(model_name)
            payload = {
                "model": model_name,
                "prompt": self.prompt_for_batch(exam_type, subject, topic, class_level, batch_size),
                "stream": False,
                "format": "json",
            }
            started = time.perf_counter()
            try:
                response = requests.post(self.ollama_url, json=payload, timeout=latency_threshold)
                elapsed = time.perf_counter() - started
                if elapsed > latency_threshold:
                    raise TimeoutError(f"Ollama latency {elapsed:.2f}s exceeded {latency_threshold}s threshold")
                response.raise_for_status()
                body = response.json()
                raw_payload = body.get("response", "").strip()
                parsed = json.loads(raw_payload)
                questions = parsed.get("questions", []) if isinstance(parsed, dict) else []
                validated = self.validate_questions(questions, exam_type=exam_type, subject=subject, topic=topic, class_level=class_level)
                if not validated:
                    raise ValueError("No valid questions returned by Ollama")
                logger.info(
                    "Primary engine succeeded with model=%s for %s/%s in %.2fs",
                    model_name,
                    exam_type,
                    subject,
                    elapsed,
                )
                return validated[:batch_size]
            except requests.Timeout as exc:
                raise RuntimeError(f"Ollama exceeded {latency_threshold}s latency threshold for model={model_name}") from exc
            except Exception as exc:
                logger.warning(
                    "Primary engine failed for model=%s (%s/%s): %s",
                    model_name,
                    exam_type,
                    subject,
                    exc,
                )
        raise RuntimeError("All Ollama model attempts failed or returned invalid JSON")

    def generate_with_moonshot(
        self,
        exam_type: str,
        subject: str,
        topic: str,
        class_level: str,
        batch_size: int,
    ) -> List[Dict[str, Any]]:
        api_key = os.getenv("MOONSHOT_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("MOONSHOT_API_KEY is not configured")
        base_url = os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1").rstrip("/")
        model_name = os.getenv("MOONSHOT_MODEL", "moonshot-v1-8k")
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "You generate curriculum-aligned Nigerian exam multiple-choice questions. Return only valid JSON matching the schema requested by the user."},
                {"role": "user", "content": self.prompt_for_batch(exam_type, subject, topic, class_level, batch_size)},
            ],
            "temperature": 0.7,
            "max_tokens": 4096,
        }
        started = time.perf_counter()
        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                timeout=120,
            )
            elapsed = time.perf_counter() - started
            response.raise_for_status()
            body = response.json()
            raw_payload = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
            parsed = json.loads(raw_payload)
            questions = parsed.get("questions", []) if isinstance(parsed, dict) else []
            validated = self.validate_questions(questions, exam_type=exam_type, subject=subject, topic=topic, class_level=class_level)
            if not validated:
                raise ValueError("No valid questions returned by Moonshot")
            logger.info(
                "Moonshot engine succeeded with model=%s for %s/%s in %.2fs",
                model_name,
                exam_type,
                subject,
                elapsed,
            )
            return validated[:batch_size]
        except requests.Timeout as exc:
            raise RuntimeError(f"Moonshot exceeded 120s latency threshold for model={model_name}") from exc
        except Exception as exc:
            raise RuntimeError(f"Moonshot generation failed for model={model_name}: {exc}") from exc

    def generate_from_local_bank(
        self,
        exam_type: str,
        subject: str,
        topic: str,
        class_level: str,
        batch_size: int,
    ) -> List[Dict[str, Any]]:
        matching = [
            question
            for question in curated_question_bank()
            if question["exam_type"] == exam_type and question["subject"] == subject
        ]
        if not matching:
            matching = [question for question in curated_question_bank() if question["exam_type"] == exam_type]
        if not matching:
            matching = curated_question_bank()

        selected_templates: List[Dict[str, Any]] = []
        if len(matching) >= batch_size:
            selected_templates.extend(self.randomizer.sample(matching, batch_size))
        else:
            selected_templates.extend(matching)
            while len(selected_templates) < batch_size:
                selected_templates.append(matching[len(selected_templates) % len(matching)])

        selected: List[Dict[str, Any]] = []
        for original_template in selected_templates:
            template = dict(original_template)
            if template["topic"] != topic:
                template["topic"] = topic
            if template["class_level"] != class_level:
                template["class_level"] = class_level
            template["question_text"] = clean_text(template["question_text"])
            selected.append(template)

        validated = self.validate_questions(selected, exam_type=exam_type, subject=subject, topic=topic, class_level=class_level)
        logger.info(
            "Fallback engine supplied %s validated questions for %s/%s",
            len(validated),
            exam_type,
            subject,
        )
        return validated[:batch_size]

    def validate_questions(
        self,
        questions: Iterable[Dict[str, Any]],
        *,
        exam_type: str | None = None,
        subject: str | None = None,
        topic: str | None = None,
        class_level: str | None = None,
    ) -> List[Dict[str, Any]]:
        validated: List[Dict[str, Any]] = []
        for item in questions:
            if not isinstance(item, dict):
                continue
            normalized_exam_type = clean_text(str(item.get("exam_type", exam_type or ""))).upper()
            if normalized_exam_type not in VALID_EXAM_TYPES:
                continue

            options = item.get("options")
            if not isinstance(options, list) or len(options) != 4:
                continue
            normalized_options = [clean_text(str(option)) for option in options]
            if any(not option for option in normalized_options):
                continue

            correct_answer = clean_text(str(item.get("correct_answer", "")))
            if correct_answer not in normalized_options:
                continue

            normalized_question = {
                "exam_type": normalized_exam_type,
                "subject": clean_text(str(item.get("subject", subject or ""))) or (subject or ""),
                "topic": clean_text(str(item.get("topic", topic or ""))) or (topic or ""),
                "class_level": clean_text(str(item.get("class_level", class_level or ""))) or (class_level or "SS2"),
                "question_text": clean_text(str(item.get("question_text", ""))),
                "options": normalized_options,
                "correct_answer": correct_answer,
                "explanation": ensure_stepwise_explanation(
                    str(item.get("explanation", "")),
                    clean_text(str(item.get("question_text", ""))),
                    correct_answer,
                ),
                "difficulty": normalize_difficulty(str(item.get("difficulty", "Medium"))),
            }
            if not normalized_question["subject"] or not normalized_question["topic"] or not normalized_question["question_text"]:
                continue
            validated.append(normalized_question)
        return validated

    def upsert_question_batch(self, questions: Sequence[Dict[str, Any]]) -> int:
        if self.connection is None:
            raise RuntimeError("Database connection is not initialized")
        inserted_count = 0
        started = time.perf_counter()
        query = """
            INSERT INTO question_bank
            (exam_type, subject, topic, class_level, question_text, options, correct_answer, explanation, difficulty)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (exam_type, subject, question_text) DO UPDATE
            SET topic = EXCLUDED.topic,
                class_level = EXCLUDED.class_level,
                options = EXCLUDED.options,
                correct_answer = EXCLUDED.correct_answer,
                explanation = EXCLUDED.explanation,
                difficulty = EXCLUDED.difficulty
        """
        for question in questions:
            try:
                with self.connection.cursor() as cur:
                    cur.execute(
                        query,
                        (
                            question["exam_type"],
                            question["subject"],
                            question["topic"],
                            question["class_level"],
                            question["question_text"],
                            psycopg2.extras.Json(question["options"]),
                            question["correct_answer"],
                            question["explanation"],
                            question["difficulty"],
                        ),
                    )
                self.connection.commit()
                inserted_count += 1
            except Exception as exc:
                self.connection.rollback()
                logger.error(
                    "Insertion failed for %s / %s / %s: %s",
                    question["exam_type"],
                    question["subject"],
                    question["question_text"][:60],
                    exc,
                )
        elapsed = time.perf_counter() - started
        logger.info(
            "Batch insertion status: %s/%s successful in %.2fs",
            inserted_count,
            len(questions),
            elapsed,
        )
        return inserted_count

    def grouped_counts(self) -> List[Dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT exam_type, subject, COUNT(*)::INT AS count
            FROM question_bank
            GROUP BY exam_type, subject
            ORDER BY exam_type, subject
            """
        )

    def seed(self) -> int:
        batch_size = 2
        total_successful = 0
        for exam_type, subject, topic, class_level in QUESTION_BLUEPRINTS:
            batch_started = time.perf_counter()
            existing = self.fetch_one(
                """
                SELECT COUNT(*)::INT AS count
                FROM question_bank
                WHERE exam_type = %s AND subject = %s AND topic = %s
                """,
                (exam_type, subject, topic),
            )
            if existing and int(existing.get("count", 0)) >= batch_size:
                logger.info(
                    "Blueprint already covered (count=%s) for exam_type=%s subject=%s topic=%s; continuing with next blueprint",
                    existing.get("count"),
                    exam_type,
                    subject,
                    topic,
                )
                continue
            engine_name = "moonshot"
            moonshot_error: Exception | None = None
            try:
                questions = self.generate_with_moonshot(exam_type, subject, topic, class_level, batch_size)
            except Exception as exc:
                moonshot_error = exc
            if moonshot_error is not None:
                logger.warning(
                    "Moonshot engine unavailable (%s), falling back to Ollama for %s/%s",
                    moonshot_error,
                    exam_type,
                    subject,
                )
                try:
                    questions = self.generate_with_ollama(exam_type, subject, topic, class_level, batch_size)
                    engine_name = "ollama"
                except Exception:
                    questions = self.generate_from_local_bank(exam_type, subject, topic, class_level, batch_size)
                    engine_name = "fallback"
            successful = self.upsert_question_batch(questions)
            total_successful += successful
            logger.info(
                "Seeded %s questions for exam_type=%s subject=%s using %s in %.2fs",
                successful,
                exam_type,
                subject,
                engine_name,
                time.perf_counter() - batch_started,
            )

        logger.info("Total questions processed successfully: %s", total_successful)
        for row in self.grouped_counts():
            logger.info(
                "Question totals | exam_type=%s | subject=%s | count=%s",
                row["exam_type"],
                row["subject"],
                row["count"],
            )
        return total_successful


def seed_once() -> int:
    load_env_file()
    seeder = PostgreSQLSeeder()
    try:
        seeder.connect()
        seeder.ensure_question_bank_schema()
        logger.info("Schema snapshot: %s", json.dumps(seeder.schema_snapshot()))
        return seeder.seed()
    finally:
        seeder.close()


def main() -> int:
    total = seed_once()
    logger.info("Autonomous seeder completed successfully (processed=%s)", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
