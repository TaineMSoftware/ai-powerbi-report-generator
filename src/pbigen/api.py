"""FastAPI surface for the report generator."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .planner import get_planner
from .schema_source import DatasetSchema, infer_csv_schema
from .spec import ReportSpec
from .validator import validate_spec


class GenerateRequest(BaseModel):
    request: str = Field(..., description="Natural-language report request")
    name: str = Field(default="Generated Report")
    planner: str = Field(default="rules")
    dataset_schema: DatasetSchema | None = Field(default=None, alias="schema")
    schema_payload: dict | None = Field(default=None, alias="schema_json")
    csv_path: str | None = None

    model_config = {"populate_by_name": True}


class GenerateResponse(BaseModel):
    spec: ReportSpec
    validation: dict


class ScaffoldRequest(BaseModel):
    request: str
    output_dir: str
    name: str = "Generated Report"
    planner: str = "rules"
    dataset_schema: DatasetSchema | None = Field(default=None, alias="schema")
    schema_payload: dict | None = Field(default=None, alias="schema_json")
    csv_path: str | None = None

    model_config = {"populate_by_name": True}


class ScaffoldResponse(BaseModel):
    output_dir: str
    report_dir: str
    semantic_model_dir: str
    validation: dict


def _resolve_schema(payload: GenerateRequest | ScaffoldRequest) -> DatasetSchema | None:
    if payload.dataset_schema is not None:
        return payload.dataset_schema
    if payload.schema_payload is not None:
        return DatasetSchema.model_validate(payload.schema_payload)
    if payload.csv_path is not None:
        return infer_csv_schema(payload.csv_path)
    return None


def create_app() -> FastAPI:
    app = FastAPI(title="pbigen", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/generate", response_model=GenerateResponse)
    def generate(payload: GenerateRequest) -> GenerateResponse:
        planner = get_planner(payload.planner)
        schema = _resolve_schema(payload)
        spec = planner.plan(payload.request, schema=schema, name=payload.name)
        validation = validate_spec(spec, schema).to_dict()
        return GenerateResponse(spec=spec, validation=validation)

    @app.post("/scaffold", response_model=ScaffoldResponse)
    def scaffold(payload: ScaffoldRequest) -> ScaffoldResponse:
        from .emit import emit_pbip

        planner = get_planner(payload.planner)
        schema = _resolve_schema(payload)
        spec = planner.plan(payload.request, schema=schema, name=payload.name)
        validation = validate_spec(spec, schema)
        if not validation.ok:
            raise HTTPException(status_code=400, detail=validation.to_dict())
        result = emit_pbip(spec, schema=schema, output_dir=payload.output_dir)
        return ScaffoldResponse(
            output_dir=str(Path(payload.output_dir).resolve()),
            report_dir=str(result["report_dir"]),
            semantic_model_dir=str(result["semantic_model_dir"]),
            validation=validation.to_dict(),
        )

    return app


app = create_app()
