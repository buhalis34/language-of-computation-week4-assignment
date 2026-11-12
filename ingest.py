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
- Section numbers in comments (for example "rules 1.1") refer to rules.md.
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

# rules 3.1: identity fields are not tracked; every other CSV column is.
IDENTITY_FIELDS = ("isbn", "isbn13", "title", "authors")
TRACKED_FIELDS = [c for c in CSV_COLUMNS if c not in IDENTITY_FIELDS]

# rules 1.1 step 8: at most 3 leading zeros, so only 7-9 characters are padded.
MIN_PAD_LENGTH = 7

# Rejection codes (rules 5).
REJ_NO_USABLE_ISBN = "REJ_NO_USABLE_ISBN"
REJ_ISBN_CONFLICT = "REJ_ISBN_CONFLICT"
REJ_ISBN_BELONGS_TO_DIFFERENT_BOOK = "REJ_ISBN_BELONGS_TO_DIFFERENT_BOOK"
SKIP_UNCHANGED = "SKIP_UNCHANGED"


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
#
# Each check returns a dict:
#   kind     'ok' (usable), 'isbn13' (valid ISBN-13 in the isbn column),
#            'rounded' (scientific notation, isbn13 column only), 'missing'
#            or 'invalid'
#   value    the usable value, or the rounded text for 'rounded'
#   problem  problem code for 'missing' / 'invalid', otherwise None
#   reason   readable text for 'missing' / 'invalid'
#   repairs  [(repair code, description)] in the order they were applied

def _result(kind, value=None, problem=None, reason=None, repairs=None):
    return {"kind": kind, "value": value, "problem": problem, "reason": reason,
            "repairs": list(repairs or [])}


def _is_blank(raw):
    """rules 1.1/1.2 step 1: empty or only spaces."""
    return raw.strip(" ") == ""


def _isbn10_steps(raw, s):
    """rules 1.1 steps 4-9 on a value whose spaces and hyphens are already removed."""
    repairs = []
    if s.endswith("x"):
        s = s[:-1] + "X"
        repairs.append(("R_UPPERCASED_X", "changed lowercase x to X"))

    if not re.fullmatch(r"\d*X?", s):
        return _result("invalid", problem="UNEXPECTED_CHARACTERS",
                       reason=f"{raw!r} contains unexpected characters")

    if len(s) == 13:
        if isbn13_ok(s):
            return _result("isbn13", value=s, repairs=repairs)
        return _result("invalid", problem="WRONG_LENGTH",
                       reason=f"{raw!r} has 13 characters but is not a valid ISBN-13")

    if len(s) == 10:
        if isbn10_ok(s):
            return _result("ok", value=s, repairs=repairs)
        return _result("invalid", problem="BAD_CHECK_DIGIT",
                       reason=f"{raw!r} fails the ISBN-10 check digit test")

    if MIN_PAD_LENGTH <= len(s) < 10:
        padded = s.zfill(10)
        if isbn10_ok(padded):
            n = 10 - len(s)
            repairs.append(("R_PADDED_ZEROS", f"added {n} leading zero{'s' if n > 1 else ''}"))
            return _result("ok", value=padded, repairs=repairs)
        return _result("invalid", problem="BAD_CHECK_DIGIT",
                       reason=f"{raw!r} fails the ISBN-10 check digit test even after adding leading zeros")

    return _result("invalid", problem="WRONG_LENGTH",
                   reason=f"{raw!r} has {len(s)} characters, which is the wrong length for an ISBN-10")


def check_isbn10_column(raw):
    """rules 1.1: the isbn (ISBN-10) column."""
    if _is_blank(raw):
        return _result("missing", problem="EMPTY", reason="empty")

    repairs = []
    s = strip_spaces_hyphens(raw)
    if s != raw:
        repairs.append(("R_REMOVED_SEPARATORS", "removed spaces/hyphens"))

    if s.endswith(".0"):
        rest = _isbn10_steps(raw, s[:-2])
        if rest["kind"] in ("ok", "isbn13"):
            rest["repairs"] = repairs + [("R_REMOVED_DECIMAL", "removed trailing .0")] + rest["repairs"]
        # Not usable: the column keeps the problem code of the value without .0.
        return rest

    result = _isbn10_steps(raw, s)
    result["repairs"] = repairs + result["repairs"]
    return result


