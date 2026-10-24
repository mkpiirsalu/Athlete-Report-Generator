"""
tools/make_synthetic_data.py
-----------------------------
Writes SYNTHETIC VALD exports -- invented athletes, made-up numbers -- for the
PDF regression check (tools/compare_reports.py) and, later, the app's
--selftest. No real athlete data goes anywhere near the repo, the build or a
log; this script is how the tests get data that still looks like the real
thing.

The repo's own sample exports (uued_testid_naidisfailid/, one file per test,
one athlete each) are the TEMPLATES: every file keeps its exact header --
column names, order, units -- and its one row becomes the reference values.
Each synthetic athlete in each testing gets a copy of that row with:

    * the athlete's name ("Name", or "GivenName" + "FamilyName" for the
      timing-gate exports, whose "Name" is the test and is kept);
    * the testing's date;
    * every number scaled -- per athlete (spread), per testing (each
      athlete's own trend: some improve, some get worse, one stays flat) and
      a small per-column wobble -- and written with the template's own number
      of decimals.

Everything is arithmetic on fixed tables: the same arguments always produce
byte-identical files.

The sample exports' names (Athlete 1, Athlete 2) do not match between
samples/ and uued_testid_naidisfailid/, so those two never produce a change %,
a Top3 Improvers podium or a trend line. The synthetic testings share their
names, which is the whole point: they exercise exactly those parts.

Usage:
    python tools/make_synthetic_data.py <output folder>
    python tools/make_synthetic_data.py <output folder> --athletes 3 --sessions 2 --tests "Countermovement Jump,10m Sprint"
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TEMPLATES = os.path.join(REPO, "uued_testid_naidisfailid")


ATHLETES = [
    ("Näidis", "Ämblik"),
    ("Näidis", "Õunake"),
    ("Näidis", "Šokolaad"),
    ("Näidis", "Žürii"),
    ("Näidis", "Üllatus"),
    ("Näidis", "Öökull"),
    ("Näidis", "Pikknimeline-Kontrollija"),
    ("Näidis", "Kaheksas"),
]

# The testings, oldest first, as VALD writes dates (day first). Folder names
# are what the app shows in section 1.
SESSIONS = [
    ("Testimine_2026-05", "12/05/2026"),
    ("Testimine_2026-08", "19/08/2026"),
    ("Testimine_2026-10", "17/10/2026"),
]

# Per athlete: level relative to the template value, and the change per
# testing (+ = the number grows; whether that is "better" is the metric's
# business -- a growing sprint time is worse).
LEVEL = [0.10, -0.06, 0.03, -0.12, 0.07, 0.0, -0.03, 0.12]
TREND = [0.06, -0.04, 0.09, 0.0, -0.07, 0.03, 0.05, -0.02]

_NUMBER = re.compile(r"^-?\d+(?:\.(\d+))?$")


def _read_template(path: str) -> tuple[list[str], list[list[str]]]:
    with open(path, "rb") as handle:
        text = handle.read().decode("utf-8-sig")
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max((",", ";", "\t"), key=first.count)
    rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)
            if any(cell.strip() for cell in row)]
    return rows[0], rows[1:]


def _scaled(cell: str, factor: float) -> str:
    """A template number times `factor`, written with the template's own
    decimals; anything that is not a plain number ("4.8 L", "", text) is
    returned unchanged."""
    text = cell.strip()
    match = _NUMBER.match(text)
    if not match:
        return cell
    decimals = len(match.group(1) or "")
    value = float(text) * factor
    if decimals == 0:
        return str(int(round(value)))
    return f"{value:.{decimals}f}"


def _factor(athlete: int, session: int, column: int, row: int) -> float:
    wobble = (((column * 7 + athlete * 13 + session * 17 + row * 5) % 11) - 5) * 0.004
    return (1 + LEVEL[athlete % len(LEVEL)]) * (1 + TREND[athlete % len(TREND)] * session) * (1 + wobble)


def build_file(header: list[str], template_rows: list[list[str]], date: str,
               athletes: list[tuple[str, str]], session: int) -> list[list[str]]:
    keys = [cell.strip().lower() for cell in header]

    def index(name: str) -> int | None:
        return keys.index(name) if name in keys else None

    name_col, date_col = index("name"), index("date")
    given_col, family_col = index("givenname"), index("familyname")
    timing_gate = given_col is not None and family_col is not None
    keep = {i for i in (name_col, date_col, given_col, family_col, index("time"),
                        index("test type"), index("externalid"), index("tags")) if i is not None}

    rows = []
    for a, (given, family) in enumerate(athletes):
        for r, template in enumerate(template_rows):
            row = list(template) + [""] * (len(header) - len(template))
            for c in range(len(header)):
                if c not in keep:
                    row[c] = _scaled(row[c], _factor(a, session, c, r))
            if timing_gate:
                row[given_col], row[family_col] = given, family
            elif name_col is not None:
                row[name_col] = f"{given} {family}"
            if date_col is not None:
                row[date_col] = date
            rows.append(row)
    return rows


def write_dataset(output_dir: str, *, templates_dir: str = DEFAULT_TEMPLATES,
                  athletes: int = len(ATHLETES), sessions: int = len(SESSIONS),
                  tests: list[str] | None = None) -> list[str]:
    """Writes `sessions` testing folders into `output_dir` and returns their
    paths, oldest first. `tests` = template file stems to use (default: all)."""
    names = sorted(f for f in os.listdir(templates_dir) if f.lower().endswith(".csv"))
    if tests:
        wanted = {t.strip().lower() for t in tests}
        names = [f for f in names if os.path.splitext(f)[0].lower() in wanted]
        if not names:
            raise SystemExit(f"Ühtegi testi {tests} kaustas {templates_dir} pole.")
    people = ATHLETES[:athletes]
    folders = []
    # The NEWEST `sessions` testings, each keeping its place in the trend.
    for s, (folder, date) in list(enumerate(SESSIONS))[-sessions:]:
        target = os.path.join(output_dir, folder)
        os.makedirs(target, exist_ok=True)
        for name in names:
            header, template_rows = _read_template(os.path.join(templates_dir, name))
            rows = build_file(header, template_rows, date, people, s)
            # UTF-8 with a BOM and CRLF, like VALD's own exports.
            with open(os.path.join(target, name), "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\r\n")
                writer.writerow(header)
                writer.writerows(rows)
        folders.append(target)
    return folders


def main() -> None:
    parser = argparse.ArgumentParser(description="Sünteetilised VALD eksportfailid testimiseks.")
    parser.add_argument("output_dir")
    parser.add_argument("--athletes", type=int, default=len(ATHLETES), choices=range(1, len(ATHLETES) + 1))
    parser.add_argument("--sessions", type=int, default=len(SESSIONS), choices=range(1, len(SESSIONS) + 1))
    parser.add_argument("--tests", default=None,
                        help="Komadega eraldatud testifailide nimed ilma .csv-ta (vaikimisi kõik).")
    parser.add_argument("--templates", default=DEFAULT_TEMPLATES)
    args = parser.parse_args()
    folders = write_dataset(args.output_dir, templates_dir=args.templates, athletes=args.athletes,
                            sessions=args.sessions,
                            tests=args.tests.split(",") if args.tests else None)
    for folder in folders:
        print(folder)


if __name__ == "__main__":
    sys.exit(main())
