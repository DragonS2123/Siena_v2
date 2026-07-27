"""Idempotent, transactional migrations for preserved user data."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path


def migrate_conversations(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(conversations)")}
        legacy_link_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_conversation_links'"
        ).fetchone() is not None
    needs_column = "model_override" not in columns
    if not needs_column and not legacy_link_table:
        return False
    backup = db_path.with_suffix(".pre-core-cleanup.bak")
    if not backup.exists():
        shutil.copy2(db_path, backup)
    with sqlite3.connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            if needs_column:
                connection.execute("ALTER TABLE conversations ADD COLUMN model_override TEXT")
            if legacy_link_table:
                connection.execute("DROP TABLE remote_conversation_links")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return True
