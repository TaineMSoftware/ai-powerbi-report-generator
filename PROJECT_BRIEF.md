# AI Natural-Language → Power BI Report Generator — Project Brief

## Overview

A system that turns plain-English report requests ("Show monthly revenue by region as a bar chart, a pie of sales by category, and a KPI card for YoY growth, filtered to 2025") into a working Power BI report. An LLM translates the request into a validated intermediate **Report Specification** (JSON), which a deterministic generator compiles into Power BI project artifacts (PBIP/PBIR + TMDL semantic model) and finally a distributable `.pbix` file. The LLM never writes binary output directly — it only produces the spec; all Power BI file mechanics are handled by tested code.

**Core value:** analysts and business users get first-draft reports in minutes without knowing DAX, the visual pane, or data modeling conventions.

## User Capabilities

- **Visuals:** describe bar/column/line/area charts, pie/donut charts, tables and matrices, KPI cards, and slicers in natural language, including which fields go on which axis/legend/values.
- **Measures:** request calculations by intent ("year-over-year growth", "average order value", "running total") — the system generates named DAX measures with correct time-intelligence patterns.
- **Filters:** specify report-, page-, and visual-level filters ("only 2025", "exclude returns", "top 10 products by revenue").
- **Layout:** describe placement and emphasis ("KPIs across the top, big trend chart on the left, table below") — the system maps this to a grid and emits pixel coordinates.
- **Iteration:** refine conversationally ("make the pie a donut", "add a slicer for region") — edits patch the existing spec rather than regenerating from scratch.
- **Grounding:** the user connects a dataset (CSV/Excel/SQL for MVP); the system reads its schema so field references resolve to real columns, and it asks for clarification when a request is ambiguous.

## System Architecture

```
┌──────────┐   ┌─────────────────┐   ┌──────────────┐   ┌──────────────────┐   ┌─────────┐
│  Chat UI │──▶│ Orchestrator/API │──▶│ LLM Planner  │──▶│ Spec Validator    │──▶│ PBIX     │
│ (web)    │◀──│ (session, state) │   │ (Claude, tool│   │ (JSON Schema +    │   │ Compiler │
└──────────┘   └────────┬────────┘   │  use, schema │   │  semantic checks) │   └────┬────┘
                        │            │  grounding)  │   └──────────────────┘        │
               ┌────────▼────────┐   └──────────────┘                          .pbix / publish
               │ Schema Ingestion │  (dataset profiling: tables, columns,
               │  & Data Profiler │   types, cardinality, sample values)
               └─────────────────┘
```

**Key components**

1. **Chat UI + Orchestrator** — manages the conversation, holds the current Report Spec as session state, renders a live preview of the layout/spec, and routes user edits as spec patches.
2. **Schema Ingestion & Profiler** — connects to the source, extracts tables/columns/types/relationships, and produces a compact schema summary injected into the LLM context so generated field references are real.
3. **LLM Planner (Claude)** — converts NL into the Report Spec via structured output/tool use; also generates DAX measure expressions and asks clarifying questions. Uses few-shot examples per visual type.
4. **Spec Validator** — JSON Schema validation plus semantic checks: fields exist, aggregations match column types, DAX parses (validated against the model), layout has no overlaps, filter targets resolve.
5. **PBIX Compiler** — deterministic code (no LLM) that renders the spec into PBIP format: PBIR visual JSON files for the report and TMDL for the semantic model, then produces `.pbix` via `pbi-tools` compile and/or publishes through the Fabric REST API.

## Data/Modeling Flow

1. **Connect & profile:** user points at a data source; the profiler extracts schema, infers relationships (key-name matching + cardinality analysis), and detects date columns.
2. **Model synthesis:** the system builds a minimal star-schema TMDL model — source tables with Power Query (M) partitions, inferred relationships, an auto-generated date dimension when time intelligence is requested, and correct column data types/format strings.
3. **Measure generation:** each NL calculation request becomes a named DAX measure in the model (not an implicit aggregation), so measures are reusable across visuals and auditable. Generated DAX is validated by loading the model and executing a test evaluation.
4. **Spec binding:** every visual in the Report Spec binds to model objects by qualified name (`Sales[Revenue]`, `[YoY Growth %]`); the validator rejects dangling references before compilation.
5. **Refinement loop:** user edits mutate the spec; only changed artifacts are re-rendered, keeping iteration fast and diffs reviewable (PBIP is git-friendly).

