"""Insights: an automatic first look at a database, with charts.

Deterministic and local. No model is involved: column *names and types* decide
what is worth looking at (revenue by channel, not the sum of a price list),
one streaming pass per table computes the numbers, and the browser draws the
charts. That keeps the product's privacy boundary intact (nothing leaves the
server) and makes the button free to click.

The output is a list of findings. Each has a title, a one-sentence takeaway
written from the numbers, a small chart spec (labels + values) and a
follow-up question the person can send to the chat agent.
"""
import math
import time
from collections import defaultdict
from dataclasses import dataclass

from services.eda_service import _parse_datetime, infer_column_role
from services.schema_agent import rank_columns


@dataclass(frozen=True)
class InsightLimits:
    max_rows_per_table: int = 100000
    max_distinct_groups: int = 2000
    max_dimension_lookup_rows: int = 50000
    top_categories: int = 8
    top_entities: int = 10
    histogram_bins: int = 10
    timeout_seconds: int = 20


def limits_from_config(config):
    return InsightLimits(
        max_rows_per_table=int(getattr(config, "EDA_MAX_ROWS_PER_TABLE", 100000)),
        max_dimension_lookup_rows=int(getattr(config, "AGENT_MAX_MATERIALIZED_ROWS", 50000)),
        timeout_seconds=int(getattr(config, "EDA_TIMEOUT_SECONDS", 30)),
    )


# ----------------------------------------------------------------- helpers

def _booleanish(column):
    name = str(column).casefold()
    return name.startswith(("is_", "has_", "was_")) or name.endswith(("_flag", "?", "_yn"))


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt(value):
    """Compact number formatting for takeaway sentences."""
    if value is None:
        return "—"
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"{value / 1_000_000:,.2f}M"
    if magnitude >= 10_000:
        return f"{value:,.0f}"
    if magnitude >= 100:
        return f"{value:,.1f}"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _pct(part, whole):
    if not whole:
        return 0.0
    return round(100.0 * part / whole, 1)


def _label(column):
    return str(column).replace("_", " ")


def _month_key(parsed):
    return f"{parsed.year:04d}-{parsed.month:02d}"


class _Tally:
    """One table's single-pass accumulators."""

    def __init__(self, measure, dimensions, temporal, entities, columns, limits):
        self.measure = measure
        self.dimensions = dimensions
        self.temporal = temporal
        self.entities = entities
        self.columns = columns
        self.limits = limits
        self.rows = 0
        self.measure_sum = 0.0
        self.measure_count = 0
        self.measure_values = []
        self.by_dimension = {d: defaultdict(lambda: [0.0, 0]) for d in dimensions}
        self.overflow = set()
        self.by_month = defaultdict(lambda: [0.0, 0])
        self.month_unparsed = 0
        self.by_entity = {e: defaultdict(lambda: [0.0, 0]) for e in entities}
        self.missing = defaultdict(int)
        self.foreign_keys = {}  # column -> {key: [sum, count]}

    def observe(self, row):
        self.rows += 1
        value = _num(row.get(self.measure)) if self.measure else None
        if self.measure:
            if value is None:
                self.missing[self.measure] += 1
            else:
                self.measure_sum += value
                self.measure_count += 1
                self.measure_values.append(value)
        weight = value if value is not None else 0.0
        for column in self.columns:
            cell = row.get(column)
            if column != self.measure and (cell is None or str(cell).strip() == ""):
                self.missing[column] += 1
        for group_map, names in ((self.by_dimension, self.dimensions), (self.by_entity, self.entities)):
            for column in names:
                if column in self.overflow:
                    continue
                key = row.get(column)
                if key is None or str(key).strip() == "":
                    continue
                key = str(key).strip()
                bucket = group_map[column]
                if key not in bucket and len(bucket) >= self.limits.max_distinct_groups:
                    self.overflow.add(column)
                    continue
                bucket[key][0] += weight
                bucket[key][1] += 1
        if self.temporal:
            parsed = _parse_datetime(row.get(self.temporal))
            if parsed is None:
                if row.get(self.temporal) not in (None, ""):
                    self.month_unparsed += 1
            else:
                bucket = self.by_month[_month_key(parsed)]
                bucket[0] += weight
                bucket[1] += 1
        for column, lookup in self.foreign_keys.items():
            key = row.get(column)
            if key is None:
                continue
            key = str(key).strip()
            if key not in lookup and len(lookup) >= self.limits.max_distinct_groups * 25:
                continue
            bucket = lookup[key]
            bucket[0] += weight
            bucket[1] += 1


