"""
rich.py — красивый вывод через Rich Messages (Telegram Bot API 10.2).

Главный формат — «карточки товаров»: у каждой позиции слева своё фото,
а подписью идут название, магазин, цены и процент скидки. Плюс заголовок,
сводка и футер.

Структура сообщения:
  ┌ 🛒 Заголовок (что искали)
  ├ Строка-сводка: сколько акций, откуда данные
  ├ ── карточка ── фото + подпись (магазин, название, ціни, −%)
  ├ ── карточка ──
  ├ …
  ├ Таблица-итог (компактный список остальных позиций)
  └ Футер: время, кэш, недоступные магазины

Если rich-сообщение не пройдёт (старый клиент, битое фото), bot.py
автоматически отправит классический текстовый вариант из formatter.py.
"""
from __future__ import annotations

import time

from aiogram.types import (
    InputMediaPhoto,
    InputRichBlockCollage,
    InputRichBlockDivider,
    InputRichBlockFooter,
    InputRichBlockParagraph,
    InputRichBlockPhoto,
    InputRichBlockSectionHeading,
    InputRichBlockTable,
    InputRichMessage,
    RichBlockCaption,
    RichBlockTableCell,
    RichTextBold,
    RichTextItalic,
    RichTextStrikethrough,
    RichTextUrl,
)

from formatter import select_deals, select_reference
from parsers.base import Product

MAX_PHOTO_CARDS = 6      # сколько позиций показываем с фото
MAX_TABLE_ROWS = 10      # сколько остальных — компактной таблицей


def _price(value: float) -> str:
    return f"{value:.2f}"


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _cell(text, align: str = "left", header: bool = False) -> RichBlockTableCell:
    return RichBlockTableCell(align=align, valign="middle", text=text,
                              is_header=header or None)


def _card(p: Product) -> InputRichBlockPhoto:
    """
    Одна карточка товара: фото + подпись.
    В подписи: магазин · название (ссылка), старая → новая цена, −%.
    """
    caption_parts = [
        RichTextBold(text=f"{p.store_emoji} −{p.discount_pct}%  "),
        RichTextUrl(text=_short(p.title, 70), url=p.url),
        "\n",
        RichTextStrikethrough(text=f"{_price(p.old_price)} грн"),
        "  →  ",
        RichTextBold(text=f"{_price(p.new_price)} грн"),
    ]
    if p.unit and p.unit.lower().strip(".") not in ("шт", "уп", "од"):
        caption_parts.append(f"  ({p.unit})")

    return InputRichBlockPhoto(
        photo=InputMediaPhoto(media=p.image_url),
        caption=RichBlockCaption(
            text=caption_parts,
            credit=p.store_name,
        ),
    )


def _deal_rows(deals: list[Product]) -> list[list[RichBlockTableCell]]:
    """Компактная таблица для позиций без карточки."""
    cells = [[
        _cell("Магазин", header=True),
        _cell("Товар", header=True),
        _cell("Ціна, грн", header=True),
        _cell("−%", align="center", header=True),
    ]]
    for p in deals:
        cells.append([
            _cell(f"{p.store_emoji} {p.store_name}"),
            _cell(RichTextUrl(text=_short(p.title, 55), url=p.url)),
            _cell([
                RichTextStrikethrough(text=_price(p.old_price)),
                " → ",
                RichTextBold(text=_price(p.new_price)),
            ]),
            _cell(RichTextBold(text=f"−{p.discount_pct}%"), align="center"),
        ])
    return cells


def _footer(failed_stores: list[str], from_cache: bool, extra: str = "") -> InputRichBlockFooter:
    parts: list[str] = []
    if extra:
        parts.append(extra)
    if failed_stores:
        parts.append("⚠️ Не відповіли: " + ", ".join(failed_stores))
    if from_cache:
        parts.append("⚡ з кешу")
    parts.append("🕐 " + time.strftime("%H:%M"))
    return InputRichBlockFooter(text="  ·  ".join(parts))


