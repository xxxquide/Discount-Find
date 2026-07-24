"""
parsers/atb.py — АТБ (atbmarket.com).

Страница поиска АТБ рендерится на сервере, поэтому достаточно одного
обычного GET-запроса + разбор HTML (BeautifulSoup). Playwright не нужен.

  GET https://www.atbmarket.com/sch?query=<запрос>

Разметка карточки (проверено 24.07.2026):
  <article class="catalog-item">
    .catalog-item__title a         → название + href="/product/<slug>"
    data.product-price__top        → атрибут value = новая цена
    data.product-price__bottom     → атрибут value = старая цена (есть только у скидок)
    класс product-price--sale      → маркер скидки
    img.catalog-item__img          → фото товара

Важно: у АТБ есть анти-бот защита. Из тестовой среды страница отдаётся
обычным запросом; если с твоего IP вдруг начнутся 403 — бот просто
покажет «АТБ не відповів», агрессивно обходить защиту мы не будем
(запасной план — Playwright, подключим отдельно, если понадобится).
"""
from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from .base import DEFAULT_HEADERS, REQUEST_TIMEOUT, Product, StoreParser, apply_relevance

SEARCH_URL = "https://www.atbmarket.com/sch"
BASE_URL = "https://www.atbmarket.com"

# Для обычной HTML-страницы браузер шлёт такие заголовки
HEADERS = dict(
    DEFAULT_HEADERS,
    Accept="text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
)


class AtbParser(StoreParser):
    key = "atb"
    name = "АТБ"
    emoji = "🔵"
    enabled = True

    async def _search_once(self, query: str) -> list[Product]:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True
        ) as client:
            response = await client.get(SEARCH_URL, params={"query": query})
            response.raise_for_status()
            html = response.text

        soup = BeautifulSoup(html, "html.parser")
        products: list[Product] = []

        for card in soup.select("article.catalog-item"):
            # Название и ссылка
            title_link = card.select_one(".catalog-item__title a")
            if title_link is None:
                continue
            title = title_link.get_text(strip=True)
            href = title_link.get("href") or ""
            if not title or not href:
                continue

            # Цены: <data value="14.20" class="product-price__top">
            top = card.select_one("data.product-price__top")
            if top is None or not top.get("value"):
                continue  # товара нет в продаже или карточка без цены
            try:
                new_price = float(top["value"])
            except (TypeError, ValueError):
                continue

            old_price = None
            bottom = card.select_one("data.product-price__bottom")
            if bottom is not None and bottom.get("value"):
                try:
                    old_price = float(bottom["value"])
                except (TypeError, ValueError):
                    old_price = None

            # Фото товара
            img = card.select_one("img.catalog-item__img")
            image_url = (img.get("src") or "") if img else ""

            products.append(
                Product(
                    store_key=self.key,
                    store_name=self.name,
                    store_emoji=self.emoji,
                    title=title,
                    new_price=new_price,
                    old_price=old_price if (old_price and old_price > new_price) else None,
                    url=BASE_URL + href if href.startswith("/") else href,
                    image_url=image_url,
                )
            )
        return apply_relevance(query, products)
