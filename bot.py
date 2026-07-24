"""
bot.py — запуск бота и обработка сообщений.

Как это работает (по шагам):
 1. Ты пишешь боту список товаров: «макарони, чіпси» (через запятую
    или каждый с новой строки).
 2. Бот отвечает «🔍 Шукаю…» и для каждого товара опрашивает магазины:
    сначала смотрит в кэш (3 часа), а если там пусто — делает по ОДНОМУ
    поисковому запросу на магазин (не больше 2 магазинов одновременно,
    с паузами 1–3 сек между запросами к одному домену).
 3. По каждому товару приходит отдельное сообщение со скидками,
    отсортированными по размеру скидки.
 4. Если какой-то магазин не ответил — бот честно напишет об этом,
    но покажет результаты остальных. Бот никогда не «падает целиком».

Запуск:  python3 bot.py   (подробно — в README.md)
Остановка: Ctrl+C в Терминале.
"""
from __future__ import annotations

import asyncio
import logging
import re

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message

from cache import Cache
from config import Config, load_config
from formatter import build_message, truncate_for_telegram
from parsers import get_parsers
from parsers.base import Product, StoreParser

# ── Настройки обработки ──────────────────────────────────────────────────────

MAX_ITEMS_PER_MESSAGE = 5   # максимум товаров в одном сообщении (защита от случайного «спама»)
MAX_QUERY_LENGTH = 60       # максимум символов в одном товаре
SEARCH_CONCURRENCY = 2      # не больше 2 магазинов опрашиваем одновременно

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("price-bot")

dp = Dispatcher()

# Эти объекты создаются один раз при старте (в main)
CONFIG: Config | None = None
CACHE = Cache()
PARSERS: list[StoreParser] = get_parsers()
SEARCH_SEMAPHORE = asyncio.Semaphore(SEARCH_CONCURRENCY)


# ── Вспомогательные функции ──────────────────────────────────────────────────

def is_owner(message: Message) -> bool:
    """Бот отвечает только владельцу (ALLOWED_USER_ID из .env)."""
    return (
        CONFIG is not None
        and message.from_user is not None
        and message.from_user.id == CONFIG.allowed_user_id
    )


def parse_items(text: str) -> list[str]:
    """
    Разбирает сообщение на список товаров.
    Разделители: запятая, точка с запятой или новая строка.
    """
    items: list[str] = []
    for raw in re.split(r"[,;\n]+", text):
        item = " ".join(raw.split())  # схлопываем лишние пробелы
        if not item or len(item) > MAX_QUERY_LENGTH:
            continue
        if item.lower() not in (i.lower() for i in items):  # без дублей
            items.append(item)
    return items[:MAX_ITEMS_PER_MESSAGE]


async def search_one_store(
    parser: StoreParser, query: str
) -> tuple[StoreParser, list[Product] | None, bool]:
    """
    Ищет один товар в одном магазине.
    Возвращает (парсер, список товаров ИЛИ None при ошибке, взято_из_кэша).
    Никогда не бросает исключение — ошибка одного магазина не ломает остальные.
    """
    # 1) кэш: повторный запрос в течение 3 часов — без похода на сайт
    cached = CACHE.get(parser.key, query)
    if cached is not None:
        payload, _age = cached
        return parser, [Product(**item) for item in payload], True

    # 2) живой запрос (не больше SEARCH_CONCURRENCY магазинов одновременно)
    try:
        async with SEARCH_SEMAPHORE:
            products = await parser.search(query)
    except Exception as e:  # noqa: BLE001
        logger.error("Магазин %s недоступний: %s: %s", parser.name, type(e).__name__, e)
        return parser, None, False

    # 3) успешный результат (даже пустой) кладём в кэш
    CACHE.set(parser.key, query, [p.to_dict() for p in products])
    return parser, products, False


async def search_item_everywhere(query: str) -> str:
    """Ищет один товар во всех магазинах и собирает готовое сообщение."""
    results = await asyncio.gather(
        *(search_one_store(parser, query) for parser in PARSERS)
    )

    products: list[Product] = []
    failed_stores: list[str] = []
    cache_flags: list[bool] = []

    for parser, store_products, from_cache in results:
        if store_products is None:
            failed_stores.append(parser.name)
        else:
            products.extend(store_products)
            cache_flags.append(from_cache)

    all_from_cache = bool(cache_flags) and all(cache_flags)
    text = build_message(query, products, failed_stores, from_cache=all_from_cache)
    return truncate_for_telegram(text)


# ── Хэндлеры сообщений ───────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not is_owner(message):
        return  # чужим бот не отвечает вообще
    await message.answer(
        "Привіт! Я шукаю знижки в супермаркетах: "
        "🟢 Сільпо, 🟣 Грош, 🟠 Фора, 🔵 АТБ.\n\n"
        "Напиши мені товар або кілька через кому, наприклад:\n"
        "<code>макарони, чіпси, сир</code>\n\n"
        "Я перевірю акції та покажу, де вигідніше 😉"
    )


@dp.message(F.text)
async def handle_search(message: Message) -> None:
    if not is_owner(message):
        # Логируем чужие сообщения, но не отвечаем (белый список)
        uid = message.from_user.id if message.from_user else "?"
        logger.info("Ігнорую повідомлення від стороннього користувача id=%s", uid)
        return

    items = parse_items(message.text or "")
    if not items:
        await message.answer(
            "Напиши назву товару (або кілька через кому), наприклад:\n"
            "<code>макарони, чіпси</code>"
        )
        return

    enabled_names = ", ".join(f"{p.emoji} {p.name}" for p in PARSERS)
    status = await message.answer(
        f"🔍 Шукаю знижки: <b>{', '.join(items)}</b>\n"
        f"Магазини: {enabled_names}"
    )

    # Товары обрабатываем последовательно: по каждому — отдельное сообщение
    for query in items:
        try:
            text = await search_item_everywhere(query)
        except Exception as e:  # noqa: BLE001 — последний рубеж защиты
            logger.exception("Несподівана помилка пошуку «%s»: %s", query, e)
            text = f"🛒 <b>{query}</b>\n\n⚠️ Сталася несподівана помилка, спробуй ще раз."
        await message.answer(text, disable_web_page_preview=True)

    # Убираем статусное сообщение «Шукаю…», чтобы не мешало
    try:
        await status.delete()
    except Exception:  # noqa: BLE001 — не критично, если не удалилось
        pass


# ── Запуск ───────────────────────────────────────────────────────────────────

async def main() -> None:
    global CONFIG
    CONFIG = load_config()
    CACHE.cleanup()  # выкидываем устаревшие записи кэша

    bot = Bot(
        token=CONFIG.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    names = ", ".join(p.name for p in PARSERS) or "немає (всі вимкнені!)"
    logger.info("Бот запущено. Підключені магазини: %s", names)
    logger.info("Відповідаю тільки користувачу з ID %s", CONFIG.allowed_user_id)
    logger.info("Зупинка: натисни Ctrl+C у цьому вікні Терміналу.")

    # long polling: бот сам опрашивает Telegram, никакой сервер не нужен
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Бот зупинено.")
