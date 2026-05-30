"""Управление подписками через команды бота (правят config.json на лету).

Бот обслуживает только чаты из allowlist (config.listeners — список chat_id).
Новый чат добавляется в список по /start (если config.open_registration=true).
В обслуживаемом чате команды доступны всем участникам.

Каждая команда привязана к чату, где она отправлена: бот находит (или создаёт)
подписку с этим chat_id и меняет её.

Команды:
    /start                 — зарегистрировать чат как слушателя + справка
    /help                  — справка
    /status                — статус поллинга
    /list                  — подписка этого чата
    /pwd                   — показать ID этого чата (работает и вне whitelist)
    /stats                 — сводка по отслеживаемому (антиплагиат/норм/допуск/прим)
    /watch <код|ФИО>       — добавить цель слежения (группу или студента)
    /unwatch <код|ФИО>     — убрать цель
    /ping <@user|id>       — добавить пинг
    /unping <@user|id>     — убрать пинг
    /dump                  — прислать полный дамп таблицы сейчас
    /test                  — тестовое сообщение
    /github                — ссылка на исходный код
    /stop                  — убрать чат из слушателей
"""
from __future__ import annotations

import re

from config import load_config_raw, load_config, load_state, save_config_raw
from report import esc, now_stamp, XLSX_MIME
from stats import build_stats
from telegram import Telegram
from xlsx import fetch_xlsx

GROUP_RE = re.compile(r"^[A-Za-zА-Яа-я]{2,}-\d+-\d+$")  # напр. ГРУППА-01-23


def is_group_code(arg: str) -> bool:
    return bool(GROUP_RE.match(arg.strip()))


def _find_sub(subs: list[dict], chat_id) -> dict | None:
    for s in subs:
        if str(s.get("chat_id")) == str(chat_id):
            return s
    return None


def _ensure_sub(raw: dict, chat_id) -> dict:
    subs = raw.setdefault("subscriptions", [])
    sub = _find_sub(subs, chat_id)
    if sub is None:
        sub = {
            "name": f"Чат {chat_id}",
            "groups": [], "students": [], "columns": [],
            "chat_id": str(chat_id), "mentions": [], "attach_dump": True,
        }
        subs.append(sub)
    return sub


def _mention_label(m) -> str:
    if isinstance(m, dict):
        return m.get("name") or m.get("username") or f"id{m.get('id')}"
    return str(m)


def _mention_matches(m, arg: str) -> bool:
    arg = arg.lstrip("@").strip().lower()
    if isinstance(m, dict):
        return (str(m.get("id")) == arg
                or str(m.get("username", "")).lstrip("@").lower() == arg
                or str(m.get("name", "")).lower() == arg)
    return str(m).lstrip("@").lower() == arg


