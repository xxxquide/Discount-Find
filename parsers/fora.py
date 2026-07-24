"""
parsers/fora.py — Фора (fora.ua), магазини у Вінниці та Іллінцях.

⚠️ ВАЖНО про акции Фори (это была главная ошибка прошлой версии):

У Фори акции разложены по «наборах» (sets) — это и есть те самые фильтры
на странице https://fora.ua/all-offers, которые ты видел:
  «Ще більше знижок», «Лайк Ціна тижня», «Знижки онлайн»,
  «Завжди свіже», «Гуртова ціна», «Фора охолодила літо», «Власні ТМ»,
  «Національний Кешбек» и т. д.

Обычный поиск (`customFilter` без набора) ищет по ВСЕМУ каталогу
и почти не показывает скидки — поэтому бот находил всего пару позиций.
Правильный путь: передавать `sets: ["<id набору>"]`, и тогда приходят
именно акционные товары этого набора. Один и тот же товар может быть
в нескольких наборах — дубликаты убираем.

Полный список наборов магазина берём из GetPromotionCollection
(поля items + skuSets) — он свой для каждого филиала.

Точки входа:
  POST https://api.catalog.ecom.fora.ua/api/2.0/exec/EcomCatalogGlobal
    GetPromotionCollection — какие акции/наборы есть у магазина
    GetSimpleCatalogItems  — товары: набор (sets) и/или текст (customFilter)
    GetPickupFilials       — магазины города (businessId=4)

Филиалы (region.py): 3745 — Вінниця, Чорновола 29Б; 4142 — Іллінці.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from region import FORA_BUSINESS_ID, FORA_DEFAULT_FILIAL, FORA_MERCHANT_ID
from .base import DEFAULT_HEADERS, REQUEST_TIMEOUT, Product, StoreParser, apply_relevance

logger = logging.getLogger(__name__)

API_URL = "https://api.catalog.ecom.fora.ua/api/2.0/exec/EcomCatalogGlobal"
PRODUCT_URL = "https://fora.ua/product/{slug}"

DELIVERY_TYPE = 1        # самовивіз — ассортимент конкретного магазина
MAX_RESULTS = 50         # позиций на один запрос поиска
SET_PAGE_SIZE = 100      # позиций за одну страницу набора
MAX_PAGES_PER_SET = 3    # не больше 3 страниц на набор (300 позиций)
SEARCH_BATCH = 4         # сколько наборов опрашиваем одновременно при поиске
DEALS_BATCH = 3          # сколько наборов одновременно при сборе каталога

# Наборы, которые точно не про скидки (сервисные/тематические без цен)
SKIP_SETS = {"fora-recommends"}

HEADERS = dict(
    DEFAULT_HEADERS,
    Origin="https://fora.ua",
    Referer="https://fora.ua/all-offers",
)


class ForaParser(StoreParser):
    key = "fora"
    name = "Фора"
    emoji = "🟠"
    enabled = True
    supports_deals_catalog = True

    def __init__(self, filial_id: str | None = None) -> None:
        super().__init__()
        self.filial_id = int(filial_id or FORA_DEFAULT_FILIAL)
        self._sets_cache: list[str] | None = None

    # ── низкоуровневый вызов API ────────────────────────────────────────
    async def _call(self, method: str, data: dict) -> dict:
        body = {"method": method, "data": {"merchantId": FORA_MERCHANT_ID, **data}}
        async with httpx.AsyncClient(headers=HEADERS, timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(API_URL, json=body)
            response.raise_for_status()
            payload = response.json()
        error = payload.get("EComError") or {}
        if error.get("ErrorCode") not in (0, None):
            raise RuntimeError(f"Fora API: {error.get('ErrorMessage')}")
        return payload

    def _to_product(self, item: dict) -> Product | None:
        name = (item.get("name") or "").strip()
        price = item.get("price")
        slug = item.get("slug") or ""
        if not name or not price or not slug:
            return None
        old_price = item.get("oldPrice")
        return Product(
            store_key=self.key,
            store_name=self.name,
            store_emoji=self.emoji,
            title=name,
            new_price=float(price),
            old_price=float(old_price) if old_price else None,
            url=PRODUCT_URL.format(slug=slug),
            unit=(item.get("unit") or "").strip(),
            image_url=item.get("mainImage") or "",
        )

    # ── список акционных наборов магазина ───────────────────────────────
    async def _promo_sets(self) -> list[str]:
        """
        Все наборы акций нашего магазина (кэшируем на время работы бота).
        Это те самые фильтры «Акції» со страницы all-offers.
        """
        if self._sets_cache is not None:
            return self._sets_cache

        payload = await self._call("GetPromotionCollection", {
            "deliveryType": DELIVERY_TYPE,
            "filialId": self.filial_id,
        })
        ids: list[str] = []
        # items — промо-акции, skuSets — товарные наборы; нужны и те, и те
        for group in ("items", "skuSets"):
            for entry in (payload.get(group) or []):
                set_id = entry.get("id")
                if set_id and set_id not in ids and set_id not in SKIP_SETS:
                    ids.append(set_id)

        # исторически полезные наборы, которых иногда нет в ответе
        for extra in ("do-70-na-vlasni-marky", "znyzhky-onlain", "laik_tsina"):
            if extra not in ids:
                ids.append(extra)

        self._sets_cache = ids
        logger.info("Фора: знайдено %d наборів акцій", len(ids))
        return ids

    # ── поиск товара ────────────────────────────────────────────────────
    async def _search_once(self, query: str) -> list[Product]:
        """
        Ищем товар в акционных наборах (там живут скидки) И в общем каталоге
        (чтобы знать обычную цену, если акции нет).
        """
        sets = await self._promo_sets()
        found: dict[str, Product] = {}

        async def query_set(set_id: str | None) -> list[dict]:
            """Один запрос: с набором (акции) или без (общий каталог)."""
            data = {
                "customFilter": query,
                "deliveryType": DELIVERY_TYPE,
                "filialId": self.filial_id,
                "from": 1, "to": MAX_RESULTS,
                "From": 1, "To": MAX_RESULTS,
            }
            if set_id:
                data["sets"] = [set_id]
            try:
                payload = await self._call("GetSimpleCatalogItems", data)
                return payload.get("items") or []
            except Exception as e:  # noqa: BLE001 — один набор не критичен
                logger.debug("Фора: набір %s не відповів (%s)", set_id, e)
                return []

        # Запросы к наборам идут пачками по SEARCH_BATCH — так поиск
        # занимает секунды, а не минуту, но сайт не заваливаем.
        targets: list[str | None] = [None] + list(sets)   # None = общий каталог
        for i in range(0, len(targets), SEARCH_BATCH):
            batch = targets[i:i + SEARCH_BATCH]
            await self._wait_turn()
            for items in await asyncio.gather(*(query_set(s) for s in batch)):
                for raw in items:
                    product = self._to_product(raw)
                    if product and product.url not in found:
                        found[product.url] = product

        return apply_relevance(query, list(found.values()))

    # ── весь каталог акций ──────────────────────────────────────────────
    async def fetch_all_deals(self, max_items: int = 400) -> list[Product]:
        """
        Все акции магазина: обходим ВСЕ акционные наборы (те самые фильтры
        со страницы all-offers) и собираем позиции со скидкой.
        """
        sets = await self._promo_sets()
        collected: dict[str, Product] = {}

        async def fetch_set(set_id: str) -> list[Product]:
            """Все страницы одного набора."""
            out: list[Product] = []
            for page in range(MAX_PAGES_PER_SET):
                try:
                    payload = await self._call("GetSimpleCatalogItems", {
                        "customFilter": "",
                        "deliveryType": DELIVERY_TYPE,
                        "filialId": self.filial_id,
                        "sets": [set_id],
                        "from": page * SET_PAGE_SIZE + 1,
                        "to": (page + 1) * SET_PAGE_SIZE,
                        "From": page * SET_PAGE_SIZE + 1,
                        "To": (page + 1) * SET_PAGE_SIZE,
                    })
                except Exception as e:  # noqa: BLE001
                    logger.debug("Фора: набір %s стор. %d — %s", set_id, page, e)
                    break
                items = payload.get("items") or []
                if not items:
                    break
                for raw in items:
                    product = self._to_product(raw)
                    if product and product.has_discount:
                        out.append(product)
                if len(items) < SET_PAGE_SIZE:   # набор закончился
                    break
            return out

        # наборы обходим пачками — быстро, но без спама
        for i in range(0, len(sets), DEALS_BATCH):
            if len(collected) >= max_items:
                break
            batch = sets[i:i + DEALS_BATCH]
            await self._wait_turn()
            for products in await asyncio.gather(*(fetch_set(s) for s in batch)):
                for product in products:
                    if product.url not in collected:
                        collected[product.url] = product

        return list(collected.values())[:max_items]

    # ── справочник магазинов (для настроек) ─────────────────────────────
    @staticmethod
    async def list_stores(city: str) -> list[dict]:
        """Магазины Фори в городе, например city='м. Вінниця'."""
        body = {"method": "GetPickupFilials",
                "data": {"merchantId": FORA_MERCHANT_ID, "businessId": FORA_BUSINESS_ID,
                         "city": city, "lat": "", "lon": "", "distance": ""}}
        async with httpx.AsyncClient(headers=HEADERS, timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(API_URL, json=body)
            response.raise_for_status()
            return (response.json().get("items") or [])
