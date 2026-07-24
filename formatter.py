"""
formatter.py — сборка красивого ответа бота.

На вход: запрос («макарони»), результаты по магазинам, список магазинов,
которые не ответили. На выход: готовый HTML-текст для Telegram.

Формат блока (как в ТЗ):
    🛒 макарони

    🟢 Сільпо — Спагетті «Премія» 500г
       💸 32.90 → 24.90 грн (−24%)
       🔗 товар на сайті

Сортировка — по размеру скидки (сначала самые большие),
не больше 5 позиций на магазин.
"""
from __future__ import annotations

import html

from parsers.base import Product

MAX_PER_STORE = 5        # максимум позиций одного магазина в ответе
MAX_REFERENCE_ITEMS = 2  # сколько «обычных цен» показать, если скидок нет


def _price(value: float) -> str:
    """32.9 → '32.90' (всегда две цифры после точки, как на ценниках)."""
    return f"{value:.2f}"


def _format_product(p: Product) -> str:
    """Одна позиция со скидкой — три строки."""
    title = html.escape(p.title)
    # фасовку показываем, только если она информативна («500г», «1 кг»),
    # а не дежурное «шт», и если её ещё нет в названии
    unit_useful = p.unit and p.unit.lower().strip(".") not in ("шт", "уп", "од")
    unit = f" ({html.escape(p.unit)})" if unit_useful and p.unit.lower() not in p.title.lower() else ""
    link = f'<a href="{html.escape(p.url, quote=True)}">товар на сайті</a>'
    return (
        f"{p.store_emoji} <b>{p.store_name}</b> — {title}{unit}\n"
        f"   💸 <s>{_price(p.old_price)}</s> → <b>{_price(p.new_price)} грн</b> (−{p.discount_pct}%)\n"
        f"   🔗 {link}"
    )


def _format_reference(p: Product) -> str:
    """Одна позиция без скидки (для ориентира) — одна строка."""
    title = html.escape(p.title)
    link = f'<a href="{html.escape(p.url, quote=True)}">{title}</a>'
    return f"• {p.store_emoji} {p.store_name}: {link} — {_price(p.new_price)} грн"


def build_message(
    query: str,
    products: list[Product],
    failed_stores: list[str],
    from_cache: bool = False,
) -> str:
    """
    Собирает готовое сообщение по одному запрошенному товару.

    products      — ВСЕ найденные позиции всех магазинов (со скидками и без)
    failed_stores — названия магазинов, которые не ответили
    from_cache    — True, если весь результат взят из кэша
    """
    header = f"🛒 <b>{html.escape(query)}</b>"

    # 1) позиции со скидкой, отсортированные по проценту скидки
    discounted = sorted(
        (p for p in products if p.has_discount),
        key=lambda p: p.discount_pct,
        reverse=True,
    )

    # не больше MAX_PER_STORE позиций на магазин
    per_store_count: dict[str, int] = {}
    selected: list[Product] = []
    for p in discounted:
        if per_store_count.get(p.store_key, 0) >= MAX_PER_STORE:
            continue
        per_store_count[p.store_key] = per_store_count.get(p.store_key, 0) + 1
        selected.append(p)

    parts: list[str] = [header]

    if selected:
        parts.extend(_format_product(p) for p in selected)
    else:
        parts.append("😕 Знижок не знайдено.")
        # покажем пару обычных цен для ориентира (самые дешёвые)
        reference = sorted(
            (p for p in products if not p.has_discount and p.new_price > 0),
            key=lambda p: p.new_price,
        )[:MAX_REFERENCE_ITEMS]
        if reference:
            parts.append(
                "Для орієнтиру, звичайні ціни:\n"
                + "\n".join(_format_reference(p) for p in reference)
            )

    footer_lines: list[str] = []
    if failed_stores:
        footer_lines.append("⚠️ Не відповіли: " + ", ".join(failed_stores))
    if from_cache:
        footer_lines.append("⚡ Результат з кешу (оновлюється раз на 3 години)")
    if footer_lines:
        parts.append("\n".join(footer_lines))

    # блоки разделяем пустой строкой
    return "\n\n".join(parts)


def truncate_for_telegram(text: str, limit: int = 4000) -> str:
    """Telegram не принимает сообщения длиннее 4096 символов — подстрахуемся."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # не обрываем посреди HTML-тега или посреди блока
    safe_cut = cut.rsplit("\n\n", 1)[0]
    return safe_cut + "\n\n…(список скорочено)"
