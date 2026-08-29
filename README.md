# Naija Scholar V2

**Property of Lighthouse Intel Academy**

Naija Scholar V2 is a FastAPI-powered backend for the Lighthouse Intel Academy Telegram study bot. It exposes a Telegram-facing HTTP API — auth, questions, quiz submissions, offline sync, access codes, and Paystack payments — plus an autonomous question-bank seeder for JAMB / WAEC / NECO content. No website or browser UI is shipped; the HTTP surface is bot-facing only.

## Features

- 🧠 **Adaptive drilling** — JAMB / WAEC / NECO questions with focus, tab-switch, and hesitation telemetry captured inside Telegram
- 📊 **Analytics engine** — student, class, and school-level stats, mastery breakdowns (recall / conceptual / problem-solving / speed), speed-accuracy trade-offs, outlier early warnings
- 👨‍👩‍👧 **Parent portal** — god-mode analytics, study curfews (study windows), micro-bounties, weekly guardian digests
- 🏫 **School administration** — school/class codes, RBAC (STUDENT / PARENT / TEACHER / SCHOOL_ADMIN / SUPER_ADMIN), assignment broadcasting
- 💳 **Paystack payments** — tuition, premium, parent and teacher premium, school quarterly fees; idempotent webhooks that unlock access codes
- 📄 **Exports** — mock exam papers (PDF + QR), PDF report cards, school report ZIP archives
- ♻️ **Autonomous question seeder** — `autonomous_seeder.py` audits and expands the question bank on a schedule (Ollama or cloud LLM driven, with a 3,702-question seed file built in)
- 🤖 **Native Telegram bot** — long-polling (dev) or webhook (production) with `/start`, `/quiz`, `/subjects`, `/me`, `/progress`, `/leaderboard`, `/report`, `/buy`, parent linking (`/linkchild`, `/mychildren`, `/child`, `/curfew`), `/help` + inline-button quiz flow that scores & persists attempts; command menu registered via `setMyCommands`
- 📡 **SSE live stream** — real-time event stream for the portal
- 🛡️ **Hardened API** — strict validation, atomic transactions, idempotent quiz submits, size-limited compressed sync payloads (2G-friendly)

## Telegram bot

The app runs the study bot when `TELEGRAM_BOT_ENABLED=true` and a valid
`TELEGRAM_BOT_TOKEN` is present. At startup it verifies the token with
`getMe`, then switches to **webhook mode** if `TELEGRAM_WEBHOOK_URL` is set, or
falls back to **long-polling** (`getUpdates` in a background thread).

The update endpoint is `POST /webhook/telegram/<token>` — Telegram posts every
update there in webhook mode. Check the live bot health via `GET /api/v1/bot/status`.

### Commands

| Command | Action |
|---|---|
| `/start` | Welcome menu + deep-link routing |
| `/start quiz_<Subject>` | Instantly start a 5-question drill (deep link) |
| `/start consult` / `assignment_<class>` / `ref_<code>` / `drill_<topic>` | Deep-link entry points |
| `/quiz <subject>` | Start a 5-question micro-drill (inline A/B/C/D buttons) |
| `/exam` | Full exam wizard: JAMB/WAEC/NECO → subject → # questions → duration |
| `/subjects` | List subjects available in the question bank |
| `/me` | Profile, role, premium & linking code |
| `/progress` | Personal analytics, JAMB prediction & weakest subject |
| `/leaderboard` | School league table (or global top students) |
| `/report [student_id]` | PDF progress report sent as a document (self or linked child) |
| `/buy [premium\|tuition]` | Paystack payment link for premium/tuition |
| `/linkchild <CODE>` | Link a child by their linking code (parent flow) |
| `/mychildren` | Linked children with average scores |
| `/child <id>` | Analytics for a linked child |
| `/curfew` | Study/curfew windows (parents) |
| `/cancel` | End the active quiz |
| `/help` | Show commands |

Quiz results are scored by the same engine as the HTTP API (`analyze_quiz`),
persisted to `quiz_attempts` / `question_responses`, and shown with a JAMB/WAEC
prediction + wrong-answer review.

## Interactive exam engine

A multi-step, crash-safe exam mode exposed both via the Telegram `/exam` wizard
and a REST surface (web app), sharing one state machine:

