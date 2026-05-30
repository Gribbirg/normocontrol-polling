"""Сводная статистика по отслеживаемым студентам/группам (команда /stats).

Читает живую таблицу (основную таблицу A..I, как и трекинг) и считает статусы.
Правый блок «Антиплагиат» (кол. 10+) — мусор, не учитывается. Логику
трекинга/диффа не трогает.

Колонки распознаются по ключевым словам в заголовке — это устойчиво к
перестановке колонок. Цвет ячейки — основной сигнал: зелёная = пройдено.
"""
from __future__ import annotations

from report import esc
from xlsx import MAX_TRACK_COL, fetch_xlsx, parse_workbook

# Считаем только основную таблицу (A..I). Правый блок «Антиплагиат» (кол. 10+)
# — мусор, его не учитываем. Нужные колонки (Антиплагиат, Прим., Допуск к
# защите) и так лежат в основной таблице.
STATS_MAX_COL = MAX_TRACK_COL

# (название категории, ключевые слова в заголовке колонки — lowercase, подстрока).
# Показываем всегда: пустая колонка = 0% — это тоже сигнал.
# «Нормоконтроль» и «Примечания» — одна и та же колонка «Прим.»: зелёная заливка
# = нормоконтроль пройден, текст в ячейке = примечание.
CATEGORIES = [
    ("Антиплагиат", ("антиплагиат",)),
    ("Нормоконтроль", ("прим",)),
    ("Допуск к защите", ("допуск",)),
    ("Примечания", ("прим",)),
]
# Категории, которые считаем по зелёной заливке (пройдено/не пройдено).
COLOR_CATS = {"Антиплагиат", "Нормоконтроль", "Допуск к защите"}


def _is_green(c: str) -> bool:
    return (c or "").startswith("🟢")


def _row_cats(cells: dict) -> dict:
    """Для строки студента: категория -> {value, green}."""
    res: dict[str, dict] = {}
    for name, keywords in CATEGORIES:
        vals: list[str] = []
        green = False
        for label, cell in cells.items():
            ll = label.lower()
            if any(k in ll for k in keywords):
                v = (cell.get("v") or "").strip()
                if v:
                    vals.append(v)
                if _is_green(cell.get("c", "")):
                    green = True
        res[name] = {"value": vals[0] if vals else "", "green": green}
    return res


def _group_lines(members: list[dict]) -> list[str]:
    n = len(members)
    cats = [_row_cats(m["cells"]) for m in members]
    out: list[str] = []
    for name, _ in CATEGORIES:
        if name in COLOR_CATS:
            ok = sum(1 for c in cats if c[name]["green"])
            pct = round(ok * 100 / n) if n else 0
            out.append(f"  • {name}: {ok}/{n} ({pct}%)")
        else:  # примечания — у скольких есть заметка
            with_note = sum(1 for c in cats if c[name]["value"])
            out.append(f"  • {name}: с заметками {with_note}/{n}")
    return out


def _student_lines(row: dict) -> list[str]:
    cats = _row_cats(row["cells"])
    out: list[str] = []
    for name, _ in CATEGORIES:
        c = cats[name]
        if name in COLOR_CATS:
            mark = "🟢" if c["green"] else "⚪️"
            extra = f" {esc(c['value'])}" if c["value"] else ""
            out.append(f"  • {name}: {mark}{extra}")
        else:
            out.append(f"  • {name}: {esc(c['value']) or '—'}")
    return out


def build_stats(cfg: dict, sub: dict | None) -> str:
    """Текст статистики для подписки чата (HTML для Telegram).

    Группы агрегируются (кол-во/всего и %), отдельные студенты — детально.
    Если подписка пустая (следит за всем) — агрегируем по всем группам листа.
    """
    raw = fetch_xlsx(cfg["sheet"])
    markers = cfg["sheet"].get("markers", [])
    snap = parse_workbook(raw, markers, max_col=STATS_MAX_COL)
    rows = list(snap.values())

    sub = sub or {}
    groups = [g.strip() for g in sub.get("groups", []) if g.strip()]
    students = [s.strip() for s in sub.get("students", []) if s.strip()]

    lines = ["📊 <b>Статистика</b> — " + esc(sub.get("name", "этот чат"))]

    if not groups and not students:  # следим за всем -> по всем группам листа
        groups = sorted({r["_group"] for r in rows if r["_group"]})

    if not groups and not students:
        lines.append("\nНечего показывать: добавь цель через /watch.")
        return "\n".join(lines)

    for g in groups:
        members = [r for r in rows if r["_group"].strip().lower() == g.lower()]
        lines.append("")
        lines.append(f"🎓 <b>{esc(g)}</b> — {len(members)} студ.")
        if members:
            lines += _group_lines(members)
        else:
            lines.append("  нет данных в таблице")

    for s in students:
        matched = [r for r in rows if s.lower() in r["_fio"].lower()]
        if not matched:
            lines.append("")
            lines.append(f"👤 {esc(s)} — не найден в таблице")
            continue
        for r in matched:
            lines.append("")
            lines.append(f"👤 <b>{esc(r['_fio'])}</b> ({esc(r['_group'])})")
            lines += _student_lines(r)

    return "\n".join(lines)
