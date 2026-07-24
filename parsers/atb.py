"""
parsers/atb.py — АТБ (atbmarket.com).

⏳ ЭТАП 4: парсер подключим последним (enabled = False пока).

Рецепт уже проверен (24.07.2026):
  Страница поиска рендерится на сервере и отдаётся обычным HTTP-запросом:
    GET https://www.atbmarket.com/sch?query=<запрос>
  На странице карточки товаров:
    <article class="catalog-item js-product-container">
      ссылка:   a.catalog-item__photo-link  → href="/product/<slug>"
      название: в aria-label кнопки «в избранное» и в заголовке карточки
      цены:     <data value="14.20" class="product-price__top">  — новая
                <data value="17.80" class="product-price__bottom"> — старая
      маркер скидки: класс product-price--sale на блоке цены
  Ссылка на товар: https://www.atbmarket.com/product/<slug>

  Важно: у АТБ есть анти-бот защита. Из нашей тестовой среды страница
  отдалась обычным запросом (784 КБ HTML) — поэтому план А: httpx +
  BeautifulSoup, без браузера. Если с твоего IP страница не будет
  отдаваться (403 / пустая) — план Б: Playwright с headless Chromium
  (загрузка страницы и чтение тех же карточек из DOM).
  Агрессивные методы обхода защиты использовать не будем.
"""
from __future__ import annotations

from .base import Product, StoreParser


class AtbParser(StoreParser):
    key = "atb"
    name = "АТБ"
    emoji = "🔵"
    enabled = False  # включим на этапе 4

    async def _search_once(self, query: str) -> list[Product]:
        raise NotImplementedError("Парсер АТБ будет добавлен на этапе 4")
