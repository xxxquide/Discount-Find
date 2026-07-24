"""
settings.py — пользовательские настройки (выбранные магазины).

Хранятся в той же SQLite-базе cache.db, но в отдельной таблице
user_settings (ключ → значение JSON). Настройка переживает
перезапуски бота.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

_SELECTED_KEY = "selected_stores"


class Settings:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.conn = sqlite3.connect(
            str(db_path or BASE_DIR / "cache.db"), check_same_thread=False
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS user_settings ("
            "  key   TEXT PRIMARY KEY,"
            "  value TEXT NOT NULL"
            ")"
        )
        self.conn.commit()

    # ── выбранные магазины ────────────────────────────────────────────────

    def get_selected_stores(self) -> list[str] | None:
        """
        Список ключей выбранных магазинов (например ['silpo', 'atb']).
        None — пользователь ещё ни разу не делал выбор (первый запуск).
        """
        row = self.conn.execute(
            "SELECT value FROM user_settings WHERE key = ?", (_SELECTED_KEY,)
        ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
            return value if isinstance(value, list) else None
        except json.JSONDecodeError:
            return None

    def set_selected_stores(self, store_keys: list[str]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO user_settings (key, value) VALUES (?, ?)",
            (_SELECTED_KEY, json.dumps(store_keys)),
        )
        self.conn.commit()
