# Part 11: XBRL validation

## 1. What was checked

Every number in the extracted statement tables was compared with the filing's own inline
XBRL facts, for both filings and both table paths:

- **Traditional:** Part 2's normalized tables (`data/tables/*.norm.csv`), already scaled.
- **Docling:** Part 4's table CSVs, scaled with Part 2's normalizer and the page caption.

Statements: income, comprehensive income, balance sheet, cash flows. The statement of
shareholders' equity is out of scope: it is a grid of equity components tagged with
dimensions, not a label-by-year table.

Facts were loaded with Arelle from the iXBRL documents (`aapl-20240928.htm`: 957 numeric
facts; `aapl-20250927.htm`: 962). Fiscal year ends were derived from the one-year
durations (2025-09-27, 2024-09-28, 2023-09-30, 2022-09-24). Arelle reports a period
ending September 27 as midnight on September 28, so one day is subtracted from every end
date.

Code: `src/xbrl.py`. Mapping: `config/label_map.yaml`. Outputs: `data/xbrl/facts.csv`,
`comparison.csv`, `summary.json`. Notebook: `notebooks/xbrl_validation.ipynb`.

## 2. Method

**Label to concept**, in order, with the method recorded for every line:
1. **Curated** (`config/label_map.yaml`, 27 entries): dimensional lines (Products and
   Services are slices of a total), labels printed twice, and the cases diagnosed below.
2. **Label linkbase:** the filing's own labels, all roles.
3. **Fuzzy:** close spelling match (`xbrl.fuzzy_cutoff: 0.88`).

Ambiguity is resolved by **period type**: a balance sheet line is a balance at a date
(instant concept); a line in a flow statement is a change over the year (duration
concept). Instants inside a flow statement (beginning and ending cash) use the concept's
own period type, with beginning balances matched at the prior year end.

**Comparison:** tolerance from the fact's `decimals` (`-6` gives plus or minus 0.5 million).
Status: `match`; `match_negated` (same amount, printed with a negated label, for example
an outflow in parentheses while XBRL stores a positive amount); `sign`; `scale_xN`;
`mismatch`; `xbrl_missing`; `unmapped`.

## 3. Results

| Statement | Traditional | Docling |
|---|---|---|
| Income | 114 / 114 | 114 / 114 |
| Comprehensive income | 60 / 60 | 60 / 60 |
| Balance sheet | 108 / 108 | 108 / 108 |
| Cash flows | 174 / 174 | 168 / 168 |
| **All** | **456 / 456 (100%)** | **450 / 450 (100%)** |

Of these, 84 per path are `match_negated` (72 in cash flows, 12 in comprehensive income).
No number on either path is a mismatch, a sign error or a scale error.

## 4. How we got there: two runs

**Run 1, automatic mapping only (linkbase and fuzzy, minimal curated map):**

| | Traditional | Docling |
|---|---|---|
| match | 327 | 327 |
| mismatch | 0 | 0 |
| scale error | 0 | 0 |
| sign | 3 | 3 |
| xbrl_missing | 12 | 6 |
| unmapped | 114 | 114 |
| **Match rate** | **72%** | **73%** |

Every number that could be mapped was already correct. The gap was entirely in mapping
and lookup, not extraction.

**Diagnosis of every non-match in run 1:**

| Cause | Lines affected | Example | Fix |
|---|---|---|---|
| Same label on a balance and a change concept | balance sheet and cash flows | "Accounts payable": `AccountsPayableCurrent` vs `IncreaseDecreaseInAccountsPayable` | Disambiguate by period type |
| Several concepts share a generic label | cash flows, balance sheet | "Other" (3 lines in cash flows), "Other current liabilities", "Deferred revenue" | Curated entries |
| **Extension concept changed between filings** | comprehensive income | Derivative lines tagged `aapl:...` in FY2024, `us-gaap:...CashFlowHedge...` in FY2025 | Curated entries list both; the one with a fact is used |
| Printed label differs from every filing label | income | "Total net sales", "Total cost of sales" | Curated entries |
| Instant concept inside a flow statement | cash flows | Beginning and ending cash looked up as one-year durations | Use the concept's period type; prior year end for beginning balances |
| Our negation logic | income | R&D flagged `sign` although values were equal: a negated label for the same concept exists elsewhere | Compare the plain value first; accept the opposite sign only for negated presentations |

**Run 2:** after these fixes, 100% on both paths.

## 5. Traditional vs Docling

Both paths extracted every number they found correctly. The difference is coverage:
**Docling's cash flow tables did not contain the first row**, "Cash, cash equivalents,
and restricted cash and cash equivalents, beginning balances", in either filing (6
numbers). Docling's table structure model merged that row into the column header: the
header cells of `AAPL_10K_20250927_pdf_p0036_t00.csv` read
`Years ended.September 27, 2025 $ 29,943`, the year label and the row's first value in
one cell, so the row's numbers never became data cells. The traditional path extracted
the row in both filings.

## 6. Limitations

- The curated map was built by diagnosing these two filings; on a new company or a new
  year, unmapped lines would need the same diagnosis (the automatic tiers carried 73% of
  numbers on their own here).
- The statement of shareholders' equity is not validated (dimensional grid).
- Only the primary statements are checked; note tables are not.v