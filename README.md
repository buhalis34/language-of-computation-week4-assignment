# Language of Computation — Week 4 Assignment

This is my Module 4 assignment for MSAI-5523.

I completed this assignment individually because I could not find a partner.

## Files

- `rules.md` — the ingestion rules, including 36 example cases
- `ingest.py` — loads a books CSV into `books.db` and appends a run to `ingestion-log.json`
- `report.html` — readable report; open it in a browser and choose `ingestion-log.json`
- `books.csv` — the original goodbooks-10k file
- `books-batch2.csv` — a second batch of books
- `tests/` — pytest tests for the rules in `rules.md`

## Setup: install pytest

Run these from the project folder. A virtual environment keeps pytest separate from the system Python.

```bash
cd ~/workspace/language-of-computation/week4-assignment
```

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
python -m pip install pytest
```

In a new Terminal window, run `source .venv/bin/activate` again before using the commands below.

## Running the tests

```bash
python -m pytest -v
```

To save the results to a file while also showing them:

```bash
python -m pytest -v 2>&1 | tee test-results-before.txt
```

The tests use a temporary `test.db` and a temporary test log, which are deleted after each test, even if it fails. They never read or change `books.db` or `ingestion-log.json`.

## Running ingest.py

Load `books.csv` twice, then `books-batch2.csv` once:

```bash
python ingest.py books.csv
```

```bash
python ingest.py books.csv
```

```bash
python ingest.py books-batch2.csv
```

Each run updates `books.db` and adds one entry to `ingestion-log.json`. Open `report.html` in a browser and choose `ingestion-log.json` to see every run.