class CommandHandler:
    def __init__(self, tg: Telegram):
        self.tg = tg

    # -- утилиты -----------------------------------------------------------
    def reply(self, chat_id, text: str):
        self.tg.send_message(chat_id, text)

    @staticmethod
    def is_listener(raw: dict, chat_id) -> bool:
        return str(chat_id) in [str(x) for x in raw.get("listeners", [])]

    # -- основной диспетчер ------------------------------------------------
    def handle(self, message: dict):
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return

        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@")[0].lower()  # убираем /cmd@botname в группах
        arg = parts[1].strip() if len(parts) > 1 else ""

        raw = load_config_raw()
        open_access = raw.get("open_access", False)  # вайтлист не обязателен — отвечаем всем
        listening = open_access or self.is_listener(raw, chat_id)

        # /start — точка входа: регистрирует чат как слушателя
        if cmd == "/start":
            if listening:
                return self.cmd_help(chat_id)
            if raw.get("open_registration", False):
                listeners = raw.setdefault("listeners", [])
                limit = raw.get("max_listeners", 50)
                if len(listeners) >= limit:
                    return self.reply(chat_id,
                        "🚫 Достигнут лимит чатов-слушателей. Регистрация временно закрыта.")
                listeners.append(chat_id)
                save_config_raw(raw)
                self.reply(chat_id, "✅ Чат добавлен в слушатели F5 Господина.")
                return self.cmd_help(chat_id)
            # регистрация закрыта — молча игнорируем чужой чат
            return

        # /pwd доступна везде: иначе не узнать chat_id, чтобы добавить чат в whitelist
        if cmd == "/pwd":
            return self.cmd_pwd(chat_id)

        # вне allowlist бот не отвечает (если open_access=true — listening всегда true)
        if not listening:
            return

        if cmd == "/help":
            return self.cmd_help(chat_id)
        if cmd == "/status":
            return self.cmd_status(chat_id)
        if cmd == "/list":
            return self.cmd_list(chat_id)
        if cmd == "/stats":
            return self.cmd_stats(chat_id)
        if cmd == "/dump":
            return self.cmd_dump(chat_id)
        if cmd == "/github":
            return self.cmd_github(chat_id)
        if cmd == "/test":
            return self.reply(chat_id, "✅ <b>F5 Господина</b> на связи. Команды: /help")
        if cmd == "/stop":
            return self.cmd_stop(raw, chat_id)

        if cmd in ("/watch", "/unwatch", "/ping", "/unping"):
            if not arg:
                return self.reply(chat_id, f"Использование: {cmd} &lt;аргумент&gt;. См. /help")
            handler = {
                "/watch": self.cmd_watch, "/unwatch": self.cmd_unwatch,
                "/ping": self.cmd_ping, "/unping": self.cmd_unping,
            }[cmd]
            return handler(raw, chat_id, arg)

        self.reply(chat_id, "❓ Неизвестная команда. /help")

    def cmd_stop(self, raw, chat_id):
        raw["listeners"] = [x for x in raw.get("listeners", []) if str(x) != str(chat_id)]
        save_config_raw(raw)
        self.reply(chat_id, "🛑 Чат убран из слушателей. Вернуться: /start")

    # -- команды -----------------------------------------------------------
    def cmd_help(self, chat_id):
        self.reply(chat_id,
            "🤖 <b>F5 Господина</b> — слежу за таблицей нормоконтроля.\n\n"
            "<b>Команды</b> (привязаны к этому чату):\n"
            "/watch <code>ГРУППА-01-23</code> — следить за группой\n"
            "/watch <code>Иванов</code> — следить за студентом (по ФИО)\n"
            "/unwatch <code>...</code> — перестать следить\n"
            "/ping <code>@user</code> или <code>id</code> — пинговать при изменениях\n"
            "/unping <code>...</code> — убрать пинг\n"
            "/list — что отслеживается в этом чате\n"
            "/pwd — показать ID этого чата\n"
            "/stats — сводка: антиплагиат, нормоконтроль, допуск, примечания\n"
            "/status — статус поллинга\n"
            "/dump — прислать полный дамп таблицы сейчас\n"
            "/test — проверка связи\n"
            "/github — исходный код бота\n"
            "/stop — убрать этот чат из слушателей\n\n"
            "Изменения подписок применяются на лету.")

    def cmd_github(self, chat_id):
        self.reply(chat_id,
            "🐙 Исходный код F5 Господина:\n"
            "https://github.com/Gribbirg/normocontrol-polling")

    def cmd_status(self, chat_id):
        cfg = load_config()
        st = load_state()
        snap = st.get("snapshot", {})
        self.reply(chat_id,
            f"📡 <b>Статус</b>\n"
            f"Интервал опроса: {cfg.get('poll_interval')} c\n"
            f"Студентов в снимке: {len(snap)}\n"
            f"Последнее обновление: {esc(str(st.get('updated', '—')))}\n"
            f"Активных подписок: {len(cfg.get('subscriptions', []))}")

    def cmd_list(self, chat_id):
        raw = load_config_raw()
        sub = _find_sub(raw.get("subscriptions", []), chat_id)
        if not sub:
            return self.reply(chat_id,
                "В этом чате пока нет подписки. Добавь: /watch ГРУППА-01-23")
        lines = ["📋 <b>Подписка этого чата</b>"]
        lines.append(f"Группы: {esc(', '.join(sub.get('groups', [])) or '—')}")
        lines.append(f"Студенты: {esc(', '.join(sub.get('students', [])) or '—')}")
        pings = [esc(_mention_label(m)) for m in sub.get("mentions", [])]
        lines.append(f"Пинги: {', '.join(pings) or '—'}")
        self.reply(chat_id, "\n".join(lines))

    def cmd_pwd(self, chat_id):
        self.reply(chat_id, f"🆔 ID этого чата: <code>{esc(str(chat_id))}</code>")

    def cmd_stats(self, chat_id):
        raw = load_config_raw()
        sub = _find_sub(raw.get("subscriptions", []), chat_id)
        if not sub:
            return self.reply(chat_id,
                "В этом чате нет подписки. Добавь цель: /watch ГРУППА-01-23")
        try:
            cfg = load_config()
            self.reply(chat_id, build_stats(cfg, sub))
        except Exception as e:  # noqa: BLE001
            self.reply(chat_id, f"⚠️ Не удалось собрать статистику: {esc(str(e))}")

    def cmd_dump(self, chat_id):
        try:
            cfg = load_config()
            raw = fetch_xlsx(cfg["sheet"])
            self.tg.send_document(chat_id, f"nk_dump_{now_stamp()}.xlsx", raw,
                                  caption="📎 Текущий дамп таблицы (с цветами)",
                                  mime=XLSX_MIME)
        except Exception as e:  # noqa: BLE001
            self.reply(chat_id, f"⚠️ Не удалось получить дамп: {esc(str(e))}")

    def cmd_watch(self, raw, chat_id, arg):
        sub = _ensure_sub(raw, chat_id)
        field = "groups" if is_group_code(arg) else "students"
        lst = sub.setdefault(field, [])
        if any(x.strip().lower() == arg.strip().lower() for x in lst):
            return self.reply(chat_id, f"Уже отслеживается: <code>{esc(arg)}</code>")
        lst.append(arg)
        save_config_raw(raw)
        kind = "группой" if field == "groups" else "студентом"
        self.reply(chat_id, f"✅ Слежу за {kind}: <code>{esc(arg)}</code>")

    def cmd_unwatch(self, raw, chat_id, arg):
        sub = _find_sub(raw.get("subscriptions", []), chat_id)
        if not sub:
            return self.reply(chat_id, "В этом чате нет подписки.")
        removed = False
        for field in ("groups", "students"):
            lst = sub.get(field, [])
            new = [x for x in lst if x.strip().lower() != arg.strip().lower()]
            if len(new) != len(lst):
                sub[field] = new
                removed = True
        if removed:
            save_config_raw(raw)
            self.reply(chat_id, f"🗑 Больше не слежу за: <code>{esc(arg)}</code>")
        else:
            self.reply(chat_id, f"Не нашёл в подписке: <code>{esc(arg)}</code>")

    def cmd_ping(self, raw, chat_id, arg):
        sub = _ensure_sub(raw, chat_id)
        mentions = sub.setdefault("mentions", [])
        if any(_mention_matches(m, arg) for m in mentions):
            return self.reply(chat_id, f"Уже пингуется: <code>{esc(arg)}</code>")
        if arg.lstrip("@").isdigit():
            mentions.append({"id": int(arg.lstrip("@"))})
        elif arg.startswith("@"):
            mentions.append(arg)
        else:
            mentions.append("@" + arg)
        save_config_raw(raw)
        self.reply(chat_id, f"🔔 Буду пинговать: <code>{esc(arg)}</code>")

    def cmd_unping(self, raw, chat_id, arg):
        sub = _find_sub(raw.get("subscriptions", []), chat_id)
        if not sub:
            return self.reply(chat_id, "В этом чате нет подписки.")
        mentions = sub.get("mentions", [])
        new = [m for m in mentions if not _mention_matches(m, arg)]
        if len(new) != len(mentions):
            sub["mentions"] = new
            save_config_raw(raw)
            self.reply(chat_id, f"🔕 Убрал пинг: <code>{esc(arg)}</code>")
        else:
            self.reply(chat_id, f"Не нашёл такой пинг: <code>{esc(arg)}</code>")
