#!/usr/bin/env python3
"""Load a books CSV file into books.db, following rules.md.

Usage:
    python3 ingest.py <books.csv>

- books.db and ingestion-log.json are kept in the same folder as this script.
- Uses only the Python standard library: no AI, no internet. The same file and
  the same database state always give the same result.
- The CSV file is only read, never changed.
- Every row is checked on every run. Each run appends one entry to
  ingestion-log.json; earlier entries are never removed or changed.
"""

import csv
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "books.db"
LOG_PATH = HERE / "ingestion-log.json"

CSV_COLUMNS = [
    "book_id", "goodreads_book_id", "best_book_id", "work_id", "books_count",
    "isbn", "isbn13", "authors", "original_publication_year", "original_title",
    "title", "language_code", "average_rating", "ratings_count",
    "work_ratings_count", "work_text_reviews_count", "ratings_1", "ratings_2",
    "ratings_3", "ratings_4", "ratings_5", "image_url", "small_image_url",
]


# ---------------------------------------------------------------- ISBN maths

def isbn10_ok(s):
    """Valid ISBN-10: 9 digits + digit or uppercase X, weighted sum divisible by 11."""
    if not re.fullmatch(r"\d{9}[\dX]", s):
        return False
    total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
    return total % 11 == 0


def isbn13_ok(s):
    """Valid ISBN-13: 13 digits starting 978/979 (not 9790), weights 1/3, divisible by 10."""
    if not re.fullmatch(r"\d{13}", s) or not s.startswith(("978", "979")) or s.startswith("9790"):
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s))
    return total % 10 == 0


def isbn13_check_digit(first12):
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(first12))
    return str((10 - total % 10) % 10)


def isbn10_check_digit(first9):
    r = (11 - sum((10 - i) * int(c) for i, c in enumerate(first9)) % 11) % 11
    return "X" if r == 10 else str(r)


def isbn10_to_13(isbn10):
    core = "978" + isbn10[:9]
    return core + isbn13_check_digit(core)


def isbn13_to_10(isbn13):
    """Only for ISBN-13s starting with 978; 979 ISBNs have no ISBN-10."""
    core = isbn13[3:12]
    return core + isbn10_check_digit(core)


def strip_spaces_hyphens(raw):
    return raw.replace(" ", "").replace("-", "")


SCI_RE = re.compile(r"\d(\.\d+)?[eE]\+?\d+")


def rounds_to(full13, stored):
    """True if the 13-digit number, rounded to the stored number of significant
    digits, equals the stored scientific-notation value."""
    mantissa = stored.lower().split("e")[0]
    sig = len(mantissa.replace(".", ""))
    try:
        return Decimal(f"{Decimal(full13):.{sig - 1}e}") == Decimal(stored)
    except InvalidOperation:
        return False


# ------------------------------------------------------- per-column checking

def check_isbn10_column(raw):
    """Return dict(kind, value, reason, repairs).

    kind: 'ok' (usable ISBN-10, possibly repaired), 'isbn13' (valid ISBN-13 found
    in this column), 'missing', 'invalid'.
    """
    repairs = []
    if raw.strip() == "":
        return {"kind": "missing", "value": None, "reason": "empty", "repairs": repairs}

    s = strip_spaces_hyphens(raw)
    if s != raw:
        repairs.append("removed spaces/hyphens")
    if s == "":
        return {"kind": "invalid", "value": None,
                "reason": f"{raw!r} contains no digits", "repairs": repairs}

    if s.endswith("x") and s[:-1].isdigit():
        s = s[:-1] + "X"
        repairs.append("changed lowercase x to X")

    if not re.fullmatch(r"\d*X?", s):
        return {"kind": "invalid", "value": None,
                "reason": f"{raw!r} contains unexpected characters", "repairs": repairs}

    if len(s) == 13 and s.isdigit():
        if isbn13_ok(s):
            return {"kind": "isbn13", "value": s, "reason": None, "repairs": repairs}
        return {"kind": "invalid", "value": None,
                "reason": f"{raw!r} has too many digits for an ISBN-10 and is not a valid ISBN-13",
                "repairs": repairs}

    if len(s) > 10:
        return {"kind": "invalid", "value": None,
                "reason": f"{raw!r} has too many digits for an ISBN-10 ({len(s)})", "repairs": repairs}

    if len(s) == 10:
        if isbn10_ok(s):
            return {"kind": "ok", "value": s, "reason": None, "repairs": repairs}
        return {"kind": "invalid", "value": None,
                "reason": f"{raw!r} fails the ISBN-10 check digit test", "repairs": repairs}

    padded = s.zfill(10)
    if isbn10_ok(padded):
        n = 10 - len(s)
        repairs.append(f"added {n} leading zero{'s' if n > 1 else ''}")
        return {"kind": "ok", "value": padded, "reason": None, "repairs": repairs}
    return {"kind": "invalid", "value": None,
            "reason": f"{raw!r} fails the ISBN-10 check digit test even after adding leading zeros",
            "repairs": repairs}


