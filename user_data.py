"""
user_data.py — личные данные пользователя: список покупок и отслеживание.

Всё лежит в той же SQLite-базе cache.db:
  shopping_list — товары, которые ты часто покупаешь («молоко», «сир»…),
                  проверяются одной командой /list;
  watchlist     — товары, за которыми следим: когда появится скидка,
                  бот пришлёт уведомление;
  search_log    — последние запросы (для кнопки «повторити»).
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class UserData:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.conn = sqlite3.connect(
            str(db_path or BASE_DIR / "cache.db"), check_same_thread=False
        )
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS shopping_list (
                item       TEXT PRIMARY KEY,
                added_at   REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS watchlist (
                item          TEXT PRIMARY KEY,
                added_at      REAL NOT NULL,
                last_notified REAL DEFAULT 0,
                last_price    REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS search_log (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                query    TEXT NOT NULL,
                searched_at REAL NOT NULL
            );
            """
        )
        self.conn.commit()

    # ── список покупок ──────────────────────────────────────────────────

    def add_to_list(self, item: str) -> bool:
        """Добавляет товар. Возвращает False, если он уже в списке."""
        item = " ".join(item.lower().split())
        if not item:
            return False
        try:
            self.conn.execute(
                "INSERT INTO shopping_list (item, added_at) VALUES (?, ?)",
                (item, time.time()),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_from_list(self, item: str) -> bool:
        item = " ".join(item.lower().split())
        cur = self.conn.execute("DELETE FROM shopping_list WHERE item = ?", (item,))
        self.conn.commit()
        return cur.rowcount > 0

    def get_list(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT item FROM shopping_list ORDER BY added_at"
        ).fetchall()
        return [r[0] for r in rows]

    def clear_list(self) -> int:
        cur = self.conn.execute("DELETE FROM shopping_list")
        self.conn.commit()
        return cur.rowcount

    # ── отслеживание товаров ────────────────────────────────────────────

    def add_watch(self, item: str) -> bool:
        item = " ".join(item.lower().split())
        if not item:
            return False
        try:
            self.conn.execute(
                "INSERT INTO watchlist (item, added_at) VALUES (?, ?)",
                (item, time.time()),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_watch(self, item: str) -> bool:
        item = " ".join(item.lower().split())
        cur = self.conn.execute("DELETE FROM watchlist WHERE item = ?", (item,))
        self.conn.commit()
        return cur.rowcount > 0

    def get_watchlist(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT item FROM watchlist ORDER BY added_at"
        ).fetchall()
        return [r[0] for r in rows]

    def should_notify(self, item: str, price: float, cooldown: float = 12 * 3600) -> bool:
        """
        Стоит ли присылать уведомление: не чаще раза в 12 часов
        и только если цена изменилась (или уведомления ещё не было).
        """
        row = self.conn.execute(
            "SELECT last_notified, last_price FROM watchlist WHERE item = ?", (item,)
        ).fetchone()
        if row is None:
            return False
        last_notified, last_price = row
        if time.time() - (last_notified or 0) < cooldown:
            return False
        return abs((last_price or 0) - price) > 0.01

    def mark_notified(self, item: str, price: float) -> None:
        self.conn.execute(
            "UPDATE watchlist SET last_notified = ?, last_price = ? WHERE item = ?",
            (time.time(), price, item),
        )
        self.conn.commit()

    # ── история запросов ────────────────────────────────────────────────

    def log_search(self, query: str) -> None:
        self.conn.execute(
            "INSERT INTO search_log (query, searched_at) VALUES (?, ?)",
            (query, time.time()),
        )
        # держим только последние 100 записей
        self.conn.execute(
            "DELETE FROM search_log WHERE id NOT IN "
            "(SELECT id FROM search_log ORDER BY searched_at DESC LIMIT 100)"
        )
        self.conn.commit()

    def recent_searches(self, limit: int = 6) -> list[str]:
        """Последние уникальные запросы — для быстрых кнопок."""
        rows = self.conn.execute(
            "SELECT query, MAX(searched_at) AS t FROM search_log "
            "GROUP BY query ORDER BY t DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]
