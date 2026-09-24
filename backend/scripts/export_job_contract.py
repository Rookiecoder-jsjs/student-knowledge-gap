"""Export the typed photo/job API subset without requiring a running server."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from app.main import app


def contract() -> dict:
    source = app.openapi()
    paths = {key: value for key, value in source["paths"].items()
             if key.startswith("/jobs/") or key in {
                 "/exams/photo-template", "/exams/{exam_id}/photo-response"}}
    schemas = {}

    def collect(value):
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[1]
                if name not in schemas:
                    schemas[name] = source["components"]["schemas"][name]
                    collect(schemas[name])
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(paths)
    return {"openapi": source["openapi"], "info": source["info"], "paths": paths,
            "components": {"schemas": schemas}}


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[2] / "frontend/app/openapi-jobs.json"
    rendered = json.dumps(contract(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        if not target.exists() or target.read_text(encoding="utf-8") != rendered:
            raise SystemExit("Job API schema changed: regenerate openapi-jobs.json and TypeScript types")
    else:
        target.write_text(rendered, encoding="utf-8")
