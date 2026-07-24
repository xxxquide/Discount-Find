"""
parsers/grosh.py — Грош (online.grosh.ua).

Грош — региональная сеть Вінниці й області, поэтому весь их ассортимент
и так «наш»: отдельная привязка магазина не нужна.

Платформа Salesbox, обычные GET-запросы:
  поиск:      /api/v4/companies/grosh/offers/search?name=<запрос>&lang=ua
  все акции:  /api/v4/companies/grosh/offers/filter?lang=ua&limit=…&offset=…

Поля товара:
  name, price (базовая цена), priceWithDiscount (текущая),
  discount (размер скидки в грн, 0 = нет), previewURL (фото), id
"""
from __future__ import annotations

import httpx

from region import GROSH_COMPANY_ID
from .base import DEFAULT_HEADERS, REQUEST_TIMEOUT, Product, StoreParser, apply_relevance

API_BASE = f"https://prod.salesbox.me/api/v4/companies/{GROSH_COMPANY_ID}"
SEARCH_URL = f"{API_BASE}/offers/search"
FILTER_URL = f"{API_BASE}/offers/filter"
PRODUCT_URL = "https://online.grosh.ua/offer/{offer_id}"

DEALS_PAGE_SIZE = 100
MAX_DEALS_PAGES = 5

HEADERS = dict(
    DEFAULT_HEADERS,
    Origin="https://online.grosh.ua",
    Referer="https://online.grosh.ua/",
)


class GroshParser(StoreParser):
    key = "grosh"
    name = "Грош"
    emoji = "🟣"
    enabled = True
    supports_deals_catalog = True

    def _to_product(self, item: dict) -> Product | None:
        name = (item.get("name") or "").strip()
        offer_id = item.get("id")
        base_price = item.get("price")
        new_price = item.get("priceWithDiscount") or base_price
        discount = item.get("discount") or 0

        if not name or not offer_id or not new_price:
            return None

        has_discount = bool(discount) and base_price and base_price > new_price
        return Product(
            store_key=self.key,
            store_name=self.name,
            store_emoji=self.emoji,
            title=name,
            new_price=float(new_price),
            old_price=float(base_price) if has_discount else None,
            url=PRODUCT_URL.format(offer_id=offer_id),
            image_url=item.get("previewURL") or "",
        )

    async def _get(self, url: str, params: dict) -> list[dict]:
        async with httpx.AsyncClient(headers=HEADERS, timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("offers") or data.get("items") or []
        return []

    async def _search_once(self, query: str) -> list[Product]:
        raw = await self._get(SEARCH_URL, {"name": query, "lang": "ua"})
        products = [p for p in (self._to_product(i) for i in raw) if p]
        return apply_relevance(query, products)

    async def fetch_all_deals(self, max_items: int = 300) -> list[Product]:
        """
        Все акционные позиции Гроша (discount > 0).

        Замечание: API Гроша игнорирует offset и на каждой странице отдаёт
        одно и то же, поэтому дубликаты отсеиваем по id товара и
        останавливаемся, как только новых позиций не приходит.
        """
        collected: list[Product] = []
        seen: set[str] = set()

        for page in range(MAX_DEALS_PAGES):
            if len(collected) >= max_items:
                break
            await self._wait_turn()
            raw = await self._get(FILTER_URL, {
                "lang": "ua",
                "limit": DEALS_PAGE_SIZE,
                "offset": page * DEALS_PAGE_SIZE,
                "page": page + 1,          # некоторые версии API ждут page
            })
            if not raw:
                break
            added = 0
            for item in raw:
                product = self._to_product(item)
                if product and product.has_discount and product.url not in seen:
                    seen.add(product.url)
                    collected.append(product)
                    added += 1
            if added == 0:      # страница повторилась — дальше листать бессмысленно
                break
        return collected[:max_items]
