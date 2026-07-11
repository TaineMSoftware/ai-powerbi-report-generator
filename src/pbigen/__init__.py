"""AI natural-language → Power BI report generator.

The package is split into three layers:

- `planner`: natural-language to validated intermediate `ReportSpec`
- `validator`: semantic checks on the spec
- `emit`: PBIP/PBIR/TMDL scaffolding for downstream Power BI packaging

The current implementation is fully offline and deterministic. An LLM-backed
planner can be added later by implementing the `Planner` protocol and
registering it with `register_planner(...)`.
"""

from .dax import DATE_TABLE, DATE_COLUMN, base_measure, pct_of_total_measure, running_total_measure, yoy_measure
from .emit import emit_pbip, emit_semantic_model, spec_needs_date_table
from .layout import apply_layout
from .planner import Planner, RuleBasedPlanner, get_planner, register_planner
from .schema_source import ColumnSchema, ColumnType, DatasetSchema, TableSchema, infer_csv_schema
from .spec import ReportSpec, report_spec_json_schema
from .validator import ValidationResult, validate_spec

__all__ = [
    "DATE_TABLE",
    "DATE_COLUMN",
    "Planner",
    "RuleBasedPlanner",
    "ValidationResult",
    "ColumnSchema",
    "ColumnType",
    "DatasetSchema",
    "ReportSpec",
    "TableSchema",
    "apply_layout",
    "base_measure",
    "emit_pbip",
    "emit_semantic_model",
    "get_planner",
    "infer_csv_schema",
    "pct_of_total_measure",
    "register_planner",
    "report_spec_json_schema",
    "running_total_measure",
    "spec_needs_date_table",
    "validate_spec",
    "yoy_measure",
]