# ------------------------------------------------------------ the findings

def _breakdown_finding(table, measure, dimension, buckets, overflow, limits, average_only):
    if not buckets or dimension in overflow:
        return None
    use_avg = measure is not None and average_only
    if measure and not use_avg:
        items = sorted(((k, v[0], v[1]) for k, v in buckets.items()), key=lambda t: -t[1])
        total = sum(v[0] for v in buckets.values())
        metric = f"total {_label(measure)}"
        values = [round(v, 2) for _, v, _ in items]
    elif measure and use_avg:
        items = sorted(((k, v[0] / v[1] if v[1] else 0.0, v[1]) for k, v in buckets.items()), key=lambda t: -t[1])
        total = None
        metric = f"average {_label(measure)}"
        values = [round(v, 2) for _, v, _ in items]
    else:
        items = sorted(((k, v[1], v[1]) for k, v in buckets.items()), key=lambda t: -t[1])
        total = sum(v[1] for v in buckets.values())
        metric = "rows"
        values = [v for _, v, _ in items]
    if len(items) < 2:
        return None
    top = items[: limits.top_categories]
    labels = [k for k, _, _ in top]
    chart_values = values[: limits.top_categories]
    if len(items) > limits.top_categories:
        rest = sum(values[limits.top_categories:]) if total is not None else None
        if rest is not None:
            labels.append(f"Other ({len(items) - limits.top_categories})")
            chart_values.append(round(rest, 2))
    leader, leader_value, leader_rows = top[0]
    if total:
        share = _pct(leader_value, total)
        runner = top[1]
        close = runner[1] > 0 and leader_value / runner[1] < 1.1
        if share >= 50:
            takeaway = f"{leader} accounts for {share}% of {metric} — more than everything else combined."
        elif close:
            takeaway = f"{leader} and {runner[0]} are neck and neck at {share}% and {_pct(runner[1], total)}% of {metric}."
        else:
            takeaway = f"{leader} leads with {share}% of {metric}; {runner[0]} is next at {_pct(runner[1], total)}%."
    else:
        takeaway = f"{leader} has the highest {metric} at {_fmt(leader_value)} (from {leader_rows:,} rows); {top[-1][0]} the lowest shown at {_fmt(top[-1][1])}."
    question = (
        f"Calculate the {metric.split(' ')[0]} {measure} in {table}, grouped by {dimension}."
        if measure else f"Count the rows in {table}, grouped by {dimension}."
    )
    return {
        "id": f"{table}.{dimension}.breakdown",
        "table": table,
        "kind": "bar",
        "title": f"{metric.capitalize()} by {_label(dimension)}",
        "takeaway": takeaway,
        "chart": {"labels": labels, "values": chart_values, "metric": metric},
        "rows": [
            {dimension: k, metric: round(v, 2), "rows": r, **({"share_pct": _pct(v, total)} if total else {})}
            for k, v, r in top
        ],
        "follow_up": question,
        "distinct": len(items),
    }