def check_isbn13_column(raw):
    """Return dict(kind, value, reason, repairs).

    kind: 'ok' (valid ISBN-13), 'rounded' (scientific notation of a 13-digit
    978/979 number; needs rebuilding), 'missing', 'not_isbn' (UPC, music code
    or other non-ISBN number), 'invalid'.
    """
    repairs = []
    if raw.strip() == "":
        return {"kind": "missing", "value": None, "reason": "empty", "repairs": repairs}

    s = strip_spaces_hyphens(raw)
    if s != raw:
        repairs.append("removed spaces/hyphens")

    if re.fullmatch(r"\d{13}", s):
        if s.startswith("9790"):
            return {"kind": "not_isbn", "value": None,
                    "reason": f"{raw!r} is a music code (ISMN, 979-0), not an ISBN", "repairs": repairs}
        if not s.startswith(("978", "979")):
            return {"kind": "not_isbn", "value": None,
                    "reason": f"{raw!r} does not start with 978 or 979, so it is another barcode, not an ISBN",
                    "repairs": repairs}
        if isbn13_ok(s):
            return {"kind": "ok", "value": s, "reason": None, "repairs": repairs}
        return {"kind": "invalid", "value": None,
                "reason": f"{raw!r} fails the ISBN-13 check digit test", "repairs": repairs}

    if SCI_RE.fullmatch(s):
        number = Decimal(s)
        digits = str(int(number)) if number == number.to_integral_value() else None
        if digits is None or len(digits) != 13:
            size = f"a {len(digits)}-digit number" if digits else "not a whole number"
            return {"kind": "not_isbn", "value": None,
                    "reason": f"{raw!r} is {size}, not an ISBN-13 (another barcode such as a UPC)",
                    "repairs": repairs}
        if digits.startswith("9790"):
            return {"kind": "not_isbn", "value": None,
                    "reason": f"{raw!r} is a music code (ISMN, 979-0), not an ISBN", "repairs": repairs}
        if not digits.startswith(("978", "979")):
            return {"kind": "not_isbn", "value": None,
                    "reason": f"{raw!r} does not start with 978 or 979, so it is another barcode, not an ISBN",
                    "repairs": repairs}
        return {"kind": "rounded", "value": s, "reason": None, "repairs": repairs}

    if re.search(r"[A-Za-z]", s):
        return {"kind": "invalid", "value": None,
                "reason": f"{raw!r} contains unexpected characters", "repairs": repairs}
    return {"kind": "not_isbn", "value": None,
            "reason": f"{raw!r} is not a valid ISBN-13", "repairs": repairs}


# ------------------------------------------------------------ row checking

