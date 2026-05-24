"""Async SQLite database engine with migration support."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import aiosqlite

from kubeforge.config import settings

logger = logging.getLogger("kubeforge.db")

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    """Return the singleton database connection."""
    global _db
    if _db is None:
        _db = await _init_db()
    return _db


async def _init_db() -> aiosqlite.Connection:
    """Create connection, enable WAL, run migrations."""
    db_url = settings.database.url
    # Extract path from sqlite URL
    db_path = db_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")

    # Ensure parent directory exists
    os.makedirs(Path(db_path).parent, exist_ok=True)

    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row

    # SQLite pragmas for performance
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA busy_timeout=5000")

    # Run migrations with FK enforcement OFF (allows schema changes)
    await db.execute("PRAGMA foreign_keys=OFF")
    await _run_migrations(db)
    await db.execute("PRAGMA foreign_keys=ON")
    logger.info(f"Database initialized at {db_path}")
    return db


async def _run_migrations(db: aiosqlite.Connection) -> None:
    """Run pending SQL migrations from the migrations directory."""
    migrations_dir = Path(settings.migrations_dir)
    if not migrations_dir.exists():
        logger.warning(f"Migrations dir not found: {migrations_dir}")
        return

    # Track applied migrations
    await db.execute(
        "CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at TEXT DEFAULT (datetime('now')))"
    )
    await db.commit()

    cursor = await db.execute("SELECT name FROM _migrations")
    applied = {row[0] for row in await cursor.fetchall()}

    # Find and apply pending up-migrations
    migration_files = sorted(migrations_dir.glob("*.up.sql"))
    for mf in migration_files:
        if mf.name in applied:
            continue

        logger.info(f"Applying migration: {mf.name}")
        sql = mf.read_text(encoding="utf-8")

        # Execute each statement (split by ;)
        # Tolerate expected ALTER TABLE errors (duplicate column, missing
        # column, etc.) so migrations are idempotent across schema variants.
        for stmt in sql.split(";"):
            # Strip leading SQL comment lines so we can check if real SQL remains
            lines = [ln for ln in stmt.strip().splitlines() if not ln.strip().startswith("--")]
            cleaned = "\n".join(lines).strip()
            if not cleaned:
                continue
            try:
                await db.execute(stmt.strip())
            except Exception as exc:
                err = str(exc).lower()
                ignorable = (
                    "duplicate column" in err
                    or "no such column" in err
                    or "already exists" in err
                    or "no column named" in err
                )
                if ignorable:
                    logger.info(f"  Skipped (already applied): {stmt[:60]}… ({exc})")
                else:
                    raise

        await db.execute("INSERT INTO _migrations (name) VALUES (?)", (mf.name,))
        await db.commit()
        logger.info(f"Migration applied: {mf.name}")


async def close_db() -> None:
    """Close the database connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
        logger.info("Database connection closed")
