# Part 6: Storage format decision

## Decision

| Role | Format | File |
|---|---|---|
| **Source of truth** | JSONL | `data/export/{stem}.jsonl` |
| **Feeds Case Study 2** (retrieval and question answering) | Markdown, generated from the same records by the `export` stage and never edited by hand | `data/export/{stem}.md` |
| Baseline only | TXT | `data/export/{stem}.txt` |

All three are written by `src/export.py` from the same validated records, so they never disagree about content. They differ only in what they keep.

## Evidence 1: size and token cost

Measured by `python -m src.format_stats`, written to `reports/format_stats.csv`. Tokens are approximated as characters / 4, as the brief allows.

| Scope | Format | Bytes | Characters | Approx. tokens | vs TXT |
|---|---|---|---|---|---|
| FY2024 full filing | JSONL | 811,191 | 809,410 | 202,352 | 4.0× |
| | Markdown | 248,304 | 246,677 | 61,669 | 1.2× |
| | TXT | 204,869 | 203,242 | 50,810 | 1.0× |
| FY2025 full filing | JSONL | 795,499 | 793,536 | 198,384 | 3.9× |
| | Markdown | 249,975 | 248,250 | 62,062 | 1.2× |
| | TXT | 207,509 | 205,784 | 51,446 | 1.0× |
| FY2025 pages 31–34 (test slice) | JSONL | 30,565 | 30,539 | 7,635 | 6.4× |
| | Markdown | 6,449 | 6,439 | 1,610 | 1.4× |
| | TXT | 4,771 | 4,761 | 1,190 | 1.0× |

The FY2025 full-filing numbers were re-measured after Part 7's managed fallback replaced two below-threshold tables (pages 22 and 47) with Textract's. The slice (pages 31–34) and every ratio are unchanged.

- JSONL costs about **4×** the tokens of plain text for a whole filing (about 200,000 tokens), and **6.4×** on statement pages, where every table is stored twice (raw and normalized) along with row kinds, column names and about 25 metadata fields per record.
- Markdown costs only **20–40% more** than plain text, while keeping the Item headings, the tables as tables, and a page and block reference before every block. On the slice it is **21%** of the JSONL's tokens (1,610 vs 7,635).

## Evidence 2: the same three questions, asked of each format

Method: the same 4-page slice (FY2025 PDF pages 31–34: Item 8 heading, statement index, income statement, comprehensive income, balance sheet) in each format, cut by `src/format_stats.py` into `reports/format_test/`. One new chat per format, with no memory, using the same model (Claude Opus 5.5, medium setting) and the same prompt, which told the model to use only the document and to say when it does not contain the answer. Scoring rules were fixed before any answer was seen. Two runs: Run 1 with the files attached, Run 2 (scored) with the text pasted. All answers are recorded word for word in `reports/format_test/llm_answers.md`.

| Question | Tests | Correct answer |
|---|---|---|
| Q1. Net income in the latest fiscal year, and on which page? | provenance | $112,010 million; PDF page 32 (printed page 29) |
| Q2. "Other income/(expense), net" in fiscal 2023: income or expense? | sign, table structure | $(565) million, an expense |
| Q3. Which Item has the balance sheets; total assets at the end of fiscal 2025? | section, number | Item 8; $359,241 million |

| Format | Run 2 (scored) | Run 1 | Where the Q1 page came from |
|---|---|---|---|
| JSONL | **3 / 3** | 3 / 3 | the `page` field (PDF page 32), plus the footer record (printed page 29) |
| Markdown | **3 / 3** | 3 / 3 | the provenance comment `p32`, plus the printed page from the statement index |
| TXT | **2 / 3 + 1 partial** | 3 / 3 (weak basis) | only by matching the index's detached page numbers to the statement titles by their order; the model flagged this itself |

All three formats got every number and sign right: the slice is clean, and the statements read correctly even as flat text. The difference is in **how well each answer is grounded**. JSONL and Markdown gave the page from explicit markers in both runs. The TXT has no page markers, so its page answer rested on an inference and changed between runs.

## Why JSONL is the source of truth

1. **It is the only format that keeps everything, as fields.** Each record has `doc_id`, `page`, `bbox` (pt, top-left), `block_id`, `section`, the extractor and its version, the OCR flags, and the source file's `sha256`. Table records keep both the raw cells and the normalized numbers with their scale. Every record is validated against `src/schema.py` when it is written (Part 5).
2. **It gave the best-grounded answers.** In both runs, the page came from the `page` field, and the printed page from the footer record. The JSONL is the only format that keeps footers. In Run 2 the model also used the stored normalized value (-565,000,000) directly.
3. **Code needs it, not an LLM.** Part 11 compares the normalized table numbers with XBRL, and any citation in Case Study 2 has to be resolved back to a page and bbox through `block_id`. Both need the fields, not text.
4. **Its cost rules it out as the text an LLM reads.** About 200,000 tokens per filing, and 6.4× plain text on statement pages, mostly fields a reader does not need.

## Why Markdown feeds Case Study 2

1. **It answered as accurately as the JSONL** (3/3 in both runs) **at about a fifth of the tokens** on the slice, and 1.2× plain text for a whole filing.
2. **It keeps the structure retrieval needs.** `## Item N.` headings mark the sections, so text can be split by section, and tables stay as tables, so rows and columns stay aligned.
3. **Every block stays citable.** The `<!-- doc_id pN block_id -->` comment before each block gives the page directly, and the `block_id` leads to the JSONL record with the bbox (checked in Part 5: no Markdown text without a comment above it).

Trade-offs, accepted: the bbox is one lookup away rather than inline, the Markdown shows table cells as printed (normalized numbers are in the JSONL), and page footers are left out, so the printed page number is only available from the JSONL.

## Why TXT is only a baseline

It is the cheapest format, but it loses the page, the section structure and the provenance. Its page answer depended on guessing which number belonged to which title, and was not stable between runs. It is kept to show what the structure in the other two formats is worth.

## Limitations

- One model and setting, two runs, and one 4-page slice of statement pages. The questions are lookups whose answers appear in all three formats. Harder cases (a table split across pages, dense notes, questions spanning several sections) were not tested, and plain text would likely do worse on them.
- Token counts are characters / 4, not a real tokenizer.
- Run 1 attached the files, so the model could see the file name. Run 2 pasted the text, and is the scored run.
- The formats reflect choices made in Part 5. For example, page footers are left out of the Markdown and TXT, which is why only the JSONL could give the printed page.

## Reproduce

```bash
python -m src.format_stats     # writes reports/format_stats.csv and the slice files in reports/format_test/
```

Then repeat the question test as described in `reports/format_test/llm_answers.md`.
