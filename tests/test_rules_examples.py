"""One test per example in rules.md section 6 (36 examples).

Each test runs ingest.py on a small CSV in a temporary folder (test.db) and
checks the exact outcome, stored ISBNs, codes, repairs and warnings that
rules.md expects. "Codes" in the rules table become:
- rejected rows: `code`, `isbn_problem`, `isbn13_problem` in `rejected_rows`
- skipped rows: `code` in `skipped_rows`
- updated rows: `changed_fields` in `updated_rows`
- repairs: {column: codes} from `repairs`
- warnings: (code, problem) pairs from `warnings`
"""

from collections import Counter

import pytest

from conftest import BOOK_A, BOOK_B, book_row, is_empty

A10, A13 = BOOK_A["isbn"], BOOK_A["isbn13"]
B10, B13 = BOOK_B["isbn"], BOOK_B["isbn13"]

OUTCOME_LISTS = {"rejected": "rejected_rows", "skipped": "skipped_rows", "updated": "updated_rows"}
COUNT_FIELDS = ["loaded", "updated", "skipped", "rejected"]


# ------------------------------------------------------------------ helpers

def field(entry, name):
    assert name in entry, f"log entry has no {name!r} field (rules.md section 4)"
    return entry[name]


def entries_for(entry, list_name, line):
    return [e for e in field(entry, list_name) if e.get("line") == line]


def check_row(entry, line, outcome, *, code=None, problems=(None, None),
              repairs=None, warnings=None, changed_fields=None):
    """Check the log entry's record of one CSV line against rules.md."""
    # The row appears in its own outcome list (if it has one) and in no other.
    for name, list_name in OUTCOME_LISTS.items():
        found = entries_for(entry, list_name, line)
        if name == outcome:
            assert len(found) == 1, f"line {line} should appear once in {list_name}, found {found}"
        else:
            assert found == [], f"line {line} should not appear in {list_name}: {found}"

    if outcome == "rejected":
        rec = entries_for(entry, "rejected_rows", line)[0]
        assert rec.get("code") == code
        assert (rec.get("isbn_problem"), rec.get("isbn13_problem")) == problems
        # Rejected rows record problem codes instead of repairs or warnings.
        assert entries_for(entry, "repairs", line) == []
        assert entries_for(entry, "warnings", line) == []
        return

    if outcome == "skipped":
        rec = entries_for(entry, "skipped_rows", line)[0]
        assert rec.get("code") == "SKIP_UNCHANGED"
    if outcome == "updated" and changed_fields is not None:
        rec = entries_for(entry, "updated_rows", line)[0]
        assert rec.get("changed_fields") == changed_fields

    repair_entries = entries_for(entry, "repairs", line)
    assert {r.get("column"): r.get("codes") for r in repair_entries} == (repairs or {})
    assert len(repair_entries) == len(repairs or {}), "expected one repair entry per column"
    assert all(r.get("row_result") == outcome for r in repair_entries)

    warning_entries = entries_for(entry, "warnings", line)
    actual_warnings = Counter((w.get("code"), w.get("problem")) for w in warning_entries)
    assert actual_warnings == Counter(warnings or [])
    assert all(w.get("row_result") == outcome for w in warning_entries)


def check_counts(entry, rows_read, **expected):
    assert field(entry, "rows_read") == rows_read
    actual = {name: field(entry, name) for name in COUNT_FIELDS}
    assert actual == {name: expected.get(name, 0) for name in COUNT_FIELDS}


def check_stored_isbns(book, isbn, isbn13):
    if isbn == "":
        assert is_empty(book["isbn"]), f"stored isbn should be empty, got {book['isbn']!r}"
    else:
        assert book["isbn"] == isbn
    assert book["isbn13"] == isbn13


# ---------------------------------------- examples 1-25 and 36 (empty database)

def loaded(n, isbn, isbn13, stored, repairs=None, warnings=None):
    return pytest.param(n, isbn, isbn13, "loaded", stored, None, (None, None),
                        repairs, warnings, id=f"example_{n:02d}_loaded")


def rejected(n, isbn, isbn13, code, problems=(None, None)):
    return pytest.param(n, isbn, isbn13, "rejected", None, code, problems,
                        None, None, id=f"example_{n:02d}_rejected")


NO_ISBN = "REJ_NO_USABLE_ISBN"
CONFLICT = "REJ_ISBN_CONFLICT"

