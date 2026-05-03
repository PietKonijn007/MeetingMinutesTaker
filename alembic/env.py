"""Alembic environment configuration."""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Import ORM Base for autogenerate support
from meeting_minutes.system3.db import Base

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate
target_metadata = Base.metadata


def _override_url_from_app_config() -> None:
    """Make alembic point at the same DB the running app uses.

    ``alembic.ini`` keeps a relative default (``sqlite:///db/meetings.db``)
    so old installs and CI keep working — but if the user has moved the DB
    via ``storage.sqlite_path`` (now defaults to a per-user absolute path),
    we want migrations to land on *that* file, not whatever the relative
    path happens to resolve to from the current working directory.
    """
    try:
        from meeting_minutes.config import ConfigLoader, resolve_db_path
    except Exception:
        return  # app not importable — fall back to the ini value

    try:
        app_config = ConfigLoader.load_default()
        db_path = resolve_db_path(app_config.storage.sqlite_path)
        config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    except Exception as e:  # pragma: no cover — keep migrations runnable
        print(f"  Note: could not override sqlalchemy.url from app config: {e}")


_override_url_from_app_config()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
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
    """Run migrations in 'online' mode."""
    # Auto-backup before migration
    from pathlib import Path

    db_url = config.get_main_option("sqlalchemy.url")
    if db_url and db_url.startswith("sqlite:///"):
        db_path = Path(db_url.replace("sqlite:///", ""))
        if db_path.exists():
            try:
                from meeting_minutes.backup import backup_database

                backup_file = backup_database(db_path, "backups", prefix="pre_migration")
                print(f"  Auto-backed up database before migration: {backup_file.name}")
            except Exception as e:
                print(f"  Warning: Could not backup database: {e}")

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
