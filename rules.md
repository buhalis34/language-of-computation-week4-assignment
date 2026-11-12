# Module 4 – Book Ingestion Rules

These rules started as my Module 3 rules. For Module 4 (completed individually) I made every rule specific enough to be checked by automated pytest tests. The intent of the original rules has not changed.

My goal is to make sure the database contains accurate book information without guessing missing data. I want the program to fix simple problems but reject anything that cannot be verified. Repairs should only happen when the correct result can be determined using clear rules. I do not want the program making guesses based on what seems likely.

The original CSV files should never be changed.

## 0. Terms

- **`isbn` column / `isbn13` column:** the two ISBN columns in the CSV file.
- **Valid ISBN-10:** 9 digits followed by a digit or an uppercase `X`, where the weighted sum (weights 10 down to 1, `X` = 10) is divisible by 11.
- **Valid ISBN-13:** 13 digits that start with `978` or `979` (but not `9790`), where the weighted sum (weights 1, 3, 1, 3, …) is divisible by 10.
- **Usable:** a column value that is a valid ISBN after the repairs allowed in section 1.
- **Row outcome:** every row ends with exactly one of `loaded`, `updated`, `skipped` or `rejected`.
- **Repair:** a change to an ISBN value that the rules allow. A repaired row still has one of the four outcomes.
- **Warning:** a note that a row was loaded, updated or skipped using only one of its ISBNs, or with an ISBN that was calculated.

## 1. Handling ISBN Problems

### 1.1 Repair order for the `isbn` (ISBN-10) column

The steps run in this order. The first step that decides the value stops the process.

1. **Empty:** a value that is empty or only spaces is missing (problem code `EMPTY`).
2. **Spaces and hyphens:** remove all spaces and hyphens (`R_REMOVED_SEPARATORS`).
3. **Trailing `.0`:** if the value ends in `.0`, remove it only if the rest becomes a usable ISBN through steps 4–9 (`R_REMOVED_DECIMAL`). If the rest is not usable, the column is unusable, and its problem code is the code the value without `.0` would get.
4. **Lowercase x:** change a lowercase `x` in the last position to uppercase `X` (`R_UPPERCASED_X`).
5. **Allowed characters:** only digits are allowed, plus an `X` in the last position. Anything else is `UNEXPECTED_CHARACTERS`.
6. **13 digits:** if the value is a valid ISBN-13, it is an ISBN-13 in the wrong column (section 1.5). If it is not a valid ISBN-13, it is `WRONG_LENGTH`.
7. **10 characters:** usable if it passes the ISBN-10 check digit test, otherwise `BAD_CHECK_DIGIT`.
8. **7, 8 or 9 characters:** add leading zeros to make 10 characters. At most 3 zeros can be added. Usable if the result passes the ISBN-10 check digit test (`R_PADDED_ZEROS`), otherwise `BAD_CHECK_DIGIT`.
9. **Any other length:** `WRONG_LENGTH`. Values shorter than 7 characters are never padded, even if padding would pass the check digit test.

### 1.2 Repair order for the `isbn13` column

1. **Empty:** a value that is empty or only spaces is missing (`EMPTY`).
2. **Spaces and hyphens:** remove all spaces and hyphens (`R_REMOVED_SEPARATORS`).
3. **Trailing `.0`:** if the value ends in `.0`, remove it only if the rest is a valid ISBN-13 (`R_REMOVED_DECIMAL`). If it is not, the column is unusable, and its problem code is the code the value without `.0` would get.
4. **13 digits:** if it does not start with `978` or `979`, or it starts with `9790` (a music code), it is `NOT_AN_ISBN`. Otherwise it is usable if it passes the ISBN-13 check digit test, or `BAD_CHECK_DIGIT` if it fails.
5. **Scientific notation** (for example `9.78043902348e+12`): if the number is not a 13-digit whole number starting with `978` or `979` (and not `9790`), it is `NOT_AN_ISBN`. Otherwise it is a rounded ISBN-13 that can only be used through section 1.3.
6. **Contains letters:** `UNEXPECTED_CHARACTERS`.
7. **Any other value:** `WRONG_LENGTH`.

An ISBN-10 found in the `isbn13` column is not moved. It is unusable (for example `195170342.0` is `WRONG_LENGTH`).

### 1.3 Rounded ISBN-13 (scientific notation)

