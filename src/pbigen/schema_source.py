"""Dataset schema ingestion.

A :class:`DatasetSchema` grounds the planner and validator so field references
resolve to real tables/columns. Schemas can be loaded from a JSON file or
inferred from a CSV header + sample rows.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

_SAMPLE_ROWS = 200

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)


class ColumnType(str, Enum):
    string = "string"
    int64 = "int64"
    double = "double"
    boolean = "boolean"
    datetime = "dateTime"


NUMERIC_TYPES = {ColumnType.int64, ColumnType.double}


class ColumnSchema(BaseModel):
    name: str
    data_type: ColumnType = ColumnType.string


class TableSchema(BaseModel):
    name: str
    source_path: str | None = None
    columns: list[ColumnSchema] = Field(default_factory=list)

    def column(self, name: str) -> ColumnSchema | None:
        for col in self.columns:
            if col.name == name:
                return col
        return None


class DatasetSchema(BaseModel):
    name: str = "Dataset"
    tables: list[TableSchema] = Field(default_factory=list)

    def table(self, name: str) -> TableSchema | None:
        for t in self.tables:
            if t.name == name:
                return t
        return None

    def iter_columns(self):
        for t in self.tables:
            for c in t.columns:
                yield t, c

    def date_column(self) -> tuple[TableSchema, ColumnSchema] | None:
        """First datetime column across tables, used for date relationships."""
        for t, c in self.iter_columns():
            if c.data_type == ColumnType.datetime:
                return t, c
        return None

    def first_numeric_column(self) -> tuple[TableSchema, ColumnSchema] | None:
        for t, c in self.iter_columns():
            if c.data_type in NUMERIC_TYPES:
                return t, c
        return None

    @classmethod
    def from_json_file(cls, path: str | Path) -> "DatasetSchema":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)


def _is_date(value: str) -> bool:
    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(value, fmt)
            return True
        except ValueError:
            continue
    return False


def _infer_type(values: list[str]) -> ColumnType:
    values = [v.strip() for v in values if v is not None and v.strip() != ""]
    if not values:
        return ColumnType.string
    if all(v.lower() in ("true", "false") for v in values):
        return ColumnType.boolean
    if all(_is_int(v) for v in values):
        return ColumnType.int64
    if all(_is_float(v) for v in values):
        return ColumnType.double
    if all(_is_date(v) for v in values):
        return ColumnType.datetime
    return ColumnType.string


def _is_int(value: str) -> bool:
    try:
        int(value)
        return True
    except ValueError:
        return False


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def infer_csv_schema(
    path: str | Path,
    table_name: str | None = None,
    dataset_name: str | None = None,
) -> DatasetSchema:
    """Infer a single-table schema from a CSV file's header and sample rows."""
    path = Path(path)
    table_name = table_name or path.stem.replace("-", "_").replace(" ", "_").title()
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header:
            raise ValueError(f"CSV file {path} has no header row")
        samples: list[list[str]] = [[] for _ in header]
        for i, row in enumerate(reader):
            if i >= _SAMPLE_ROWS:
                break
            for j, cell in enumerate(row[: len(header)]):
                samples[j].append(cell)
    columns = [
        ColumnSchema(name=name.strip(), data_type=_infer_type(samples[i]))
        for i, name in enumerate(header)
    ]
    table = TableSchema(name=table_name, source_path=str(path), columns=columns)
    return DatasetSchema(name=dataset_name or table_name, tables=[table])
