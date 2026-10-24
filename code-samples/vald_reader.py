"""
vald_reader.py
---------------
Reads ONE VALD export file (CSV or XLSX) into plain Python objects: which
test(s) it holds, who was tested when, their body weight, and every numeric
metric cell. Replaces the 2026-09 split-export readers (data_combiner.py,
split_export_combiner.py), which needed nine specially split files.

    read_export(path) -> ExportFile          # never raises for a bad file

WHAT IT UNDERSTANDS
    * ForceDecks-style exports: "Name" (athlete), "Test Type" (CMJ, SLJ,
      IMTP, ...), "Date" (+ "Time"), "BW [KG]", then the metric columns. A
      file may even mix several test types -- each row is assigned to the
      test its own "Test Type" names.
    * Timing-gate exports (sprints, change-of-direction drills): "Date",
      "Name" (= the TEST, e.g. "10m Sprint"), "GivenName" + "FamilyName"
      (= the athlete).
    * UTF-8 with or without BOM, Windows-1257/1252; comma, semicolon or tab
      separated (a CSV re-saved by an Estonian Excel uses ";" and decimal
      commas -- both are read); trailing spaces in headers ("Peak Power / BM
      [W/kg] ") and filler rows without a name are ignored.
    * Dates day-first, as VALD writes them ("17/10/2026", "12/8/2026"),
      12- or 24-hour times; an impossible day-first date ("10/30/2026") is
      read month-first rather than dropped.

WHAT IT DOES NOT DO
    Body-weight fallbacks, unit conversion, the "/ BM" metrics and the
    history are dataset.py's job -- they need ALL the files of a testing
    session at once. This module only reads one file faithfully.

Deliberately free of pandas: the desktop app scans the testing folders with
it (to list the tests and fill the chart picker) the moment a folder is
added, long before pandas would have finished importing.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import math
import os
import re
from dataclasses import dataclass, field

import metric_catalog
import test_catalog
from test_catalog import FORMAT_FORCEDECKS, FORMAT_RUNNING, norm_text

EXPORT_EXTENSIONS = (".csv", ".xlsx", ".xlsm", ".xls")


_MISSING_TEXT = {"", "nan", "none", "null", "-", "—", "–", "n/a", "na", "#n/a", "#value!"}


def _clean_name(value) -> str:
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return "" if text.lower() in _MISSING_TEXT else text


def name_key(name) -> str:
    """'Athlete  1' and 'athlete 1' are the same athlete in every file."""
    return re.sub(r"\s+", " ", str(name or "").strip()).lower()


def to_number(cell) -> tuple[float | None, int | None]:
    """(value, decimals as written) or (None, None). Accepts real numbers
    from an .xlsx, "27.6", "27,6" (decimal comma), "1 234". Text such as
    VALD's asymmetry "4.8 L" is not a number and returns None."""
    if cell is None or isinstance(cell, bool):
        return None, None
    if isinstance(cell, (int, float)):
        value = float(cell)
        if math.isnan(value) or math.isinf(value):
            return None, None
        text = format(value, ".10g")
        if "e" in text or "E" in text:
            return value, None
        return value, (len(text.split(".", 1)[1]) if "." in text else 0)
    if isinstance(cell, (_dt.date, _dt.time)):
        return None, None
    text = str(cell).strip().replace(" ", "").replace(" ", "")
    if text.lower() in _MISSING_TEXT:
        return None, None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")      
    try:
        value = float(text)
    except ValueError:
        return None, None
    if math.isnan(value) or math.isinf(value):
        return None, None
    mantissa = re.split(r"[eE]", text, 1)[0]
    return value, (len(mantissa.split(".", 1)[1]) if "." in mantissa else 0)


_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?")
_DMY = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})(?:\s+(.+))?$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*([AaPp])?\.?\s*[Mm]?\.?$")


