"""
parsers/silpo.py — Сільпо (silpo.ua).

Используем внутренний REST API сайта (его же использует библиотека pysilpo):
  GET https://sf-ecom-api.silpo.ua/v1/uk/branches/{branch_id}/products
      ?search=<запрос>&limit=30&offset=0&sortBy=productsList&...

Это официальный бэкенд фронтенда silpo.ua: один лёгкий JSON-запрос,
без рендеринга страниц и без Playwright.

Поля ответа (проверено 24.07.2026):
  items[] → title, price (новая цена), oldPrice (старая или null),
            slug (для ссылки), ratio (фасовка), stock.
Ссылка на товар: https://silpo.ua/product/<slug>
"""
from __future__ import annotations

import httpx

from .base import DEFAULT_HEADERS, REQUEST_TIMEOUT, Product, StoreParser, apply_relevance

API_URL = "https://sf-ecom-api.silpo.ua/v1/uk/branches/{branch_id}/products"

# «Нулевой» филиал — виртуальный магазин по умолчанию, его использует
# и сам сайт до выбора адреса доставки, и библиотека pysilpo.
DEFAULT_BRANCH_ID = "00000000-0000-0000-0000-000000000000"

PRODUCT_URL = "https://silpo.ua/product/{slug}"

# CDN картинок Сільпо: поле icon в ответе — имя файла (проверено, отдаёт 200)
IMAGE_URL = "https://images.silpo.ua/products/300x300/{icon}"

MAX_RESULTS = 30  # сколько позиций берём из первой страницы результатов


class SilpoParser(StoreParser):
    key = "silpo"
    name = "Сільпо"
    emoji = "🟢"
    enabled = True

    async def _search_once(self, query: str) -> list[Product]:
        params = {
            "limit": MAX_RESULTS,
            "offset": 0,
            "search": query,
            "sortBy": "productsList",   # так сортирует сам сайт при поиске
            "sortDirection": "desc",
            "inStock": True,            # только товары в наличии
            "includeChildCategories": True,
        }
        url = API_URL.format(branch_id=DEFAULT_BRANCH_ID)

        async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()  # если не 200 — уйдёт в повтор/ошибку
            data = response.json()

        products: list[Product] = []
        for item in data.get("items", []):
            title = (item.get("title") or "").strip()
            # Для весовых товаров price — цена за кг, а displayPrice — цена
            # за «витринную» единицу (например, за 100 г). Показываем как на сайте.
            price = item.get("displayPrice") or item.get("price")
            old_price = item.get("displayOldPrice") or item.get("oldPrice")
            unit = (item.get("displayRatio") or item.get("ratio") or "").strip()
            slug = item.get("slug") or ""

            if not title or not price:
                continue  # битая позиция — пропускаем

            icon = (item.get("icon") or "").strip()
            products.append(
                Product(
                    store_key=self.key,
                    store_name=self.name,
                    store_emoji=self.emoji,
                    title=title,
                    new_price=float(price),
                    old_price=float(old_price) if old_price else None,
                    url=PRODUCT_URL.format(slug=slug),
                    unit=unit,
                    image_url=IMAGE_URL.format(icon=icon) if icon else "",
                )
            )
        return apply_relevance(query, products)
