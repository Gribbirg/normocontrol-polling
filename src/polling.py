#!/usr/bin/env python3
"""F5 Господина — поллинг Google-таблицы нормоконтроля и репорт изменений в Telegram.

Конфиг-driven: любое число подписок (группы и/или конкретные студенты ->
любой Telegram-чат -> пинги любых людей). Зависимостей нет, только stdlib.

Команды:
    python3 polling.py run     # бесконечный цикл с интервалом poll_interval
    python3 polling.py once    # один проход (удобно для cron)
    python3 polling.py init    # засеять состояние без отправки уведомлений
    python3 polling.py test    # тестовое сообщение во все чаты подписок

Конфиг: config.json рядом со скриптом (см. config.example.json).
Токен можно переопределить через env TG_BOT_TOKEN.
"""
from __future__ import annotations

import fcntl
import os
import sys
import threading
import time

from commands import CommandHandler
from config import STATE_PATH, load_config, load_state, save_state
from diff import diff_snapshots, sub_matches
from report import esc, now_stamp, render_mention, send_report
from telegram import Telegram
from xlsx import fetch_xlsx, parse_workbook


def watched_markers(cfg: dict) -> list[str]:
    """Коды групп из всех подписок (+ sheet.tab_match) — для выбора нужного листа."""
    markers = []
    for sub in cfg.get("subscriptions", []):
        markers += sub.get("groups", [])
    extra = (cfg.get("sheet") or {}).get("tab_match")
    if extra:
        markers.append(extra)
    return markers


def poll_once(cfg: dict, tg: Telegram, alert: bool = True) -> None:
    raw = fetch_xlsx(cfg["sheet"])
    new_snap = parse_workbook(raw, watched_markers(cfg))
    state = load_state()
    old_snap = state.get("snapshot", {})

    if not old_snap:
        save_state({"snapshot": new_snap, "updated": now_stamp()})
        print(f"[init] засеяно состояние: {len(new_snap)} студентов, уведомления не отправлялись.")
        return

    changes = diff_snapshots(old_snap, new_snap)
    if not changes:
        print(f"[{now_stamp()}] изменений нет ({len(new_snap)} студентов).")
        save_state({"snapshot": new_snap, "updated": now_stamp()})
        return

    print(f"[{now_stamp()}] изменений: {len(changes)}")
    if alert:
        for sub in cfg["subscriptions"]:
            sub_changes = [c for c in changes if sub_matches(sub, c)]
            if sub_changes:
                print(f"    -> подписка «{sub.get('name')}»: {len(sub_changes)} изм.")
                send_report(tg, sub, sub_changes, raw)

    save_state({"snapshot": new_snap, "updated": now_stamp()})


def acquire_single_instance_lock():
    """Эксклюзивный лок: не даёт запустить второй инстанс (иначе getUpdates
    конфликтуют 409 и бот тормозит до минуты). Возвращает дескриптор лока."""
    lock_path = os.path.join(os.path.dirname(STATE_PATH) or ".", ".f5gospodina.lock")
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("Уже запущен другой инстанс F5 Господина (лок занят). "
                 "Останови его, иначе бот будет тормозить из-за конфликта getUpdates.")
    fd.write(str(os.getpid()))
    fd.flush()
    return fd  # держим открытым весь рантайм


def sheet_loop(tg: Telegram, stop: threading.Event) -> None:
    """Поток опроса таблицы. Конфиг перечитывается каждый цикл — правки команд
    подхватываются на лету."""
    while not stop.is_set():
        try:
            cfg = load_config()
            poll_once(cfg, tg, alert=True)
        except Exception as e:  # noqa: BLE001 — цикл не должен падать
            print(f"[!] ошибка опроса: {e}", file=sys.stderr)
        stop.wait(int(load_config().get("poll_interval", 30)))


def bot_loop(tg: Telegram, stop: threading.Event) -> None:
    """Поток команд бота: long-poll getUpdates и обработка."""
    handler = CommandHandler(tg)
    # пропускаем «хвост» старых апдейтов, чтобы не выполнять команды до запуска
    offset = None
    drain = tg.get_updates(offset=-1, timeout=0)
    if drain.get("ok") and drain["result"]:
        offset = drain["result"][-1]["update_id"] + 1
    print("F5 Господина: слушатель команд запущен.")
    while not stop.is_set():
        try:
            res = tg.get_updates(offset=offset, timeout=25)
            if not res.get("ok"):
                # частый случай — 409 Conflict: запущено несколько инстансов бота
                print(f"[!] getUpdates не ok: {res.get('error_code')} "
                      f"{res.get('description')}", file=sys.stderr)
                time.sleep(1)
                continue
            for upd in res["result"]:
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if msg:
                    try:
                        handler.handle(msg)
                    except Exception as e:  # noqa: BLE001
                        print(f"[!] ошибка команды: {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[!] ошибка getUpdates: {e}", file=sys.stderr)
            time.sleep(3)


def cmd_test(cfg: dict, tg: Telegram) -> None:
    for sub in cfg["subscriptions"]:
        text = (f"✅ <b>F5 Господина</b> на связи.\nПодписка: "
                f"{esc(sub.get('name', '?'))}\nГруппы: {esc(', '.join(sub.get('groups', [])) or '—')}\n"
                f"Студенты: {esc(', '.join(sub.get('students', [])) or '—')}")
        mentions = sub.get("mentions", [])
        if mentions:
            text += "\n🔔 " + " ".join(render_mention(m) for m in mentions)
        res = tg.send_message(sub["chat_id"], text)
        print(f"test -> {sub['chat_id']}: ok={res.get('ok')}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    cfg = load_config()
    tg = Telegram(cfg["bot_token"])

    if cmd == "test":
        cmd_test(cfg, tg)
    elif cmd == "init":
        if os.path.exists(STATE_PATH):
            os.remove(STATE_PATH)
        poll_once(cfg, tg, alert=False)
    elif cmd == "once":
        poll_once(cfg, tg, alert=True)
    elif cmd == "run":
        # таблица + команды бота в двух потоках
        _lock = acquire_single_instance_lock()  # noqa: F841 — держим лок открытым
        print(f"F5 Господина: поллинг каждые {cfg.get('poll_interval', 30)}s "
              f"+ команды бота. Ctrl+C для выхода.")
        stop = threading.Event()
        t = threading.Thread(target=sheet_loop, args=(tg, stop), daemon=True)
        t.start()
        try:
            bot_loop(tg, stop)
        except KeyboardInterrupt:
            stop.set()
            print("\nОстановлено.")
    elif cmd == "bot":
        # только слушатель команд (без опроса таблицы)
        _lock = acquire_single_instance_lock()  # noqa: F841 — держим лок открытым
        print("F5 Господина: только команды бота. Ctrl+C для выхода.")
        stop = threading.Event()
        try:
            bot_loop(tg, stop)
        except KeyboardInterrupt:
            stop.set()
            print("\nОстановлено.")
    else:
        sys.exit(f"Неизвестная команда: {cmd}. Доступно: run, once, init, test, bot.")


if __name__ == "__main__":
    main()
