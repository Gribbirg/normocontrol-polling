"""Вычисление изменений между снапшотами и матчинг изменений на подписки."""
from __future__ import annotations


def _cell(c) -> tuple[str, str]:
    """Нормализует ячейку к (текст, цвет). Поддерживает старый формат (строка)."""
    if isinstance(c, dict):
        return c.get("v", ""), c.get("c", "")
    return (c or ""), ""


def diff_snapshots(old: dict, new: dict) -> list[dict]:
    """Изменения на уровне ячеек (учитывает и текст, и цвет заливки).

    Элемент: {group, fio, column, old_v, new_v, old_c, new_c}.
    """
    changes: list[dict] = []
    for key, rec in new.items():
        old_rec = old.get(key, {})
        old_cells = old_rec.get("cells", {}) if isinstance(old_rec, dict) else {}
        new_cells = rec["cells"]
        for col in set(old_cells) | set(new_cells):
            ov, oc = _cell(old_cells.get(col))
            nv, nc = _cell(new_cells.get(col))
            if ov == nv and oc == nc:
                continue
            changes.append({
                "group": rec["_group"], "fio": rec["_fio"], "column": col,
                "old_v": ov, "new_v": nv, "old_c": oc, "new_c": nc,
            })
    return changes


def sub_matches(sub: dict, change: dict) -> bool:
    """Следит ли подписка за этим изменением.

    Матч по группе ИЛИ по ФИО студента (подстрока). Опционально фильтр по
    столбцам. Если у подписки не заданы ни groups, ни students — следит за всем.
    """
    groups = [g.strip().lower() for g in sub.get("groups", [])]
    students = [s.strip().lower() for s in sub.get("students", [])]
    cols = [c.strip().lower() for c in sub.get("columns", [])]

    if cols and change["column"].strip().lower() not in cols:
        return False
    if groups and change["group"].strip().lower() in groups:
        return True
    if students and any(s in change["fio"].strip().lower() for s in students):
        return True
    return not groups and not students
