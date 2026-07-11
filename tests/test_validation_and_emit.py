from pathlib import Path

from pbigen.emit import emit_pbip, emit_semantic_model, spec_needs_date_table
from pbigen.planner import RuleBasedPlanner
from pbigen.schema_source import ColumnSchema, ColumnType, DatasetSchema, TableSchema
from pbigen.validator import validate_spec


def sample_schema() -> DatasetSchema:
    return DatasetSchema(
        name="SalesDataset",
        tables=[
            TableSchema(
                name="Sales",
                columns=[
                    ColumnSchema(name="OrderDate", data_type=ColumnType.datetime),
                    ColumnSchema(name="Region", data_type=ColumnType.string),
                    ColumnSchema(name="Category", data_type=ColumnType.string),
                    ColumnSchema(name="Revenue", data_type=ColumnType.double),
                ],
            )
        ],
    )


def test_validation_passes_for_generated_spec(tmp_path: Path):
    planner = RuleBasedPlanner()
    schema = sample_schema()
    spec = planner.plan("Show revenue by region as a bar chart and a KPI card for YoY growth.", schema=schema)

    result = validate_spec(spec, schema)
    assert result.ok, result.to_dict()
    assert spec_needs_date_table(spec)

    emit_dir = tmp_path / "out"
    out = emit_pbip(spec, schema=schema, output_dir=emit_dir)
    assert (out["report_dir"] / "definition" / "report.json").exists()
    assert (out["semantic_model_dir"] / "definition" / "model.tmdl").exists()


def test_emit_semantic_model_writes_tmdl(tmp_path: Path):
    planner = RuleBasedPlanner()
    schema = sample_schema()
    spec = planner.plan("Show sales by category as a pie chart.", schema=schema)

    out = emit_semantic_model(spec, schema=schema, output_dir=tmp_path / "semantic")
    assert (out / "model.tmdl").exists()