def _trend_finding(table, measure, temporal, by_month, unparsed, average_only):
    if len(by_month) < 2:
        return None
    months = sorted(by_month)
    if measure and not average_only:
        series = [round(by_month[m][0], 2) for m in months]
        metric = f"total {_label(measure)}"
        question = f"Show the total {measure} in {table} by month of {temporal}."
    elif measure and average_only:
        series = [round(by_month[m][0] / by_month[m][1], 2) if by_month[m][1] else 0 for m in months]
        metric = f"average {_label(measure)}"
        question = f"Show the average {measure} in {table} by month of {temporal}."
    else:
        series = [by_month[m][1] for m in months]
        metric = "rows"
        question = f"Count the rows in {table} by month of {temporal}."
    peak_index = max(range(len(series)), key=lambda i: series[i])
    low_index = min(range(len(series)), key=lambda i: series[i])
    parts = [f"Best month {months[peak_index]} ({_fmt(series[peak_index])})"]
    if len(series) >= 3:
        # Compare the last complete-looking month with the one before; the very
        # last month is often partial, so say so.
        last, prev = series[-1], series[-2]
        if prev:
            change = _pct(last - prev, prev)
            direction = "up" if change > 0 else "down"
            parts.append(f"latest month {months[-1]} is {direction} {abs(change)}% vs {months[-2]} (the latest month may be incomplete)")
        half = len(series) // 2
        first_avg = sum(series[:half]) / half
        second_avg = sum(series[half:]) / (len(series) - half)
        if first_avg:
            drift = _pct(second_avg - first_avg, first_avg)
            trend_word = "growing" if drift > 10 else "declining" if drift < -10 else "roughly flat"
            parts.append(f"overall {trend_word} ({'+' if drift > 0 else ''}{drift}% second half vs first)")
    takeaway = "; ".join(parts) + "."
    if unparsed:
        takeaway += f" {unparsed:,} rows had unreadable dates and were left out."
    return {
        "id": f"{table}.{temporal}.trend",
        "table": table,
        "kind": "line",
        "title": f"{metric.capitalize()} per month ({_label(temporal)})",
        "takeaway": takeaway,
        "chart": {"labels": months, "values": series, "metric": metric},
        "rows": [{"month": m, metric: v, "rows": by_month[m][1]} for m, v in zip(months, series)],
        "follow_up": question,
        "low": {"month": months[low_index], "value": series[low_index]},
    }


def _ranking_finding(table, measure, entity, buckets, overflow, limits, average_only):
    if not buckets or entity in overflow or len(buckets) < 3:
        return None
    if (not measure or average_only) and max(v[1] for v in buckets.values()) < 3:
        return None  # one row per entity: ranking by row count says nothing
    if measure and not average_only:
        items = sorted(((k, v[0], v[1]) for k, v in buckets.items()), key=lambda t: -t[1])
        total = sum(v[0] for v in buckets.values())
        metric = f"total {_label(measure)}"
    else:
        items = sorted(((k, v[1], v[1]) for k, v in buckets.items()), key=lambda t: -t[1])
        total = sum(v[1] for v in buckets.values())
        metric = "rows"
    top = items[: limits.top_entities]
    top_share = _pct(sum(v for _, v, _ in top), total) if total else 0
    fifth = max(1, math.ceil(len(items) * 0.2))
    pareto = _pct(sum(v for _, v, _ in items[:fifth]), total) if total else 0
    takeaway = (
        f"Top {len(top)} of {len(items):,} {_label(entity)} values make {top_share}% of {metric}; "
        f"the top 20% make {pareto}%" + (" — revenue is concentrated." if pareto >= 60 else " — fairly spread out.")
    )
    return {
        "id": f"{table}.{entity}.ranking",
        "table": table,
        "kind": "bar",
        "title": f"Top {len(top)} {_label(entity)} by {metric}",
        "takeaway": takeaway,
        "chart": {"labels": [k for k, _, _ in top], "values": [round(v, 2) for _, v, _ in top], "metric": metric},
        "rows": [{entity: k, metric: round(v, 2), "rows": r} for k, v, r in top],
        "follow_up": (
            f"Show the top {len(top)} {entity} in {table} by total {measure}." if measure and not average_only
            else f"Count the rows in {table}, grouped by {entity}, and show the top {len(top)}."
        ),
        "concentration_top20_pct": pareto,
    }


