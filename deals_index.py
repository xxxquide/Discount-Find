"""
deals_index.py — локальный индекс всех акций.

Идея, которая делает бота быстрым и точным:
вместо того чтобы на каждый запрос ходить в поиск каждого магазина
(медленно + каждый магазин ищет по-своему плохо), бот один раз в 3 часа
скачивает КАТАЛОГ АКЦИЙ всех выбранных магазинов и складывает в SQLite.

Дальше всё происходит локально и мгновенно:
  • поиск «макарони» — по названиям в индексе с умным ранжированием;
  • «всі акції» — просто листаем индекс;
  • «топ знижок» — сортируем индекс по проценту;
  • сравнение цен между магазинами — группируем индекс.

Плюс: нагрузка на сайты падает в разы (один обход вместо десятков поисков).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path

from parsers.base import Product, StoreParser
from search_engine import search_index

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
INDEX_TTL = 3 * 60 * 60      # индекс живёт 3 часа
MAX_PER_STORE = 400          # сколько акций тянем с одного магазина


class DealsIndex:
    """Хранилище акций всех магазинов + поиск по нему."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.conn = sqlite3.connect(
            str(db_path or BASE_DIR / "cache.db"), check_same_thread=False
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deals_index (
                store_key  TEXT NOT NULL,
                payload    TEXT NOT NULL,   -- список товаров в JSON
                updated_at REAL NOT NULL,
                PRIMARY KEY (store_key)
            )
            """
        )
        self.conn.commit()
        self._refresh_lock = asyncio.Lock()

    # ── чтение/запись ───────────────────────────────────────────────────

    def get_store_deals(self, store_key: str) -> tuple[list[Product], float] | None:
        """Акции одного магазина из индекса + возраст записи в секундах."""
        row = self.conn.execute(
            "SELECT payload, updated_at FROM deals_index WHERE store_key = ?",
            (store_key,),
        ).fetchone()
        if row is None:
            return None
        payload, updated_at = row
        age = time.time() - updated_at
        products = [Product(**item) for item in json.loads(payload)]
        return products, age

    def save_store_deals(self, store_key: str, products: list[Product]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO deals_index (store_key, payload, updated_at) VALUES (?, ?, ?)",
            (store_key, json.dumps([p.to_dict() for p in products], ensure_ascii=False), time.time()),
        )
        self.conn.commit()

    def is_fresh(self, store_key: str) -> bool:
        entry = self.get_store_deals(store_key)
        return entry is not None and entry[1] < INDEX_TTL

    def age_of(self, store_key: str) -> float | None:
        entry = self.get_store_deals(store_key)
        return entry[1] if entry else None

    # ── обновление индекса ──────────────────────────────────────────────

    async def refresh_store(self, parser: StoreParser, force: bool = False) -> tuple[int, str | None]:
        """
        Обновляет акции одного магазина.
        Возвращает (сколько акций, текст ошибки или None).
        """
        if not force and self.is_fresh(parser.key):
            cached = self.get_store_deals(parser.key)
            return (len(cached[0]) if cached else 0), None

        if not getattr(parser, "supports_deals_catalog", False):
            return 0, None

        try:
            deals = await parser.fetch_all_deals(max_items=MAX_PER_STORE)
            self.save_store_deals(parser.key, deals)
            logger.info("Індекс акцій %s оновлено: %d позицій", parser.name, len(deals))
            return len(deals), None
        except Exception as e:  # noqa: BLE001 — падение одного магазина не критично
            logger.error("Не вдалося оновити акції %s: %s: %s", parser.name, type(e).__name__, e)
            return 0, parser.name

    async def refresh_all(
        self, parsers: list[StoreParser], force: bool = False, concurrency: int = 2
    ) -> tuple[int, list[str]]:
        """
        Обновляет индекс по всем магазинам (не больше `concurrency` одновременно).
        Возвращает (всего акций, список названий магазинов с ошибкой).
        """
        async with self._refresh_lock:      # не запускаем два обхода сразу
            semaphore = asyncio.Semaphore(concurrency)

            async def worker(parser: StoreParser):
                async with semaphore:
                    return await self.refresh_store(parser, force=force)

            results = await asyncio.gather(*(worker(p) for p in parsers))

        total = sum(count for count, _ in results)
        failed = [name for _, name in results if name]
        return total, failed

    # ── выборки для функций бота ────────────────────────────────────────

    def all_deals(self, store_keys: list[str]) -> list[Product]:
        """Все акции выбранных магазинов, отсортированные по % скидки."""
        deals: list[Product] = []
        for key in store_keys:
            entry = self.get_store_deals(key)
            if entry:
                deals.extend(entry[0])
        deals.sort(key=lambda p: p.discount_pct, reverse=True)
        return deals

    def search(self, query: str, store_keys: list[str], limit: int = 40) -> list[Product]:
        """Умный поиск по индексу акций (мгновенный, без запросов к сайтам)."""
        return search_index(query, self.all_deals(store_keys), limit=limit)

    def top_deals(self, store_keys: list[str], limit: int = 15) -> list[Product]:
        """Топ самых больших скидок."""
        return self.all_deals(store_keys)[:limit]

    def stats(self, store_keys: list[str]) -> dict[str, int]:
        """Сколько акций у каждого магазина (для статуса)."""
        return {
            key: len(self.get_store_deals(key)[0]) if self.get_store_deals(key) else 0
            for key in store_keys
        }
