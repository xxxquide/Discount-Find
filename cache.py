"""
cache.py — кэш результатов поиска в SQLite.

Зачем: если один и тот же товар спрашивают повторно в течение 3 часов,
бот отвечает мгновенно из кэша и НЕ ходит на сайты магазинов ещё раз.
Это и быстрее, и вежливее к сайтам.

Устройство: одна таблица search_cache, ключ — пара (магазин, запрос).
Файл базы cache.db создаётся автоматически рядом с bot.py.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

# Сколько живёт запись кэша: 3 часа (в секундах)
TTL_SECONDS = 3 * 60 * 60

BASE_DIR = Path(__file__).resolve().parent


class Cache:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else BASE_DIR / "cache.db"
        # check_same_thread=False безопасно: пишем/читаем только из одного event loop
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_cache (
                store      TEXT NOT NULL,   -- ключ магазина, например 'silpo'
                query      TEXT NOT NULL,   -- нормализованный запрос, например 'макарони'
                payload    TEXT NOT NULL,   -- список товаров в JSON
                created_at REAL NOT NULL,   -- когда сохранили (unix-время)
                PRIMARY KEY (store, query)
            )
            """
        )
        self.conn.commit()

    @staticmethod
    def normalize(query: str) -> str:
        """'  Макарони ' и 'макарони' — один и тот же запрос."""
        return " ".join(query.lower().split())

    def get(self, store: str, query: str) -> tuple[list[dict], float] | None:
        """
        Возвращает (список_товаров, возраст_записи_в_секундах),
        если запись есть и ей меньше 3 часов. Иначе None.
        """
        row = self.conn.execute(
            "SELECT payload, created_at FROM search_cache WHERE store = ? AND query = ?",
            (store, self.normalize(query)),
        ).fetchone()
        if row is None:
            return None
        payload, created_at = row
        age = time.time() - created_at
        if age > TTL_SECONDS:
            return None  # запись устарела — пусть парсер сходит заново
        return json.loads(payload), age

    def set(self, store: str, query: str, products: list[dict]) -> None:
        """Сохраняет успешный результат поиска (в том числе пустой список —
        «скидок нет» тоже валидный ответ, его незачем перепроверять 3 часа)."""
        self.conn.execute(
            "INSERT OR REPLACE INTO search_cache (store, query, payload, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                store,
                self.normalize(query),
                json.dumps(products, ensure_ascii=False),
                time.time(),
            ),
        )
        self.conn.commit()

    def cleanup(self) -> None:
        """Удаляет устаревшие записи (вызывается при старте бота, чтобы файл не рос)."""
        self.conn.execute(
            "DELETE FROM search_cache WHERE created_at < ?", (time.time() - TTL_SECONDS,)
        )
        self.conn.commit()