**Flow:** exam type (JAMB/WAEC/NECO) → subject → number of questions → total
duration → timed session with a ticking timer.

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/exam/start` | Launch a session (`exam_type`, `subject`, `num_questions`, `duration_minutes`, `mode=standard\|adaptive`) |
| `GET /api/v1/exam/{id}` | State probe (reconnect recovery; sanitized question, no answer leak) |
| `POST /api/v1/exam/{id}/answer` | Per-question submission — persisted *before* the next item is served |
| `POST /api/v1/exam/{id}/heartbeat` | Client heart-beat; charges time and auto-pauses on idle |
| `POST /api/v1/exam/{id}/pause` / `resume` | Manual pause; resume keeps answers + remaining time inside the window |
| `POST /api/v1/exam/{id}/finish` | Quit guard: auto-submit scored **only** on answered questions |
| `GET /api/v1/exam/weakness` | Underground tracker: weakest subjects/topics + cached profile vector |

**Resilience & recovery**

- Every answer is written to `answer_telemetry` immediately, then the
  `quiz_sessions` row advances (crash-safe ordering: a mid-write failure can
  duplicate an answer, never lose one).
- The timer is **server-side** (`time_remaining` + `last_activity_at`): a socket
  drop beyond `EXAM_IDLE_PAUSE_SECONDS` (default 180 s) freezes the clock and
  flags the session `PAUSED` — the idle gap is never charged.
- Resume is allowed within `EXAM_RESUME_WINDOW_MINUTES` (default 120); beyond
  that the session auto-submits what was answered.
- Timer expiry auto-submits on the next heart-beat/state probe; `/cancel` in the
  bot triggers the same automated scoring instead of discarding progress.

**Analytics ("underground tracker")**

- `time_spent_seconds`, `answer_changes`, skips and `confidence_level`
  (`high`/`medium`/`low`) are logged per question; low-confidence correct answers
  surface as `lucky_guesses` in the diagnostics.
- On completion a background worker (`compute_weakness_vectors`) rebuilds
  `student_topic_stats` (per-topic accuracy + average time) and caches weakest
  subjects/topics on `profiles.weakness_json` for Adaptive Weakness Rescue and
  guardian reporting.
- The completion payload includes topic-level accuracy %, average time per
  question, and recommended focus areas (topics under 60% accuracy).

**Adaptive Weakness Rescue:** `mode=adaptive` pulls ≥30% of the paper from the
student's historically weakest topics (`student_topic_stats`, falling back to
`question_responses` history for cold-start students).

## Tech stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn |
| Database | PostgreSQL (primary) with SQLite fallback |
| Cache | Redis (optional, gracefully degrades) |
| PDF/QR | fpdf2 + qrcode + Pillow |
| Payments | Paystack |

## Local setup

### Windows

```bat
setup.bat
```

### macOS / Linux

```bash
./setup.sh
```

The setup scripts create a virtualenv, install `requirements.txt`, run the verification suite (`test_all.py`) and — if everything passes — boot the API server on port `8000` plus the autonomous seeder.

### Manual setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in real values
python main.py                   # API on http://localhost:8000
python autonomous_seeder.py      # optional: question-bank seeder
python import_master_db.py       # optional: import legacy master_exam_db.db
```

## Environment variables

Copy `.env.example` to `.env` and fill in the values. Key settings:

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather token for the study bot |
| `TELEGRAM_BOT_USERNAME` | Public bot username (no `@`) used for all deep links & QR codes |
| `TELEGRAM_BOT_ENABLED` | Master switch for the bot runtime |
| `TELEGRAM_POLLING_ENABLED` | Use long-polling when no webhook is configured |
| `TELEGRAM_WEBHOOK_URL` | Public HTTPS base (e.g. `https://naija-scholar.onrender.com`) to enable webhook mode |
| `APP_BASE_URL` | Base URL for referral & paystack callback links |
| `PAYSTACK_SECRET_KEY` | Paystack secret key (test keys start `sk_test_`) |
| `PAYSTACK_WEBHOOK_SECRET` | Paystack webhook signature key |
| `DATABASE_URL` | Hosted Postgres DSN — takes precedence over `POSTGRES_*` when set |
| `POSTGRES_HOST/PORT/USER/PASSWORD/DB` | Local Postgres connection |
| `SQLITE_PATH` / `ENABLE_SQLITE_FALLBACK` | SQLite fallback location and toggle |
| `REDIS_URL` | Redis for caching (optional) |
| `SEED_ENABLED` / `SEED_INTERVAL_HOURS` | Autonomous seeder schedule |
| `RATE_LIMIT_WEBHOOK_PER_MIN` / `RATE_LIMIT_WINDOW_SECONDS` | Telegram webhook sliding-window rate limit (default 30 / 60s, fails open when Redis is down) |
| `SUPER_ADMIN_IDS` | Comma-separated Telegram IDs with full god-mode access |
| `MOONSHOT_API_KEY` / `OLLAMA_URL` / `OLLAMA_MODEL` | LLM model sources for question generation |

