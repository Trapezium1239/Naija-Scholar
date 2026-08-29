from logging.config import fileConfig
import os
import sys

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import settings
import main

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Override sqlalchemy.url with environment variable or settings
db_url = os.environ.get("DATABASE_URL") or main.settings.DATABASE_URL
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)
else:
    # Fallback: build from individual settings
    from urllib.parse import quote_plus
    pg_url = f"postgresql://{main.settings.POSTGRES_USER}:{quote_plus(main.settings.POSTGRES_PASSWORD)}@{main.settings.POSTGRES_HOST}:{main.settings.POSTGRES_PORT}/{main.settings.POSTGRES_DB}"
    config.set_main_option("sqlalchemy.url", pg_url)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No SQLAlchemy ORM models - project uses raw SQL with psycopg2/sqlite3
# Set to None for manual migration scripts
target_metadata = None

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