SINGLE_ROW_EXAMPLES = [
    loaded(1, "0439023483", "9780439023481", (A10, A13)),
    loaded(2, "439023483", "9.78043902348e+12", (A10, A13),
           repairs={"isbn": ["R_PADDED_ZEROS"], "isbn13": ["R_REBUILT_ROUNDED_ISBN13"]}),
    loaded(3, "7442912", "", ("0007442912", "9780007442911"),
           repairs={"isbn": ["R_PADDED_ZEROS"]},
           warnings=[("W_ISBN13_CALCULATED", None)]),
    loaded(4, "0-439-02348-3", "978-0-439-02348-1", (A10, A13),
           repairs={"isbn": ["R_REMOVED_SEPARATORS"], "isbn13": ["R_REMOVED_SEPARATORS"]}),
    loaded(5, "0 439 02348 3", "9780439023481", (A10, A13),
           repairs={"isbn": ["R_REMOVED_SEPARATORS"]}),
    loaded(6, "031200009x", "9780312000097", ("031200009X", "9780312000097"),
           repairs={"isbn": ["R_UPPERCASED_X"]}),
    loaded(7, "0-312-00009-x", "", ("031200009X", "9780312000097"),
           repairs={"isbn": ["R_REMOVED_SEPARATORS", "R_UPPERCASED_X"]},
           warnings=[("W_ISBN13_CALCULATED", None)]),
    rejected(8, "19", "", NO_ISBN, ("WRONG_LENGTH", "EMPTY")),
    rejected(9, "812971060", "", NO_ISBN, ("BAD_CHECK_DIGIT", "EMPTY")),
    loaded(10, "1854433017", "9781854433015", ("1854433016", "9781854433015"),
           warnings=[("W_ISBN10_UNUSABLE", "BAD_CHECK_DIGIT"), ("W_ISBN10_CALCULATED", None)]),
    rejected(11, "1854433017", "", NO_ISBN, ("BAD_CHECK_DIGIT", "EMPTY")),
    loaded(12, "04390A3483", "9780439023481", (A10, A13),
           warnings=[("W_ISBN10_UNUSABLE", "UNEXPECTED_CHARACTERS"), ("W_ISBN10_CALCULATED", None)]),
    rejected(13, "15287463097", "", NO_ISBN, ("WRONG_LENGTH", "EMPTY")),
    loaded(14, "553609416", "76783609419.0", ("0553609416", "9780553609417"),
           repairs={"isbn": ["R_PADDED_ZEROS"]},
           warnings=[("W_ISBN13_UNUSABLE", "WRONG_LENGTH"), ("W_ISBN13_CALCULATED", None)]),
    loaded(15, "", "9780439023481.0", (A10, A13),
           repairs={"isbn13": ["R_REMOVED_DECIMAL"]},
           warnings=[("W_ISBN10_CALCULATED", None)]),
    rejected(16, "", "", NO_ISBN, ("EMPTY", "EMPTY")),
    loaded(17, "9781982147600", "", ("1982147601", "9781982147600"),
           repairs={"isbn13": ["R_MOVED_ISBN13_TO_ISBN13_COLUMN"]},
           warnings=[("W_ISBN10_CALCULATED", None)]),
    rejected(18, "9781982147600", "9780439023481", CONFLICT),
    rejected(19, "0439023483", "9781982147600", CONFLICT),
    rejected(20, "0439023483", "9.78198214760e+12", CONFLICT),
    rejected(21, "", "9.78043902348e+12", NO_ISBN, ("EMPTY", "ROUNDED_UNVERIFIABLE")),
    loaded(22, "", "9791234567896", ("", "9791234567896")),
    rejected(23, "", "9790000000001", NO_ISBN, ("EMPTY", "NOT_AN_ISBN")),
    loaded(24, "0439023483", "9780439023482", (A10, A13),
           warnings=[("W_ISBN13_UNUSABLE", "BAD_CHECK_DIGIT"), ("W_ISBN13_CALCULATED", None)]),
    rejected(25, "", "5012345678900", NO_ISBN, ("EMPTY", "NOT_AN_ISBN")),
    loaded(36, "195170342", "195170342.0", ("0195170342", "9780195170344"),
           repairs={"isbn": ["R_PADDED_ZEROS"]},
           warnings=[("W_ISBN13_UNUSABLE", "WRONG_LENGTH"), ("W_ISBN13_CALCULATED", None)]),
]


@pytest.mark.parametrize(
    "n, isbn, isbn13, outcome, stored, code, problems, repairs, warnings",
    SINGLE_ROW_EXAMPLES,
)
def test_single_row_example(sandbox, n, isbn, isbn13, outcome, stored, code,
                            problems, repairs, warnings):
    entry = sandbox.run_rows([book_row(isbn, isbn13)])

    check_counts(entry, 1, **{outcome: 1})
    check_row(entry, 2, outcome, code=code, problems=problems,
              repairs=repairs, warnings=warnings)

    books = sandbox.books()
    if outcome == "rejected":
        assert books == [], "a rejected row must not be stored"
    else:
        assert len(books) == 1
        check_stored_isbns(books[0], *stored)


# ------------------------------- examples 26-31 (Book A and Book B already stored)

def seeded(n, row, outcome, code=None, changed_fields=None):
    return pytest.param(n, row, outcome, code, changed_fields, id=f"example_{n:02d}_{outcome}")