- Rebuild the ISBN-13 from a usable ISBN-10 on the same row. If the rebuilt number, rounded to the same number of significant digits as the stored value, equals the stored value, use the rebuilt number (`R_REBUILT_ROUNDED_ISBN13`).
- If it does not match, the row is rejected (`REJ_ISBN_CONFLICT`).
- If the row has no usable ISBN-10, the rounded ISBN-13 is unusable (`ROUNDED_UNVERIFIABLE`). It is never guessed.

### 1.4 Conflicting ISBNs

- If both columns are usable, converting the ISBN-10 to an ISBN-13 must give the same ISBN-13. Otherwise the row is rejected (`REJ_ISBN_CONFLICT`).
- An ISBN that fails its check digit test cannot conflict with anything. It is only unusable, and section 1.6 applies.

### 1.5 ISBN in the wrong column

A valid ISBN-13 found in the `isbn` column is moved to the ISBN-13 field (`R_MOVED_ISBN13_TO_ISBN13_COLUMN`) only when nothing conflicts with it:

- If the `isbn13` column holds a different valid ISBN-13, or a rounded value that does not round from it, the row is rejected (`REJ_ISBN_CONFLICT`).
- If the `isbn13` column holds the same ISBN-13, the row continues with that ISBN-13.
- If the `isbn13` column is empty or unusable, the moved value is used, and section 1.6 applies to the unusable value.

### 1.6 One usable ISBN, or none

- **Neither column usable:** reject the row (`REJ_NO_USABLE_ISBN`). The report gives the problem code for each column.
- **Only one usable:** load the row using the usable ISBN, with warnings:
  - `W_ISBN10_UNUSABLE` or `W_ISBN13_UNUSABLE` when the other column held a value that could not be used. This does not apply when the value was the ISBN-13 moved out of the `isbn` column.
  - `W_ISBN13_CALCULATED` when the ISBN-13 was calculated from the ISBN-10 (always possible).
  - `W_ISBN10_CALCULATED` when the ISBN-10 was calculated from an ISBN-13 starting with `978`.
  - These warnings apply even when the other column was empty.
- **ISBN-13 starting with `979`:** there is no ISBN-10, so it loads with ISBN-10 empty. There is no calculation warning. `W_ISBN10_UNUSABLE` is still given if the `isbn` column held a value that could not be used.
- A rebuilt rounded ISBN-13 (section 1.3) is a repair, not a calculation, so it gives no warning.

### 1.7 Other barcodes

UPC codes, music codes (`9790…`), and 13-digit numbers not starting with `978` or `979` are never treated as ISBNs (`NOT_AN_ISBN`).

## 2. Identifying Duplicate Books

- Two rows count as the same book when they share a final ISBN-13 or a final ISBN-10.
- To find a stored book, look it up first by the row's final ISBN-13, then by its final ISBN-10.
- Title and author are compared after changing them to lowercase, removing spaces at the start and end, and replacing repeated spaces with one space. Punctuation and author order are kept, so `Suzanne Collins` and `Collins, Suzanne` are different authors.
- If the same ISBN is attached to a different normalized title or author, the row is rejected (`REJ_ISBN_BELONGS_TO_DIFFERENT_BOOK`) rather than treated as the same book. This also covers an ISBN-10 that belongs to one stored book while the ISBN-13 belongs to another.
- If a book has no usable ISBN, the row is rejected instead of using the title alone to identify it.

### 2.1 Duplicates within the same file

Rows are processed in file order. Each row is compared with the database as it stands after the rows before it. So:

- An identical repeat of an earlier row is `skipped`.
- A repeat with changed tracked fields is `updated`, and the last row wins.
- A repeat whose ISBN belongs to a different title or author is `rejected`.

## 3. Books Already in the Database

### 3.1 Tracked fields

These CSV columns are tracked for updates and compared as exact text: `book_id`, `goodreads_book_id`, `best_book_id`, `work_id`, `books_count`, `original_publication_year`, `original_title`, `language_code`, `average_rating`, `ratings_count`, `work_ratings_count`, `work_text_reviews_count`, `ratings_1`, `ratings_2`, `ratings_3`, `ratings_4`, `ratings_5`, `image_url`, `small_image_url`.

The identity fields are `isbn`, `isbn13`, `title` and `authors`. They are not tracked fields.

### 3.2 Outcomes for a book that is already stored

