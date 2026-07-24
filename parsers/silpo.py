"""
parsers/silpo.py — Сільпо (silpo.ua), магазин у Вінниці.

Внутренний REST API сайта (его же использует библиотека pysilpo):
  GET https://sf-ecom-api.silpo.ua/v1/uk/branches/{branch_id}/products

branch_id — конкретный магазин (см. region.py). Раньше использовался
«виртуальный» филиал, который показывал средние по Украине цены;
теперь берём реальный магазин у Вінниці — цены и наличие точные.

Два режима:
  • search(query)     — обычный поиск товара;
  • fetch_all_deals() — ВСЕ акции магазина (mustHavePromotion=true).
    Этот режим используется для локального индекса и раздела «всі акції».
"""
from __future__ import annotations

import httpx

from region import SILPO_DEFAULT_BRANCH
from .base import DEFAULT_HEADERS, REQUEST_TIMEOUT, Product, StoreParser, apply_relevance

API_URL = "https://sf-ecom-api.silpo.ua/v1/uk/branches/{branch_id}/products"
PRODUCT_URL = "https://silpo.ua/product/{slug}"
IMAGE_URL = "https://images.silpo.ua/products/300x300/{icon}"

DEFAULT_BRANCH_ID = SILPO_DEFAULT_BRANCH  # магазин у Вінниці

MAX_RESULTS = 30        # позиций за один поисковый запрос
DEALS_PAGE_SIZE = 100   # позиций за одну страницу каталога акций
MAX_DEALS_PAGES = 5     # не больше 5 страниц (500 позиций) за раз


class SilpoParser(StoreParser):
    key = "silpo"
    name = "Сільпо"
    emoji = "🟢"
    enabled = True
    supports_deals_catalog = True   # умеет отдавать весь каталог акций

    def __init__(self, branch_id: str | None = None) -> None:
        super().__init__()
        self.branch_id = branch_id or DEFAULT_BRANCH_ID

    # ── разбор одной позиции ────────────────────────────────────────────
    def _to_product(self, item: dict) -> Product | None:
        title = (item.get("title") or "").strip()
        # для весовых товаров displayPrice — цена за витринную единицу
        price = item.get("displayPrice") or item.get("price")
        old_price = item.get("displayOldPrice") or item.get("oldPrice")
        if not title or not price:
            return None

        icon = (item.get("icon") or "").strip()
        return Product(
            store_key=self.key,
            store_name=self.name,
            store_emoji=self.emoji,
            title=title,
            new_price=float(price),
            old_price=float(old_price) if old_price else None,
            url=PRODUCT_URL.format(slug=item.get("slug") or ""),
            unit=(item.get("displayRatio") or item.get("ratio") or "").strip(),
            image_url=IMAGE_URL.format(icon=icon) if icon else "",
        )

    async def _request(self, params: dict) -> dict:
        url = API_URL.format(branch_id=self.branch_id)
        async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    # ── обычный поиск ───────────────────────────────────────────────────
    async def _search_once(self, query: str) -> list[Product]:
        data = await self._request({
            "limit": MAX_RESULTS,
            "offset": 0,
            "search": query,
            "sortBy": "productsList",
            "sortDirection": "desc",
            "inStock": True,
            "includeChildCategories": True,
        })
        products = [p for p in (self._to_product(i) for i in data.get("items", [])) if p]
        return apply_relevance(query, products)

    # ── все акции магазина ──────────────────────────────────────────────
    async def fetch_all_deals(self, max_items: int = 300) -> list[Product]:
        """
        Возвращает товары со скидкой из нашего магазина.
        Один запрос на страницу по 100 позиций, максимум MAX_DEALS_PAGES.
        """
        collected: list[Product] = []
        for page in range(MAX_DEALS_PAGES):
            if len(collected) >= max_items:
                break
            await self._wait_turn()  # вежливая пауза между страницами
            data = await self._request({
                "limit": DEALS_PAGE_SIZE,
                "offset": page * DEALS_PAGE_SIZE,
                "mustHavePromotion": True,     # только акционные позиции
                "sortBy": "promotion",
                "sortDirection": "desc",
                "inStock": True,
                "includeChildCategories": True,
            })
            items = data.get("items") or []
            if not items:
                break
            for raw in items:
                product = self._to_product(raw)
                if product and product.has_discount:
                    collected.append(product)
        return collected[:max_items]
