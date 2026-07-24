"""
parsers/grosh.py — Грош (online.grosh.ua).

Сайт работает на платформе Salesbox, у которой чистый JSON API.
Один GET-запрос на поиск (проверено живыми запросами 24.07.2026):

  GET https://prod.salesbox.me/api/v4/companies/grosh/offers/search
      ?name=<запрос>&lang=ua

Поля ответа data[]:
  name              — название товара
  price             — старая (базовая) цена
  priceWithDiscount — текущая цена (равна price, если скидки нет)
  discount          — размер скидки в гривнах (0 = скидки нет)
  previewURL        — фото товара (полный URL)
  id                — идентификатор для ссылки

Ссылка на товар: https://online.grosh.ua/offer/<id>
(маршрут /offer/:id подтверждён в коде приложения сайта)
"""
from __future__ import annotations

import httpx

from .base import DEFAULT_HEADERS, REQUEST_TIMEOUT, Product, StoreParser, apply_relevance

API_URL = "https://prod.salesbox.me/api/v4/companies/grosh/offers/search"
PRODUCT_URL = "https://online.grosh.ua/offer/{offer_id}"

# Представляемся сайтом Грош, как это делает настоящий браузер
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

    async def _search_once(self, query: str) -> list[Product]:
        params = {"name": query, "lang": "ua"}

        async with httpx.AsyncClient(headers=HEADERS, timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(API_URL, params=params)
            response.raise_for_status()
            payload = response.json()

        products: list[Product] = []
        for item in payload.get("data") or []:
            name = (item.get("name") or "").strip()
            base_price = item.get("price")          # старая/базовая цена
            new_price = item.get("priceWithDiscount")
            discount = item.get("discount") or 0
            offer_id = item.get("id")

            if not name or not offer_id:
                continue
            if new_price in (None, 0):
                new_price = base_price
            if new_price in (None, 0):
                continue  # товар без цены — пропускаем

            # Скидка есть, если магазин явно её указал и старая цена выше новой
            has_discount = bool(discount) and base_price and base_price > new_price

            products.append(
                Product(
                    store_key=self.key,
                    store_name=self.name,
                    store_emoji=self.emoji,
                    title=name,
                    new_price=float(new_price),
                    old_price=float(base_price) if has_discount else None,
                    url=PRODUCT_URL.format(offer_id=offer_id),
                    image_url=item.get("previewURL") or "",
                )
            )
        return apply_relevance(query, products)
