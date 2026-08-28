"""
PostgreSQL snapshot utility for Naija Scholar V2.

Dumps the live database to a plain-text .sql file (schema + data) that can be
restored instantly with:

    psql -U postgres -d naija_scholar -f backups/<dump>.sql

Usage:
    python backup_db.py                    # dump to backups/naija_scholar_YYYYmmdd_HHMMSS.sql
    python backup_db.py -o my_dump.sql     # custom output path
    python backup_db.py --keep 10          # keep the 10 newest dumps (default 20)

Falls back to a JSON export of the question bank if pg_dump is unavailable.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

APP_ROOT = Path(__file__).resolve().parent
BACKUP_DIR = APP_ROOT / "backups"


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


def db_conn_info() -> dict:
    url = os.getenv("DATABASE_URL", "")
    if url.startswith(("postgresql://", "postgres://")):
        rest = url.split("://", 1)[1]
        cred, _, hostpart = rest.rpartition("@")
        user, _, password = cred.partition(":")
        hostport, _, dbname = hostpart.partition("/")
        host, _, port = hostport.partition(":")
        return {
            "host": host or "localhost",
            "port": port or "5432",
            "user": user or "postgres",
            "password": password,
            "dbname": dbname or "postgres",
        }
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("POSTGRES_PORT", "5432"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", ""),
        "dbname": os.getenv("POSTGRES_DB", "postgres"),
    }

def find_pg_dump() -> Optional[str]:
    explicit = os.getenv("PG_DUMP")
    if explicit and Path(explicit).exists():
        return explicit
    on_path = shutil.which("pg_dump")
    if on_path:
        return on_path
    hits = sorted(glob.glob("C:\\Program Files\\PostgreSQL\\*\\bin\\pg_dump.exe"), reverse=True)
    return hits[0] if hits else None


def prune_old_dumps(keep: int) -> int:
    dumps = sorted(BACKUP_DIR.glob("naija_scholar_*.sql"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in dumps[keep:]:
        old.unlink()
        removed += 1
    return removed


def dump_with_pg_dump(pg_dump: str, conn: dict, out_path: Path) -> bool:
    env = dict(os.environ)
    if conn["password"]:
        env["PGPASSWORD"] = conn["password"]
    cmd = [
        pg_dump, "--no-owner", "--no-privileges",
        "-h", conn["host"], "-p", conn["port"], "-U", conn["user"],
        "-d", conn["dbname"], "-f", str(out_path),
    ]
    print(f"Running pg_dump -U {conn['user']} -d {conn['dbname']} ...")
    completed = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if completed.returncode != 0:
        print(f"pg_dump failed (exit {completed.returncode}): {completed.stderr.strip()}", file=sys.stderr)
        return False
    return True


def dump_question_bank_json(conn: dict, out_path: Path) -> bool:
    """Fallback: export the question bank to JSON when pg_dump is unavailable."""
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        print("psycopg2 not installed; cannot fall back to JSON export.", file=sys.stderr)
        return False
    try:
        db = psycopg2.connect(
            host=conn["host"], port=conn["port"], user=conn["user"],
            password=conn["password"], dbname=conn["dbname"], connect_timeout=10,
        )
    except Exception as exc:
        print(f"Could not connect to PostgreSQL: {exc}", file=sys.stderr)
        return False
    try:
        with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM question_bank ORDER BY exam_type, subject, topic, id")
            rows = [dict(r) for r in cur.fetchall()]
        out_path.write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
        print(f"JSON fallback export: {len(rows)} questions -> {out_path.name}")
        return True
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot the Naija Scholar PostgreSQL database")
    parser.add_argument("-o", "--output", help="Output .sql path (default backups/naija_scholar_<ts>.sql)")
    parser.add_argument("--keep", type=int, default=20, help="How many timestamped dumps to retain (default 20)")
    args = parser.parse_args()

    load_env_file()
    BACKUP_DIR.mkdir(exist_ok=True)
    conn = db_conn_info()

    if args.output:
        out_path = Path(args.output)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = BACKUP_DIR / f"naija_scholar_{stamp}.sql"

    ok = False
    pg_dump = find_pg_dump()
    if pg_dump:
        ok = dump_with_pg_dump(pg_dump, conn, out_path)
    else:
        print("pg_dump not found (set PG_DUMP or add PostgreSQL bin to PATH); trying JSON fallback.")

    if not ok and out_path.suffix != ".json":
        out_path = out_path.with_suffix(".json")
        ok = dump_question_bank_json(conn, out_path)

    if not ok:
        return 1

    size_kb = out_path.stat().st_size / 1024
    print(f"Backup complete: {out_path} ({size_kb:.1f} KB)")
    pruned = prune_old_dumps(max(1, args.keep))
    if pruned:
        print(f"Pruned {pruned} old dump(s); keeping the {args.keep} newest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