def _isbn13_steps(raw, s):
    """rules 1.2 steps 4-7 on a value whose spaces and hyphens are already removed."""
    if re.fullmatch(r"\d{13}", s):
        if s.startswith("9790"):
            return _result("invalid", problem="NOT_AN_ISBN",
                           reason=f"{raw!r} is a music code (979-0), not an ISBN")
        if not s.startswith(("978", "979")):
            return _result("invalid", problem="NOT_AN_ISBN",
                           reason=f"{raw!r} does not start with 978 or 979, so it is not an ISBN")
        if isbn13_ok(s):
            return _result("ok", value=s)
        return _result("invalid", problem="BAD_CHECK_DIGIT",
                       reason=f"{raw!r} fails the ISBN-13 check digit test")

    if SCI_RE.fullmatch(s):
        number = Decimal(s)
        digits = str(int(number)) if number == number.to_integral_value() else None
        if (digits is None or len(digits) != 13 or not digits.startswith(("978", "979"))
                or digits.startswith("9790")):
            return _result("invalid", problem="NOT_AN_ISBN",
                           reason=f"{raw!r} is not a 13-digit number starting with 978 or 979")
        return _result("rounded", value=s)

    if re.search(r"[A-Za-z]", s):
        return _result("invalid", problem="UNEXPECTED_CHARACTERS",
                       reason=f"{raw!r} contains unexpected characters")

    return _result("invalid", problem="WRONG_LENGTH",
                   reason=f"{raw!r} is the wrong length for an ISBN-13")


def check_isbn13_column(raw):
    """rules 1.2: the isbn13 column."""
    if _is_blank(raw):
        return _result("missing", problem="EMPTY", reason="empty")

    repairs = []
    s = strip_spaces_hyphens(raw)
    if s != raw:
        repairs.append(("R_REMOVED_SEPARATORS", "removed spaces/hyphens"))

    if s.endswith(".0"):
        rest = s[:-2]
        if isbn13_ok(rest):
            return _result("ok", value=rest, repairs=repairs + [("R_REMOVED_DECIMAL", "removed trailing .0")])
        result = _isbn13_steps(raw, rest)
        if result["kind"] == "rounded":
            # A rounded value followed by .0 is never a valid ISBN-13.
            return _result("invalid", problem="ROUNDED_UNVERIFIABLE",
                           reason=f"{raw!r} is a rounded ISBN-13 that cannot be verified")
        return result

    result = _isbn13_steps(raw, s)
    result["repairs"] = repairs + result["repairs"]
    return result


# ------------------------------------------------------------ row checking

def _repair_entry(column, original, repaired, repairs):
    return {"column": column, "original": original, "repaired": repaired,
            "codes": [code for code, _ in repairs],
            "change": "; ".join(text for _, text in repairs)}


def _reject(code, reason, isbn_problem=None, isbn13_problem=None):
    return {"reject": code, "reason": reason,
            "isbn_problem": isbn_problem, "isbn13_problem": isbn13_problem}