# ── результаты поиска ────────────────────────────────────────────────────

def build_rich_results(
    query: str,
    products: list[Product],
    failed_stores: list[str],
    from_cache: bool = False,
) -> InputRichMessage:
    """Красивый ответ по одному запрошенному товару."""
    deals = select_deals(products)

    blocks: list = [
        InputRichBlockSectionHeading(text=f"🛒 {query.strip().capitalize()}", size=2),
    ]

    if deals:
        best = deals[0]
        blocks.append(InputRichBlockParagraph(text=[
            RichTextItalic(text=f"Знайдено акцій: {len(deals)}. Найвигідніша — "),
            RichTextBold(text=f"−{best.discount_pct}%"),
            RichTextItalic(text=f" у {best.store_name}."),
        ]))

        # карточки с фото — самые выгодные позиции
        with_photo = [p for p in deals if p.image_url][:MAX_PHOTO_CARDS]
        if with_photo:
            blocks.append(InputRichBlockDivider())
            for p in with_photo:
                blocks.append(_card(p))

        # остальные — компактной таблицей
        rest = [p for p in deals if p not in with_photo][:MAX_TABLE_ROWS]
        if rest:
            blocks.append(InputRichBlockDivider())
            blocks.append(InputRichBlockParagraph(text=[
                RichTextItalic(text="Інші пропозиції:")
            ]))
            blocks.append(InputRichBlockTable(cells=_deal_rows(rest),
                                              is_bordered=True, is_striped=True))
    else:
        blocks.append(InputRichBlockParagraph(text="😕 Знижок на цей товар зараз немає."))
        reference = select_reference(products)
        if reference:
            blocks.append(InputRichBlockParagraph(text=[
                RichTextItalic(text="Звичайні ціни для орієнтиру:")
            ]))
            cells = [[_cell("Магазин", header=True), _cell("Товар", header=True),
                      _cell("Ціна, грн", header=True)]]
            for p in reference:
                cells.append([
                    _cell(f"{p.store_emoji} {p.store_name}"),
                    _cell(RichTextUrl(text=_short(p.title, 55), url=p.url)),
                    _cell(RichTextBold(text=_price(p.new_price))),
                ])
            blocks.append(InputRichBlockTable(cells=cells, is_bordered=True))

    blocks.append(_footer(failed_stores, from_cache))
    return InputRichMessage(blocks=blocks)


# ── «всі акції» / «топ знижок» ───────────────────────────────────────────

def build_rich_catalog(
    title: str,
    deals: list[Product],
    page: int = 1,
    total_pages: int = 1,
    subtitle: str = "",
) -> InputRichMessage:
    """
    Витрина акций: страница каталога или топ скидок.
    Каждая позиция — карточка с фото.
    """
    blocks: list = [InputRichBlockSectionHeading(text=title, size=2)]

    if subtitle:
        blocks.append(InputRichBlockParagraph(text=[RichTextItalic(text=subtitle)]))

    if not deals:
        blocks.append(InputRichBlockParagraph(text="Поки що акцій не знайдено 🤷"))
        blocks.append(_footer([], False))
        return InputRichMessage(blocks=blocks)

    blocks.append(InputRichBlockDivider())
    for p in deals:
        if p.image_url:
            blocks.append(_card(p))
    # позиции без фото — таблицей, чтобы не потерялись
    no_photo = [p for p in deals if not p.image_url]
    if no_photo:
        blocks.append(InputRichBlockTable(cells=_deal_rows(no_photo),
                                          is_bordered=True, is_striped=True))

    extra = f"Сторінка {page} з {total_pages}" if total_pages > 1 else ""
    blocks.append(_footer([], False, extra))
    return InputRichMessage(blocks=blocks)


# ── сравнение одного товара между магазинами ─────────────────────────────

