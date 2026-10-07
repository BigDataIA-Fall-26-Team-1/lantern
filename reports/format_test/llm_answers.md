# Part 6: LLM question test

- Model: Claude Opus 5.5 (setting shown in the chat tool: medium)
- Date: 2026-10-07
- Slice: FY2025 (`AAPL_10K_20250927`) PDF pages 31-34, files in this folder:
  `AAPL_10K_20250927_p31-34.jsonl`, `.md`, `.txt`
- Every chat was new, with no memory, using the same model and setting, and the same prompt word for word.
- **Run 1:** the slice file attached in each chat (the file name was visible to the model).
- **Run 2 (scored run):** the slice file's text pasted in each chat (no file name).
- Scoring rules were fixed before any answer was seen (see Scoring).

## Prompt

```
Answer using only the document above. If the document does not contain the information, say so instead of guessing.

1. What was Apple's net income in the latest fiscal year, and on which page of the filing is it reported?
2. What was "Other income/(expense), net" in fiscal 2023, and was it income or an expense?
3. Which Item of the 10-K contains the consolidated balance sheets, and what were total assets at the end of fiscal 2025?
```

---

## Run 2: text pasted (scored)

### JSONL

> 1. Net income, fiscal 2025 (year ended September 27, 2025): $112,010 million (about $112.0 billion). It's in the Consolidated Statements of Operations on PDF page 32, which is printed as page 29 of the 10-K ("Apple Inc. | 2025 Form 10-K | 29").
> 2. Other income/(expense), net, fiscal 2023: (565), so it was an expense of $565 million. The parentheses mark a negative value, and the parsed table stores it as -565,000,000.
> 3. The Consolidated Balance Sheets are in Item 8 (Financial Statements and Supplementary Data). Total assets as of September 27, 2025 were $359,241 million (about $359.2 billion). They're on PDF page 34, printed page 31.

### Markdown

> 1. Net income, fiscal 2025: $112,010 million (fiscal year ended September 27, 2025). It is reported in the Consolidated Statements of Operations, which the filing's index lists as page 29. In the source document's page markers (likely the PDF page), that statement is on p32. The same figure also appears at the top of the Consolidated Statements of Comprehensive Income (index page 30, marker p33).
> 2. Other income/(expense), net, fiscal 2023: (565), or $565 million. The parentheses mean it was a net expense. Note that the (321) figure in the same row is for fiscal 2025, which was also a net expense. Fiscal 2024 was a net income of 269.
> 3. Balance sheets and total assets: The Consolidated Balance Sheets are in Item 8, Financial Statements and Supplementary Data (index page 31, marker p34). Total assets as of September 27, 2025 were $359,241 million, down from $364,980 million a year earlier.

### TXT

> 1. Net income in the latest fiscal year: Apple's net income for fiscal 2025 (the year ended September 27, 2025) was $112,010 million. It appears in the Consolidated Statements of Operations, which the index puts on page 29. The same figure is repeated as the opening line of the Consolidated Statements of Comprehensive Income on page 30.
> One caveat on the page numbers: in this text the numbers appear as a separate column (29, 30, 31, 32, 33, 34, 49) after the list of statements. I matched them to the statements in order, which fits cleanly, since there are seven statements and seven page numbers.
> 2. Other income/(expense), net in fiscal 2023: It was (565), meaning $565 million. The parentheses show a negative value, so it was a net expense, not income.
> 3. Item containing the balance sheets, and fiscal 2025 total assets: The Consolidated Balance Sheets are in Item 8, Financial Statements and Supplementary Data, on page 31 per the index. Total assets as of September 27, 2025 were $359,241 million, down from $364,980 million a year earlier.

---

## Run 1: files attached

### JSONL

