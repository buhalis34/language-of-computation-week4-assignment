"""Whole-file runs: repeated runs, the append-only log, and the report totals.

Uses copies of the real books.csv and books-batch2.csv inside a temporary
folder, in the same order as the README: books.csv twice, then
books-batch2.csv once. The originals in the project folder are only read.
"""

import pytest

from conftest import TEST_DB_NAME, make_sandbox

COUNT_FIELDS = ["loaded", "updated", "skipped", "rejected"]
RUN_NAMES = ["books.csv (run 1)", "books.csv (run 2)", "books-batch2.csv"]


def isbn_values(books, column):
    return [b[column] for b in books if b[column] not in (None, "")]


@pytest.fixture(scope="module")
def readme_sequence():
    """Run books.csv twice and books-batch2.csv once in one temporary test.db.

    Records the log and the database after each run. The temporary folder is
    deleted when the module's tests finish, even if some of them fail.
    """
    with make_sandbox() as box:
        books_csv = box.copy_project_csv("books.csv")
        batch2_csv = box.copy_project_csv("books-batch2.csv")
        result = {"entries": [], "logs": [], "books": []}
        for csv_path in (books_csv, books_csv, batch2_csv):
            result["entries"].append(box.run(csv_path))
            result["logs"].append(box.log())
            result["books"].append(box.books())
        result["db_name"] = box.db_path.name
        yield result


# ------------------------------------------------- 2. no duplicate books

def test_running_books_csv_twice_adds_no_books(readme_sequence):
    books_after_1, books_after_2 = readme_sequence["books"][:2]
    second_run = readme_sequence["entries"][1]

    assert second_run["loaded"] == 0, "the second run must not load any new books"
    assert len(books_after_2) == len(books_after_1)


def test_running_books_csv_twice_leaves_no_duplicate_isbns(readme_sequence):
    books = readme_sequence["books"][1]
    isbn13s = isbn_values(books, "isbn13")
    isbn10s = isbn_values(books, "isbn")

    assert len(isbn13s) == len(books), "every stored book must have an ISBN-13"
    assert len(set(isbn13s)) == len(isbn13s), "an ISBN-13 is stored more than once"
    assert len(set(isbn10s)) == len(isbn10s), "an ISBN-10 is stored more than once"


def test_running_batch2_twice_adds_no_books(sandbox):
    csv_path = sandbox.copy_project_csv("books-batch2.csv")
    sandbox.run(csv_path)
    count_after_first = len(sandbox.books())

    second = sandbox.run(csv_path)

    assert second["loaded"] == 0
    assert len(sandbox.books()) == count_after_first


# ----------------------------------- 3. the log is appended, never overwritten

def test_each_run_appends_one_log_entry(readme_sequence):
    for run_number, log in enumerate(readme_sequence["logs"], start=1):
        assert len(log) == run_number, f"after run {run_number} the log should have {run_number} entries"


def test_earlier_log_entries_are_never_changed(readme_sequence):
    logs = readme_sequence["logs"]
    for later in range(1, len(logs)):
        earlier = later - 1
        assert logs[later][:len(logs[earlier])] == logs[earlier], (
            f"run {later + 1} changed or removed log entries written by earlier runs")


def test_each_log_entry_names_its_csv_file(readme_sequence):
    files = [e["csv_file"] for e in readme_sequence["entries"]]
    assert files == ["books.csv", "books.csv", "books-batch2.csv"]


# ---------------------------------------- 4. the counts add up to rows read

@pytest.mark.parametrize("run_index", range(3), ids=RUN_NAMES)
def test_counts_add_up_to_rows_read(readme_sequence, run_index):
    entry = readme_sequence["entries"][run_index]
    total = sum(entry[name] for name in COUNT_FIELDS)
    assert total == entry["rows_read"], (
        f"loaded + updated + skipped + rejected = {total}, but rows_read = {entry['rows_read']}")


@pytest.mark.parametrize("run_index", range(3), ids=RUN_NAMES)
def test_row_lists_match_their_counts(readme_sequence, run_index):
    """rules.md 4.2: the rejected, skipped and updated lists match their counts."""
    entry = readme_sequence["entries"][run_index]
    for count_name, list_name in [("rejected", "rejected_rows"), ("skipped", "skipped_rows"),
                                  ("updated", "updated_rows")]:
        assert list_name in entry, f"log entry has no {list_name!r} field"
        assert len(entry[list_name]) == entry[count_name], f"{list_name} does not match {count_name}"


def test_rows_read_matches_the_csv_files(readme_sequence):
    """books.csv has 10,000 data rows; books-batch2.csv has 31."""
    assert [e["rows_read"] for e in readme_sequence["entries"]] == [10000, 10000, 31]


# ------------------------------------- 5. same file twice, same final database

def test_running_books_csv_twice_gives_same_database(readme_sequence):
    books_after_1, books_after_2 = readme_sequence["books"][:2]
    assert books_after_2 == books_after_1


def test_running_batch2_twice_gives_same_database(sandbox):
    csv_path = sandbox.copy_project_csv("books-batch2.csv")
    sandbox.run(csv_path)
    books_after_first = sandbox.books()

    sandbox.run(csv_path)

    assert sandbox.books() == books_after_first


def test_fresh_runs_of_books_csv_give_same_database(readme_sequence):
    """The same file and the same starting state always give the same result."""
    with make_sandbox() as fresh:
        fresh.run(fresh.copy_project_csv("books.csv"))
        fresh_books = fresh.books()
    assert fresh_books == readme_sequence["books"][0]


# ------------------------------------------------ 6. tests use test.db only

def test_tests_use_test_db(readme_sequence, sandbox):
    assert readme_sequence["db_name"] == TEST_DB_NAME == "test.db"
    assert sandbox.db_path.name == "test.db"