def _distribution_finding(table, measure, values, limits):
    if len(values) < 20:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mean = sum(ordered) / n
    median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    low, high = ordered[0], ordered[-1]
    if high == low:
        return None
    bins = limits.histogram_bins
    width = (high - low) / bins
    counts = [0] * bins
    for value in ordered:
        index = min(bins - 1, int((value - low) / width))
        counts[index] += 1
    labels = [f"{_fmt(low + i * width)}–{_fmt(low + (i + 1) * width)}" for i in range(bins)]
    over_double = sum(1 for v in ordered if v > 2 * mean)
    skew = "right-skewed: a few large values pull the mean above the median" if mean > median * 1.15 else (
        "left-skewed" if median > mean * 1.15 else "fairly symmetric")
    takeaway = (
        f"Typical {_label(measure)} is {_fmt(median)} (median) vs a mean of {_fmt(mean)}; "
        f"{skew}. {over_double:,} rows ({_pct(over_double, n)}%) exceed twice the mean."
    )
    return {
        "id": f"{table}.{measure}.distribution",
        "table": table,
        "kind": "histogram",
        "title": f"Distribution of {_label(measure)}",
        "takeaway": takeaway,
        "chart": {"labels": labels, "values": counts, "metric": "rows"},
        "rows": [{"range": l, "rows": c} for l, c in zip(labels, counts)],
        "follow_up": f"Show the 10 rows in {table} with the highest {measure}.",
        "stats": {"mean": round(mean, 4), "median": round(median, 4), "min": low, "max": high, "count": n},
    }


def _quality_finding(table, missing, rows, columns):
    if not rows:
        return None
    flagged = sorted(((c, m) for c, m in missing.items() if m and _pct(m, rows) >= 5), key=lambda t: -t[1])
    if not flagged:
        return None
    labels = [c for c, _ in flagged[:8]]
    values = [_pct(m, rows) for _, m in flagged[:8]]
    worst = flagged[0]
    takeaway = (
        f"{_label(worst[0])} is missing in {_pct(worst[1], rows)}% of rows"
        + (f"; {len(flagged) - 1} more column{'s' if len(flagged) > 2 else ''} miss at least 5%." if len(flagged) > 1 else ".")
        + " Missing values are excluded from totals above."
    )
    return {
        "id": f"{table}.quality",
        "table": table,
        "kind": "bar",
        "title": "Columns with missing values",
        "takeaway": takeaway,
        "chart": {"labels": labels, "values": values, "metric": "% missing"},
        "rows": [{"column": c, "missing_rows": m, "missing_pct": _pct(m, rows)} for c, m in flagged],
        "follow_up": f"How many rows in {table} have an empty {worst[0]}?",
    }


# --------------------------------------------------------- cross-table joins

def _candidate_links(schema, relationships):
    """(fact_table, fact_column, dim_table, dim_column) pairs worth joining."""
    links = []
    seen = set()
    for rel in relationships or []:
        pair = (rel.get("from_table"), rel.get("from_column"), rel.get("to_table"), rel.get("to_column"))
        if all(pair) and pair[0] in schema and pair[2] in schema and pair not in seen:
            seen.add(pair)
            links.append(pair)
    # Same-named *_id columns in two tables where one side looks like the key table.
    tables = list(schema)
    for i, left in enumerate(tables):
        for right in tables[i + 1:]:
            common = set(schema[left].get("types", {})) & set(schema[right].get("types", {}))
            for column in common:
                if infer_column_role(column, "int") != "identifier":
                    continue
                stem = column.casefold().removesuffix("_id").removesuffix("id").rstrip("_")
                if stem and (stem in right.casefold() or right.casefold().rstrip("s") == stem.rstrip("s")):
                    pair = (left, column, right, column)
                elif stem and (stem in left.casefold() or left.casefold().rstrip("s") == stem.rstrip("s")):
                    pair = (right, column, left, column)
                else:
                    continue
                if pair not in seen:
                    seen.add(pair)
                    links.append(pair)
    return links


