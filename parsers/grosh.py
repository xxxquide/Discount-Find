"""
parsers/grosh.py — Грош (online.grosh.ua).

⏳ ЭТАП 2: парсер подключим следующим шагом (enabled = False пока).

Рецепт уже проверен живыми запросами (24.07.2026):
  Сайт работает на платформе Salesbox. Поиск — чистый JSON API, один GET:
    GET https://prod.salesbox.me/api/v4/companies/grosh/offers/search
        ?name=<запрос>&lang=ua
  Поля ответа data[]:
    name              — название товара
    price             — старая (базовая) цена
    priceWithDiscount — текущая цена (если скидки нет, равна price)
    discount          — размер скидки в гривнах (0 = скидки нет)
    id                — идентификатор товара
  Ссылка на товар: https://online.grosh.ua/offer/<id>
"""
from __future__ import annotations

from .base import Product, StoreParser


class GroshParser(StoreParser):
    key = "grosh"
    name = "Грош"
    emoji = "🟣"
    enabled = False  # включим на этапе 2

    async def _search_once(self, query: str) -> list[Product]:
        raise NotImplementedError("Парсер Грош будет добавлен на этапе 2")
