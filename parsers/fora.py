"""
parsers/fora.py — Фора (fora.ua).

⏳ ЭТАП 3: парсер подключим после Грош (enabled = False пока).

Рецепт уже проверен живыми запросами (24.07.2026):
  Фора — сеть группы Fozzy (как и Сільпо), у сайта есть внутренний JSON API:
    POST https://api.catalog.ecom.fora.ua/api/2.0/exec/EcomCatalogGlobal
    Content-Type: application/json
    {"method": "GetSimpleCatalogItems",
     "data": {"merchantId": 2,          # 2 = Фора (SILPO:1, FORA:2)
              "customFilter": "<запрос>",
              "deliveryType": 2,
              "filialId": 310,          # филиал по умолчанию
              "From": 1, "To": 30}}
  Поля ответа items[]:
    name, price (новая), oldPrice (старая или null),
    priceDiscountValue, promoTitle, slug, unit
  Ссылка на товар: https://fora.ua/product/<slug>

  Дефолтный филиал можно уточнить методом GetDefaultFilial:
    POST https://api.ecom.fora.ua/api/2.0/exec/EComGlobal
    {"method":"GetDefaultFilial","data":{"owner":"fora","merchantId":2,"deliveryType":2}}
    → {"data": {"filialId": 310, ...}}

  Замечание из верификации: поиск Форы по слову «макарони» даёт мало
  результатов (ищет буквально по названию). На этапе 3 проверим выдачу
  по разным словам и при необходимости добавим подсказку в README.
"""
from __future__ import annotations

from .base import Product, StoreParser


class ForaParser(StoreParser):
    key = "fora"
    name = "Фора"
    emoji = "🟠"
    enabled = False  # включим на этапе 3

    async def _search_once(self, query: str) -> list[Product]:
        raise NotImplementedError("Парсер Фора будет добавлен на этапе 3")
