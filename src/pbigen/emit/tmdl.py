"""TMDL / semantic-model scaffold emitter.

This does not attempt to reverse-engineer the proprietary `.pbix` binary.
Instead it emits a text-based semantic-model scaffold that downstream tooling
can compile or publish later.
"""

from __future__ import annotations

from pathlib import Path

from ..dax import DATE_TABLE, DATE_COLUMN
from ..schema_source import DatasetSchema
from ..spec import FieldKind, ReportSpec


def spec_needs_date_table(spec: ReportSpec) -> bool:
    """Heuristic: if any visual/filter references time intelligence, add Date."""
    for visual in spec.all_visuals():
        for role in visual.fields.all_roles():
            if role.field.table == DATE_TABLE or role.date_grain is not None:
                return True
        for filt in visual.filters:
            if filt.field.table == DATE_TABLE:
                return True
    for filt in spec.filters:
        if filt.field.table == DATE_TABLE:
            return True
    for measure in spec.measures:
        expr = measure.expression.upper()
        if any(token in expr for token in ("DATEADD(", "ALLSELECTED(", "MAX('DATE'", "MIN('DATE'")):
            return True
    return False


def _emit_table_block(name: str, columns: list[tuple[str, str]]) -> str:
    lines = [f"table '{name}' {{"]
    for col_name, col_type in columns:
        lines.append(f"  column '{col_name}' : {col_type}")
    lines.append("}")
    return "\n".join(lines)


def emit_semantic_model(
    spec: ReportSpec,
    schema: DatasetSchema | None,
    output_dir: str | Path,
) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    blocks: list[str] = [f"model '{spec.name}' {{"]

    if schema is not None:
        for table in schema.tables:
            cols = [(c.name, c.data_type.value) for c in table.columns]
            blocks.append(_emit_table_block(table.name, cols))
    else:
        blocks.append(_emit_table_block("Data", [("Revenue", "double"), ("Category", "string")]))

    if spec_needs_date_table(spec):
        blocks.append(_emit_table_block(DATE_TABLE, [(DATE_COLUMN, "dateTime"), ("Year", "int64"), ("Month", "string")]))

    if spec.measures:
        blocks.append("measures {")
        for measure in spec.measures:
            lines = [f"  measure '{measure.name}' = {measure.expression}"]
            if measure.format_string:
                lines.append(f'    formatString = "{measure.format_string}"')
            blocks.append("\n".join(lines))
        blocks.append("}")

    blocks.append("}")
    tmdl = "\n\n".join(blocks)
    (output_dir / "model.tmdl").write_text(tmdl, encoding="utf-8")
    return output_dir