def check_row_isbns(raw10, raw13):
    """Apply rules.md section 1 to one row.

    Returns either a rejection ({'reject': code, 'reason', 'isbn_problem',
    'isbn13_problem'}) or {'isbn', 'isbn13', 'repairs', 'warnings'} where
    repairs are report entries (one per final column) and warnings are
    {'code', 'problem', 'warning'}.
    """
    c10 = check_isbn10_column(raw10)
    c13 = check_isbn13_column(raw13)

    v10 = c10["value"] if c10["kind"] == "ok" else None
    v13 = c13["value"] if c13["kind"] == "ok" else None
    repairs13 = None  # (original, repairs) that produced the final ISBN-13

    if v13 is not None:
        repairs13 = (raw13, c13["repairs"])

    # rules 1.5: valid ISBN-13 in the isbn column.
    if c10["kind"] == "isbn13":
        moved = c10["value"]
        if c13["kind"] == "ok":
            if v13 != moved:
                return _reject(REJ_ISBN_CONFLICT,
                               f"isbn column holds ISBN-13 {moved}, which conflicts with isbn13 {v13}")
            # Same ISBN-13 in both columns: continue with the isbn13 column's value.
        elif c13["kind"] == "rounded" and not rounds_to(moved, c13["value"]):
            return _reject(REJ_ISBN_CONFLICT,
                           f"isbn column holds ISBN-13 {moved}, which conflicts with isbn13 {raw13!r}")
        else:
            # isbn13 column is empty, unusable, or a rounded value that matches.
            v13 = moved
            repairs13 = (raw10, c10["repairs"] + [("R_MOVED_ISBN13_TO_ISBN13_COLUMN",
                                                   "moved valid ISBN-13 from the isbn column to isbn13")])
            if c13["kind"] == "rounded":
                c13 = _result("ok", value=moved)  # verified by the moved ISBN-13

    # rules 1.3: rounded ISBN-13, verified only by a usable ISBN-10 on the row.
    if c13["kind"] == "rounded":
        if v10:
            rebuilt = isbn10_to_13(v10)
            if not rounds_to(rebuilt, c13["value"]):
                return _reject(REJ_ISBN_CONFLICT,
                               f"ISBN-10 {v10} and rounded ISBN-13 {raw13!r} identify different books "
                               f"(ISBN-13 from the ISBN-10 would be {rebuilt})")
            v13 = rebuilt
            repairs13 = (raw13, c13["repairs"] + [("R_REBUILT_ROUNDED_ISBN13",
                                                   f"rebuilt rounded ISBN-13 from ISBN-10 {v10}")])
        else:
            c13 = _result("invalid", problem="ROUNDED_UNVERIFIABLE",
                          reason=f"{raw13!r} is a rounded ISBN-13 that cannot be verified without a usable ISBN-10")

    # rules 1.4: both usable, so they must identify the same book.
    if v10 and v13 and isbn10_to_13(v10) != v13:
        return _reject(REJ_ISBN_CONFLICT, f"ISBN-10 {v10} and ISBN-13 {v13} identify different books")

    # rules 1.6: neither usable.
    if not v10 and not v13:
        return _reject(REJ_NO_USABLE_ISBN,
                       f"no usable ISBN (isbn: {c10['reason']}; isbn13: {c13['reason']})",
                       c10["problem"], c13["problem"])

    repairs = []
    if v10 and c10["repairs"]:
        repairs.append(_repair_entry("isbn", raw10, v10, c10["repairs"]))
    if v13 and repairs13 and repairs13[1]:
        repairs.append(_repair_entry("isbn13", repairs13[0], v13, repairs13[1]))

    # rules 1.6: only one usable ISBN.
    warnings = []
    if c10["kind"] == "invalid":
        warnings.append({"code": "W_ISBN10_UNUSABLE", "problem": c10["problem"],
                         "warning": f"isbn value could not be used ({c10['reason']}); used the ISBN-13 instead"})
    if c13["kind"] == "invalid":
        warnings.append({"code": "W_ISBN13_UNUSABLE", "problem": c13["problem"],
                         "warning": f"isbn13 value could not be used ({c13['reason']}); used the other ISBN instead"})

    if v10 and not v13:
        v13 = isbn10_to_13(v10)
        warnings.append({"code": "W_ISBN13_CALCULATED", "problem": None,
                         "warning": f"ISBN-13 {v13} calculated from ISBN-10 {v10}"})
    elif v13 and not v10 and v13.startswith("978"):
        v10 = isbn13_to_10(v13)
        warnings.append({"code": "W_ISBN10_CALCULATED", "problem": None,
                         "warning": f"ISBN-10 {v10} calculated from ISBN-13 {v13}"})
    # A 979 ISBN-13 has no ISBN-10, so the row loads with ISBN-10 empty.

    return {"isbn": v10, "isbn13": v13, "repairs": repairs, "warnings": warnings}


def norm(text):
    """rules 2: lowercase, remove spaces at the ends, collapse repeated spaces.
    Punctuation and author order are kept."""
    return re.sub(r" +", " ", (text or "").strip(" ")).lower()


