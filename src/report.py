"""Формирование текста репорта и его отправка в Telegram (с дампом таблицы)."""
from __future__ import annotations

import datetime
import html
import sys

from telegram import Telegram

def esc(s: str) -> str:
    return html.escape(s, quote=False)


def trunc(s: str, n: int = 300) -> str:
    s = s.replace("\n", " ⏎ ")
    return s if len(s) <= n else s[: n - 1] + "…"


def now_stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def render_mention(m) -> str:
    """Упоминание из конфига. Пинг — по @username (надёжно уведомляет).
      - строку "@username"              -> пинг по нику
      - строку-число                    -> пинг по id (если ника нет)
      - объект {"name","username","id"} -> по username; иначе по id
    """
    if isinstance(m, dict):
        uname = m.get("username")
        if uname:
            return esc(uname if uname.startswith("@") else "@" + uname)
        uid = m.get("id")
        if uid:
            name = m.get("name") or f"id{uid}"
            return f'<a href="tg://user?id={uid}">{esc(name)}</a>'
        return esc(m.get("name") or "?")
    s = str(m).strip()
    if s.startswith("@"):
        return esc(s)
    if s.isdigit():
        return f'<a href="tg://user?id={s}">пользователь</a>'
    return esc("@" + s)


def describe_change(ch: dict) -> str:
    """Описание одного изменения ячейки: цвет (главный сигнал) и/или текст."""
    col = esc(ch["column"])
    ov, nv = ch.get("old_v", ""), ch.get("new_v", "")
    oc, nc = ch.get("old_c", ""), ch.get("new_c", "")
    parts = []
    if nc != oc:
        if nc and "зелён" in nc:
            parts.append("🟢 <b>отмечено зелёным (успех)</b>")
        elif nc:
            parts.append(f"🎨 заливка → {esc(nc)}")
        else:
            parts.append("⬜ заливка снята")
    if nv != ov:
        if not ov:
            parts.append(f"🆕 текст: {esc(trunc(nv))}")
        elif not nv:
            parts.append(f"🗑 текст удалён (было: {esc(trunc(ov))})")
        else:
            parts.append(f"✏️ текст: {esc(trunc(ov))} → {esc(trunc(nv))}")
    return f"   «{col}»: " + "; ".join(parts)


def build_report(sub: dict, changes: list[dict]) -> str:
    lines = [f"📋 <b>Нормоконтроль</b> — {esc(sub.get('name', 'подписка'))}"]
    mentions = sub.get("mentions", [])
    if mentions:
        lines.append("🔔 " + " ".join(render_mention(m) for m in mentions))
    lines.append("")

    by_student: dict[str, list[dict]] = {}
    for ch in changes:
        label = f"{ch['fio']} ({ch['group']})" if ch["group"] else ch["fio"]
        by_student.setdefault(label, []).append(ch)

    for student, chs in by_student.items():
        lines.append(f"👤 <b>{esc(student)}</b>")
        for ch in chs:
            lines.append(describe_change(ch))
        lines.append("")
    return "\n".join(lines).strip()


def split_chunks(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    return chunks


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def send_report(tg: Telegram, sub: dict, changes: list[dict], dump_bytes: bytes) -> None:
    chat_id = sub["chat_id"]
    text = build_report(sub, changes)
    stamp = now_stamp()
    fname = f"nk_dump_{stamp}.xlsx"
    attach = sub.get("attach_dump", True)

    # компактный репорт -> xlsx-дамп (с цветами) и диф прямо в подписи
    if attach and len(text) <= 1000:
        res = tg.send_document(chat_id, fname, dump_bytes, caption=text, mime=XLSX_MIME)
        if not res.get("ok"):
            print(f"[!] sendDocument({chat_id}): {res}", file=sys.stderr)
        return

    for chunk in split_chunks(text):
        res = tg.send_message(chat_id, chunk)
        if not res.get("ok"):
            print(f"[!] sendMessage({chat_id}): {res}", file=sys.stderr)
    if attach:
        res = tg.send_document(chat_id, fname, dump_bytes,
                               caption=f"📎 Полный дамп таблицы ({stamp})", mime=XLSX_MIME)
        if not res.get("ok"):
            print(f"[!] sendDocument({chat_id}): {res}", file=sys.stderr)
