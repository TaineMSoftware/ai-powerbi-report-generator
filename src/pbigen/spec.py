"""Report Specification models.

The Report Spec is the validated intermediate representation between a
natural-language request and Power BI artifacts. A planner (rule-based today,
LLM-backed later) produces a :class:`ReportSpec`; the validator and emitters
only ever consume this structure, never raw natural language.
"""

from __future__ import annotations

import json
from enum import Enum

from pydantic import BaseModel, Field

SPEC_VERSION = "0.1"

CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720
GRID_COLS = 12
GRID_ROW_HEIGHT = 90  # 720 / 8 rows on the default canvas


class VisualType(str, Enum):
    bar_chart = "barChart"
    column_chart = "columnChart"
    line_chart = "lineChart"
    area_chart = "areaChart"
    pie_chart = "pieChart"
    donut_chart = "donutChart"
    table = "tableEx"
    matrix = "pivotTable"
    card = "card"
    slicer = "slicer"


class FieldKind(str, Enum):
    column = "column"
    measure = "measure"


class Aggregation(str, Enum):
    none = "none"
    sum = "sum"
    average = "average"
    min = "min"
    max = "max"
    count = "count"
    distinct_count = "distinctCount"


class DateGrain(str, Enum):
    year = "year"
    quarter = "quarter"
    month = "month"
    week = "week"
    day = "day"


class FieldRef(BaseModel):
    """Reference to a model object by qualified name."""

    kind: FieldKind = FieldKind.column
    table: str | None = None
    name: str

    def qualified(self) -> str:
        if self.kind == FieldKind.measure:
            return f"[{self.name}]"
        if self.table:
            return f"'{self.table}'[{self.name}]"
        return f"[{self.name}]"


class DataRole(BaseModel):
    """A field bound to a visual role, optionally aggregated / date-grained."""

    field: FieldRef
    aggregation: Aggregation = Aggregation.none
    date_grain: DateGrain | None = None
    display_name: str | None = None

    def label(self) -> str:
        return self.display_name or self.field.name


class VisualFields(BaseModel):
    category: DataRole | None = None
    legend: DataRole | None = None
    values: list[DataRole] = Field(default_factory=list)

    def all_roles(self) -> list[DataRole]:
        roles = []
        if self.category:
            roles.append(self.category)
        if self.legend:
            roles.append(self.legend)
        roles.extend(self.values)
        return roles


class FilterOperator(str, Enum):
    in_ = "in"
    not_in = "notIn"
    equals = "equals"
    between = "between"
    gte = "gte"
    lte = "lte"
    top_n = "topN"


class Filter(BaseModel):
    field: FieldRef
    operator: FilterOperator
    values: list[str | int | float | bool] = Field(default_factory=list)
    top_n: int | None = None
    order_by: FieldRef | None = None


class GridPos(BaseModel):
    """Position on a 12-column grid; rows are 90px each."""

    col: int = Field(ge=0, lt=GRID_COLS)
    row: int = Field(ge=0)
    col_span: int = Field(ge=1, le=GRID_COLS)
    row_span: int = Field(ge=1)


class PixelPos(BaseModel):
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def from_grid(cls, grid: GridPos, canvas_width: int = CANVAS_WIDTH) -> "PixelPos":
        x1 = round(grid.col * canvas_width / GRID_COLS)
        x2 = round((grid.col + grid.col_span) * canvas_width / GRID_COLS)
        return cls(
            x=x1,
            y=grid.row * GRID_ROW_HEIGHT,
            width=x2 - x1,
            height=grid.row_span * GRID_ROW_HEIGHT,
        )


class Visual(BaseModel):
    id: str
    type: VisualType
    title: str | None = None
    fields: VisualFields = Field(default_factory=VisualFields)
    filters: list[Filter] = Field(default_factory=list)
    layout_hint: str | None = None
    grid: GridPos | None = None
    position: PixelPos | None = None


class Page(BaseModel):
    name: str
    display_name: str
    width: int = CANVAS_WIDTH
    height: int = CANVAS_HEIGHT
    visuals: list[Visual] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)


class Measure(BaseModel):
    name: str
    table: str
    expression: str
    format_string: str | None = None
    description: str | None = None


class ReportSpec(BaseModel):
    version: str = SPEC_VERSION
    name: str = "Generated Report"
    pages: list[Page] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)

    def all_visuals(self) -> list[Visual]:
        return [v for page in self.pages for v in page.visuals]

    def measure_names(self) -> set[str]:
        return {m.name for m in self.measures}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.model_dump(mode="json", exclude_none=True), indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "ReportSpec":
        return cls.model_validate_json(text)


def report_spec_json_schema() -> dict:
    """JSON Schema for the Report Spec (for external tooling / LLM planners)."""
    return ReportSpec.model_json_schema()