def is_empty(value):
    return value is None or value == ""


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
    rejected_rows, skipped_rows, updated_rows, repairs_out, warnings_out = [], [], [], [], []

    con = open_db()
    try:
        with con:  # one transaction for the whole run
            for line, row in rows:
                raw10, raw13 = row["isbn"] or "", row["isbn13"] or ""
                base = {"line": line, "book_id": row["book_id"], "title": row["title"]}
                ident = {**base, "authors": row["authors"], "isbn": raw10, "isbn13": raw13}

                def reject(code, reason, isbn_problem=None, isbn13_problem=None):
                    counts["rejected"] += 1
                    rejected_rows.append({**ident, "code": code, "reason": reason,
                                          "isbn_problem": isbn_problem, "isbn13_problem": isbn13_problem})

                result = check_row_isbns(raw10, raw13)
                if "reject" in result:
                    reject(result["reject"], result["reason"], result["isbn_problem"], result["isbn13_problem"])
                    continue
                v10, v13 = result["isbn"], result["isbn13"]

                # rules 2: find the stored book by ISBN-13, then by ISBN-10.
                by13, by10 = find(con, "isbn13", v13), find(con, "isbn", v10)
                if by10 is not None and by13 is not None and by10["isbn13"] != by13["isbn13"]:
                    reject(REJ_ISBN_BELONGS_TO_DIFFERENT_BOOK,
                           f"ISBN-10 {v10} belongs to {by10['title']!r} (book_id {by10['book_id']}) "
                           f"but ISBN-13 {v13} belongs to {by13['title']!r} (book_id {by13['book_id']})")
                    continue
                existing = by13 if by13 is not None else by10

                if existing is not None:
                    if norm(existing["title"]) != norm(row["title"]) or norm(existing["authors"]) != norm(row["authors"]):
                        isbn_used = v13 if by13 is not None else v10
                        reject(REJ_ISBN_BELONGS_TO_DIFFERENT_BOOK,
                               f"ISBN {isbn_used} already belongs to a different book: "
                               f"{existing['title']!r} by {existing['authors']!r} (book_id {existing['book_id']})")
                        continue

                    # rules 3.2: tracked fields that changed, plus an empty stored
                    # ISBN-10 that this row fills in, in CSV column order.
                    changed = [c for c in CSV_COLUMNS
                               if (c in TRACKED_FIELDS and existing[c] != row[c])
                               or (c == "isbn" and is_empty(existing["isbn"]) and v10)]
                    if not changed:
                        outcome = "skipped"
                        counts["skipped"] += 1
                        skipped_rows.append({**ident, "code": SKIP_UNCHANGED,
                                             "reason": f"already in the database with the same information "
                                                       f"(ISBN-13 {existing['isbn13']})"})
                    else:
                        outcome = "updated"
                        counts["updated"] += 1
                        # Title, authors and the stored ISBN-13 are never changed,
                        # and a stored ISBN is never cleared.
                        values = {c: (v10 if c == "isbn" else row[c]) for c in changed}
                        sets = ", ".join(f"{c} = ?" for c in values)
                        con.execute(f"UPDATE books SET {sets} WHERE isbn13 = ?",
                                    list(values.values()) + [existing["isbn13"]])
                        updated_rows.append({**base, "isbn13": existing["isbn13"], "changed_fields": changed})
                else:
                    outcome = "loaded"
                    counts["loaded"] += 1
                    new_values = {c: row[c] for c in CSV_COLUMNS}
                    new_values["isbn"], new_values["isbn13"] = v10, v13
                    cols = CSV_COLUMNS + ["original_isbn", "original_isbn13"]
                    con.execute(f"INSERT INTO books ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                [new_values[c] for c in CSV_COLUMNS] + [raw10, raw13])

                # rules 4.3: repairs and warnings for loaded, updated and skipped rows.
                for repair in result["repairs"]:
                    repairs_out.append({**base, **repair, "row_result": outcome})
                for warning in result["warnings"]:
                    warnings_out.append({**base, **warning, "row_result": outcome})
    finally:
        con.close()

    entry = {
        "run_at": run_at,
        "csv_file": csv_path.name,
        "csv_path": str(csv_path),
        **counts,
        "rejected_rows": rejected_rows,
        "skipped_rows": skipped_rows,
        "updated_rows": updated_rows,
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
