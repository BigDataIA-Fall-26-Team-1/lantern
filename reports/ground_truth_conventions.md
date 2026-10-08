# Ground truth transcription conventions (Part 9)

Everyone transcribing follows these rules so the files are consistent.

## The one rule
Transcribe from the PAGE IMAGE (the rendered PDF page), never by editing parser output
(pdfplumber, Docling, the export) and never by copying text out of a PDF viewer: that
is the same text layer pdfplumber reads, and it would make the parser look perfect.
For long prose pages, starting from the original HTML filing in data/raw/ is allowed,
but every line must then be checked against the page image. Record the method used in
data/ground_truth/pages.csv.

## Which pages: data/ground_truth/pages.csv
    stem,page,stratum,transcriber,method
    AAPL_10K_20250927,32,statement,pradyumna,typed
stratum is one of: prose, statement, notes, multicolumn, scanned, cover.
method is "typed" or "html-assisted".

## Page text: data/ground_truth/text/{stem}_p{NNNN}.txt
- UTF-8, one file per page (page number with 4 digits: p0032).
- Everything printed on the page, in the order a person reads it, including headings,
  footnotes and the page footer ("Apple Inc. | 2025 Form 10-K | 29").
- Two columns: the whole left column, then the whole right column.
- Line breaks do not matter (scoring compares words), but keep one paragraph per line
  for readability.
- Keep punctuation exactly as printed: $ % , . ( ) and footnote markers.
- Numbers exactly as printed: 416,161 and (321), never 416161 or -321.
- Table rows: one line per row, cells left to right separated by single spaces,
  including $ signs and dashes as printed.
- Footnote markers attached to the word as printed: "Level 2(1)".
- Symbols such as (R) and TM: type the symbol if you can, otherwise (R) / (TM).
- Leave out checkbox symbols and images (the logo).

## Statement tables: data/ground_truth/tables/{stem}_p{NNNN}_{statement}.{keyer}.csv
- Columns: row_label,column,value   (one row per number)
- row_label: exactly as printed, no section prefix. A label that wraps onto two lines
  is joined with one space.
- Keep rows in the order they appear on the page (top to bottom, then left to right).
  A label printed twice, such as "Products" under Net sales and under Cost of sales,
  is simply written twice, in page order.
- column: the year from the column header (2025, 2024, 2023).
- value: exactly as printed: "307,003", "(321)", "7.46", or "-" for a dash.
- Any field containing a comma MUST be in double quotes:
    "Other income/(expense), net",2025,"(321)"
  Easiest: type it in a spreadsheet and save as CSV, which adds the quotes for you.
- Header rows and label-only rows (such as "Net sales:") get no line of their own.
- statement names: income, balance (matching Part 2's file names).
- Two people key each table without looking at each other's file
  (...keyer1.csv and ...keyer2.csv); differences are checked on the page image and
  merged into the final ...csv.

## Scoring normalization (applied by src/evaluate.py, not by you)
Unicode NFKC, curly quotes to straight, all dash variants to "-", "$" separated from
the number, whitespace collapsed, lowercase. Punctuation is KEPT, because stripping it
would hide sign errors such as (321).