- **Skip (`SKIP_UNCHANGED`):** the normalized title and authors match, every tracked field is unchanged, and the row does not fill in a missing stored ISBN. The stored record is not changed, even if the title or authors differ only in capitalization or spacing.
- **Update:** the normalized title and authors match, and either a tracked field has changed or the stored record has an empty ISBN that the row provides. The update:
  - writes the row's tracked fields and fills in the empty ISBN;
  - keeps the stored title and authors text;
  - never changes the stored ISBN-13;
  - never clears a stored ISBN.
- **Reject:** the ISBN belongs to a different title or author (`REJ_ISBN_BELONGS_TO_DIFFERENT_BOOK`). The existing book is never overwritten.

Every run checks every row, even if the file has already been processed.

Note: because a missing ISBN-10 is always calculated from a `978` ISBN-13, and `979` ISBN-13s have no ISBN-10, the "fills in a missing ISBN" case is not expected to happen with data loaded under these rules. The rule is kept as a safeguard and has no example.

## 4. Ingestion Report

### 4.1 Files

- **`ingestion-log.json`:** a JSON list with one entry per run. Each run appends one entry, and earlier entries are never removed or changed. This keeps the results of previous runs so they can be compared later.
- **`report.html`:** the readable report. It opens `ingestion-log.json` and shows every run, newest first. It is not regenerated by the program.

### 4.2 Fields in each run entry

| Field | Meaning |
|---|---|
| `run_at` | When the run started: ISO 8601, to the second, with the local time-zone offset (for example `2026-11-11T19:16:05-05:00`) |
| `csv_file` | File name of the CSV processed |
| `csv_path` | Full path of the CSV processed |
| `rows_read` | Number of data rows checked (the header is not counted) |
| `loaded` | Books loaded for the first time |
| `updated` | Existing books updated |
| `skipped` | Rows skipped because the book already existed unchanged |
| `rejected` | Rows rejected |
| `rejected_rows` | One entry per rejected row |
| `skipped_rows` | One entry per skipped row |
| `updated_rows` | One entry per updated row |
| `repairs` | One entry per repaired ISBN column |
| `warnings` | One entry per warning |

These must always hold: `rows_read = loaded + updated + skipped + rejected`, `len(rejected_rows) = rejected`, `len(skipped_rows) = skipped` and `len(updated_rows) = updated`.

### 4.3 Fields in each list entry

- **Every entry** has `line`, `book_id` and `title`. `line` is the line number in the CSV file, where the header is line 1 and the first data row is line 2.
- **`rejected_rows`:** `authors`, `isbn` and `isbn13` (the original values), `code`, `reason`, `isbn_problem` and `isbn13_problem`. The two problem fields hold the problem codes when `code` is `REJ_NO_USABLE_ISBN`, and `null` otherwise.
- **`skipped_rows`:** `authors`, `isbn`, `isbn13`, `code` (`SKIP_UNCHANGED`) and `reason`.
- **`updated_rows`:** `isbn13` (the stored ISBN-13) and `changed_fields`, which lists the changed tracked fields and any filled-in ISBN field, in CSV column order.
- **`repairs`:**
  - `column`: the final field, `isbn` or `isbn13`
  - `original`: the original CSV value
  - `repaired`: the final value
  - `codes`: the repair codes, in the order they were applied
  - `change`: a readable description
  - `row_result`: the row outcome
- **`warnings`:** `code`, `problem` (the problem code for `*_UNUSABLE` warnings, otherwise `null`), `warning` (a readable message) and `row_result`.

Repairs and warnings are recorded for loaded, updated and skipped rows. Rejected rows record their problem codes in `rejected_rows` instead.

The report should make it easy for someone to understand what happened without looking through the database.

## 5. Codes

