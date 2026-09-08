"""Reproducible latency benchmark for the custom AIStora DataFrame engine.

Generates a synthetic finance-style CSV and times the core analytics
operations the agent's typed tools execute (groupby+aggregate, filter+top-K,
streaming max_by). Reports median and p95 wall-clock latency per query,
including CSV parse time, so the numbers reflect a cold end-to-end request.

Usage:
    python benchmarks/engine_benchmark.py --rows 250000 --runs 12
"""

import argparse
import csv
import os
import random
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.dataframe import DataFrame  # noqa: E402

CATEGORIES = [
    "Software", "Payroll", "Rent", "Travel", "Marketing",
    "Utilities", "Insurance", "Legal", "Supplies", "Meals",
]


def generate_csv(path, rows, seed=7):
    random.seed(seed)
    vendors = [f"Vendor_{i:03d}" for i in range(400)]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["txn_id", "date", "vendor", "category", "account", "amount", "memo"]
        )
        for i in range(rows):
            writer.writerow([
                i,
                f"2025-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}",
                random.choice(vendors),
                random.choice(CATEGORIES),
                f"ACCT-{random.randint(1000, 1099)}",
                round(random.uniform(-5000, 25000), 2),
                'invoice payment, ref "Q%d"' % random.randint(1, 4),
            ])
    return os.path.getsize(path)


def measure(fn, runs):
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    samples.sort()
    p95_index = int(0.95 * (len(samples) - 1))
    return statistics.median(samples), samples[p95_index]


def build_queries(path):
    def spend_by_category():
        frame = DataFrame(path)
        return frame.aggregate(frame.groupby("category"), {"amount": "sum"})

    def large_transactions():
        frame = DataFrame(path)
        return frame.filter(lambda r: float(r["amount"]) > 10000).top_k_by("amount", 10)

    def largest_transaction():
        return DataFrame(path).max_by("amount")

    def average_by_vendor():
        frame = DataFrame(path)
        return frame.aggregate(frame.groupby("vendor"), {"amount": "avg"})

    return [
        ("groupby(category) + sum", spend_by_category),
        ("filter(amount > 10k) + top_k", large_transactions),
        ("max_by(amount), streamed", largest_transaction),
        ("groupby(vendor, 400 keys) + avg", average_by_vendor),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=250000)
    parser.add_argument("--runs", type=int, default=12)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as workdir:
        path = os.path.join(workdir, "transactions.csv")
        size = generate_csv(path, args.rows)
        print(f"dataset: {args.rows:,} rows, {size / 1e6:.1f} MB, {args.runs} runs each\n")
        print(f"{'query':34s} {'median':>9s} {'p95':>9s} {'throughput':>14s}")
        print("-" * 70)

        worst_p95 = 0.0
        for name, query in build_queries(path):
            median, p95 = measure(query, args.runs)
            worst_p95 = max(worst_p95, p95)
            print(
                f"{name:34s} {median:8.3f}s {p95:8.3f}s "
                f"{args.rows / median / 1000:10.0f}K rows/s"
            )

        print("-" * 70)
        print(f"worst-case p95 across all queries: {worst_p95:.3f}s")


if __name__ == "__main__":
    main()
