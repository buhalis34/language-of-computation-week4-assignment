"""Shared pytest fixtures for testing ingest.py against rules.md.

Safety rules followed here:
- ingest.py is imported, never edited. Its DB_PATH and LOG_PATH settings are
  pointed at a fresh temporary folder (test.db and test-ingestion-log.json)
  for each test, then put back.
- The temporary folder, and everything in it, is deleted when the test ends,
  even if the test fails.
- The real books.db and ingestion-log.json are fingerprinted before the test
  session starts and checked again at the end, so any change to them is reported.
"""

import contextlib
import csv
import hashlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Do not leave a compiled copy of ingest.py (__pycache__) in the project folder.
sys.dont_write_bytecode = True

PROJECT = Path(__file__).resolve().parent.parent
INGEST_PY = PROJECT / "ingest.py"
REAL_DB = PROJECT / "books.db"
REAL_LOG = PROJECT / "ingestion-log.json"

TEST_DB_NAME = "test.db"
TEST_LOG_NAME = "test-ingestion-log.json"

CSV_COLUMNS = [
    "book_id", "goodreads_book_id", "best_book_id", "work_id", "books_count",
    "isbn", "isbn13", "authors", "original_publication_year", "original_title",
    "title", "language_code", "average_rating", "ratings_count",
    "work_ratings_count", "work_text_reviews_count", "ratings_1", "ratings_2",
    "ratings_3", "ratings_4", "ratings_5", "image_url", "small_image_url",
]


def _load_ingest_module():
    """Import ingest.py from the project folder without changing it."""
    spec = importlib.util.spec_from_file_location("ingest_under_test", INGEST_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ingest_module = _load_ingest_module()


# Book A from rules.md section 6. Its title, authors, ISBNs and ratings_count
# come from rules.md; the other tracked fields come from the Hunger Games row
# (line 2) of the original books.csv.
BOOK_A = {
    "book_id": "1",
    "goodreads_book_id": "2767052",
    "best_book_id": "2767052",
    "work_id": "2792775",
    "books_count": "272",
    "isbn": "0439023483",
    "isbn13": "9780439023481",
    "authors": "Suzanne Collins",
    "original_publication_year": "2008.0",
    "original_title": "The Hunger Games",
    "title": "The Hunger Games",
    "language_code": "eng",
    "average_rating": "4.34",
    "ratings_count": "4780653",
    "work_ratings_count": "4942365",
    "work_text_reviews_count": "155254",
    "ratings_1": "66715",
    "ratings_2": "127936",
    "ratings_3": "560092",
    "ratings_4": "1481305",
    "ratings_5": "2706317",
    "image_url": "https://images.gr-assets.com/books/1447303603m/2767052.jpg",
    "small_image_url": "https://images.gr-assets.com/books/1447303603s/2767052.jpg",
}

# Book B from rules.md section 6 (ISBNs, title and authors). Its other fields
# do not affect any expected result.
BOOK_B = {
    **BOOK_A,
    "book_id": "2",
    "goodreads_book_id": "2",
    "best_book_id": "2",
    "work_id": "2",
    "isbn": "1982147601",
    "isbn13": "9781982147600",
    "title": "Book B",
    "authors": "Author B",
    "original_title": "Book B",
}


def book_row(isbn, isbn13, **changes):
    """A CSV row that matches Book A except for the given ISBNs and changes."""
    return {**BOOK_A, "isbn": isbn, "isbn13": isbn13, **changes}


def is_empty(value):
    """rules.md shows an empty stored ISBN as *(empty)*; accept NULL or ''."""
    return value is None or value == ""


class Sandbox:
    """A temporary folder holding test.db, the test log and test CSV files."""

    def __init__(self, folder):
        self.folder = folder
        self.db_path = folder / TEST_DB_NAME
        self.log_path = folder / TEST_LOG_NAME
        self._csv_count = 0

    def write_csv(self, rows):
        """Write rows (dicts) to a new CSV file in the sandbox; return its path."""
        self._csv_count += 1
        path = self.folder / f"input-{self._csv_count}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def copy_project_csv(self, name):
        """Copy a CSV from the project folder into the sandbox; return its path."""
        target = self.folder / name
        target.write_bytes((PROJECT / name).read_bytes())
        return target

    def run(self, csv_path):
        """Run ingest.py on csv_path and return the log entry for this run."""
        # Last-line guard: never let a test run against the real files.
        assert Path(ingest_module.DB_PATH) == self.db_path != REAL_DB
        assert Path(ingest_module.LOG_PATH) == self.log_path != REAL_LOG
        ingest_module.ingest(str(csv_path))
        return self.log()[-1]

    def run_rows(self, rows):
        """Write rows to a CSV file, ingest it and return the log entry."""
        return self.run(self.write_csv(rows))

    def log(self):
        """All entries in the test log (an empty list if no run has happened)."""
        if not self.log_path.exists():
            return []
        return json.loads(self.log_path.read_text(encoding="utf-8"))

    def books(self):
        """Every stored book as a dict, ordered by ISBN-13."""
        if not self.db_path.exists():
            return []
        with contextlib.closing(sqlite3.connect(self.db_path)) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("SELECT * FROM books ORDER BY isbn13").fetchall()
        return [dict(r) for r in rows]


@contextlib.contextmanager
def make_sandbox():
    """Create a sandbox, point ingest.py at it, and always clean up afterwards."""
    with tempfile.TemporaryDirectory(prefix="ingest-test-") as tmp:
        box = Sandbox(Path(tmp))
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ingest_module, "DB_PATH", box.db_path)
            mp.setattr(ingest_module, "LOG_PATH", box.log_path)
            yield box
    # Leaving the with-blocks restores DB_PATH/LOG_PATH and deletes the folder.


@pytest.fixture
def sandbox():
    """A fresh, empty test.db and test log for one test."""
    with make_sandbox() as box:
        yield box


@pytest.fixture
def seeded_sandbox(sandbox):
    """A sandbox whose test.db already holds Book A and Book B (rules.md section 6)."""
    entry = sandbox.run_rows([BOOK_A, BOOK_B])
    assert entry["loaded"] == 2, "setup failed: Book A and Book B were not both loaded"
    return sandbox


def _fingerprint(path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def real_files_are_never_touched():
    """Fail the session if the real books.db or ingestion-log.json changes."""
    before = {p: _fingerprint(p) for p in (REAL_DB, REAL_LOG)}
    yield
    after = {p: _fingerprint(p) for p in (REAL_DB, REAL_LOG)}
    changed = [p.name for p in before if before[p] != after[p]]
    assert not changed, f"tests changed real project files: {changed}"