> Here are the answers, all taken from the uploaded excerpt (pages 31–34 of Apple's FY2025 10-K).
>
> 1. Net income, latest fiscal year
> Apple's net income for fiscal 2025 (year ended September 27, 2025) was $112,010 million. It appears in the Consolidated Statements of Operations.
> The page number depends on which numbering you use:
>
> * PDF page: 32, according to the record's `page` field.
> * Filing's own page: 29, according to the printed footer ("Apple Inc. | 2025 Form 10-K | 29"). The filing's index also lists the Statements of Operations on page 29.
>
> 2. Other income/(expense), net, fiscal 2023
> It was (565), meaning $565 million. The parentheses show it was an expense, not income. It is the same line on the Statements of Operations, for the year ended September 30, 2023.
>
> 3. Balance sheets and total assets
> The Consolidated Balance Sheets are in Item 8, "Financial Statements and Supplementary Data." Total assets as of September 27, 2025 were $359,241 million. That is on PDF page 34, which is printed page 31 of the filing.

### Markdown

> 1. Net income, fiscal 2025
> Apple's net income for the fiscal year ended September 27, 2025 was $112,010 million. It appears in the Consolidated Statements of Operations, which is tagged as page 32 of the filing in this excerpt (`p32`). The filing's own index lists that statement at printed page 29, so the difference is likely PDF page numbering versus the 10-K's printed page numbers. The same figure also appears as the first line of the Statements of Comprehensive Income on p33.
>
> 2. Other income/(expense), net, fiscal 2023
> It was (565), meaning $565 million. The parentheses mark it as a negative amount, so it was a net expense, not income.
>
> 3. Balance sheets and total assets
> The consolidated balance sheets are in Item 8, Financial Statements and Supplementary Data. Total assets as of September 27, 2025 were $359,241 million.

### TXT

> 1. Net income, fiscal 2025: $112,010 million (about $112.0 billion) for the year ended September 27, 2025. It appears in the Consolidated Statements of Operations, which the Item 8 index lists on page 29. The same figure also appears at the top of the Consolidated Statements of Comprehensive Income, on page 30.
> One caveat on the page number: the text extraction separated the index entries from their page numbers. I matched them by order. There are seven entries and seven numbers (29, 30, 31, 32, 33, 34, 49), and that order gives page 29 for Operations. The file name says "p31-34," which likely uses PDF page numbers rather than the filing's printed page numbers. The document doesn't confirm that, though.
>
> 2. Other income/(expense), net, fiscal 2023: $(565) million. The parentheses mean it was a net expense of $565 million, not income.
>
> 3. Balance sheets: They are in Item 8, Financial Statements and Supplementary Data. The index lists them on page 31. Total assets at the end of fiscal 2025 (September 27, 2025) were $359,241 million, down from $364,980 million a year earlier.

---

## Scoring

Rules (fixed before the test):

| Q | Correct | Partial | Wrong |
|---|---|---|---|
| 1 | $112,010 million **and** page 32 (from the page markers), or printed page 29 if the answer says it is the printed page number | right number, page missing, unsupported or wrong | wrong number |
| 2 | $(565) million **and** says it is an expense | right number, income/expense wrong or missing | wrong number |
| 3 | Item 8 **and** $359,241 million | one of the two | neither |

An honest "the document does not say" is scored Partial and noted, not Wrong.

### Run 2 (scored)

| Format | Q1 | Q2 | Q3 | Score | Notes |
|---|---|---|---|---|---|
| JSONL | Correct | Correct | Correct | 3 / 3 | PDF page from the `page` field; printed page from the footer record (the JSONL keeps footers). Q2 also quoted the normalized value -565,000,000 |
| Markdown | Correct | Correct | Correct | 3 / 3 | PDF page from the provenance comment (`p32`); printed page from the statement index |
| TXT | Partial | Correct | Correct | 2 / 3 + 1 partial | Page 29 found only by matching the index's detached page numbers to titles by order; not identified as the printed page; no PDF page available |

### Run 1 (files attached)

| Format | Q1 | Q2 | Q3 | Score | Notes |
|---|---|---|---|---|---|
| JSONL | Correct | Correct | Correct | 3 / 3 | PDF page from the `page` field (32); printed page (29) from the footer record |
| Markdown | Correct | Correct | Correct | 3 / 3 | PDF page from the provenance comment (`p32`); printed page (29) inferred from the index and hedged ("likely") |
| TXT | Correct (weak basis) | Correct | Correct | 3 / 3 | Printed page (29) found only by order-matching the index, flagged by the model as unconfirmed; it referred to the file name |

### Across runs

JSONL and Markdown gave correct, grounded page answers in both runs. The TXT's page answer depended on an order-based match from the index and changed between runs (meets the rule in Run 1, partial in Run 2).