## Running tests

```bash
python test_all.py
```

The suite (61 tests) boots the app against a throwaway SQLite database and verifies auth,
payments, RBAC, analytics, exports, SSE, idempotency contracts, cache fallback,
invalidation, and rate limiting.

## Database backups

```bash
python backup_db.py                 # -> backups/naija_scholar_YYYYmmdd_HHMMSS.sql
python backup_db.py -o my_dump.sql  # custom path
python backup_db.py --keep 5        # retain only the 5 newest dumps
```

Uses `pg_dump` (auto-found in `C:\Program Files\PostgreSQL\*\bin`, or set `PG_DUMP`).
If `pg_dump` is unavailable it falls back to a JSON export of the question bank.
Restore a dump with:

```bash
psql -U postgres -d naija_scholar -f backups/<dump>.sql
```

## Cache operations

- **Inspect/purge visually:** install the *Redis for VS Code* extension
  (`code --install-extension redis.redis-for-vscode`) and connect to `localhost:6379`.
  Namespaces used by the app: `remedial:*`, `stats:student:*`, `benchmark:*`, `ratelimit:*`.
- **Hit/miss tracking:** every cache read logs `CACHE_HIT key=…` / `CACHE_MISS key=…`
  (and `CACHE_ERROR …` when Redis is unreachable) to the console.
- **Invalidation:** `invalidate_student_caches(telegram_id)` purges a student's analytics
  namespaces; it runs automatically after every persisted quiz attempt (API and bot flows).
  Ad-hoc purge from a shell: `docker exec naija-redis redis-cli --scan --pattern 'remedial:*'`.
- **Resilience:** all cache helpers catch `ConnectionError`/`TimeoutError` and fall back to
  Postgres — a Redis outage degrades performance, never availability. The rate limiter
  **fails open** under the same conditions.

## Seeder & reload note

`python main.py` starts uvicorn with `reload=False`, so the 6-hour seeder loop is created
exactly once per process. Do **not** run uvicorn with `--reload` (a worker restart would
re-run startup hooks); run `python autonomous_seeder.py` manually alongside instead — its
upserts are idempotent, so overlap is harmless.

## Deployment (Render)

`render.yaml` defines the web service and a free managed Postgres database. On render:

1. Push to the `master` branch — Render builds with `pip install -r requirements.txt` and starts `uvicorn main:app`.
2. Set the `sync: false` secrets in the Render dashboard (they are intentionally not stored in the repo):
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_BOT_USERNAME`
   - `PAYSTACK_SECRET_KEY`
   - `PAYSTACK_WEBHOOK_SECRET`
   - `REDIS_URL`
   - `SUPER_ADMIN_IDS`
   - `MOONSHOT_API_KEY`
3. Health checks hit `/healthz` (returns DB mode, cache status, and server time).

## Security notes

- `.env` is gitignored — never commit real secrets.
- The token + username in this README's quickstart are placeholders; update them before any public deployment.
- If a Telegram/Paystack key was ever committed or shared, rotate it immediately via BotFather / Paystack dashboard.
- `X-Dev-User` auth bypass is only active in `ENVIRONMENT=development`.

## Repository layout

| File | Purpose |
|---|---|
| `main.py` | FastAPI application (schema, auth, all API endpoints, PDF/QR export) |
| `autonomous_seeder.py` | Background question-bank seeder for PostgreSQL |
| `backup_db.py` | pg_dump snapshot utility with JSON fallback and retention pruning |
| `import_master_db.py` | Idempotent importer for a legacy `master_exam_db.db` |
| `question_bank_seed.json` | 3,702 built-in JAMB/WAEC/NECO questions |
| `test_all.py` | Executable verification suite |
| `render.yaml` | Render blueprint (web service + Postgres) |
| `setup.bat` / `setup.sh` | One-shot local bootstrapping |