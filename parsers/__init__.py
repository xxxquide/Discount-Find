"""
parsers — пакет с парсерами магазинов.

Чтобы временно выключить магазин, поставь у его класса enabled = False.
Чтобы добавить новый магазин — создай файл по образцу silpo.py
и добавь класс в список ALL_PARSER_CLASSES.
"""
from __future__ import annotations

from .atb import AtbParser
from .base import Product, StoreParser
from .fora import ForaParser
from .grosh import GroshParser
from .silpo import SilpoParser

# Порядок в этом списке = порядок магазинов в ответе бота
ALL_PARSER_CLASSES = [
    SilpoParser,   # ✅ внутренний REST API
    GroshParser,   # ✅ JSON API платформы Salesbox
    ForaParser,    # ✅ JSON API группы Fozzy
    AtbParser,     # ✅ HTML со скидочной разметкой (httpx + BeautifulSoup)
]


def get_parsers() -> list[StoreParser]:
    """Создаёт по одному экземпляру каждого ВКЛЮЧЁННОГО парсера."""
    return [cls() for cls in ALL_PARSER_CLASSES if cls.enabled]


__all__ = ["Product", "StoreParser", "get_parsers", "ALL_PARSER_CLASSES"]