BELONGS = "REJ_ISBN_BELONGS_TO_DIFFERENT_BOOK"

SEEDED_EXAMPLES = [
    seeded(26, dict(BOOK_A), "skipped"),
    seeded(27, book_row(A10, A13, title="the  hunger games"), "skipped"),
    seeded(28, book_row(A10, A13, ratings_count="4780654"), "updated",
           changed_fields=["ratings_count"]),
    seeded(29, book_row(A10, A13, title="Catching Fire"), "rejected", code=BELONGS),
    seeded(30, book_row(A10, A13, authors="Collins, Suzanne"), "rejected", code=BELONGS),
    seeded(31, book_row(B10, B13, title="The Hunger Games"), "rejected", code=BELONGS),
]


@pytest.mark.parametrize("n, row, outcome, code, changed_fields", SEEDED_EXAMPLES)
def test_example_with_existing_books(seeded_sandbox, n, row, outcome, code, changed_fields):
    before = seeded_sandbox.books()

    entry = seeded_sandbox.run_rows([row])

    check_counts(entry, 1, **{outcome: 1})
    check_row(entry, 2, outcome, code=code, changed_fields=changed_fields)

    after = seeded_sandbox.books()
    assert len(after) == 2, "no book may be added or removed"
    if outcome == "updated":
        # Only ratings_count changes; ISBNs, title and authors stay the same.
        expected = [dict(b) for b in before]
        for b in expected:
            if b["isbn13"] == A13:
                b["ratings_count"] = "4780654"
        assert after == expected
    else:
        # Skipped and rejected rows leave both stored books exactly as they were
        # (example 27: the stored title stays "The Hunger Games").
        assert after == before

    book_a = next(b for b in after if b["isbn13"] == A13)
    book_b = next(b for b in after if b["isbn13"] == B13)
    check_stored_isbns(book_a, A10, A13)
    check_stored_isbns(book_b, B10, B13)
    assert book_a["title"] == "The Hunger Games"
    assert book_a["authors"] == "Suzanne Collins"


# --------------------------------- examples 32-34 (duplicates in the same file)

def test_example_32_identical_repeat_in_same_file_is_skipped(sandbox):
    entry = sandbox.run_rows([BOOK_A, BOOK_A])

    check_counts(entry, 2, loaded=1, skipped=1)
    check_row(entry, 2, "loaded")
    check_row(entry, 3, "skipped")
    books = sandbox.books()
    assert len(books) == 1
    check_stored_isbns(books[0], A10, A13)


def test_example_33_repeat_with_different_title_in_same_file_is_rejected(sandbox):
    entry = sandbox.run_rows([BOOK_A, book_row(A10, A13, title="Catching Fire")])

    check_counts(entry, 2, loaded=1, rejected=1)
    check_row(entry, 2, "loaded")
    check_row(entry, 3, "rejected", code=BELONGS)
    books = sandbox.books()
    assert len(books) == 1
    check_stored_isbns(books[0], A10, A13)
    assert books[0]["title"] == "The Hunger Games"


def test_example_34_changed_repeat_in_same_file_updates_last_wins(sandbox):
    entry = sandbox.run_rows([BOOK_A, book_row(A10, A13, ratings_count="4780654")])

    check_counts(entry, 2, loaded=1, updated=1)
    check_row(entry, 2, "loaded")
    check_row(entry, 3, "updated", changed_fields=["ratings_count"])
    books = sandbox.books()
    assert len(books) == 1
    check_stored_isbns(books[0], A10, A13)
    assert books[0]["ratings_count"] == "4780654"


# ------------------------------------------ example 35 (same file run twice)

def test_example_35_same_file_run_twice(sandbox):
    csv_path = sandbox.write_csv([
        book_row("0439023483", "9780439023481"),  # example 1
        book_row("19", ""),                        # example 8
        book_row("", "9791234567896"),             # example 22
    ])

    first = sandbox.run(csv_path)
    log_after_first = sandbox.log()
    books_after_first = sandbox.books()
    second = sandbox.run(csv_path)

    check_counts(first, 3, loaded=2, rejected=1)
    check_counts(second, 3, skipped=2, rejected=1)
    check_row(second, 2, "skipped")
    check_row(second, 3, "rejected", code=NO_ISBN, problems=("WRONG_LENGTH", "EMPTY"))
    check_row(second, 4, "skipped")

    log = sandbox.log()
    assert len(log) == 2
    assert log[0] == log_after_first[0], "run 2 must not change the run 1 log entry"
    assert sandbox.books() == books_after_first
    assert len(books_after_first) == 2


# ------------------------------------------------------------ coverage check

def test_all_36_rules_examples_have_a_test():
    numbers = {p.values[0] for p in SINGLE_ROW_EXAMPLES + SEEDED_EXAMPLES} | {32, 33, 34, 35}
    assert numbers == set(range(1, 37))