def build_rich_compare(query: str, products: list[Product]) -> InputRichMessage:
    """Одна таблица: где этот товар дешевле всего прямо сейчас."""
    ranked = sorted(
        [p for p in products if p.new_price > 0],
        key=lambda p: p.new_price,
    )[:12]

    blocks: list = [
        InputRichBlockSectionHeading(text=f"⚖️ Порівняння: {query}", size=2),
    ]
    if not ranked:
        blocks.append(InputRichBlockParagraph(text="Нічого не знайшов для порівняння 🤷"))
        blocks.append(_footer([], False))
        return InputRichMessage(blocks=blocks)

    cheapest = ranked[0]
    blocks.append(InputRichBlockParagraph(text=[
        RichTextItalic(text="Найдешевше зараз у "),
        RichTextBold(text=f"{cheapest.store_emoji} {cheapest.store_name}"),
        RichTextItalic(text=f" — {_price(cheapest.new_price)} грн."),
    ]))

    cells = [[
        _cell("Магазин", header=True),
        _cell("Товар", header=True),
        _cell("Ціна, грн", header=True),
        _cell("Знижка", align="center", header=True),
    ]]
    for p in ranked:
        price_cell = (
            [RichTextStrikethrough(text=_price(p.old_price)), " → ",
             RichTextBold(text=_price(p.new_price))]
            if p.has_discount else RichTextBold(text=_price(p.new_price))
        )
        cells.append([
            _cell(f"{p.store_emoji} {p.store_name}"),
            _cell(RichTextUrl(text=_short(p.title, 50), url=p.url)),
            _cell(price_cell),
            _cell(f"−{p.discount_pct}%" if p.has_discount else "—", align="center"),
        ])
    blocks.append(InputRichBlockTable(cells=cells, is_bordered=True, is_striped=True))
    blocks.append(_footer([], False))
    return InputRichMessage(blocks=blocks)


# ── список покупок ───────────────────────────────────────────────────────

def build_rich_shopping_list(
    items: dict[str, list[Product]],
    empty_items: list[str],
) -> InputRichMessage:
    """
    Проверка всего списка покупок разом:
    по каждому товару — лучшая найденная акция.
    """
    blocks: list = [InputRichBlockSectionHeading(text="🧾 Мій список покупок", size=2)]

    found_total = sum(1 for v in items.values() if v)
    saving = sum((v[0].old_price - v[0].new_price) for v in items.values() if v)

    blocks.append(InputRichBlockParagraph(text=[
        RichTextItalic(text=f"Зі списку зі знижкою зараз: "),
        RichTextBold(text=f"{found_total} з {len(items) + len(empty_items)}"),
        RichTextItalic(text=f". Загальна економія — "),
        RichTextBold(text=f"{saving:.2f} грн"),
        RichTextItalic(text="."),
    ]))

    if items:
        cells = [[
            _cell("Товар зі списку", header=True),
            _cell("Найкраща акція", header=True),
            _cell("Ціна, грн", header=True),
            _cell("−%", align="center", header=True),
        ]]
        for query, found in items.items():
            if not found:
                continue
            best = found[0]
            cells.append([
                _cell(RichTextBold(text=_short(query, 22))),
                _cell([f"{best.store_emoji} ", RichTextUrl(text=_short(best.title, 42), url=best.url)]),
                _cell([RichTextStrikethrough(text=_price(best.old_price)), " → ",
                       RichTextBold(text=_price(best.new_price))]),
                _cell(RichTextBold(text=f"−{best.discount_pct}%"), align="center"),
            ])
        blocks.append(InputRichBlockTable(cells=cells, is_bordered=True, is_striped=True))

    if empty_items:
        blocks.append(InputRichBlockParagraph(text=[
            RichTextItalic(text="Без знижок зараз: " + ", ".join(empty_items))
        ]))

    blocks.append(_footer([], False))
    return InputRichMessage(blocks=blocks)
