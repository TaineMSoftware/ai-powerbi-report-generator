"""Deterministic DAX measure generation.

Every numeric value used by a visual becomes a named, reusable DAX measure
(never an implicit aggregation), so measures are auditable and shared across
visuals. Time-intelligence measures reference the auto-generated Date table.
"""

from __future__ import annotations

from .schema_source import ColumnType
from .spec import Aggregation, FieldRef, Measure

DATE_TABLE = "Date"
DATE_COLUMN = "Date"

_AGG_FUNCTIONS = {
    Aggregation.sum: "SUM",
    Aggregation.average: "AVERAGE",
    Aggregation.min: "MIN",
    Aggregation.max: "MAX",
    Aggregation.count: "COUNT",
    Aggregation.distinct_count: "DISTINCTCOUNT",
}

_AGG_NAME_PREFIX = {
    Aggregation.sum: "Total",
    Aggregation.average: "Average",
    Aggregation.min: "Min",
    Aggregation.max: "Max",
    Aggregation.count: "Count of",
    Aggregation.distinct_count: "Distinct",
}


def _date_ref() -> str:
    return f"'{DATE_TABLE}'[{DATE_COLUMN}]"


def format_for(aggregation: Aggregation, data_type: ColumnType | None) -> str:
    if aggregation in (Aggregation.count, Aggregation.distinct_count):
        return "#,0"
    if data_type == ColumnType.int64:
        return "#,0"
    return "#,0.00"


def base_measure(
    aggregation: Aggregation,
    field: FieldRef,
    data_type: ColumnType | None = None,
) -> Measure:
    """Named measure for a simple aggregation over a column."""
    if aggregation == Aggregation.none:
        aggregation = Aggregation.sum
    func = _AGG_FUNCTIONS[aggregation]
    prefix = _AGG_NAME_PREFIX[aggregation]
    table = field.table or "Data"
    column_ref = f"'{table}'[{field.name}]"
    return Measure(
        name=f"{prefix} {field.name}",
        table=table,
        expression=f"{func}({column_ref})",
        format_string=format_for(aggregation, data_type),
        description=f"{aggregation.value} of {column_ref}",
    )


def yoy_measure(base: Measure, base_field_name: str) -> Measure:
    """Year-over-year growth percentage for an existing base measure."""
    expression = (
        f"VAR __prev =\n"
        f"    CALCULATE([{base.name}], DATEADD({_date_ref()}, -1, YEAR))\n"
        f"RETURN\n"
        f"    DIVIDE([{base.name}] - __prev, __prev)"
    )
    return Measure(
        name=f"{base_field_name} YoY %",
        table=base.table,
        expression=expression,
        format_string="0.0%",
        description=f"Year-over-year growth of [{base.name}]",
    )


def running_total_measure(base: Measure, base_field_name: str) -> Measure:
    expression = (
        f"CALCULATE(\n"
        f"    [{base.name}],\n"
        f"    FILTER(ALLSELECTED({_date_ref()}), {_date_ref()} <= MAX({_date_ref()}))\n"
        f")"
    )
    return Measure(
        name=f"{base_field_name} Running Total",
        table=base.table,
        expression=expression,
        format_string=base.format_string,
        description=f"Running total of [{base.name}] over {_date_ref()}",
    )


def pct_of_total_measure(base: Measure, base_field_name: str) -> Measure:
    expression = (
        f"DIVIDE(\n"
        f"    [{base.name}],\n"
        f"    CALCULATE([{base.name}], ALLSELECTED())\n"
        f")"
    )
    return Measure(
        name=f"{base_field_name} % of Total",
        table=base.table,
        expression=expression,
        format_string="0.0%",
        description=f"[{base.name}] as a share of the selected total",
    )
