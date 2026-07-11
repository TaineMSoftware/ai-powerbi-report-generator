import json
from pathlib import Path

from fastapi.testclient import TestClient

from pbigen.api import app
from pbigen.cli import main


client = TestClient(app)


def sample_schema_json() -> dict:
    return {
        "name": "SalesDataset",
        "tables": [
            {
                "name": "Sales",
                "columns": [
                    {"name": "OrderDate", "data_type": "dateTime"},
                    {"name": "Region", "data_type": "string"},
                    {"name": "Revenue", "data_type": "double"},
                ],
            }
        ],
    }


def test_generate_endpoint_returns_spec():
    response = client.post(
        "/generate",
        json={"request": "Show revenue by region as a bar chart", "schema_json": sample_schema_json()},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["spec"]["pages"]
    assert payload["validation"]["ok"] is True


def test_cli_generate_spec(capsys):
    schema_path = Path("/tmp/pbigen-schema.json")
    schema_path.write_text(json.dumps(sample_schema_json()), encoding="utf-8")
    exit_code = main([
        "generate-spec",
        "Show revenue by region as a bar chart",
        "--schema-json",
        str(schema_path),
        "--pretty",
    ])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"spec"' in captured.out