def check_row_isbns(raw10, raw13):
    """Apply rules.md section 1 to one row.

    Returns dict with either 'reject' (reason) or 'isbn'/'isbn13' (final values),
    plus 'repairs' [(column, original, new, change)] and 'warnings' [message].
    """
    c10 = check_isbn10_column(raw10)
    c13 = check_isbn13_column(raw13)
    repairs, warnings = [], []
    v10 = c10["value"] if c10["kind"] == "ok" else None
    v13 = c13["value"] if c13["kind"] == "ok" else None

    if v10 and c10["repairs"]:
        repairs.append(("isbn", raw10, v10, "; ".join(c10["repairs"])))
    if v13 and c13["repairs"]:
        repairs.append(("isbn13", raw13, v13, "; ".join(c13["repairs"])))

    # ISBN-13 found in the ISBN-10 column: move it only if nothing conflicts.
    if c10["kind"] == "isbn13":
        moved = c10["value"]
        if c13["kind"] == "ok" and v13 != moved:
            return {"reject": f"isbn column holds ISBN-13 {moved}, which conflicts with isbn13 {v13}"}
        if c13["kind"] == "rounded" and not rounds_to(moved, c13["value"]):
            return {"reject": f"isbn column holds ISBN-13 {moved}, which conflicts with isbn13 {raw13!r}"}
        change = "moved valid ISBN-13 from the isbn column to isbn13"
        if c10["repairs"]:
            change = "; ".join(c10["repairs"]) + "; " + change
        repairs.append(("isbn13", raw10, moved, change))
        v13 = moved

    # Rounded (scientific notation) ISBN-13: rebuild only from a valid ISBN-10.
    if c13["kind"] == "rounded" and v13 is None:
        if v10:
            rebuilt = isbn10_to_13(v10)
            if rounds_to(rebuilt, c13["value"]):
                v13 = rebuilt
                repairs.append(("isbn13", raw13, rebuilt,
                                f"rebuilt rounded ISBN-13 from ISBN-10 {v10}"))
            else:
                return {"reject": f"ISBN-10 {v10} and rounded ISBN-13 {raw13!r} identify different books "
                                  f"(ISBN-13 from the ISBN-10 would be {rebuilt})"}

    # Both valid: they must identify the same book.
    if v10 and v13 and isbn10_to_13(v10) != v13:
        return {"reject": f"ISBN-10 {v10} and ISBN-13 {v13} identify different books"}

    def why_unusable(check, raw):
        if check["kind"] == "missing":
            return "empty"
        if check["kind"] == "rounded":
            return f"{raw!r} is a rounded ISBN-13 that cannot be verified without a valid ISBN-10"
        return check["reason"]

    if not v10 and not v13:
        return {"reject": "no usable ISBN (isbn: " + why_unusable(c10, raw10)
                          + "; isbn13: " + why_unusable(c13, raw13) + ")"}

    # One valid, the other missing or unusable: calculate it.
    if v10 and not v13:
        v13 = isbn10_to_13(v10)
        warnings.append(f"ISBN-13 {v13} calculated from ISBN-10 {v10} because isbn13 was unusable: "
                        + why_unusable(c13, raw13))
    elif v13 and not v10:
        moved_note = c10["kind"] == "isbn13"
        if v13.startswith("978"):
            v10 = isbn13_to_10(v13)
            reason = ("it held the ISBN-13" if moved_note else why_unusable(c10, raw10))
            warnings.append(f"ISBN-10 {v10} calculated from ISBN-13 {v13} because isbn was unusable: " + reason)
        elif not moved_note and c10["kind"] != "missing":
            # 979 ISBNs have no ISBN-10; warn only if a value was dropped.
            warnings.append(f"isbn value dropped ({c10['reason']}); ISBN-13 {v13} starts with 979 and has no ISBN-10")

    return {"isbn": v10, "isbn13": v13, "repairs": repairs, "warnings": warnings}


def norm(text):
    """Title/author comparison: ignore case, spaces at the ends and repeated spaces."""
    return " ".join((text or "").split()).casefold()


# --------------------------------------------------------------- database

def open_db():
    con = sqlite3.connect(DB_PATH)
    cols = ",\n  ".join(
        "isbn13 TEXT PRIMARY KEY" if c == "isbn13"
        else "isbn TEXT UNIQUE" if c == "isbn"
        else f"{c} TEXT"
        for c in CSV_COLUMNS
    )
    con.execute(f"CREATE TABLE IF NOT EXISTS books (\n  {cols},\n  original_isbn TEXT,\n  original_isbn13 TEXT\n)")
    con.row_factory = sqlite3.Row
    return con


def find(con, column, value):
    if value is None:
        return None
    return con.execute(f"SELECT * FROM books WHERE {column} = ?", (value,)).fetchone()


# ---------------------------------------------------------------- the run

