"""
config.py — чтение настроек из файла .env.

Здесь нет никакой логики бота: только аккуратная загрузка токена
и твоего Telegram ID с понятными сообщениями об ошибках.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

# Папка, в которой лежит этот файл (корень проекта)
BASE_DIR = Path(__file__).resolve().parent


@dataclass
class Config:
    bot_token: str
    allowed_user_id: int


def _fail(message: str) -> None:
    """Печатает понятную ошибку и останавливает программу."""
    print()
    print("❌ Ошибка настройки:", message)
    print("   Открой файл .env в папке проекта и исправь значение.")
    print("   Образец правильного файла — .env.example")
    print()
    sys.exit(1)


def load_config() -> Config:
    """Читает .env и возвращает настройки. Вызывается один раз при старте бота."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        _fail(
            "не найден файл .env. Скопируй .env.example в .env "
            "и заполни его (см. README.md, шаг 3)."
        )

    values = dotenv_values(env_path)

    token = (values.get("BOT_TOKEN") or "").strip()
    if not token or "вставь" in token or ":" not in token:
        _fail("BOT_TOKEN пуст или заполнен неправильно. Возьми токен у @BotFather.")

    raw_id = (values.get("ALLOWED_USER_ID") or "").strip()
    if not raw_id.isdigit():
        _fail(
            "ALLOWED_USER_ID должен быть числом (твой Telegram ID). "
            "Узнай его у @userinfobot."
        )

    return Config(bot_token=token, allowed_user_id=int(raw_id))
