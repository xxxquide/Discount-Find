"""
bot.py — запуск бота и все команды.

КАК РАБОТАЕТ (важное отличие от прошлой версии):
Бот раз в 3 часа скачивает КАТАЛОГ АКЦИЙ твоих магазинов у Вінниці/Іллінцях
и держит его локально. Поэтому:
  • поиск мгновенный (миллисекунды, а не секунды);
  • ищет по-умному: «макарони» находит «Макаронні вироби» и «Спагеті»;
  • сайты нагружаются в разы меньше (один обход вместо десятков поисков).

ЧТО УМЕЕТ:
  /start   — знакомство и выбор магазинов
  /deals   — 🔥 всі акції (листалка по страницам)
  /top     — 🏆 топ найбільших знижок
  /compare — ⚖️ порівняти ціни на товар між магазинами
  /list    — 🧾 список покупок (перевірити все одразу)
  /watch   — 👀 стежити за товаром (сповіщення про знижку)
  /stores  — 🏪 обрати магазини
  /refresh — 🔄 оновити акції примусово
  просто текст — пошук товару (можна кілька через кому)

Запуск:  python3 bot.py    Остановка: Ctrl+C
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
from deals_index import DealsIndex
from formatter import build_message, truncate_for_telegram
from parsers import get_parsers
from parsers.base import Product, StoreParser
from region import REGION_SUMMARY, REGION_TITLE
from rich import (
    build_rich_catalog,
    build_rich_compare,
    build_rich_results,
    build_rich_shopping_list,
)
from settings import Settings
from user_data import UserData

# ── Настройки ────────────────────────────────────────────────────────────

MAX_ITEMS_PER_MESSAGE = 5
MAX_QUERY_LENGTH = 60
SEARCH_CONCURRENCY = 2
DEALS_PER_PAGE = 6          # карточек на страницу в «всі акції»
WATCH_INTERVAL = 3 * 3600   # как часто проверять отслеживаемые товары

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("price-bot")

dp = Dispatcher()

CONFIG: Config | None = None
CACHE = Cache()
SETTINGS = Settings()
INDEX = DealsIndex()
USER = UserData()
PARSERS: list[StoreParser] = get_parsers()
SEARCH_SEMAPHORE = asyncio.Semaphore(SEARCH_CONCURRENCY)


# ── Помощники ────────────────────────────────────────────────────────────

def is_owner_id(user_id: int | None) -> bool:
    return CONFIG is not None and user_id is not None and user_id == CONFIG.allowed_user_id


def selected_store_keys() -> list[str]:
    saved = SETTINGS.get_selected_stores()
    all_keys = [p.key for p in PARSERS]
    if saved is None:
        return all_keys
    return [k for k in saved if k in all_keys] or all_keys


def selected_parsers() -> list[StoreParser]:
    keys = set(selected_store_keys())
    return [p for p in PARSERS if p.key in keys]


def stores_summary() -> str:
    return ", ".join(f"{p.emoji} {p.name}" for p in selected_parsers()) or "жодного"


def parse_items(text: str) -> list[str]:
    items: list[str] = []
    for raw in re.split(r"[,;\n]+", text):
        item = " ".join(raw.split())
        if not item or len(item) > MAX_QUERY_LENGTH:
            continue
        if item.lower() not in (i.lower() for i in items):
            items.append(item)
    return items[:MAX_ITEMS_PER_MESSAGE]


async def send_rich(message: Message, rich_message, fallback_text: str) -> None:
    """Пытаемся отправить красивое сообщение, при сбое — обычный текст."""
    try:
        await message.bot(SendRichMessage(chat_id=message.chat.id, rich_message=rich_message))
        return
    except Exception as e:  # noqa: BLE001
        logger.warning("Rich не пройшло (%s: %s) — текстовий варіант",
                       type(e).__name__, str(e)[:160])
    await message.answer(truncate_for_telegram(fallback_text), disable_web_page_preview=True)


# ── Клавиатуры ───────────────────────────────────────────────────────────

def main_keyboard() -> InlineKeyboardMarkup:
    """Главное меню под сообщением — быстрый доступ к функциям."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Всі акції", callback_data="deals:1"),
         InlineKeyboardButton(text="🏆 Топ знижок", callback_data="top")],
        [InlineKeyboardButton(text="🧾 Мій список", callback_data="list:check"),
         InlineKeyboardButton(text="👀 Стежу", callback_data="watch:show")],
        [InlineKeyboardButton(text="🏪 Магазини", callback_data="stores:open"),
         InlineKeyboardButton(text="🔄 Оновити", callback_data="refresh")],
    ])