def ingest(csv_arg):
    csv_path = Path(csv_arg).resolve()
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in CSV_COLUMNS if c not in header]
        if missing:
            sys.exit(f"Error: {csv_arg} is missing column(s): {', '.join(missing)}. Nothing was loaded.")
        rows = [(reader.line_num, row) for row in reader]

    if LOG_PATH.exists():
        try:
            log = json.loads(LOG_PATH.read_text(encoding="utf-8"))
            if not isinstance(log, list):
                raise ValueError("not a list")
        except (ValueError, json.JSONDecodeError) as e:
            sys.exit(f"Error: {LOG_PATH.name} exists but cannot be read ({e}). "
                     "It was not changed and nothing was loaded.")
    else:
        log = []

    run_at = datetime.now().astimezone().isoformat(timespec="seconds")
    counts = {"rows_read": len(rows), "loaded": 0, "updated": 0, "skipped": 0, "rejected": 0}
    rejected_rows, skipped_rows, repairs_out, warnings_out = [], [], [], []

    con = open_db()
    try:
        with con:  # one transaction for the whole run
            for line, row in rows:
                raw10, raw13 = row["isbn"] or "", row["isbn13"] or ""
                ident = {"line": line, "book_id": row["book_id"], "title": row["title"],
                         "authors": row["authors"], "isbn": raw10, "isbn13": raw13}

                def reject(reason):
                    counts["rejected"] += 1
                    rejected_rows.append({**ident, "reason": reason})

                result = check_row_isbns(raw10, raw13)
                if "reject" in result:
                    reject(result["reject"])
                    continue
                v10, v13 = result["isbn"], result["isbn13"]

                by10, by13 = find(con, "isbn", v10), find(con, "isbn13", v13)
                if by10 is not None and by13 is not None and by10["isbn13"] != by13["isbn13"]:
                    reject(f"ISBN-10 {v10} belongs to {by10['title']!r} (book_id {by10['book_id']}) "
                           f"but ISBN-13 {v13} belongs to {by13['title']!r} (book_id {by13['book_id']})")
                    continue
                existing = by13 if by13 is not None else by10

                new_values = {c: row[c] for c in CSV_COLUMNS}
                new_values["isbn"], new_values["isbn13"] = v10, v13

                if existing is not None:
                    if norm(existing["title"]) != norm(row["title"]) or norm(existing["authors"]) != norm(row["authors"]):
                        isbn_used = v13 if by13 is not None else v10
                        reject(f"ISBN {isbn_used} already belongs to a different book: "
                               f"{existing['title']!r} by {existing['authors']!r} (book_id {existing['book_id']})")
                        continue
                    if all(existing[c] == new_values[c] for c in CSV_COLUMNS):
                        outcome = "skipped"
                        counts["skipped"] += 1
                        skipped_rows.append({**ident, "reason": f"already in the database with the same information "
                                                                f"(ISBN-13 {existing['isbn13']})"})
                    else:
                        outcome = "updated"
                        counts["updated"] += 1
                        sets = ", ".join(f"{c} = ?" for c in CSV_COLUMNS) + ", original_isbn = ?, original_isbn13 = ?"
                        con.execute(f"UPDATE books SET {sets} WHERE isbn13 = ?",
                                    [new_values[c] for c in CSV_COLUMNS] + [raw10, raw13, existing["isbn13"]])
                else:
                    outcome = "loaded"
                    counts["loaded"] += 1
                    cols = CSV_COLUMNS + ["original_isbn", "original_isbn13"]
                    con.execute(f"INSERT INTO books ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                [new_values[c] for c in CSV_COLUMNS] + [raw10, raw13])

                for column, original, new, change in result["repairs"]:
                    repairs_out.append({"line": line, "book_id": row["book_id"], "title": row["title"],
                                        "column": column, "original": original, "repaired": new,
                                        "change": change, "row_result": outcome})
                if outcome != "skipped":
                    for message in result["warnings"]:
                        warnings_out.append({"line": line, "book_id": row["book_id"], "title": row["title"],
                                             "row_result": outcome, "warning": message})
    finally:
        con.close()

    entry = {
        "run_at": run_at,
        "csv_file": csv_path.name,
        "csv_path": str(csv_path),
        **counts,
        "rejected_rows": rejected_rows,
        "skipped_rows": skipped_rows,
        "repairs": repairs_out,
        "warnings": warnings_out,
    }
    log.append(entry)
    tmp = LOG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, LOG_PATH)

    print(f"{csv_path.name}: read {counts['rows_read']}, loaded {counts['loaded']}, "
          f"updated {counts['updated']}, skipped {counts['skipped']}, rejected {counts['rejected']}, "
          f"repairs {len(repairs_out)}, warnings {len(warnings_out)}")
    print(f"Database: {DB_PATH}\nLog: {LOG_PATH} (run {len(log)})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python3 ingest.py <books.csv>")
    ingest(sys.argv[1])
