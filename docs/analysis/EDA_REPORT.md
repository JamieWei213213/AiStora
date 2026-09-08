# Exploratory data analysis report

AIStora's EDA report is a deterministic, read-only profile of the currently selected database. It is separate from **Auto analyze**: the report does not ask Gemini to choose calculations or write conclusions. Statistics are computed locally from the uploaded CSV files.

## What the report includes

- Database and table size overview
- Missing-value counts and rates for every included column, plus strong status/category-dependent missingness patterns
- Duplicate-row checks within the configured scope
- Semantic column roles so identifiers and date parts are not treated as business measures
- Numeric quartiles, mean, median, range, standard deviation, skewness, zero counts, and sample-qualified IQR tail flags
- Categorical cardinality and most common values
- Date/time coverage for recognizable date columns
- Strongest Pearson correlations between numeric columns
- Local join-key coverage for validated relationship hints
- Deterministic findings, privacy notes, execution trace, limits, and honest limitations

Sensitive category values are never returned by the report. Direct identifiers, row identifiers, free text, financial fields, and credentials are classified using the project's privacy policy. Relationship coverage uses local digests rather than retaining original keys in the report profile. The downloaded JSON contains summaries, not raw rows.

## How to test it

1. Start AIStora and open `http://localhost:5000/app`.
2. Log in, select a database, and open **AI chat**.
3. Choose **EDA report** in the top toolbar.
4. Confirm that the report shows the expected table and row counts.
5. Expand each table and review missingness, numeric summaries, categorical summaries, time ranges, and correlations.
6. Confirm sensitive columns show `[REDACTED]` instead of category values.
7. Choose **Download JSON** and inspect the saved report if you want the full structured output.

The status at the top is **complete** when every selected row was scanned. A **partial** status is not a hidden failure: it means a table, row, column, duplicate-check, or time budget was reached. The exact warning appears in that table and in the limitations section.

## Configuration

All budgets can be set in `.env`:

| Setting | Default | Purpose |
|---|---:|---|
| `EDA_MAX_TABLES` | 20 | Maximum tables in one report |
| `EDA_MAX_COLUMNS_PER_TABLE` | 60 | Maximum planned columns per table |
| `EDA_MAX_ROWS_PER_TABLE` | 100000 | Maximum streamed rows per table |
| `EDA_MAX_NUMERIC_COLUMNS` | 25 | Maximum numeric columns profiled per table |
| `EDA_MAX_CATEGORICAL_COLUMNS` | 25 | Maximum categorical columns profiled per table |
| `EDA_MAX_CORRELATION_COLUMNS` | 12 | Maximum numeric columns used for pairwise correlations |
| `EDA_MAX_TOP_CATEGORIES` | 5 | Maximum common values returned for ordinary columns |
| `EDA_MAX_DISTINCT_VALUES` | 25000 | Cardinality tracking limit per categorical column |
| `EDA_MAX_DUPLICATE_ROWS` | 100000 | Duplicate-check scope per table |
| `EDA_MAX_CATEGORY_TRACKED_VALUES` | 1000 | Maximum raw category labels counted per ordinary column |
| `EDA_MIN_OUTLIER_SAMPLE_SIZE` | 30 | Minimum numeric values required before IQR tails are assessed |
| `EDA_TIMEOUT_SECONDS` | 30 | Global report time budget |
| `EDA_RATE_LIMIT_PER_MINUTE` | 3 | Reports allowed per signed-in user each minute |

## Verification

Run the full suite on Windows:

```powershell
& .\.venv-win\Scripts\python.exe -m pytest -q
```

The synthetic fixture is `tests/fixtures/eda_synthetic.csv`. It deliberately contains missing values, a duplicate row, a large numeric tail value, dates, categorical values, and email addresses so the report and redaction behavior stay testable without private data. Additional generated in-memory cases cover late duplicates, small-sample safeguards, status-dependent missingness, and semantic privacy roles.

## Honest limitations

This is comprehensive descriptive EDA for structured CSV data, not causal inference, forecasting, anomaly confirmation, or automated business advice. IQR tail flags require human review and are suppressed for very small samples, Pearson correlation only measures linear association, and join coverage only validates detected key overlap within the scanned scope. Semantic roles and sensitivity are inferred from column names and declared types; the report does not perform content-level DLP. Very large datasets are intentionally bounded to protect memory and response time, and a bounded duplicate scope always makes the report status partial.
