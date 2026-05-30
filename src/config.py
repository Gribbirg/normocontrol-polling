"""Загрузка конфига и персистентного состояния.

config.json — настройки и подписки (статичный файл, скрипт его не перезаписывает).
state.json — снапшот таблицы; пишется атомарно (tmp + os.replace), чтобы
переживать падения скрипта без порчи данных.

Пути можно переопределить через env CONFIG_PATH / STATE_PATH (удобно для деплоя
с персистентным volume).
"""
from __future__ import annotations

import json
import os
import sys

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SRC_DIR)
CONFIG_DIR = os.path.join(ROOT_DIR, "config")
CONFIG_PATH = os.environ.get("CONFIG_PATH", os.path.join(CONFIG_DIR, "config.json"))
STATE_PATH = os.environ.get("STATE_PATH", os.path.join(CONFIG_DIR, "state.json"))


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        sys.exit(f"Нет конфига: {CONFIG_PATH}\nСкопируй config.example.json -> config.json и заполни.")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    token = os.environ.get("TG_BOT_TOKEN") or cfg.get("bot_token")
    if not token:
        sys.exit("Не задан bot_token (в config.json или env TG_BOT_TOKEN).")
    cfg["bot_token"] = token
    cfg.setdefault("poll_interval", 30)
    cfg.setdefault("subscriptions", [])
    cfg.setdefault("listeners", [])
    cfg.setdefault("open_registration", False)  # строго: вне whitelist игнор даже /start
    sheet = cfg.get("sheet") or {}
    if not sheet.get("id") or not sheet.get("gid"):
        sys.exit("В config.sheet нужны поля id и gid.")
    return cfg


def load_config_raw() -> dict:
    """Сырой конфиг с диска (без подстановки токена из env) — для правок командами."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config_raw(raw: dict) -> None:
    """Атомарная запись config.json (команды бота меняют его на лету)."""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)  # атомарно: при падении старый state.json цел
