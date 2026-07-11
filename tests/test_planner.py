from pbigen.planner import RuleBasedPlanner
from pbigen.schema_source import ColumnSchema, ColumnType, DatasetSchema, TableSchema
from pbigen.spec import VisualType


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


def test_rule_based_planner_builds_requested_visuals():
    planner = RuleBasedPlanner()
    spec = planner.plan(
        "Show monthly revenue by region as a bar chart, a pie of sales by category, and a KPI card for YoY growth, filtered to 2025.",
        schema=sample_schema(),
    )

    assert len(spec.pages) == 1
    visuals = spec.pages[0].visuals
    assert [v.type for v in visuals] == [VisualType.bar_chart, VisualType.pie_chart, VisualType.card]
    assert spec.filters, "expected the year filter to be extracted"
    assert any(m.name.startswith("Total") for m in spec.measures)
    assert any("YoY" in m.name for m in spec.measures)
