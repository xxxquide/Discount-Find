"""
parsers/base.py — общий «каркас» для всех парсеров магазинов.

Каждый магазин (silpo.py, atb.py, ...) наследует класс StoreParser
и реализует один метод _search_once(query) — РОВНО ОДИН запрос
к сайту/API магазина, который возвращает список товаров.

Всё общее живёт здесь:
  • класс Product — единый формат товара для всех магазинов;
  • вежливые паузы 1–3 сек со случайным джиттером между запросами
    к одному и тому же магазину;
  • таймаут 20 секунд и максимум 1 повтор при ошибке;
  • реалистичный User-Agent обычного браузера;
  • фильтр релевантности (чтобы «сир» не приносил «сирники»... хотя
    иногда принесёт — тогда просто игнорируй лишнее :).
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

# ── Общие настройки «вежливости» ─────────────────────────────────────────────

# Представляемся обычным браузером Safari/Chrome на macOS
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8,en;q=0.7",
}

REQUEST_TIMEOUT = 20.0   # секунд на один запрос
MIN_PAUSE = 1.0          # минимальная пауза между запросами к одному домену
MAX_PAUSE = 3.0          # максимальная пауза (выбирается случайно между ними)
MAX_RETRIES = 1          # при ошибке — один повтор, не больше


# ── Единый формат товара ─────────────────────────────────────────────────────

@dataclass
class Product:
    store_key: str          # 'silpo', 'atb', 'fora', 'grosh'
    store_name: str         # 'Сільпо', 'АТБ', ...
    store_emoji: str        # 🟢 🔵 🟠 🟣
    title: str              # название товара
    new_price: float        # текущая цена, грн
    old_price: float | None # старая цена, грн (None — товар без скидки)
    url: str                # ссылка на товар
    unit: str = ""          # фасовка/единица ('500г', 'шт'), если магазин отдал
    image_url: str = ""     # ссылка на фото товара (для красивого вывода)

    @property
    def has_discount(self) -> bool:
        """Скидка есть, если известна старая цена и она выше новой."""
        return (
            self.old_price is not None
            and self.new_price > 0
            and self.old_price > self.new_price
        )

    @property
    def discount_pct(self) -> int:
        """Размер скидки в процентах, например 24 (означает −24%)."""
        if not self.has_discount:
            return 0
        return round((1 - self.new_price / self.old_price) * 100)

    def to_dict(self) -> dict:
        """Для сохранения в кэш."""
        return asdict(self)


# ── Фильтр релевантности ─────────────────────────────────────────────────────

# Магазины пишут по-разному: «чіпси»/«чипси», «яйця»/«яйца».
# Приводим украинские/русские варианты букв к одному виду перед сравнением.
_LETTER_SUBS = str.maketrans({
    "і": "и", "ї": "и", "є": "е", "ґ": "г", "ё": "е",
    "'": "", "’": "", "ʼ": "",
})


def _normalize(text: str) -> str:
    return text.lower().translate(_LETTER_SUBS)


def is_relevant(query: str, title: str) -> bool:
    """
    Проверяем, что товар похож на запрос: каждое значимое слово запроса
    (от 3 букв, с обрезанным окончанием) должно встретиться в названии.
    «макарони» → «макаро» найдёт и «Макаронні вироби».
    """
    title_norm = _normalize(title)
    words = [w for w in re.split(r"[^\wа-яіїєґё]+", _normalize(query)) if len(w) >= 3]
    if not words:
        return True
    for word in words:
        stem = word[: max(4, len(word) - 2)]  # «чипси» → «чипс», «макарони» → «макаро»
        if stem not in title_norm:
            return False
    return True


def apply_relevance(query: str, products: list[Product], fallback_top: int = 10) -> list[Product]:
    """
    Оставляет релевантные товары. Если наш фильтр отсеял ВСЁ, а магазин
    что-то нашёл — доверяем ранжированию магазина и берём верхние позиции
    (поиск магазина часто умнее простого совпадения букв: синонимы и т.п.).
    """
    good = [p for p in products if is_relevant(query, p.title)]
    if good:
        return good
    return products[:fallback_top]


# ── Базовый класс парсера ────────────────────────────────────────────────────

class StoreParser(ABC):
    """Родитель всех парсеров. Наследники задают key/name/emoji и _search_once()."""

    key: str = ""
    name: str = ""
    emoji: str = ""
    enabled: bool = True  # выключенный магазин просто не участвует в поиске

    def __init__(self) -> None:
        # Замок гарантирует: к одному магазину запросы идут строго по очереди,
        # с паузой 1–3 сек между ними (даже если товаров в запросе несколько).
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def _wait_turn(self) -> None:
        """Вежливая пауза перед КАЖДЫМ запросом к этому магазину."""
        async with self._lock:
            pause = random.uniform(MIN_PAUSE, MAX_PAUSE)
            wait = self._last_request_at + pause - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    @abstractmethod
    async def _search_once(self, query: str) -> list[Product]:
        """Ровно один поисковый запрос к магазину. Реализуется в наследнике."""

    async def search(self, query: str) -> list[Product]:
        """
        Публичный метод: одна попытка + один повтор при ошибке.
        Если оба раза неудачно — исключение уходит наверх, и bot.py
        честно покажет, что магазин недоступен (не ломая остальные).
        """
        attempts = 1 + MAX_RETRIES
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                await self._wait_turn()
                return await self._search_once(query)
            except Exception as e:  # noqa: BLE001 — нам важно не уронить бот целиком
                last_error = e
                logger.warning(
                    "%s: попытка %d/%d не удалась (%s: %s)",
                    self.name, attempt, attempts, type(e).__name__, e,
                )
                if attempt < attempts:
                    await asyncio.sleep(random.uniform(1.0, 2.0))
        raise last_error  # type: ignore[misc]
