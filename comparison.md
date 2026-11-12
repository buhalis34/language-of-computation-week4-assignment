# Module 4 – Part 5: Database Comparison

## Working individually

I completed this assignment individually. I reached out to my classmates but could not find a partner.

Because I have no partner, **a partner-to-partner database comparison could not be performed.** There is no second `books.db` or `ingestion-log.json` to compare against, so this document does not contain any partner results. Instead, it records a check of my own database and ingestion log.

## What I checked

I opened `books.db` and `ingestion-log.json` in read-only mode. I compared each file's checksum before and after, and neither file changed.

The database was built by running these commands, in order, after removing any old `books.db` and `ingestion-log.json`:

```bash
python3 ingest.py books.csv
```

```bash
python3 ingest.py books.csv
```

```bash
python3 ingest.py books-batch2.csv
```

## The three runs in ingestion-log.json

The log contains exactly three entries, one per run, in the order they were run.

| Run | Time (`run_at`) | File | Rows read | Loaded | Updated | Skipped | Rejected | Total of outcomes |
|---|---|---|---|---|---|---|---|---|
| 1 | 2026-11-11T19:50:58-06:00 | books.csv | 10,000 | 9,266 | 0 | 0 | 734 | 10,000 ✓ |
| 2 | 2026-11-11T19:51:02-06:00 | books.csv | 10,000 | 0 | 0 | 9,266 | 734 | 10,000 ✓ |
| 3 | 2026-11-11T19:51:05-06:00 | books-batch2.csv | 31 | 8 | 4 | 11 | 8 | 31 ✓ |

For every run, loaded + updated + skipped + rejected equals the rows read. The detailed lists also match their counts: the number of entries in `rejected_rows`, `skipped_rows` and `updated_rows` equals the `rejected`, `skipped` and `updated` totals for each run.

### Run 1: books.csv

- **9,266 books loaded.**
  - **Repairs:** 6,580 ISBN-10s were repaired by adding leading zeros (`R_PADDED_ZEROS`), and 9,239 rounded ISBN-13s were rebuilt from their ISBN-10 (`R_REBUILT_ROUNDED_ISBN13`).
  - **Warnings:** 35 in total. 27 rows had their ISBN-13 calculated from the ISBN-10, and 8 had an `isbn13` value that could not be used.
- **734 rows rejected:**
  - **723 had no usable ISBN (`REJ_NO_USABLE_ISBN`):**
    - 565 had both ISBN columns empty.
    - 110 had an empty `isbn` and a rounded `isbn13` that could not be verified without an ISBN-10.
    - 25 had an empty `isbn` and an `isbn13` value that is not an ISBN.
    - 21 had a bad ISBN-10 check digit and an unverifiable rounded `isbn13`.
    - 1 had a bad ISBN-10 check digit and an empty `isbn13`.
  - **11 had conflicting ISBNs (`REJ_ISBN_CONFLICT`):** the ISBN-10 and ISBN-13 identify different books.

### Run 2: books.csv again

- **No new books were loaded.** All 9,266 books from run 1 were skipped as unchanged (`SKIP_UNCHANGED`).
- **The same 734 rows were rejected again, for the same reasons.** Every row is checked on every run.
- This shows that running the same file twice does not create duplicate books.

### Run 3: books-batch2.csv

- **8 new books loaded.**
- **4 existing books updated.** In each case, `ratings_count`, `work_ratings_count` and `ratings_5` changed:
  - *The Five People You Meet in Heaven*
  - *Animal Farm*
  - *1984*
  - *City of Glass*
- **11 rows skipped**, because they were already stored unchanged.
- **8 rows rejected:**
  - 6 had no usable ISBN.
  - 1 had conflicting ISBNs.
  - 1 had an ISBN that already belongs to a different book (`REJ_ISBN_BELONGS_TO_DIFFERENT_BOOK`).

## Final database

- **`books.db` contains 9,274 books.** That is the 9,266 loaded in run 1 plus the 8 loaded in run 3. Run 2 added none, and updates don't add books.
- **Every book has its own ISBN-13**, with 9,274 different ISBN-13 values, and no ISBN-10 is stored twice.
- **One book has no ISBN-10.** It was loaded with a valid ISBN-13 starting with 979, which has no ISBN-10 (rules.md, section 1.6).

## Why our data differs

No partner database was available, so I could not determine whether, or why, my data differs from another student's. I do not know any other student's book count, rejected rows or log totals, so I have not listed any differences or explanations for them.

The only comparison I could make is within my own work. The two runs of `books.csv` gave identical results (9,266 books stored and 734 rows rejected both times), and the final database matches the totals in the log.
