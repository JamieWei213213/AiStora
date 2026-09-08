# Engine benchmarks

`engine_benchmark.py` generates a synthetic finance-style CSV and times the
operations the agent's typed tools actually execute. Each timing includes CSV
parse time, so the numbers reflect a cold end-to-end request rather than a warm
in-memory operation.

```bash
python benchmarks/engine_benchmark.py --rows 200000 --runs 7
```

## Reference results

200,000 rows / 17.2 MB, 7 runs each, single core:

| Query | Median | p95 | Throughput |
|---|---|---|---|
| `groupby(category) + sum` | 0.883 s | 0.899 s | 226K rows/s |
| `filter(amount > 10k) + top_k` | 0.786 s | 0.804 s | 254K rows/s |
| `max_by(amount)`, streamed | 0.724 s | 0.727 s | 276K rows/s |
| `groupby(vendor, 400 keys) + avg` | 0.913 s | 0.928 s | 219K rows/s |

Worst-case p95 across all queries: **0.928 s**.

## Reading these numbers

Roughly 220–280K rows per second, dominated by CSV parsing rather than by the
aggregation itself. A typical accounting export is well under 200,000 rows, so
a single tool call is comfortably sub-second and a multi-step agent run stays
inside the 45-second budget.

The scaling limit is honest and worth stating: parsing is single-threaded pure
Python and the file is re-read per DataFrame construction. Within one agent run
base tables are cached, so a three-step analysis parses once rather than three
times. Beyond a few million rows the right answer is a columnar format or a
real database, not a faster parser.