def _cross_table_findings(schema, loader, links, tallies, limits, deadline):
    findings = []
    for fact, fact_column, dim, dim_column in links:
        if time.monotonic() > deadline:
            break
        fact_tally = tallies.get(fact)
        if not fact_tally or not fact_tally.measure:
            continue
        dim_rank = rank_columns(schema[dim].get("types", {}))
        dim_dims = [d for d in dim_rank["dimensions"] if d != dim_column and not _booleanish(d)][:3]
        if not dim_dims:
            continue
        # Lookup: key -> dimension values, bounded.
        lookup = {}
        try:
            dim_df = loader(dim)
        except Exception:
            continue
        if dim_df is None:
            continue
        for row in dim_df._get_data():
            key = row.get(dim_column)
            if key is None:
                continue
            lookup[str(key).strip()] = {d: row.get(d) for d in dim_dims}
            if len(lookup) >= limits.max_dimension_lookup_rows:
                break
        if not lookup:
            continue
        # Second pass over the fact table, aggregating the measure by each dimension.
        try:
            fact_df = loader(fact)
        except Exception:
            continue
        if fact_df is None:
            continue
        measure = fact_tally.measure
        agg = {d: defaultdict(lambda: [0.0, 0]) for d in dim_dims}
        unmatched = 0
        scanned = 0
        for row in fact_df._get_data():
            scanned += 1
            if scanned > limits.max_rows_per_table:
                break
            key = row.get(fact_column)
            target = lookup.get(str(key).strip()) if key is not None else None
            if target is None:
                unmatched += 1
                continue
            value = _num(row.get(measure))
            if value is None:
                continue
            for d in dim_dims:
                label = target.get(d)
                if label is None or str(label).strip() == "":
                    continue
                bucket = agg[d][str(label).strip()]
                bucket[0] += value
                bucket[1] += 1
        for d in dim_dims:
            finding = _breakdown_finding(fact, measure, f"{dim}.{d}", agg[d], set(), limits, False)
            if not finding:
                continue
            finding["id"] = f"{fact}.{fact_column}->{dim}.{d}"
            finding["title"] = f"Total {_label(measure)} by {_label(d)} (via {_label(dim)})"
            finding["kind"] = "bar"
            finding["cross_table"] = {"fact": fact, "dimension_table": dim, "key": fact_column, "unmatched_rows": unmatched}
            finding["follow_up"] = (
                f"Join {fact} and {dim} using {fact}.{fact_column} and {dim}.{dim_column}, "
                f"then calculate the total {measure} grouped by {d}."
            )
            if unmatched:
                finding["takeaway"] += f" {unmatched:,} {fact} rows had no matching {dim} row."
            findings.append(finding)
    return findings


# ----------------------------------------------------------------- entry

