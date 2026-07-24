"""
parsers/fora.py — Фора (fora.ua).

Фора — сеть группы Fozzy (как и Сільпо). У сайта есть внутренний JSON API,
один POST-запрос на поиск (проверено живыми запросами 24.07.2026):

  POST https://api.catalog.ecom.fora.ua/api/2.0/exec/EcomCatalogGlobal
  {"method": "GetSimpleCatalogItems",
   "data": {"merchantId": 2,            # 2 = Фора в словаре брендов Fozzy
            "customFilter": "<запрос>", # текст поиска
            "deliveryType": 2,
            "filialId": 310,            # филиал по умолчанию (Киев)
            "From": 1, "To": 30}}

Поля ответа items[]:
  name, price (новая цена), oldPrice (старая или null),
  promoTitle, slug, unit, mainImage (полный URL фото)

Ссылка на товар: https://fora.ua/product/<slug>

Примечание: поиск Форы довольно «буквальный» — ищет вхождение слова
в название. Например, «макарони» может дать мало результатов, а
«спагеті» или «сир» — много. Это особенность самого сайта.
"""
from __future__ import annotations

import httpx

from .base import DEFAULT_HEADERS, REQUEST_TIMEOUT, Product, StoreParser, apply_relevance

API_URL = "https://api.catalog.ecom.fora.ua/api/2.0/exec/EcomCatalogGlobal"
PRODUCT_URL = "https://fora.ua/product/{slug}"

MERCHANT_ID = 2      # Фора (словарь брендов Fozzy: SILPO=1, FORA=2)
FILIAL_ID = 310      # филиал по умолчанию — его выдаёт GetDefaultFilial
DELIVERY_TYPE = 2
MAX_RESULTS = 30

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

    async def _search_once(self, query: str) -> list[Product]:
        body = {
            "method": "GetSimpleCatalogItems",
            "data": {
                "merchantId": MERCHANT_ID,
                "customFilter": query,
                "deliveryType": DELIVERY_TYPE,
                "filialId": FILIAL_ID,
                "From": 1,
                "To": MAX_RESULTS,
            },
        }

        async with httpx.AsyncClient(headers=HEADERS, timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(API_URL, json=body)
            response.raise_for_status()
            payload = response.json()

        # API Форы всегда отвечает 200, а ошибку кладёт в EComError
        error = payload.get("EComError") or {}
        if error.get("ErrorCode") not in (0, None):
            raise RuntimeError(f"Fora API error: {error.get('ErrorMessage')}")

        products: list[Product] = []
        for item in payload.get("items") or []:
            name = (item.get("name") or "").strip()
            price = item.get("price")
            old_price = item.get("oldPrice")
            slug = item.get("slug") or ""

            if not name or not price or not slug:
                continue

            products.append(
                Product(
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
            )
        return apply_relevance(query, products)
