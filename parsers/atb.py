"""
parsers/atb.py — АТБ (atbmarket.com), магазин у Вінниці.

Два источника, оба — обычные HTTP-запросы без браузера:

  1) Поиск:      GET https://www.atbmarket.com/sch?query=<запрос>
  2) Все акции:  GET https://www.atbmarket.com/catalog/economy?sort=discount&page=N
     (раздел «Економія» — тот самый, что ты присылал)

Привязка к твоему магазину — cookie nstore_id (см. region.py):
  город Вінниця = 583, магазин вул. Київська, 27 = 473.
Проверено: на странице появляется data-store="473", то есть сайт
показывает ассортимент именно этого магазина.

Разметка карточки:
  .catalog-item__title a          → название + ссылка /product/<slug>
  data.product-price__top[value]  → новая цена
  data.product-price__bottom[val] → старая цена (только у акций)
  img.catalog-item__img           → фото
"""
from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from region import ATB_CITY_ID, ATB_STORE_ID
from .base import DEFAULT_HEADERS, REQUEST_TIMEOUT, Product, StoreParser, apply_relevance

BASE_URL = "https://www.atbmarket.com"
SEARCH_URL = f"{BASE_URL}/sch"
ECONOMY_URL = f"{BASE_URL}/catalog/economy"

MAX_DEALS_PAGES = 4     # страниц каталога акций за раз (по ~36 позиций)

HEADERS = dict(
    DEFAULT_HEADERS,
    Accept="text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
)

# Привязка к нашему магазину: сайт читает эти cookie
COOKIES = {
    "nstore_id": ATB_STORE_ID,
    "city": ATB_CITY_ID,
}


class AtbParser(StoreParser):
    key = "atb"
    name = "АТБ"
    emoji = "🔵"
    enabled = True
    supports_deals_catalog = True

    async def _fetch(self, url: str, params: dict | None = None) -> str:
        async with httpx.AsyncClient(
            headers=HEADERS, cookies=COOKIES, timeout=REQUEST_TIMEOUT, follow_redirects=True
        ) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.text

    def _parse_cards(self, html: str) -> list[Product]:
        soup = BeautifulSoup(html, "html.parser")
        products: list[Product] = []

        for card in soup.select("article.catalog-item"):
            link = card.select_one(".catalog-item__title a")
            if link is None:
                continue
            title = link.get_text(strip=True)
            href = link.get("href") or ""
            if not title or not href:
                continue

            top = card.select_one("data.product-price__top")
            if top is None or not top.get("value"):
                continue
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

            img = card.select_one("img.catalog-item__img")
            products.append(
                Product(
                    store_key=self.key,
                    store_name=self.name,
                    store_emoji=self.emoji,
                    title=title,
                    new_price=new_price,
                    old_price=old_price if (old_price and old_price > new_price) else None,
                    url=BASE_URL + href if href.startswith("/") else href,
                    image_url=(img.get("src") or "") if img else "",
                )
            )
        return products

    async def _search_once(self, query: str) -> list[Product]:
        html = await self._fetch(SEARCH_URL, {"query": query})
        return apply_relevance(query, self._parse_cards(html))

    async def fetch_all_deals(self, max_items: int = 300) -> list[Product]:
        """
        Раздел «Економія» — все акции, отсортированные по размеру скидки.
        Листаем страницы, пока есть карточки.
        """
        collected: list[Product] = []
        seen_urls: set[str] = set()

        for page in range(1, MAX_DEALS_PAGES + 1):
            if len(collected) >= max_items:
                break
            await self._wait_turn()
            params = {"sort": "discount"}
            if page > 1:
                params["page"] = page
            html = await self._fetch(ECONOMY_URL, params)
            cards = self._parse_cards(html)
            if not cards:
                break
            new_cards = 0
            for product in cards:
                if product.has_discount and product.url not in seen_urls:
                    seen_urls.add(product.url)
                    collected.append(product)
                    new_cards += 1
            if new_cards == 0:   # страница повторилась — дальше смысла нет
                break
        return collected[:max_items]
