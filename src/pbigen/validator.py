"""Report Spec validation.

Structural validity is guaranteed by the Pydantic models; this module adds the
semantic checks: references resolve against the dataset schema, aggregations
match column types, measure references exist, layout rectangles don't overlap,
and visuals have sensible field shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schema_source import NUMERIC_TYPES, ColumnType, DatasetSchema
from .spec import (
    Aggregation,
    FieldKind,
    FieldRef,
    Filter,
    FilterOperator,
    ReportSpec,
    Visual,
    VisualType,
)

# Columns of the auto-generated Date dimension are always valid references.
DATE_TABLE_COLUMNS = {
    "Date": ColumnType.datetime,
    "Year": ColumnType.int64,
    "Quarter": ColumnType.string,
    "Month": ColumnType.string,
    "MonthNumber": ColumnType.int64,
    "Week": ColumnType.int64,
    "Day": ColumnType.int64,
}


@dataclass
class Issue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    location: str = ""

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "location": self.location,
        }


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "issues": [i.to_dict() for i in self.issues]}


def validate_spec(spec: ReportSpec, schema: DatasetSchema | None = None) -> ValidationResult:
    result = ValidationResult()
    _check_unique_ids(spec, result)
    _check_measures(spec, result)

    measure_names = spec.measure_names()
    for filt in spec.filters:
        _check_filter(filt, "report", schema, measure_names, result)

    for page in spec.pages:
        for filt in page.filters:
            _check_filter(filt, f"page:{page.name}", schema, measure_names, result)
        for visual in page.visuals:
            _check_visual(visual, schema, measure_names, result)
        _check_layout(page.visuals, page.width, page.height, result)

    return result


def _check_unique_ids(spec: ReportSpec, result: ValidationResult) -> None:
    seen: set[str] = set()
    for v in spec.all_visuals():
        if v.id in seen:
            result.issues.append(
                Issue("error", "duplicate-visual-id", f"Duplicate visual id {v.id!r}", v.id)
            )
        seen.add(v.id)


def _check_measures(spec: ReportSpec, result: ValidationResult) -> None:
    seen: set[str] = set()
    for m in spec.measures:
        if m.name in seen:
            result.issues.append(
                Issue("error", "duplicate-measure", f"Duplicate measure name {m.name!r}", m.name)
            )
        seen.add(m.name)
        if not m.expression.strip():
            result.issues.append(
                Issue("error", "empty-measure", f"Measure {m.name!r} has no expression", m.name)
            )


def _resolve_column(
    ref: FieldRef, schema: DatasetSchema | None
) -> tuple[bool, ColumnType | None]:
    """(known, data_type). known=False only when a schema is present and lookup fails."""
    if ref.table == "Date" and ref.name in DATE_TABLE_COLUMNS:
        return True, DATE_TABLE_COLUMNS[ref.name]
    if schema is None:
        return True, None
    table = schema.table(ref.table) if ref.table else None
    if table is None:
        return False, None
    col = table.column(ref.name)
    if col is None:
        return False, None
    return True, col.data_type


def _check_field_ref(
    ref: FieldRef,
    location: str,
    schema: DatasetSchema | None,
    measure_names: set[str],
    result: ValidationResult,
    aggregation: Aggregation = Aggregation.none,
) -> None:
    if ref.kind == FieldKind.measure:
        if ref.name not in measure_names:
            result.issues.append(
                Issue(
                    "error",
                    "unknown-measure",
                    f"Measure reference {ref.qualified()} is not declared in spec.measures",
                    location,
                )
            )
        return
    known, dtype = _resolve_column(ref, schema)
    if not known:
        result.issues.append(
            Issue(
                "error",
                "unknown-field",
                f"Field {ref.qualified()} does not exist in the dataset schema",
                location,
            )
        )
        return
    if (
        aggregation in (Aggregation.sum, Aggregation.average)
        and dtype is not None
        and dtype not in NUMERIC_TYPES
    ):
        result.issues.append(
            Issue(
                "error",
                "bad-aggregation",
                f"Cannot {aggregation.value} non-numeric field {ref.qualified()} ({dtype.value})",
                location,
            )
        )


def _check_filter(
    filt: Filter,
    location: str,
    schema: DatasetSchema | None,
    measure_names: set[str],
    result: ValidationResult,
) -> None:
    _check_field_ref(filt.field, location, schema, measure_names, result)
    if filt.operator == FilterOperator.top_n:
        if not filt.top_n or filt.top_n < 1:
            result.issues.append(
                Issue("error", "bad-topn", "topN filter requires top_n >= 1", location)
            )
        if filt.order_by is None:
            result.issues.append(
                Issue("error", "bad-topn", "topN filter requires order_by", location)
            )
        elif filt.order_by.kind == FieldKind.measure:
            _check_field_ref(filt.order_by, location, schema, measure_names, result)
    elif filt.operator == FilterOperator.between:
        if len(filt.values) != 2:
            result.issues.append(
                Issue("error", "bad-range", "between filter requires exactly 2 values", location)
            )
    elif filt.operator in (FilterOperator.in_, FilterOperator.not_in, FilterOperator.equals):
        if not filt.values:
            result.issues.append(
                Issue("error", "empty-filter", "categorical filter has no values", location)
            )


def _check_visual(
    visual: Visual,
    schema: DatasetSchema | None,
    measure_names: set[str],
    result: ValidationResult,
) -> None:
    loc = f"visual:{visual.id}"
    for role in visual.fields.all_roles():
        _check_field_ref(role.field, loc, schema, measure_names, result, role.aggregation)
    for filt in visual.filters:
        _check_filter(filt, loc, schema, measure_names, result)

    n_values = len(visual.fields.values)
    if visual.type in (VisualType.pie_chart, VisualType.donut_chart):
        if n_values != 1:
            result.issues.append(
                Issue(
                    "warning",
                    "pie-shape",
                    f"{visual.type.value} should have exactly 1 value (has {n_values})",
                    loc,
                )
            )
        if visual.fields.category is None:
            result.issues.append(
                Issue("warning", "pie-shape", f"{visual.type.value} has no category field", loc)
            )
    elif visual.type == VisualType.card:
        if n_values != 1:
            result.issues.append(
                Issue(
                    "error",
                    "card-shape",
                    f"card requires exactly 1 value (has {n_values})",
                    loc,
                )
            )
    elif visual.type == VisualType.slicer:
        if n_values != 1:
            result.issues.append(
                Issue("error", "slicer-shape", "slicer requires exactly 1 field", loc)
            )
    elif visual.type in (VisualType.table, VisualType.matrix):
        if n_values == 0:
            result.issues.append(
                Issue("error", "empty-table", f"{visual.type.value} has no fields", loc)
            )
    else:  # cartesian charts
        if n_values == 0:
            result.issues.append(
                Issue("error", "no-values", f"{visual.type.value} has no value fields", loc)
            )
        if visual.fields.category is None:
            result.issues.append(
                Issue("warning", "no-category", f"{visual.type.value} has no category/axis", loc)
            )


def _check_layout(
    visuals: list[Visual], width: int, height: int, result: ValidationResult
) -> None:
    placed = [(v, v.position) for v in visuals if v.position is not None]
    for v, pos in placed:
        if pos.x < 0 or pos.y < 0 or pos.x + pos.width > width or pos.y + pos.height > height:
            result.issues.append(
                Issue(
                    "error",
                    "out-of-bounds",
                    f"Visual {v.id} exceeds the {width}x{height} canvas",
                    f"visual:{v.id}",
                )
            )
    for i, (a, pa) in enumerate(placed):
        for b, pb in placed[i + 1 :]:
            if (
                pa.x < pb.x + pb.width
                and pb.x < pa.x + pa.width
                and pa.y < pb.y + pb.height
                and pb.y < pa.y + pa.height
            ):
                result.issues.append(
                    Issue(
                        "error",
                        "overlap",
                        f"Visuals {a.id} and {b.id} overlap",
                        f"visual:{a.id}",
                    )
                )
