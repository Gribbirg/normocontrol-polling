"""Загрузка таблицы как XLSX и разбор в снапшот С УЧЁТОМ ЦВЕТА ЗАЛИВКИ ЯЧЕЕК.

CSV-экспорт Google Sheets не отдаёт форматирование, а в этой таблице успех
нормоконтроля отмечается ИМЕННО заливкой ячейки (зелёным). Поэтому тянем
xlsx-экспорт (тот же публичный линк, без авторизации) и парсим заливки из
xl/styles.xml. Зависимостей нет — только стандартная библиотека.

Снапшот: {"ГРУППА | ФИО": {"_group", "_fio", "cells": {столбец: {"v","c"}}}},
где v — текст ячейки, c — метка цвета заливки ("" если фон белый/пустой).
"""
from __future__ import annotations

import io
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

NAME_COL = 1            # B — ФИО студента
GROUP_COL = 2           # C — Группа
SKIP_COLS = {0, 1, 2}   # №, ФИО, Группа — не следим за значениями
# Следим за основной таблицей: первые 9 колонок (A..I, индексы 0..8) — это
# Защита ВКР, Антиплагиат, Прим., Допуск к защите, Проверяющий + запас.
# Всё правее (правый «Антиплагиат», кол. 10 и далее) — вне основной таблицы.
MAX_TRACK_COL = 8

# Известные «зелёные» оттенки Google Sheets (ARGB). Плюс эвристика greenish().
GREEN_RGBS = {
    "FF00FF00", "FFB6D7A8", "FFD9EAD3", "FF93C47D", "FF6AA84F",
    "FF38761D", "FFE2EFDA", "FFC6EFCE", "FF274E13", "FFB7E1CD",
}