def generate_insights(schema, dataframe_loader, relationships=None, limits=None):
    limits = limits or InsightLimits()
    started = time.monotonic()
    deadline = started + limits.timeout_seconds
    tables_out = []
    findings = []
    tallies = {}
    skipped = []

    for table, details in schema.items():
        if time.monotonic() > deadline:
            skipped.append({"table": table, "reason": "time limit reached"})
            continue
        types = details.get("types", {}) or {}
        ranked = rank_columns(types)
        roles = {c: infer_column_role(c, t) for c, t in types.items()}
        measures = [m for m in ranked["measures"] if roles.get(m) == "numeric_measure"]
        measure = measures[0] if measures else None
        average_only = measure in ranked["average_only"] if measure else False
        dimensions = [d for d in ranked["dimensions"] if roles.get(d) == "categorical"]
        booleanish = [d for d in dimensions if _booleanish(d)]
        dimensions = ([d for d in dimensions if d not in booleanish] or dimensions)[:3]
        temporal = next((c for c in ranked["temporal"] if roles.get(c) == "datetime"), None)
        entities = [e for e in ranked["entities"] if e not in dimensions[:1]][:1]
        try:
            df = dataframe_loader(table)
        except Exception as exc:  # unreadable file: report, keep going
            skipped.append({"table": table, "reason": f"could not read table: {str(exc)[:120]}"})
            continue
        if df is None:
            skipped.append({"table": table, "reason": "table not found"})
            continue
        tally = _Tally(measure, dimensions, temporal, entities, list(types), limits)
        truncated = False
        for row in df._get_data():
            tally.observe(row)
            if tally.rows >= limits.max_rows_per_table:
                truncated = True
                break
            if tally.rows % 5000 == 0 and time.monotonic() > deadline:
                truncated = True
                break
        tallies[table] = tally

        table_findings = []
        for dimension in dimensions:
            finding = _breakdown_finding(table, measure, dimension, tally.by_dimension[dimension], tally.overflow, limits, average_only)
            if finding:
                table_findings.append(finding)
        if temporal:
            # A per-unit or age-like measure has no meaningful monthly total; the
            # volume of rows per month (signups, orders) is the useful series.
            finding = _trend_finding(table, None if average_only else measure, temporal, tally.by_month, tally.month_unparsed, False)
            if finding:
                table_findings.append(finding)
        for entity in entities:
            finding = _ranking_finding(table, measure, entity, tally.by_entity[entity], tally.overflow, limits, average_only)
            if finding:
                table_findings.append(finding)
        if measure and not average_only:
            finding = _distribution_finding(table, measure, tally.measure_values, limits)
            if finding:
                table_findings.append(finding)
        finding = _quality_finding(table, tally.missing, tally.rows, list(types))
        if finding:
            table_findings.append(finding)

        kpis = [{"label": "Rows", "value": tally.rows, "format": "int"}]
        if measure and tally.measure_count:
            avg = tally.measure_sum / tally.measure_count
            if not average_only:
                kpis.append({"label": f"Total {_label(measure)}", "value": round(tally.measure_sum, 2), "format": "number"})
            kpis.append({"label": f"Average {_label(measure)}", "value": round(avg, 2), "format": "number"})
        if dimensions and dimensions[0] not in tally.overflow:
            kpis.append({"label": f"Distinct {_label(dimensions[0])}", "value": len(tally.by_dimension[dimensions[0]]), "format": "int"})
        if temporal and tally.by_month:
            months = sorted(tally.by_month)
            kpis.append({"label": "Date range", "value": f"{months[0]} to {months[-1]}", "format": "text"})

        tables_out.append({
            "table": table,
            "rows_scanned": tally.rows,
            "truncated": truncated,
            "measure": measure,
            "dimensions": dimensions,
            "temporal": temporal,
            "entity": entities[0] if entities else None,
            "kpis": kpis,
            "finding_count": len(table_findings),
        })
        findings.extend(table_findings)

    # The same numbers reached through two columns (city vs state when every
    # city sits in one state) are one insight, not two: keep the first.
    deduped, seen_series = [], set()
    for finding in findings:
        key = (finding["table"], finding["kind"], tuple(finding["chart"]["values"]))
        if finding["kind"] == "bar" and key in seen_series:
            continue
        seen_series.add(key)
        deduped.append(finding)
    findings = deduped
    seen_series = {tuple(f["chart"]["values"]) for f in findings}
    links = _candidate_links(schema, relationships)
    for finding in _cross_table_findings(schema, dataframe_loader, links, tallies, limits, deadline):
        # The same numbers reached through a different column (ship_state vs
        # customers.state) are one insight, not two.
        key = tuple(finding["chart"]["values"])
        if key in seen_series:
            continue
        seen_series.add(key)
        findings.append(finding)
    rows_by_table = {t["table"]: t["rows_scanned"] for t in tables_out}

    # Headline: the three most decision-relevant findings, cross-table first.
    def _priority(item):
        order = {"line": 0, "bar": 1, "histogram": 3}
        # Biggest table first (that is the fact table), then trends, then
        # breakdowns; cross-table breakdowns beat same-table ones.
        return (
            -rows_by_table.get(item["table"], 0),
            order.get(item["kind"], 2),
            0 if item.get("cross_table") else 1,
            -(item.get("distinct", 0)),
        )

    findings = sorted(findings, key=_priority)
    headline = [f["takeaway"] for f in findings[:3]]

    return {
        "status": "complete" if not skipped else "partial",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "tables": tables_out,
        "findings": findings,
        "headline": headline,
        "skipped": skipped,
        "method": (
            "Columns were ranked by name and type (measures such as totals and amounts, "
            "dimensions such as status or region, dates, and name-like columns). Every "
            "number was computed on the server from your rows in one pass per table; no "
            "AI model was involved and no data left the server."
        ),
    }
