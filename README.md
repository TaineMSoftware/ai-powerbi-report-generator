# AI PowerBI Report Generator

A runnable MVP for turning natural-language report requests into a validated Power BI report *spec* and a git-friendly PBIP/PBIR/TMDL scaffold.

## What it does

Describe the report you want:

> show monthly revenue by region as a bar chart, add a pie of sales by category, and include a KPI card for YoY growth

The app will:

- detect requested visuals
- resolve fields against a dataset schema
- generate reusable DAX measures
- validate the resulting spec
- emit a PBIP-style project scaffold
- expose the same flow over HTTP

## What it does *not* do yet

- It does **not** compile a binary `.pbix` on Linux.
- It does **not** attempt to reverse-engineer Power BI Desktop internals.
- It **does** leave a clean adapter boundary for a future Windows/Fabric packaging step.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

## CLI

Generate a report spec:

```bash
pbigen generate-spec "Show revenue by region as a bar chart" --csv-path data/sales.csv --pretty
```

Create a PBIP scaffold:

```bash
pbigen scaffold "Show revenue by region as a bar chart and a KPI card for YoY growth" \
  --csv-path data/sales.csv \
  ./out
```

Run the API:

```bash
pbigen serve --reload
```

Then POST to `/generate` or `/scaffold`.

## Repo layout

```text
src/pbigen/
  api.py          FastAPI app factory
  cli.py          Command-line entry point
  dax.py          DAX measure helpers
  layout.py       Deterministic visual placement
  planner.py      Offline NL planner
  schema_source.py Dataset ingestion
  spec.py         ReportSpec models
  validator.py    Semantic validation
  emit/           PBIP + TMDL scaffold writers
```

## Development

```bash
pytest
```

## Project status

This is an MVP foundation: usable, test-covered, and built to be extended with a real LLM planner and a Windows/Fabric `.pbix` compiler later.