def stores_keyboard() -> InlineKeyboardMarkup:
    chosen = set(selected_store_keys())
    rows = [[InlineKeyboardButton(
        text=f"{'✅' if p.key in chosen else '⬜'} {p.emoji} {p.name}",
        callback_data=f"store:{p.key}")] for p in PARSERS]
    rows.append([InlineKeyboardButton(text="💾 Готово", callback_data="store:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deals_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"deals:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="Далі ▶️", callback_data=f"deals:{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [InlineKeyboardButton(text="🏆 Топ знижок", callback_data="top"),
         InlineKeyboardButton(text="🏪 Магазини", callback_data="stores:open")],
    ])


def result_keyboard(query: str) -> InlineKeyboardMarkup:
    """Кнопки под результатом поиска."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚖️ Порівняти ціни", callback_data=f"cmp:{query[:40]}"),
         InlineKeyboardButton(text="👀 Стежити", callback_data=f"watchadd:{query[:40]}")],
        [InlineKeyboardButton(text="🧾 У список покупок", callback_data=f"listadd:{query[:40]}")],
    ])


# ── Индекс акций ─────────────────────────────────────────────────────────

async def ensure_index(message: Message | None = None, force: bool = False) -> list[str]:
    """
    Проверяет свежесть индекса и при необходимости обновляет.
    Возвращает список магазинов, которые не ответили.
    """
    parsers = selected_parsers()
    stale = [p for p in parsers if force or not INDEX.is_fresh(p.key)]
    if not stale:
        return []

    status = None
    if message is not None:
        status = await message.answer(
            f"🔄 Оновлюю каталог акцій ({', '.join(p.name for p in stale)})…\n"
            f"Це займе до хвилини, далі пошук буде миттєвим ⚡"
        )
    _total, failed = await INDEX.refresh_all(stale, force=force)
    if status is not None:
        try:
            await status.delete()
        except Exception:  # noqa: BLE001
            pass
    return failed


# ── Поиск ────────────────────────────────────────────────────────────────

async def search_live(parser: StoreParser, query: str) -> tuple[StoreParser, list[Product] | None]:
    """Живой поиск в магазине (используется как дополнение к индексу)."""
    cached = CACHE.get(parser.key, query)
    if cached is not None:
        return parser, [Product(**item) for item in cached[0]]
    try:
        async with SEARCH_SEMAPHORE:
            products = await parser.search(query)
    except Exception as e:  # noqa: BLE001
        logger.error("Магазин %s недоступний: %s: %s", parser.name, type(e).__name__, e)
        return parser, None
    CACHE.set(parser.key, query, [p.to_dict() for p in products])
    return parser, products


async def find_products(query: str, deep: bool = False) -> tuple[list[Product], list[str]]:
    """
    Ищет товар. Сначала — по локальному индексу акций (мгновенно).
    Если ничего не нашлось или нужен полный список цен (deep=True) —
    дополнительно опрашивает магазины напрямую.
    """
    keys = selected_store_keys()
    found = INDEX.search(query, keys, limit=40)
    failed: list[str] = []

    if found and not deep:
        return found, failed

    results = await asyncio.gather(*(search_live(p, query) for p in selected_parsers()))
    live: list[Product] = []
    for parser, products in results:
        if products is None:
            failed.append(parser.name)
        else:
            live.extend(products)

    # объединяем: индекс + живой поиск, без дублей по ссылке
    seen = {p.url for p in found}
    merged = found + [p for p in live if p.url not in seen]
    return merged, failed


# ── Хэндлеры: старт и меню ───────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not is_owner_id(message.from_user.id if message.from_user else None):
        return
    first_time = SETTINGS.get_selected_stores() is None

    await message.answer(
        f"Привіт! Я шукаю знижки у твоїх магазинах — <b>{REGION_TITLE}</b>.\n\n"
        f"{REGION_SUMMARY}\n\n"
        "<b>Просто напиши товар</b> — звичайними словами:\n"
        "<code>макарони, сир, кава</code>\n\n"
        "<b>Команди:</b>\n"
        "🔥 /deals — всі акції\n"
        "🏆 /top — топ найбільших знижок\n"
        "⚖️ /compare молоко — порівняти ціни\n"
        "🧾 /list — список покупок\n"
        "👀 /watch кава — стежити за знижкою\n"
        "🏪 /stores — обрати магазини\n"
        "🔄 /refresh — оновити акції",
        reply_markup=main_keyboard(),
    )
    if first_time:
        await message.answer(
            "🏪 Спочатку обери, де шукати знижки:", reply_markup=stores_keyboard()
        )


@dp.message(Command("stores"))
async def cmd_stores(message: Message) -> None:
    if not is_owner_id(message.from_user.id if message.from_user else None):
        return
    await message.answer(
        f"🏪 <b>Магазини для пошуку</b>\nРегіон: {REGION_TITLE}\n\n{REGION_SUMMARY}",
        reply_markup=stores_keyboard(),
    )


@dp.callback_query(F.data == "stores:open")
async def cb_stores_open(callback: CallbackQuery) -> None:
    if not is_owner_id(callback.from_user.id if callback.from_user else None):
        await callback.answer(); return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"🏪 <b>Магазини для пошуку</b>\nРегіон: {REGION_TITLE}",
            reply_markup=stores_keyboard(),
        )
    await callback.answer()


@dp.callback_query(F.data.startswith("store:"))
async def cb_store_toggle(callback: CallbackQuery) -> None:
    if not is_owner_id(callback.from_user.id if callback.from_user else None):
        await callback.answer(); return
    action = (callback.data or "").split(":", 1)[1]

    if action == "done":
        if not selected_parsers():
            await callback.answer("Увімкни хоча б один магазин 🙂", show_alert=True); return
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"✅ Шукатиму тут: {stores_summary()}\n\nНадішли товар, наприклад: <code>сир</code>",
                reply_markup=main_keyboard(),
            )
        await callback.answer("Збережено!")
        return

    keys = selected_store_keys()
    keys = [k for k in keys if k != action] if action in keys else keys + [action]
    SETTINGS.set_selected_stores(keys)
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(reply_markup=stores_keyboard())
        except Exception:  # noqa: BLE001
            pass
    await callback.answer()


# ── Хэндлеры: всі акції / топ ────────────────────────────────────────────

async def show_deals_page(message: Message, page: int) -> None:
    failed = await ensure_index(message)
    deals = INDEX.all_deals(selected_store_keys())
    if not deals:
        await message.answer("Акцій не знайдено 🤷 Спробуй /refresh")
        return

    total_pages = max(1, (len(deals) + DEALS_PER_PAGE - 1) // DEALS_PER_PAGE)
    page = max(1, min(page, total_pages))
    chunk = deals[(page - 1) * DEALS_PER_PAGE: page * DEALS_PER_PAGE]

    subtitle = f"Всього акцій: {len(deals)} · {stores_summary()}"
    if failed:
        subtitle += f" · ⚠️ не відповіли: {', '.join(failed)}"

    rich_message = build_rich_catalog("🔥 Всі акції", chunk, page, total_pages, subtitle)
    fallback = build_message(f"Всі акції (стор. {page}/{total_pages})", chunk, failed)
    await send_rich(message, rich_message, fallback)
    await message.answer("Гортай сторінки 👇", reply_markup=deals_keyboard(page, total_pages))


@dp.message(Command("deals"))
async def cmd_deals(message: Message) -> None:
    if not is_owner_id(message.from_user.id if message.from_user else None):
        return
    await show_deals_page(message, 1)


@dp.callback_query(F.data.startswith("deals:"))
async def cb_deals(callback: CallbackQuery) -> None:
    if not is_owner_id(callback.from_user.id if callback.from_user else None):
        await callback.answer(); return
    page = int((callback.data or "deals:1").split(":", 1)[1] or 1)
    if isinstance(callback.message, Message):
        await show_deals_page(callback.message, page)
    await callback.answer()


@dp.message(Command("top"))
async def cmd_top(message: Message) -> None:
    if not is_owner_id(message.from_user.id if message.from_user else None):
        return
    await show_top(message)


async def show_top(message: Message) -> None:
    failed = await ensure_index(message)
    top = INDEX.top_deals(selected_store_keys(), limit=8)
    if not top:
        await message.answer("Поки що акцій немає 🤷 Спробуй /refresh")
        return
    subtitle = f"Найбільші знижки прямо зараз · {stores_summary()}"
    rich_message = build_rich_catalog("🏆 Топ знижок", top, subtitle=subtitle)
    await send_rich(message, rich_message, build_message("Топ знижок", top, failed))


@dp.callback_query(F.data == "top")
async def cb_top(callback: CallbackQuery) -> None:
    if not is_owner_id(callback.from_user.id if callback.from_user else None):
        await callback.answer(); return
    if isinstance(callback.message, Message):
        await show_top(callback.message)
    await callback.answer()


# ── Хэндлеры: порівняння ─────────────────────────────────────────────────

@dp.message(Command("compare"))
async def cmd_compare(message: Message) -> None:
    if not is_owner_id(message.from_user.id if message.from_user else None):
        return
    query = (message.text or "").partition(" ")[2].strip()
    if not query:
        await message.answer("Напиши, що порівняти: <code>/compare молоко</code>")
        return
    await do_compare(message, query)


async def do_compare(message: Message, query: str) -> None:
    status = await message.answer(f"⚖️ Порівнюю ціни на <b>{query}</b>…")
    products, failed = await find_products(query, deep=True)  # тут потрібні всі ціни
    try:
        await status.delete()
    except Exception:  # noqa: BLE001
        pass
    if not products:
        await message.answer(f"Нічого не знайшов по «{query}» 🤷")
        return
    await send_rich(message, build_rich_compare(query, products),
                    build_message(query, products, failed))


@dp.callback_query(F.data.startswith("cmp:"))
async def cb_compare(callback: CallbackQuery) -> None:
    if not is_owner_id(callback.from_user.id if callback.from_user else None):
        await callback.answer(); return
    query = (callback.data or "cmp:").split(":", 1)[1]
    if isinstance(callback.message, Message) and query:
        await do_compare(callback.message, query)
    await callback.answer()


# ── Хэндлеры: список покупок ─────────────────────────────────────────────

@dp.message(Command("list"))
async def cmd_list(message: Message) -> None:
    if not is_owner_id(message.from_user.id if message.from_user else None):
        return
    args = (message.text or "").partition(" ")[2].strip()

    if args.startswith("+"):
        item = args[1:].strip()
        ok = USER.add_to_list(item)
        await message.answer(f"{'✅ Додав' if ok else 'ℹ️ Вже є'}: <b>{item}</b>")
        return
    if args.startswith("-"):
        item = args[1:].strip()
        ok = USER.remove_from_list(item)
        await message.answer(f"{'🗑 Прибрав' if ok else '🤷 Не знайшов'}: <b>{item}</b>")
        return
    if args == "clear":
        n = USER.clear_list()
        await message.answer(f"🧹 Список очищено ({n} позицій)")
        return

    await check_shopping_list(message)


async def check_shopping_list(message: Message) -> None:
    items = USER.get_list()
    if not items:
        await message.answer(
            "🧾 Список покупок порожній.\n\n"
            "Додати: <code>/list + молоко</code>\n"
            "Прибрати: <code>/list - молоко</code>\n"
            "Або тисни «🧾 У список покупок» під результатами пошуку."
        )
        return

    await ensure_index(message)
    keys = selected_store_keys()
    found: dict[str, list[Product]] = {}
    empty: list[str] = []
    for item in items:
        res = INDEX.search(item, keys, limit=5)
        if res:
            found[item] = res
        else:
            empty.append(item)

    rich_message = build_rich_shopping_list(found, empty)
    flat = [v[0] for v in found.values()]
    await send_rich(message, rich_message, build_message("Список покупок", flat, []))


@dp.callback_query(F.data == "list:check")
async def cb_list_check(callback: CallbackQuery) -> None:
    if not is_owner_id(callback.from_user.id if callback.from_user else None):
        await callback.answer(); return
    if isinstance(callback.message, Message):
        await check_shopping_list(callback.message)
    await callback.answer()


@dp.callback_query(F.data.startswith("listadd:"))
async def cb_list_add(callback: CallbackQuery) -> None:
    if not is_owner_id(callback.from_user.id if callback.from_user else None):
        await callback.answer(); return
    item = (callback.data or "listadd:").split(":", 1)[1]
    ok = USER.add_to_list(item)
    await callback.answer(f"{'✅ Додано у список' if ok else 'Вже у списку'}: {item}", show_alert=False)


# ── Хэндлеры: стеження ───────────────────────────────────────────────────

@dp.message(Command("watch"))
async def cmd_watch(message: Message) -> None:
    if not is_owner_id(message.from_user.id if message.from_user else None):
        return
    args = (message.text or "").partition(" ")[2].strip()
    if args.startswith("-"):
        item = args[1:].strip()
        ok = USER.remove_watch(item)
        await message.answer(f"{'🚫 Більше не стежу' if ok else '🤷 Не знайшов'}: <b>{item}</b>")
        return
    if args:
        ok = USER.add_watch(args)
        await message.answer(
            f"{'👀 Стежу' if ok else 'ℹ️ Вже стежу'} за <b>{args}</b>.\n"
            f"Повідомлю, щойно з'явиться знижка (перевіряю кожні 3 години)."
        )
        return
    await show_watchlist(message)


async def show_watchlist(message: Message) -> None:
    items = USER.get_watchlist()
    if not items:
        await message.answer(
            "👀 Поки що ні за чим не стежу.\n\n"
            "Додати: <code>/watch кава</code>\n"
            "Прибрати: <code>/watch - кава</code>"
        )
        return
    lines = "\n".join(f"• {i}" for i in items)
    await message.answer(
        f"👀 <b>Стежу за товарами:</b>\n{lines}\n\n"
        f"Повідомлю, щойно з'явиться знижка.\nПрибрати: <code>/watch - назва</code>"
    )


@dp.callback_query(F.data == "watch:show")
async def cb_watch_show(callback: CallbackQuery) -> None:
    if not is_owner_id(callback.from_user.id if callback.from_user else None):
        await callback.answer(); return
    if isinstance(callback.message, Message):
        await show_watchlist(callback.message)
    await callback.answer()


@dp.callback_query(F.data.startswith("watchadd:"))
async def cb_watch_add(callback: CallbackQuery) -> None:
    if not is_owner_id(callback.from_user.id if callback.from_user else None):
        await callback.answer(); return
    item = (callback.data or "watchadd:").split(":", 1)[1]
    ok = USER.add_watch(item)
    await callback.answer(f"{'👀 Стежу за' if ok else 'Вже стежу за'}: {item}")


# ── Хэндлер: оновлення ───────────────────────────────────────────────────

@dp.message(Command("refresh"))
async def cmd_refresh(message: Message) -> None:
    if not is_owner_id(message.from_user.id if message.from_user else None):
        return
    await do_refresh(message)


async def do_refresh(message: Message) -> None:
    status = await message.answer("🔄 Оновлюю каталог акцій…")
    failed = await ensure_index(None, force=True)
    stats = INDEX.stats(selected_store_keys())
    lines = "\n".join(
        f"{p.emoji} {p.name}: {stats.get(p.key, 0)} акцій" for p in selected_parsers()
    )
    text = f"✅ Готово!\n\n{lines}"
    if failed:
        text += f"\n\n⚠️ Не відповіли: {', '.join(failed)}"
    try:
        await status.edit_text(text, reply_markup=main_keyboard())
    except Exception:  # noqa: BLE001
        await message.answer(text, reply_markup=main_keyboard())


@dp.callback_query(F.data == "refresh")
async def cb_refresh(callback: CallbackQuery) -> None:
    if not is_owner_id(callback.from_user.id if callback.from_user else None):
        await callback.answer(); return
    if isinstance(callback.message, Message):
        await do_refresh(callback.message)
    await callback.answer()


@dp.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# ── Хэндлер: пошук товару ────────────────────────────────────────────────

@dp.message(F.text)
async def handle_search(message: Message) -> None:
    if not is_owner_id(message.from_user.id if message.from_user else None):
        uid = message.from_user.id if message.from_user else "?"
        logger.info("Ігнорую повідомлення від id=%s", uid)
        return

    if SETTINGS.get_selected_stores() is None:
        await message.answer("🏪 Спочатку обери магазини:", reply_markup=stores_keyboard())
        return

    items = parse_items(message.text or "")
    if not items:
        await message.answer("Напиши товар, наприклад: <code>сир, кава</code>")
        return

    await ensure_index(message)

    for query in items:
        USER.log_search(query)
        try:
            products, failed = await find_products(query)
            rich_message = build_rich_results(query, products, failed)
            fallback = build_message(query, products, failed)
            await send_rich(message, rich_message, fallback)
            if products:
                await message.answer("Що далі?", reply_markup=result_keyboard(query))
        except Exception as e:  # noqa: BLE001
            logger.exception("Помилка пошуку «%s»: %s", query, e)
            await message.answer(f"🛒 <b>{query}</b>\n\n⚠️ Сталася помилка, спробуй ще раз.")


# ── Фоновая проверка отслеживаемых товаров ───────────────────────────────

async def watch_worker(bot: Bot) -> None:
    """Раз в 3 часа проверяет, не появилась ли скидка на отслеживаемое."""
    await asyncio.sleep(60)  # даём боту спокойно стартовать
    while True:
        try:
            items = USER.get_watchlist()
            if items and CONFIG is not None:
                await INDEX.refresh_all(selected_parsers())
                keys = selected_store_keys()
                for item in items:
                    found = INDEX.search(item, keys, limit=3)
                    if not found:
                        continue
                    best = found[0]
                    if not best.has_discount:
                        continue
                    if USER.should_notify(item, best.new_price):
                        USER.mark_notified(item, best.new_price)
                        await bot.send_message(
                            CONFIG.allowed_user_id,
                            f"🔔 <b>Знижка на «{item}»!</b>\n\n"
                            f"{best.store_emoji} {best.store_name} — {best.title}\n"
                            f"💸 <s>{best.old_price:.2f}</s> → <b>{best.new_price:.2f} грн</b> "
                            f"(−{best.discount_pct}%)\n"
                            f'🔗 <a href="{best.url}">відкрити товар</a>',
                            disable_web_page_preview=True,
                        )
        except Exception as e:  # noqa: BLE001
            logger.error("Помилка у стеженні: %s: %s", type(e).__name__, e)
        await asyncio.sleep(WATCH_INTERVAL)


# ── Запуск ───────────────────────────────────────────────────────────────

async def main() -> None:
    global CONFIG
    CONFIG = load_config()
    CACHE.cleanup()

    bot = Bot(token=CONFIG.bot_token,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    await bot.set_my_commands([
        BotCommand(command="start", description="Що вміє бот"),
        BotCommand(command="deals", description="🔥 Всі акції"),
        BotCommand(command="top", description="🏆 Топ знижок"),
        BotCommand(command="compare", description="⚖️ Порівняти ціни"),
        BotCommand(command="list", description="🧾 Список покупок"),
        BotCommand(command="watch", description="👀 Стежити за товаром"),
        BotCommand(command="stores", description="🏪 Обрати магазини"),
        BotCommand(command="refresh", description="🔄 Оновити акції"),
    ])

    logger.info("Регіон: %s", REGION_TITLE)
    logger.info("Магазини: %s", ", ".join(p.name for p in selected_parsers()))
    logger.info("Відповідаю тільки користувачу з ID %s", CONFIG.allowed_user_id)
    logger.info("Зупинка: Ctrl+C")

    asyncio.create_task(watch_worker(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Бот зупинено.")