| Kind | Code | Meaning |
|---|---|---|
| Problem | `EMPTY` | Column is empty or only spaces |
| Problem | `UNEXPECTED_CHARACTERS` | Contains letters or other characters that are not allowed |
| Problem | `WRONG_LENGTH` | Wrong number of characters for that column |
| Problem | `BAD_CHECK_DIGIT` | Fails the check digit test (after any allowed padding) |
| Problem | `NOT_AN_ISBN` | UPC, music code, or a number not starting with 978/979 |
| Problem | `ROUNDED_UNVERIFIABLE` | Rounded ISBN-13 with no usable ISBN-10 to verify it |
| Repair | `R_REMOVED_SEPARATORS` | Removed spaces and/or hyphens |
| Repair | `R_REMOVED_DECIMAL` | Removed a trailing `.0` |
| Repair | `R_UPPERCASED_X` | Changed a final lowercase `x` to `X` |
| Repair | `R_PADDED_ZEROS` | Added 1–3 leading zeros to an ISBN-10 |
| Repair | `R_REBUILT_ROUNDED_ISBN13` | Rebuilt a rounded ISBN-13 from the matching ISBN-10 |
| Repair | `R_MOVED_ISBN13_TO_ISBN13_COLUMN` | Moved a valid ISBN-13 from the `isbn` column |
| Warning | `W_ISBN10_UNUSABLE` | The `isbn` value could not be used; the row used the ISBN-13 |
| Warning | `W_ISBN13_UNUSABLE` | The `isbn13` value could not be used; the row used the ISBN-10 |
| Warning | `W_ISBN10_CALCULATED` | ISBN-10 calculated from a 978 ISBN-13 |
| Warning | `W_ISBN13_CALCULATED` | ISBN-13 calculated from the ISBN-10 |
| Rejection | `REJ_NO_USABLE_ISBN` | Neither ISBN column is usable |
| Rejection | `REJ_ISBN_CONFLICT` | The row's ISBN-10 and ISBN-13 identify different books |
| Rejection | `REJ_ISBN_BELONGS_TO_DIFFERENT_BOOK` | The ISBN is already stored for a different title or author |
| Skip | `SKIP_UNCHANGED` | Already stored with the same identity and tracked fields |

## 6. ISBN Examples and Expected Results

Each row is one test case.

- **Rows 1–25 and 36** use an empty database and a single CSV row.
- **Rows 26–31** start with a database that already holds two books:
  - **Book A:** `isbn` `0439023483`, `isbn13` `9780439023481`, title `The Hunger Games`, authors `Suzanne Collins`, `ratings_count` `4780653`
  - **Book B:** `isbn` `1982147601`, `isbn13` `9781982147600`, title `Book B`, authors `Author B`
- **Rows 32–35** use an empty database and a CSV file with more than one row.

Unless a row says otherwise, the title, authors and tracked fields are the same as Book A's.

