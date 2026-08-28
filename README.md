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
- ♻️ **Autonomous question seeder** — `autonomous_seeder.py` audits and expands the question bank on a schedule (Ollama or cloud LLM driven, with a 3,667-question seed file built in)
- 🤖 **Native Telegram bot** — long-polling (dev) or webhook (production) with `/start`, `/quiz`, `/subjects`, `/me`, `/help` + inline-button quiz flow that scores & persists attempts
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
| `/subjects` | List subjects available in the question bank |
| `/me` | Profile, role, premium & linking code |
| `/cancel` | End the active quiz |
| `/help` | Show commands |

Quiz results are scored by the same engine as the HTTP API (`analyze_quiz`),
persisted to `quiz_attempts` / `question_responses`, and shown with a JAMB/WAEC
prediction + wrong-answer review.

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
| `SUPER_ADMIN_IDS` | Comma-separated Telegram IDs with full god-mode access |
| `MOONSHOT_API_KEY` / `OLLAMA_URL` / `OLLAMA_MODEL` | LLM model sources for question generation |

## Running tests

```bash
python test_all.py
```

The suite (27 tests) boots the app against a throwaway SQLite database and verifies auth,
payments, RBAC, analytics, exports, SSE, and idempotency contracts.

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
| `import_master_db.py` | Idempotent importer for a legacy `master_exam_db.db` |
| `question_bank_seed.json` | 3,667 built-in JAMB/WAEC/NECO questions |
| `test_all.py` | Executable verification suite |
| `render.yaml` | Render blueprint (web service + Postgres) |
| `setup.bat` / `setup.sh` | One-shot local bootstrapping |