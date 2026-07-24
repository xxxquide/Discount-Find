"""
parsers/fora.py — Фора (fora.ua), магазини у Вінниці та Іллінцях.

Внутренний JSON API группы Fozzy, один POST-запрос:
  POST https://api.catalog.ecom.fora.ua/api/2.0/exec/EcomCatalogGlobal
  {"method": "GetSimpleCatalogItems",
   "data": {"merchantId": 2, "filialId": <магазин>, "customFilter": "<запрос>", …}}

filialId — конкретный магазин (см. region.py):
  3745 — Вінниця, вул. Чорновола В'ячеслава, 29Б
  4142 — Іллінці, вул. Європейська, 22 (у Фори записан как «Маркса Карла, 22»)

Раньше использовался филиал 310 (Київ) — цены и ассортимент были чужие.

Список магазинов любого города можно получить так:
  {"method":"GetPickupFilials","data":{"merchantId":2,"businessId":4,"city":"м. Вінниця"}}
"""
from __future__ import annotations

import httpx

from region import FORA_BUSINESS_ID, FORA_DEFAULT_FILIAL, FORA_MERCHANT_ID
from .base import DEFAULT_HEADERS, REQUEST_TIMEOUT, Product, StoreParser, apply_relevance

API_URL = "https://api.catalog.ecom.fora.ua/api/2.0/exec/EcomCatalogGlobal"
PRODUCT_URL = "https://fora.ua/product/{slug}"

DELIVERY_TYPE = 1       # самовивіз — ассортимент конкретного магазина
MAX_RESULTS = 30
DEALS_PAGE_SIZE = 100
MAX_DEALS_PAGES = 5

HEADERS = dict(
    DEFAULT_HEADERS,
    Origin="https://fora.ua",
    Referer="https://fora.ua/",
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

    async def _search_once(self, query: str) -> list[Product]:
        payload = await self._call("GetSimpleCatalogItems", {
            "customFilter": query,
            "deliveryType": DELIVERY_TYPE,
            "filialId": self.filial_id,
            "From": 1,
            "To": MAX_RESULTS,
        })
        products = [p for p in (self._to_product(i) for i in (payload.get("items") or [])) if p]
        return apply_relevance(query, products)

    async def fetch_all_deals(self, max_items: int = 300) -> list[Product]:
        """
        Все акции нашего магазина.

        Особенность Фори: запрос с пустым фильтром отдаёт ВЕСЬ каталог
        (7000+ позиций, скидочных среди первых почти нет). Зато есть
        метод GetPromoFilters — он возвращает категории именно акционных
        товаров (это и есть раздел «Акції» на сайте). Поэтому идём так:
        берём список акционных категорий и обходим их по очереди.
        """
        collected: list[Product] = []
        seen: set[str] = set()

        # 1) какие категории есть в разделе акций
        await self._wait_turn()
        filters = await self._call("GetPromoFilters", {
            "deliveryType": DELIVERY_TYPE,
            "filialId": self.filial_id,
        })
        categories: list[str] = []
        for f in (filters.get("filters") or []):
            if f.get("typeId") == 3:      # тип 3 = категории товаров
                for item in ((f.get("props") or {}).get("items") or []):
                    if item.get("id"):
                        categories.append(str(item["id"]))
        if not categories:
            return []

        # 2) обходим категории (самые крупные идут первыми)
        for category_id in categories[:MAX_DEALS_PAGES * 2]:
            if len(collected) >= max_items:
                break
            await self._wait_turn()
            payload = await self._call("GetSimpleCatalogItems", {
                "customFilter": "",
                "deliveryType": DELIVERY_TYPE,
                "filialId": self.filial_id,
                "categoryId": category_id,
                "From": 1,
                "To": DEALS_PAGE_SIZE,
            })
            for raw in (payload.get("items") or []):
                product = self._to_product(raw)
                if product and product.has_discount and product.url not in seen:
                    seen.add(product.url)
                    collected.append(product)
        return collected[:max_items]

    # ── справочник магазинов (для настроек бота) ────────────────────────
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