## .pbix Generation Strategy

- **Primary path — PBIP/PBIR + compile:** emit the open, documented **Power BI Project** format: `*.Report/definition/` (report.json, pages, one JSON file per visual, PBIR schema) and `*.SemanticModel/definition/` (TMDL). This is Microsoft's supported, text-based format — far safer than hand-crafting the legacy binary `Layout` (UTF-16 JSON) and xPress9-compressed `DataModel` inside a raw `.pbix` zip.
- **PBIX packaging:** use `pbi-tools compile` to produce a `.pbix` from the PBIP folder for download. Fallback/alternative: publish the PBIP directly to a Fabric workspace via **Fabric Git integration or the Fabric REST APIs**, then let the user download the `.pbix` from the service — this offloads packaging to Microsoft-supported machinery.
- **Templates over synthesis:** maintain a library of golden per-visual JSON templates (one per supported visual type, extracted from real Desktop-authored reports) and fill in field bindings, titles, and positions — rather than asking the LLM to emit raw PBIR. This keeps output schema-valid as the PBIR schema evolves.
- **Verification gate:** every generated report is smoke-tested before delivery — model loads, DAX evaluates, PBIR passes schema validation, and (in CI) the report opens headlessly in the service. Never ship an artifact that hasn't passed this gate.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| PBIR/TMDL schema drift as Microsoft evolves the formats | Generated files stop opening in Desktop | Pin schema versions, template-based generation, CI that opens output in a real Fabric workspace nightly |
| `pbi-tools` compile depends on Windows/Desktop bits | Constrains hosting for the packaging step | Isolate packaging in a Windows build worker; offer Fabric-publish path that avoids local compile entirely |
| LLM hallucinates fields or invalid DAX | Broken or silently wrong reports | Schema grounding in context, hard validation pass, DAX execution test; clarify rather than guess |
| Ambiguous NL ("sales by area") | Wrong chart or wrong field | Clarifying-question policy + always show the spec/preview for confirmation before compile |
| Wrong numbers are worse than no numbers (bad relationships/aggregations) | Loss of user trust | Conservative relationship inference with user confirmation; measure unit tests against sample data |
| Legacy `.pbix` internals (xPress9 DataModel) are undocumented | Dead end if attempted directly | Explicit non-goal: never write the binary model by hand; PBIP/Fabric only |
| Data privacy (schema + samples sent to LLM) | Compliance exposure | Send schema metadata only by default; sampling opt-in; support customer-managed keys/region pinning |

## MVP Plan

**Milestone 1 — Spec pipeline (weeks 1–3):** Report Spec JSON schema; Claude planner with structured output producing specs from NL for bar/line/pie/table/card + basic filters; validator; CLI harness. *Exit: 20 canned prompts produce valid specs deterministically checked.*

**Milestone 2 — Compile to .pbix (weeks 3–6):** template library for the five MVP visuals; TMDL model generation from a CSV/Excel source (single table + auto date table); PBIP emission; `pbi-tools` packaging on a Windows worker. *Exit: generated `.pbix` opens cleanly in Power BI Desktop with live visuals.*

**Milestone 3 — Measures & layout (weeks 6–9):** DAX measure generation (sum/avg/count, YoY, running total) with execution validation; NL layout mapping to a 12-column grid; visual-level filters and slicers. *Exit: the "revenue dashboard" scenario from the Overview works end-to-end.*

**Milestone 4 — Conversational iteration + web UI (weeks 9–12):** chat UI with live spec/layout preview; patch-based edits; SQL source support; Fabric workspace publish option. *Exit: a business user builds and refines a 3-visual report in under 10 minutes without touching Desktop.*

**Deferred post-MVP:** custom visuals, bookmarks/drill-through, multi-page reports, RLS, theme/branding control, DirectQuery sources.