| # | `isbn` | `isbn13` | Outcome | Stored `isbn` | Stored `isbn13` | Codes |
|---|---|---|---|---|---|---|
| 1 | `0439023483` | `9780439023481` | loaded | `0439023483` | `9780439023481` | none |
| 2 | `439023483` | `9.78043902348e+12` | loaded | `0439023483` | `9780439023481` | isbn: R_PADDED_ZEROS; isbn13: R_REBUILT_ROUNDED_ISBN13 |
| 3 | `7442912` | *(empty)* | loaded | `0007442912` | `9780007442911` | R_PADDED_ZEROS; W_ISBN13_CALCULATED |
| 4 | `0-439-02348-3` | `978-0-439-02348-1` | loaded | `0439023483` | `9780439023481` | isbn: R_REMOVED_SEPARATORS; isbn13: R_REMOVED_SEPARATORS |
| 5 | `0 439 02348 3` | `9780439023481` | loaded | `0439023483` | `9780439023481` | R_REMOVED_SEPARATORS |
| 6 | `031200009x` | `9780312000097` | loaded | `031200009X` | `9780312000097` | R_UPPERCASED_X |
| 7 | `0-312-00009-x` | *(empty)* | loaded | `031200009X` | `9780312000097` | R_REMOVED_SEPARATORS, R_UPPERCASED_X; W_ISBN13_CALCULATED |
| 8 | `19` | *(empty)* | rejected | — | — | REJ_NO_USABLE_ISBN (isbn: WRONG_LENGTH; isbn13: EMPTY) |
| 9 | `812971060` | *(empty)* | rejected | — | — | REJ_NO_USABLE_ISBN (isbn: BAD_CHECK_DIGIT; isbn13: EMPTY) |
| 10 | `1854433017` | `9781854433015` | loaded | `1854433016` | `9781854433015` | W_ISBN10_UNUSABLE (BAD_CHECK_DIGIT); W_ISBN10_CALCULATED |
| 11 | `1854433017` | *(empty)* | rejected | — | — | REJ_NO_USABLE_ISBN (isbn: BAD_CHECK_DIGIT; isbn13: EMPTY) |
| 12 | `04390A3483` | `9780439023481` | loaded | `0439023483` | `9780439023481` | W_ISBN10_UNUSABLE (UNEXPECTED_CHARACTERS); W_ISBN10_CALCULATED |
| 13 | `15287463097` | *(empty)* | rejected | — | — | REJ_NO_USABLE_ISBN (isbn: WRONG_LENGTH; isbn13: EMPTY) |
| 14 | `553609416` | `76783609419.0` | loaded | `0553609416` | `9780553609417` | R_PADDED_ZEROS; W_ISBN13_UNUSABLE (WRONG_LENGTH); W_ISBN13_CALCULATED |
| 15 | *(empty)* | `9780439023481.0` | loaded | `0439023483` | `9780439023481` | R_REMOVED_DECIMAL; W_ISBN10_CALCULATED |
| 16 | *(empty)* | *(empty)* | rejected | — | — | REJ_NO_USABLE_ISBN (isbn: EMPTY; isbn13: EMPTY) |
| 17 | `9781982147600` | *(empty)* | loaded | `1982147601` | `9781982147600` | R_MOVED_ISBN13_TO_ISBN13_COLUMN; W_ISBN10_CALCULATED |
| 18 | `9781982147600` | `9780439023481` | rejected | — | — | REJ_ISBN_CONFLICT |
| 19 | `0439023483` | `9781982147600` | rejected | — | — | REJ_ISBN_CONFLICT |
| 20 | `0439023483` | `9.78198214760e+12` | rejected | — | — | REJ_ISBN_CONFLICT |
| 21 | *(empty)* | `9.78043902348e+12` | rejected | — | — | REJ_NO_USABLE_ISBN (isbn: EMPTY; isbn13: ROUNDED_UNVERIFIABLE) |
| 22 | *(empty)* | `9791234567896` | loaded | *(empty)* | `9791234567896` | none |
| 23 | *(empty)* | `9790000000001` | rejected | — | — | REJ_NO_USABLE_ISBN (isbn: EMPTY; isbn13: NOT_AN_ISBN) |
| 24 | `0439023483` | `9780439023482` | loaded | `0439023483` | `9780439023481` | W_ISBN13_UNUSABLE (BAD_CHECK_DIGIT); W_ISBN13_CALCULATED |
| 25 | *(empty)* | `5012345678900` | rejected | — | — | REJ_NO_USABLE_ISBN (isbn: EMPTY; isbn13: NOT_AN_ISBN) |
| 26 | Book A's ISBNs | unchanged row | skipped | `0439023483` | `9780439023481` | SKIP_UNCHANGED |
| 27 | Book A's ISBNs | title `the  hunger games` | skipped | `0439023483` | `9780439023481` | SKIP_UNCHANGED; stored title stays `The Hunger Games` |
| 28 | Book A's ISBNs | `ratings_count` `4780654` | updated | `0439023483` | `9780439023481` | `changed_fields` = `["ratings_count"]` |
| 29 | Book A's ISBNs | title `Catching Fire` | rejected | `0439023483` | `9780439023481` | REJ_ISBN_BELONGS_TO_DIFFERENT_BOOK; Book A unchanged |
| 30 | Book A's ISBNs | authors `Collins, Suzanne` | rejected | `0439023483` | `9780439023481` | REJ_ISBN_BELONGS_TO_DIFFERENT_BOOK; Book A unchanged |
| 31 | Book B's ISBNs | title `The Hunger Games` | rejected | `1982147601` | `9781982147600` | REJ_ISBN_BELONGS_TO_DIFFERENT_BOOK; Book B unchanged |
| 32 | Line 2 and line 3: the same Book A row | | line 2 loaded, line 3 skipped | `0439023483` | `9780439023481` | line 3: SKIP_UNCHANGED |
| 33 | Line 2: Book A; line 3: Book A's ISBNs with title `Catching Fire` | | line 2 loaded, line 3 rejected | `0439023483` | `9780439023481` | line 3: REJ_ISBN_BELONGS_TO_DIFFERENT_BOOK |
| 34 | Line 2: Book A; line 3: Book A with `ratings_count` `4780654` | | line 2 loaded, line 3 updated | `0439023483` | `9780439023481` | stored `ratings_count` = `4780654` |
| 35 | The same file (rows 1, 8 and 22) run twice | | run 1: 2 loaded, 1 rejected; run 2: 2 skipped, 1 rejected | | | `ingestion-log.json` has 2 entries; the first is unchanged after run 2 |
| 36 | `195170342` | `195170342.0` | loaded | `0195170342` | `9780195170344` | R_PADDED_ZEROS; W_ISBN13_UNUSABLE (WRONG_LENGTH); W_ISBN13_CALCULATED |

These examples will be used to test whether the program follows my rules correctly.
