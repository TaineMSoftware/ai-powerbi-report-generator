"""Command-line interface for pbigen."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .emit import emit_pbip
from .planner import get_planner
from .schema_source import DatasetSchema, infer_csv_schema
from .validator import validate_spec


def _load_schema(args: argparse.Namespace) -> DatasetSchema | None:
    if args.schema_json:
        return DatasetSchema.model_validate_json(Path(args.schema_json).read_text(encoding="utf-8"))
    if args.csv_path:
        return infer_csv_schema(args.csv_path)
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pbigen", description="Natural-language Power BI report generator")
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate-spec", help="Print a validated report spec as JSON")
    generate.add_argument("request", help="Natural-language report request")
    generate.add_argument("--name", default="Generated Report")
    generate.add_argument("--planner", default="rules")
    generate.add_argument("--schema-json", dest="schema_json")
    generate.add_argument("--csv-path", dest="csv_path")
    generate.add_argument("--pretty", action="store_true", help="Pretty-print JSON")

    scaffold = sub.add_parser("scaffold", help="Write PBIP/PBIR/TMDL scaffold to disk")
    scaffold.add_argument("request", help="Natural-language report request")
    scaffold.add_argument("output_dir", help="Directory to write the scaffold to")
    scaffold.add_argument("--name", default="Generated Report")
    scaffold.add_argument("--planner", default="rules")
    scaffold.add_argument("--schema-json", dest="schema_json")
    scaffold.add_argument("--csv-path", dest="csv_path")

    serve = sub.add_parser("serve", help="Run the FastAPI app with uvicorn")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        uvicorn.run("pbigen.api:app", host=args.host, port=args.port, reload=args.reload)
        return 0

    schema = _load_schema(args)
    planner = get_planner(args.planner)
    spec = planner.plan(args.request, schema=schema, name=args.name)
    validation = validate_spec(spec, schema)

    if args.command == "generate-spec":
        payload = {"spec": spec.model_dump(mode="json", exclude_none=True), "validation": validation.to_dict()}
        if args.pretty:
            print(json.dumps(payload, indent=2))
        else:
            print(json.dumps(payload, separators=(",", ":")))
        return 0 if validation.ok else 2

    if args.command == "scaffold":
        result = emit_pbip(spec, schema=schema, output_dir=args.output_dir)
        print(json.dumps({
            "output_dir": str(Path(args.output_dir).resolve()),
            "report_dir": str(result["report_dir"]),
            "semantic_model_dir": str(result["semantic_model_dir"]),
            "validation": validation.to_dict(),
        }, indent=2))
        return 0 if validation.ok else 2

    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
