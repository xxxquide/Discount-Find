"""
bot.py — запуск бота и обработка сообщений.

Как это работает (по шагам):
 1. При первом запуске бот предложит выбрать магазины (кнопки-переключатели).
    Выбор сохраняется и переживает перезапуски; изменить — команда /stores.
 2. Ты пишешь боту список товаров: «макарони, чіпси» (через запятую
    или каждый с новой строки).
 3. Бот отвечает «🔍 Шукаю…» и для каждого товара опрашивает ВЫБРАННЫЕ
    магазины: сначала смотрит в кэш (3 часа), а если там пусто — делает
    по ОДНОМУ поисковому запросу на магазин (не больше 2 одновременно,
    с паузами 1–3 сек между запросами к одному домену).
 4. По каждому товару приходит красивое rich-сообщение (Bot API 10.2):
    заголовок, коллаж фото товаров и таблица цен со ссылками.
    Если rich-сообщение не пройдёт — бот автоматически отправит
    классический текстовый вариант. Без ответа не останешься.
 5. Падение одного магазина не ломает остальные — бот пишет, кто
    не ответил, и показывает всё, что нашлось.

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
from aiogram.filters import Command, CommandStart
from aiogram.methods import SendRichMessage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from cache import Cache
from config import Config, load_config
from formatter import build_message, truncate_for_telegram
from parsers import get_parsers
from parsers.base import Product, StoreParser
from rich import build_rich_results
from settings import Settings

# ── Настройки обработки ──────────────────────────────────────────────────────

MAX_ITEMS_PER_MESSAGE = 5   # максимум товаров в одном сообщении
MAX_QUERY_LENGTH = 60       # максимум символов в одном товаре
SEARCH_CONCURRENCY = 2      # не больше 2 магазинов опрашиваем одновременно

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("price-bot")

dp = Dispatcher()

# Эти объекты создаются один раз при старте
CONFIG: Config | None = None
CACHE = Cache()
SETTINGS = Settings()
PARSERS: list[StoreParser] = get_parsers()          # все реализованные магазины
SEARCH_SEMAPHORE = asyncio.Semaphore(SEARCH_CONCURRENCY)


# ── Вспомогательные функции ──────────────────────────────────────────────────

def is_owner_id(user_id: int | None) -> bool:
    """Бот отвечает только владельцу (ALLOWED_USER_ID из .env)."""
    return CONFIG is not None and user_id is not None and user_id == CONFIG.allowed_user_id


def selected_store_keys() -> list[str]:
    """Ключи выбранных магазинов (если выбор ещё не делался — все)."""
    saved = SETTINGS.get_selected_stores()
    all_keys = [p.key for p in PARSERS]
    if saved is None:
        return all_keys
    # оставляем только реально существующие ключи (защита от старых настроек)
    valid = [k for k in saved if k in all_keys]
    return valid or all_keys


def selected_parsers() -> list[StoreParser]:
    keys = set(selected_store_keys())
    return [p for p in PARSERS if p.key in keys]


def parse_items(text: str) -> list[str]:
    """Разбирает сообщение на список товаров (запятая / ; / новая строка)."""
    items: list[str] = []
    for raw in re.split(r"[,;\n]+", text):
        item = " ".join(raw.split())
        if not item or len(item) > MAX_QUERY_LENGTH:
            continue
        if item.lower() not in (i.lower() for i in items):
            items.append(item)
    return items[:MAX_ITEMS_PER_MESSAGE]


# ── Клавиатура выбора магазинов ──────────────────────────────────────────────

def stores_keyboard() -> InlineKeyboardMarkup:
    """Кнопки-переключатели: ✅ магазин включён, ⬜ — выключен."""
    chosen = set(selected_store_keys())
    rows = []
    for p in PARSERS:
        mark = "✅" if p.key in chosen else "⬜"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {p.emoji} {p.name}",
                    callback_data=f"store:{p.key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="💾 Готово", callback_data="store:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def stores_summary() -> str:
    names = ", ".join(f"{p.emoji} {p.name}" for p in selected_parsers())
    return names or "жодного (увімкни хоча б один: /stores)"


async def send_store_picker(message: Message, intro: str) -> None:
    await message.answer(
        f"{intro}\n\n"
        "Натискай на магазин, щоб увімкнути/вимкнути його. "
        "Вибір зберігається — змінити можна будь-коли командою /stores.",
        reply_markup=stores_keyboard(),
    )


# ── Поиск ────────────────────────────────────────────────────────────────────

async def search_one_store(
    parser: StoreParser, query: str
) -> tuple[StoreParser, list[Product] | None, bool]:
    """
    Ищет один товар в одном магазине.
    Возвращает (парсер, список товаров ИЛИ None при ошибке, взято_из_кэша).
    Никогда не бросает исключение — ошибка одного магазина не ломает остальные.
    """
    cached = CACHE.get(parser.key, query)
    if cached is not None:
        payload, _age = cached
        return parser, [Product(**item) for item in payload], True

    try:
        async with SEARCH_SEMAPHORE:
            products = await parser.search(query)
    except Exception as e:  # noqa: BLE001
        logger.error("Магазин %s недоступний: %s: %s", parser.name, type(e).__name__, e)
        return parser, None, False

    CACHE.set(parser.key, query, [p.to_dict() for p in products])
    return parser, products, False


async def search_item_everywhere(query: str) -> tuple[list[Product], list[str], bool]:
    """Ищет один товар во всех ВЫБРАННЫХ магазинах."""
    parsers = selected_parsers()
    results = await asyncio.gather(
        *(search_one_store(parser, query) for parser in parsers)
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
    return products, failed_stores, all_from_cache


async def answer_with_results(
    message: Message, query: str, products: list[Product],
    failed: list[str], from_cache: bool,
) -> None:
    """
    Пытаемся отправить красивое rich-сообщение (таблица + фото).
    Если не вышло (старый клиент, битая картинка, изменение API) —
    молча переходим на классический текстовый формат.
    """
    try:
        rich_message = build_rich_results(query, products, failed, from_cache)
        await message.bot(
            SendRichMessage(chat_id=message.chat.id, rich_message=rich_message)
        )
        return
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Rich-повідомлення не пройшло (%s: %s) — надсилаю текстовий варіант",
            type(e).__name__, str(e)[:200],
        )

    text = truncate_for_telegram(build_message(query, products, failed, from_cache))
    await message.answer(text, disable_web_page_preview=True)


# ── Хэндлеры ─────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not is_owner_id(message.from_user.id if message.from_user else None):
        return  # чужим бот не отвечает вообще
    first_time = SETTINGS.get_selected_stores() is None
    await message.answer(
        "Привіт! Я шукаю знижки в супермаркетах: "
        "🟢 Сільпо, 🟣 Грош, 🟠 Фора, 🔵 АТБ.\n\n"
        "Напиши мені товар або кілька через кому, наприклад:\n"
        "<code>макарони, чіпси, сир</code>\n\n"
        "Команди:\n"
        "/stores — обрати магазини для пошуку\n"
        "/start — це повідомлення"
    )
    if first_time:
        await send_store_picker(message, "🛍 Спочатку обери, де шукати знижки:")


@dp.message(Command("stores"))
async def cmd_stores(message: Message) -> None:
    if not is_owner_id(message.from_user.id if message.from_user else None):
        return
    await send_store_picker(message, "🛍 Магазини для пошуку:")


@dp.callback_query(F.data.startswith("store:"))
async def on_store_toggle(callback: CallbackQuery) -> None:
    if not is_owner_id(callback.from_user.id if callback.from_user else None):
        await callback.answer()
        return

    action = (callback.data or "").split(":", 1)[1]

    if action == "done":
        parsers = selected_parsers()
        if not parsers:
            await callback.answer("Увімкни хоча б один магазин 🙂", show_alert=True)
            return
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"✅ Шукатиму тут: {stores_summary()}\n\n"
                "Тепер надішли список товарів, наприклад: <code>макарони, сир</code>\n"
                "Змінити магазини: /stores"
            )
        await callback.answer("Збережено!")
        return

    # переключаем магазин
    keys = selected_store_keys()
    if action in keys:
        keys = [k for k in keys if k != action]
    else:
        keys = keys + [action]
    SETTINGS.set_selected_stores(keys)

    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(reply_markup=stores_keyboard())
        except Exception:  # noqa: BLE001 — например, разметка не изменилась
            pass
    await callback.answer()


@dp.message(F.text)
async def handle_search(message: Message) -> None:
    if not is_owner_id(message.from_user.id if message.from_user else None):
        uid = message.from_user.id if message.from_user else "?"
        logger.info("Ігнорую повідомлення від стороннього користувача id=%s", uid)
        return

    # Первый запуск без выбора магазинов — сначала выбор
    if SETTINGS.get_selected_stores() is None:
        await send_store_picker(
            message, "🛍 Спочатку обери, де шукати знижки (потім повтори запит):"
        )
        return

    items = parse_items(message.text or "")
    if not items:
        await message.answer(
            "Напиши назву товару (або кілька через кому), наприклад:\n"
            "<code>макарони, чіпси</code>"
        )
        return

    status = await message.answer(
        f"🔍 Шукаю знижки: <b>{', '.join(items)}</b>\n"
        f"Магазини: {stores_summary()}"
    )

    for query in items:
        try:
            products, failed, from_cache = await search_item_everywhere(query)
            await answer_with_results(message, query, products, failed, from_cache)
        except Exception as e:  # noqa: BLE001 — последний рубеж защиты
            logger.exception("Несподівана помилка пошуку «%s»: %s", query, e)
            await message.answer(
                f"🛒 <b>{query}</b>\n\n⚠️ Сталася несподівана помилка, спробуй ще раз."
            )

    try:
        await status.delete()
    except Exception:  # noqa: BLE001
        pass


# ── Запуск ───────────────────────────────────────────────────────────────────

async def main() -> None:
    global CONFIG
    CONFIG = load_config()
    CACHE.cleanup()

    bot = Bot(
        token=CONFIG.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Меню команд в интерфейсе Telegram
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Що вміє бот"),
            BotCommand(command="stores", description="Обрати магазини для пошуку"),
        ]
    )

    logger.info("Бот запущено. Реалізовані магазини: %s", ", ".join(p.name for p in PARSERS))
    logger.info("Обрані магазини: %s", ", ".join(p.name for p in selected_parsers()))
    logger.info("Відповідаю тільки користувачу з ID %s", CONFIG.allowed_user_id)
    logger.info("Зупинка: натисни Ctrl+C у цьому вікні Терміналу.")

    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Бот зупинено.")
