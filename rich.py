"""
rich.py — «богатый» вывод через Rich Messages (Telegram Bot API 10.2, июль 2026).

Rich Messages позволяют ботам присылать по-настоящему свёрстанные
сообщения: заголовки, коллажи фотографий, НАСТОЯЩИЕ таблицы с рамками
и «зеброй», футеры. Мы собираем сообщение из блоков:

  ┌ 🛒 Заголовок (название товара из запроса)
  ├ Коллаж из фото топ-товаров со скидками (подписи: −%, магазин)
  ├ Таблица: Магазин | Товар (кликабельно) | Ціна (старая → новая) | Знижка
  └ Футер: недоступные магазины / пометка «из кэша» / время

Если у Telegram или клиента что-то пойдёт не так с rich-сообщением,
bot.py автоматически отправит классический текстовый вариант
(см. formatter.py) — бот не останется без ответа никогда.

Требуется aiogram >= 3.30 (поддержка Bot API 10.2).
"""
from __future__ import annotations

import time

from aiogram.types import (
    InputMediaPhoto,
    InputRichBlockCollage,
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

MAX_TABLE_ROWS = 12      # максимум строк в таблице (по всем магазинам)
MAX_COLLAGE_PHOTOS = 4   # максимум фото в коллаже

# ── маленькие помощники ──────────────────────────────────────────────────────


def _price(value: float) -> str:
    return f"{value:.2f}"


def _short(text: str, limit: int) -> str:
    """Обрезает длинные названия, чтобы таблица оставалась аккуратной."""
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _cell(text, align: str = "left", header: bool = False) -> RichBlockTableCell:
    return RichBlockTableCell(
        align=align,
        valign="middle",
        text=text,
        is_header=header or None,
    )


# ── главный сборщик ──────────────────────────────────────────────────────────


def build_rich_results(
    query: str,
    products: list[Product],
    failed_stores: list[str],
    from_cache: bool = False,
) -> InputRichMessage:
    """Собирает rich-сообщение с результатами по одному запрошенному товару."""
    deals = select_deals(products)

    blocks: list = [
        InputRichBlockSectionHeading(text=f"🛒 {query.strip().capitalize()}", size=2),
    ]

    if deals:
        # 1) Коллаж из фото самых выгодных позиций
        with_photo = [p for p in deals if p.image_url][:MAX_COLLAGE_PHOTOS]
        if with_photo:
            blocks.append(
                InputRichBlockCollage(
                    blocks=[
                        InputRichBlockPhoto(
                            photo=InputMediaPhoto(media=p.image_url),
                            caption=RichBlockCaption(
                                text=[
                                    RichTextBold(text=f"−{p.discount_pct}% "),
                                    f"{p.store_emoji} {p.store_name} · {_price(p.new_price)} грн",
                                ]
                            ),
                        )
                        for p in with_photo
                    ]
                )
            )

        # 2) Таблица со всеми скидками (названия кликабельны)
        cells: list[list[RichBlockTableCell]] = [
            [
                _cell("Магазин", header=True),
                _cell("Товар", header=True),
                _cell("Ціна, грн", header=True),
                _cell("Знижка", align="center", header=True),
            ]
        ]
        for p in deals[:MAX_TABLE_ROWS]:
            cells.append(
                [
                    _cell(f"{p.store_emoji} {p.store_name}"),
                    _cell(RichTextUrl(text=_short(p.title, 60), url=p.url)),
                    _cell(
                        [
                            RichTextStrikethrough(text=_price(p.old_price)),
                            " → ",
                            RichTextBold(text=_price(p.new_price)),
                        ]
                    ),
                    _cell(RichTextBold(text=f"−{p.discount_pct}%"), align="center"),
                ]
            )
        blocks.append(InputRichBlockTable(cells=cells, is_bordered=True, is_striped=True))

        hidden = len(deals) - min(len(deals), MAX_TABLE_ROWS)
        summary = f"Знайдено акцій: {len(deals)}. Назви товарів у таблиці клікабельні 🔗"
        if hidden > 0:
            summary += f" (ще {hidden} не помістилось)"
        blocks.append(InputRichBlockParagraph(text=[RichTextItalic(text=summary)]))
    else:
        blocks.append(InputRichBlockParagraph(text="😕 Знижок не знайдено."))
        reference = select_reference(products)
        if reference:
            blocks.append(
                InputRichBlockParagraph(
                    text=[RichTextItalic(text="Для орієнтиру, звичайні ціни:")]
                )
            )
            ref_cells = [
                [
                    _cell("Магазин", header=True),
                    _cell("Товар", header=True),
                    _cell("Ціна, грн", header=True),
                ]
            ]
            for p in reference:
                ref_cells.append(
                    [
                        _cell(f"{p.store_emoji} {p.store_name}"),
                        _cell(RichTextUrl(text=_short(p.title, 60), url=p.url)),
                        _cell(RichTextBold(text=_price(p.new_price))),
                    ]
                )
            blocks.append(InputRichBlockTable(cells=ref_cells, is_bordered=True))

    # 3) Футер: проблемы и служебные пометки
    footer_parts: list[str] = []
    if failed_stores:
        footer_parts.append("⚠️ Не відповіли: " + ", ".join(failed_stores))
    if from_cache:
        footer_parts.append("⚡ з кешу")
    footer_parts.append("🕐 " + time.strftime("%H:%M"))
    blocks.append(InputRichBlockFooter(text="  ·  ".join(footer_parts)))

    return InputRichMessage(blocks=blocks)
