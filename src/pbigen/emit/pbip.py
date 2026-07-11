"""PBIP/PBIR scaffold emitter.

The output is intentionally text-based and git-friendly. It is not a raw `.pbix`
compiler; instead it creates a clean intermediate project structure that a future
Windows/Fabric packaging step can compile to PBIX.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..schema_source import DatasetSchema
from ..spec import ReportSpec, Visual
from .tmdl import emit_semantic_model


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug or "report"


def _visual_payload(visual: Visual) -> dict:
    return visual.model_dump(mode="json", exclude_none=True)


def emit_pbip(
    spec: ReportSpec,
    schema: DatasetSchema | None,
    output_dir: str | Path,
) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    slug = _slugify(spec.name)
    report_dir = output_dir / f"{slug}.Report"
    semantic_model_dir = output_dir / f"{slug}.SemanticModel"
    report_def_dir = report_dir / "definition"
    pages_dir = report_def_dir / "pages"
    semantic_def_dir = semantic_model_dir / "definition"
    visuals_dir = pages_dir / "page-1" / "visuals"

    visuals_dir.mkdir(parents=True, exist_ok=True)
    semantic_def_dir.mkdir(parents=True, exist_ok=True)

    report_manifest = {
        "name": spec.name,
        "version": spec.version,
        "pages": [
            {
                "name": page.name,
                "displayName": page.display_name,
                "width": page.width,
                "height": page.height,
                "visuals": [visual.id for visual in page.visuals],
            }
            for page in spec.pages
        ],
        "filters": [f.model_dump(mode="json", exclude_none=True) for f in spec.filters],
        "measures": [m.model_dump(mode="json", exclude_none=True) for m in spec.measures],
    }
    (report_def_dir / "report.json").write_text(json.dumps(report_manifest, indent=2), encoding="utf-8")

    for page in spec.pages:
        page_dir = pages_dir / page.name
        page_visuals_dir = page_dir / "visuals"
        page_visuals_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "page.json").write_text(json.dumps(page.model_dump(mode="json", exclude_none=True), indent=2), encoding="utf-8")
        for visual in page.visuals:
            (page_visuals_dir / f"{visual.id}.json").write_text(
                json.dumps(_visual_payload(visual), indent=2), encoding="utf-8"
            )

    emit_semantic_model(spec, schema=schema, output_dir=semantic_def_dir)

    pbip = {
        "name": spec.name,
        "report": str(report_dir.name),
        "semanticModel": str(semantic_model_dir.name),
        "description": "Generated Power BI project scaffold",
    }
    (output_dir / f"{slug}.pbip").write_text(json.dumps(pbip, indent=2), encoding="utf-8")

    return {"output_dir": output_dir, "report_dir": report_dir, "semantic_model_dir": semantic_model_dir}