def fetch_xlsx(sheet: dict) -> bytes:
    url = f"https://docs.google.com/spreadsheets/d/{sheet['id']}/export?format=xlsx"
    req = urllib.request.Request(url, headers={"User-Agent": "f5-gospodina/1.0"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return resp.read()


def col_to_idx(letters: str) -> int:
    n = 0
    for ch in letters:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _greenish(rgb8: str) -> bool:
    try:
        r = int(rgb8[2:4], 16); g = int(rgb8[4:6], 16); b = int(rgb8[6:8], 16)
    except ValueError:
        return False
    return g > r + 10 and g > b + 10 and g > 80


# Цветные эмодзи-квадраты по тону (hue, градусы 0..360). Рядом с hex показываем
# ближайший квадрат, чтобы оттенок читался с первого взгляда. Серые/чёрные/белые
# берём по светлоте (низкая насыщенность), цветные — по тону.
_HUE_EMOJI = [
    (15, "🟥"), (45, "🟧"), (70, "🟨"), (160, "🟩"),
    (255, "🟦"), (310, "🟪"), (360, "🟥"),
]


def _emoji_for_rgb(rgb6: str) -> str:
    """Цветной квадрат, подобранный по тону/светлоте hex-цвета."""
    import colorsys
    try:
        r = int(rgb6[0:2], 16) / 255
        g = int(rgb6[2:4], 16) / 255
        b = int(rgb6[4:6], 16) / 255
    except ValueError:
        return "⬜"
    h, light, s = colorsys.rgb_to_hls(r, g, b)
    if s < 0.18:  # почти серый — различаем по светлоте
        return "⬛" if light < 0.25 else "⬜" if light > 0.85 else "⬜"
    deg = h * 360
    # коричневый — тёмно-оранжевый тон при невысокой светлоте
    if 20 <= deg <= 50 and light < 0.45:
        return "🟫"
    for upper, emoji in _HUE_EMOJI:
        if deg <= upper:
            return emoji
    return "🟥"


def color_label(fill) -> str:
    """Человекочитаемая стабильная метка заливки (для диффа и репорта)."""
    if not fill:
        return ""
    kind = fill[0]
    if kind == "rgb":
        rgb = fill[1]
        if rgb in ("FFFFFFFF", "00000000"):
            return ""
        if rgb in GREEN_RGBS or _greenish(rgb):
            return "🟢 зелёный"
        hex6 = rgb[-6:]
        return f"{_emoji_for_rgb(hex6)} #{hex6}"
    if kind == "theme":
        tint = fill[2]
        return f"theme{fill[1]}" + (f"/{tint}" if tint else "")
    return ""


def _parse_shared_strings(z: zipfile.ZipFile) -> list[str]:
    out: list[str] = []
    if "xl/sharedStrings.xml" not in z.namelist():
        return out
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    for si in root.findall(M + "si"):
        out.append("".join(t.text or "" for t in si.iter(M + "t")))
    return out


def _parse_fills(z: zipfile.ZipFile) -> list:
    """xf-индекс -> дескриптор заливки: ('rgb', ARGB) | ('theme', idx, tint) | None."""
    root = ET.fromstring(z.read("xl/styles.xml"))
    fills = []
    for fill in root.find(M + "fills"):
        desc = None
        pf = fill.find(M + "patternFill")
        if pf is not None and pf.get("patternType") not in (None, "none"):
            fg = pf.find(M + "fgColor")
            if fg is not None:
                if fg.get("rgb"):
                    desc = ("rgb", fg.get("rgb"))
                elif fg.get("theme") is not None:
                    desc = ("theme", fg.get("theme"), fg.get("tint"))
        fills.append(desc)

    xf_fill = []
    for xf in root.find(M + "cellXfs"):
        fid = int(xf.get("fillId", "0"))
        xf_fill.append(fills[fid] if fid < len(fills) else None)
    return xf_fill


def _parse_worksheet(data: bytes, shared: list[str], xf_fill: list) -> dict:
    """rownum -> {col_idx: (text, fill_desc)}."""
    root = ET.fromstring(data)
    sheet_data = root.find(M + "sheetData")
    grid: dict[int, dict[int, tuple]] = {}
    if sheet_data is None:
        return grid
    for row in sheet_data.findall(M + "row"):
        for c in row.findall(M + "c"):
            ref = c.get("r") or ""
            letters = "".join(ch for ch in ref if ch.isalpha())
            digits = "".join(ch for ch in ref if ch.isdigit())
            if not digits:
                continue
            rownum = int(digits)
            cidx = col_to_idx(letters)
            s = c.get("s")
            fill = xf_fill[int(s)] if (s is not None and int(s) < len(xf_fill)) else None
            t = c.get("t")
            text = ""
            v = c.find(M + "v")
            if t == "s":
                if v is not None and v.text is not None:
                    idx = int(v.text)
                    text = shared[idx] if idx < len(shared) else ""
            elif t == "inlineStr":
                is_ = c.find(M + "is")
                if is_ is not None:
                    text = "".join(x.text or "" for x in is_.iter(M + "t"))
            else:
                if v is not None:
                    text = v.text or ""
                    # целые числа xlsx экспортирует как "1.0" -> приводим к "1"
                    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
                        text = text[:-2]
            grid.setdefault(rownum, {})[cidx] = (text.strip(), fill)
    return grid


def _worksheet_files(z: zipfile.ZipFile) -> list[str]:
    return sorted(n for n in z.namelist()
                  if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))


def _is_header(rowcells: dict) -> bool:
    a = (rowcells.get(0) or ("", None))[0].strip()
    b = (rowcells.get(1) or ("", None))[0].strip()
    return a == "№" and b.startswith("ФИО")


def _grid_to_snapshot(grid: dict, max_col: int = MAX_TRACK_COL) -> dict:
    snapshot: dict = {}
    headers: dict[int, str] = {}
    cur_group = ""
    for rownum in sorted(grid):
        rowcells = grid[rownum]
        if _is_header(rowcells):
            headers = {idx: (val or "").strip() for idx, (val, _) in rowcells.items()}
            cur_group = ""
            continue
        first = (rowcells.get(0) or ("", None))[0].strip()
        if not first.isdigit() or not headers:
            continue
        group_cell = (rowcells.get(GROUP_COL) or ("", None))[0].strip()
        if group_cell:
            cur_group = group_cell
        fio = (rowcells.get(NAME_COL) or ("", None))[0].strip()
        if not fio:
            continue

        cells: dict[str, dict] = {}
        for idx, (text, fill) in rowcells.items():
            if idx in SKIP_COLS or idx > max_col:
                continue
            clr = color_label(fill)
            if not text and not clr:
                continue
            label = headers.get(idx, "").strip() or f"Столбец {idx + 1}"
            base = label
            n = 2
            while label in cells:  # дизамбигуация одинаковых заголовков (два «Антиплагиат»)
                label = f"{base} ({idx + 1})"
                n += 1
                if n > 3:
                    break
            cells[label] = {"v": text, "c": clr}
        snapshot[f"{cur_group} | {fio}"] = {"_group": cur_group, "_fio": fio, "cells": cells}
    return snapshot


def parse_workbook(raw: bytes, markers: list[str], max_col: int = MAX_TRACK_COL) -> dict:
    """Парсит xlsx и выбирает нужный лист по содержимому (где встречаются
    коды отслеживаемых групп). Возвращает снапшот с текстом и цветом.

    max_col ограничивает читаемые колонки. По умолчанию — основная таблица
    (MAX_TRACK_COL); команда /stats передаёт больший предел, чтобы дочитать
    блок «Антиплагиат» и правый «Допуск к защите», лежащие вне трекинга."""
    z = zipfile.ZipFile(io.BytesIO(raw))
    shared = _parse_shared_strings(z)
    xf_fill = _parse_fills(z)

    markers = [m.strip().lower() for m in markers if m.strip()]
    best_grid, best_hits = None, -1
    for wf in _worksheet_files(z):
        grid = _parse_worksheet(z.read(wf), shared, xf_fill)
        if not markers:  # нет ориентиров — берём первый непустой лист
            if grid:
                best_grid = grid
                break
            continue
        flat = " ".join(t.lower() for r in grid.values() for (t, _) in r.values())
        hits = sum(flat.count(m) for m in markers)
        if hits > best_hits:
            best_hits, best_grid = hits, grid

    return _grid_to_snapshot(best_grid or {}, max_col)
