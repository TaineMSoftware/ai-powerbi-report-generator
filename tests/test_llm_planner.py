import json

import pytest

from pbigen.planner import get_planner
from pbigen.schema_source import ColumnSchema, ColumnType, DatasetSchema, TableSchema
from pbigen.spec import ReportSpec, VisualType


def sample_schema() -> DatasetSchema:
    return DatasetSchema(
        name="SalesDataset",
        tables=[
            TableSchema(
                name="Sales",
                columns=[
                    ColumnSchema(name="Region", data_type=ColumnType.string),
                    ColumnSchema(name="Revenue", data_type=ColumnType.double),
                ],
            )
        ],
    )


def test_llm_planner_is_registered():
    planner = get_planner("llm")
    assert planner is not None
    assert planner.__class__.__name__ == "LLMPlanner"


def test_llm_planner_calls_command_and_parses_report_spec(monkeypatch):
    captured = {}

    def fake_run(command, *, capture_output=None, text=None, check=None):
        captured["command"] = command
        payload = {
            "version": "0.1",
            "name": "LLM Report",
            "pages": [
                {
                    "name": "page1",
                    "display_name": "Page 1",
                    "visuals": [
                        {
                            "id": "visual-001",
                            "type": VisualType.bar_chart.value,
                            "fields": {
                                "category": {"field": {"kind": "column", "table": "Sales", "name": "Region"}},
                                "values": [
                                    {"field": {"kind": "measure", "name": "Total Revenue"}}
                                ],
                            },
                        }
                    ],
                }
            ],
            "measures": [
                {"name": "Total Revenue", "table": "Sales", "expression": "SUM('Sales'[Revenue])"}
            ],
            "filters": [],
        }
        return type("Result", (), {"stdout": json.dumps(payload), "returncode": 0})()

    from pbigen import planner as planner_module

    monkeypatch.setattr(planner_module.subprocess, "run", fake_run)

    planner = planner_module.LLMPlanner(command=["claude", "--model", "fable"])
    spec = planner.plan("Show revenue by region as a bar chart", schema=sample_schema())

    assert captured["command"][0] == "claude"
    assert "Show revenue by region" in captured["command"][-1]
    assert isinstance(spec, ReportSpec)
    assert spec.name == "LLM Report"
    assert spec.pages[0].visuals[0].type == VisualType.bar_chart
    assert spec.pages[0].visuals[0].position is not None