def _parse_time(cell) -> _dt.time | None:
    if cell is None:
        return None
    if isinstance(cell, _dt.datetime):
        return cell.time()
    if isinstance(cell, _dt.time):
        return cell
    if isinstance(cell, (int, float)) and not isinstance(cell, bool) and 0 <= cell < 1:
        seconds = int(round(float(cell) * 86400))
        return _dt.time(min(23, seconds // 3600), (seconds % 3600) // 60, seconds % 60)
    match = _TIME.match(str(cell).strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    second = int(match.group(3) or 0)
    meridiem = (match.group(4) or "").lower()
    if meridiem == "p" and hour < 12:
        hour += 12
    elif meridiem == "a" and hour == 12:
        hour = 0
    try:
        return _dt.time(hour, minute, second)
    except ValueError:
        return None


def parse_datetime(date_cell, time_cell=None) -> _dt.datetime | None:
    """The test's moment, or None when the date cannot be read."""
    if date_cell is None:
        return None
    when: _dt.datetime | None = None
    if isinstance(date_cell, _dt.datetime):
        when = date_cell
    elif isinstance(date_cell, _dt.date):
        when = _dt.datetime(date_cell.year, date_cell.month, date_cell.day)
    elif isinstance(date_cell, (int, float)) and not isinstance(date_cell, bool):
        if 20000 < float(date_cell) < 80000:   # an Excel serial date
            when = _dt.datetime(1899, 12, 30) + _dt.timedelta(days=float(date_cell))
    else:
        text = str(date_cell).strip()
        match = _ISO.match(text)
        if match:
            year, month, day = (int(g) for g in match.groups()[:3])
            hour, minute, second = (int(g or 0) for g in match.groups()[3:])
            try:
                when = _dt.datetime(year, month, day, hour, minute, second)
            except ValueError:
                when = None
        else:
            match = _DMY.match(text)
            if match:
                day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
                if year < 100:
                    year += 2000
                if month > 12 and day <= 12:      
                    day, month = month, day
                try:
                    when = _dt.datetime(year, month, day)
                except ValueError:
                    when = None
                if when is not None and match.group(4) and time_cell is None:
                    time_cell = match.group(4)
    if when is None:
        return None
    clock = _parse_time(time_cell)
    if clock is not None and (when.hour, when.minute, when.second) == (0, 0, 0):
        when = _dt.datetime.combine(when.date(), clock)
    return when


def _read_csv(path: str) -> list[list]:
    with open(path, "rb") as handle:
        raw = handle.read()
    text = None
    for encoding in ("utf-8-sig", "cp1257", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    text = (text or "").lstrip("﻿")
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max((",", ";", "\t"), key=first.count) if first else ","
    if first and first.count(delimiter) == 0:
        delimiter = ","
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _read_xlsx(path: str) -> list[list]:
    import openpyxl  

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        return [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def _read_xls(path: str) -> list[list]:
    """Old binary .xls -- only if pandas + xlrd happen to be installed."""
    import pandas as pd  

    frame = pd.read_excel(path, header=None)
    return frame.where(frame.notna(), None).values.tolist()


def read_table(path: str) -> list[list]:
    low = path.lower()
    if low.endswith((".xlsx", ".xlsm")):
        return _read_xlsx(path)
    if low.endswith(".xls"):
        return _read_xls(path)
    return _read_csv(path)


@dataclass
class ExportColumn:
    index: int
    parsed: metric_catalog.ParsedHeader


@dataclass
class ExportRow:
    test: dict
    name: str
    name_key: str
    when: _dt.datetime | None
    body_weight: float | None
    values: dict = field(default_factory=dict)    
    decimals: dict = field(default_factory=dict)   
    line: int = 0

    @property
    def date(self) -> str | None:
        return self.when.strftime("%Y-%m-%d") if self.when else None


@dataclass
class ExportFile:
    path: str
    ok: bool = False
    reason: str = ""
    format: str | None = None
    columns: list = field(default_factory=list)   
    rows: list = field(default_factory=list)       
    mtime: float = 0.0

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def stem(self) -> str:
        return os.path.splitext(self.name)[0]

    def tests(self) -> list[dict]:
        """The distinct tests in this file, in the order rows name them."""
        seen: dict[str, dict] = {}
        for row in self.rows:
            seen.setdefault(row.test["id"], row.test)
        return list(seen.values())

    def newest(self) -> _dt.datetime | None:
        stamps = [row.when for row in self.rows if row.when]
        return max(stamps) if stamps else None


def _find(headers_norm: list[str], *names) -> int | None:
    for name in names:
        target = norm_text(name)
        if target in headers_norm:
            return headers_norm.index(target)
    return None


def read_export(path: str) -> ExportFile:
    """Reads one file. A file that is not a VALD export (or cannot be read)
    comes back with ok=False and a reason the log can print -- one stray
    file in a testing folder must never stop a run."""
    result = ExportFile(path=path)
    try:
        result.mtime = os.path.getmtime(path)
    except OSError:
        pass
    try:
        table = read_table(path)
    except Exception as exc: 
        result.reason = f"ei õnnestunud lugeda ({exc})"
        return result

    while table and not any(_clean_name(cell) for cell in table[0]):
        table.pop(0)                
    if not table:
        result.reason = "fail on tühi"
        return result

    headers = [str(cell if cell is not None else "") for cell in table[0]]
    headers_norm = [norm_text(h) for h in headers]
    i_name = _find(headers_norm, "Name")
    i_type = _find(headers_norm, "Test Type")
    i_given = _find(headers_norm, "GivenName", "Given Name", "First Name")
    i_family = _find(headers_norm, "FamilyName", "Family Name", "Last Name")
    i_date = _find(headers_norm, "Date")
    i_time = _find(headers_norm, "Time")
    i_bw = [i for i in (_find(headers_norm, c) for c in test_catalog.BODY_WEIGHT_COLUMNS)
            if i is not None]

   
    has_split_name = i_given is not None and i_family is not None
    if i_type is not None and (i_name is not None or has_split_name):
        fmt = FORMAT_FORCEDECKS
    elif has_split_name:
        fmt = FORMAT_RUNNING
    elif i_name is not None:
        fmt = FORMAT_FORCEDECKS
    else:
        result.reason = "ei ole VALD eksport (puudub 'Name' veerg)"
        return result
    result.format = fmt

    parsed = metric_catalog.resolve_sides([metric_catalog.parse_header(h) for h in headers])
    used = {i for i in (i_name, i_type, i_given, i_family, i_date, i_time) if i is not None}
    result.columns = [ExportColumn(index=i, parsed=p) for i, p in enumerate(parsed)
                      if p is not None and i not in used]
    if not result.columns:
        result.reason = "failis pole ühtegi tulemuste veergu"
        return result

    def cell(row, index):
        return row[index] if index is not None and index < len(row) else None

    bw_indexes = set(i_bw)
    tests_by_code: dict[str, dict] = {}
    for line, row in enumerate(table[1:], start=2):
        split_name = (_clean_name(f"{_clean_name(cell(row, i_given))} {_clean_name(cell(row, i_family))}")
                      if has_split_name else "")
        if fmt == FORMAT_RUNNING:
            athlete = split_name
            code = _clean_name(cell(row, i_name))
        else:
            athlete = _clean_name(cell(row, i_name)) or split_name
            code = _clean_name(cell(row, i_type))
        if not athlete:
            continue                      
        test = tests_by_code.get(code)
        if test is None:
            test = test_catalog.identify(code or None, result.stem, fmt)
            tests_by_code[code] = test

        weight = None
        for index in i_bw:
            value, _ = to_number(cell(row, index))
            if value is not None and value > 0:   # VALD writes -1 for "none"
                weight = value
                break

        entry = ExportRow(test=test, name=athlete, name_key=name_key(athlete),
                          when=parse_datetime(cell(row, i_date), cell(row, i_time)),
                          body_weight=weight, line=line)
        for column in result.columns:
            value, decimals = to_number(cell(row, column.index))
            if value is None:
                continue
            if column.index in bw_indexes and value <= 0:
                continue                      # VALD's -1 = "no body weight"
            entry.values[column.index] = value
            if decimals is not None:
                entry.decimals[column.index] = decimals
        result.rows.append(entry)

    if not result.rows:
        result.reason = "failis pole ühegi sportlase ridu"
        return result
    result.ok = True
    return result


def list_export_files(directory: str) -> list[str]:
    """Every file in `directory` that could be an export, sorted by name.
    The summary workbook the app itself writes into the folder, Excel lock
    files and the app's own outputs are skipped."""
    if not directory or not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        if name.startswith(("~$", ".")):
            continue
        if not name.lower().endswith(EXPORT_EXTENSIONS):
            continue
        if name.lower().startswith("sportlaste_kokkuvottev_tabel"):
            continue
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            out.append(path)
    return out
