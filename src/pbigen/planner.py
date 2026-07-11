"""Natural-language planning.

:class:`RuleBasedPlanner` deterministically converts a plain-English report
request into a :class:`~pbigen.spec.ReportSpec`. It is fully offline — no LLM
calls — and is intentionally interchangeable: implement the :class:`Planner`
protocol (e.g. with an LLM producing spec JSON validated against
``report_spec_json_schema()``) and register it to swap the front end without
touching validation, layout, or emission.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field as dc_field
from typing import Protocol, Sequence, runtime_checkable

from . import dax
from .layout import apply_layout
from .schema_source import NUMERIC_TYPES, ColumnType, DatasetSchema
from .spec import (
    Aggregation,
    DataRole,
    DateGrain,
    FieldKind,
    FieldRef,
    Filter,
    FilterOperator,
    Measure,
    Page,
    ReportSpec,
    Visual,
    VisualFields,
    VisualType,
    report_spec_json_schema,
)

DEFAULT_TABLE = "Data"

# ---------------------------------------------------------------------------
# Planner protocol (LLM extension point)
# ---------------------------------------------------------------------------


@runtime_checkable
class Planner(Protocol):
    def plan(
        self,
        request: str,
        schema: DatasetSchema | None = None,
        name: str = "Generated Report",
    ) -> ReportSpec: ...


_PLANNERS: dict[str, type] = {}


def register_planner(key: str, planner_cls: type) -> None:
    _PLANNERS[key] = planner_cls


def get_planner(key: str = "rules") -> Planner:
    try:
        return _PLANNERS[key]()
    except KeyError:
        raise KeyError(
            f"Unknown planner {key!r}. Registered: {sorted(_PLANNERS)}"
        ) from None


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# Ordered longest-first so e.g. "pie chart" wins over "pie", "kpi card" over "card".
_VISUAL_PHRASES: list[tuple[str, VisualType]] = [
    ("stacked bar chart", VisualType.bar_chart),
    ("horizontal bar chart", VisualType.bar_chart),
    ("bar chart", VisualType.bar_chart),
    ("bar graph", VisualType.bar_chart),
    ("column chart", VisualType.column_chart),
    ("column graph", VisualType.column_chart),
    ("line chart", VisualType.line_chart),
    ("line graph", VisualType.line_chart),
    ("trend chart", VisualType.line_chart),
    ("trend line", VisualType.line_chart),
    ("area chart", VisualType.area_chart),
    ("donut chart", VisualType.donut_chart),
    ("donut", VisualType.donut_chart),
    ("pie chart", VisualType.pie_chart),
    ("pie", VisualType.pie_chart),
    ("pivot table", VisualType.matrix),
    ("matrix", VisualType.matrix),
    ("kpi cards", VisualType.card),
    ("kpi card", VisualType.card),
    ("kpis", VisualType.card),
    ("kpi", VisualType.card),
    ("cards", VisualType.card),
    ("card", VisualType.card),
    ("slicers", VisualType.slicer),
    ("slicer", VisualType.slicer),
    ("tables", VisualType.table),
    ("table", VisualType.table),
]

_VISUAL_RE = re.compile(
    "|".join(rf"\b{re.escape(p)}\b" for p, _ in _VISUAL_PHRASES), re.IGNORECASE
)

_GRAINS = {
    "daily": DateGrain.day,
    "weekly": DateGrain.week,
    "monthly": DateGrain.month,
    "quarterly": DateGrain.quarter,
    "yearly": DateGrain.year,
    "annual": DateGrain.year,
}

_GRAIN_COLUMN = {
    DateGrain.year: "Year",
    DateGrain.quarter: "Quarter",
    DateGrain.month: "Month",
    DateGrain.week: "Week",
    DateGrain.day: "Date",
}

_FILLERS = {
    "show", "me", "a", "an", "the", "as", "add", "display", "include",
    "please", "big", "large", "small", "new", "create", "make", "build",
    "i", "want", "need", "with", "us", "our", "my", "also", "then", "chart",
    "graph", "visual", "each",
}

_HINT_PHRASES: list[tuple[str, str]] = [
    ("across the top", "top"),
    ("at the top", "top"),
    ("on top", "top"),
    ("on the left", "left"),
    ("to the left", "left"),
    ("on the right", "right"),
    ("to the right", "right"),
    ("at the bottom", "bottom"),
    ("on the bottom", "bottom"),
    ("below", "bottom"),
    ("full width", "wide"),
    ("full-width", "wide"),
]

# Words that map "sales"-style vocabulary onto common column names.
_SYNONYMS: dict[str, list[str]] = {
    "sales": ["revenue", "salesamount", "amount", "salestotal", "total"],
    "revenue": ["salesamount", "amount", "sales"],
    "income": ["revenue", "amount"],
    "customers": ["customerid", "customername", "customer"],
    "orders": ["orderid", "ordernumber", "order"],
    "products": ["productname", "product"],
    "date": ["orderdate", "invoicedate", "saledate", "transactiondate"],
    "quantity": ["qty", "units", "unitssold"],
    "profit": ["margin", "grossprofit"],
}

_YEAR_FILTER_RE = re.compile(
    r"\b(?:filtered? (?:to|for|on|by)|only(?: for| in)?|for the year|in|during)\s+"
    r"((?:19|20)\d{2})(?:\s*(?:-|to|through)\s*((?:19|20)\d{2}))?\b",
    re.IGNORECASE,
)

_EXCLUDE_RE = re.compile(
    r"\b(?:excluding|exclude|without)\s+([\w ]+?)(?=,|\.|;|\band\b|$)",
    re.IGNORECASE,
)

_TOP_N_RE = re.compile(
    r"\btop\s+(\d+)\s+([\w ]+?)\s+by\s+([\w ]+?)(?=,|\.|;|$)",
    re.IGNORECASE,
)

_BY_RE = re.compile(
    r"([\w %#&'./-]+?)\s+by\s+([\w ,&'./-]+)",
    re.IGNORECASE,
)


def _normalize(token: str) -> str:
    return re.sub(r"[^a-z0-9]", "", token.lower())


def _singular(token: str) -> str:
    if len(token) > 3 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _clean_words(text: str, extra_fillers: set[str] | None = None) -> str:
    fillers = _FILLERS | (extra_fillers or set())
    words = re.findall(r"[\w'&%#./-]+", text)
    kept = [w for w in words if w.lower() not in fillers]
    # Strip leading/trailing connective words that survived
    while kept and kept[0].lower() in ("of", "for", "and", "by", "in", "on"):
        kept.pop(0)
    while kept and kept[-1].lower() in ("of", "for", "and", "by", "in", "on"):
        kept.pop()
    return " ".join(kept)


# ---------------------------------------------------------------------------
# Field resolution against the dataset schema
# ---------------------------------------------------------------------------


class FieldResolver:
    def __init__(self, schema: DatasetSchema | None):
        self.schema = schema
        self.default_table = schema.tables[0].name if schema and schema.tables else DEFAULT_TABLE
        self._index: dict[str, tuple[str, str, ColumnType]] = {}
        if schema:
            for t, c in schema.iter_columns():
                for key in {_normalize(c.name), _singular(_normalize(c.name))}:
                    self._index.setdefault(key, (t.name, c.name, c.data_type))

    def _lookup(self, key: str) -> tuple[str, str, ColumnType] | None:
        for candidate in (key, _singular(key)):
            if candidate in self._index:
                return self._index[candidate]
        for candidate in _SYNONYMS.get(key, []) + _SYNONYMS.get(_singular(key), []):
            if candidate in self._index:
                return self._index[candidate]
        # substring containment either way (e.g. "value" -> "OrderValue")
        for idx_key, entry in sorted(self._index.items()):
            if len(key) >= 4 and (key in idx_key or idx_key in key):
                return entry
        return None

    def resolve(self, token: str) -> tuple[FieldRef, ColumnType | None]:
        """Resolve a NL token to a column ref; falls back to a titled guess."""
        token = token.strip()
        key = _normalize(token)
        if key:
            entry = self._lookup(key)
            if entry:
                table, column, dtype = entry
                return FieldRef(kind=FieldKind.column, table=table, name=column), dtype
        guess = " ".join(w.capitalize() for w in re.split(r"[\s_]+", token) if w)
        return (
            FieldRef(kind=FieldKind.column, table=self.default_table, name=guess or "Value"),
            None,
        )

    def default_numeric(self) -> tuple[FieldRef, ColumnType | None]:
        if self.schema:
            found = self.schema.first_numeric_column()
            if found:
                t, c = found
                return FieldRef(kind=FieldKind.column, table=t.name, name=c.name), c.data_type
        return FieldRef(kind=FieldKind.column, table=self.default_table, name="Revenue"), None

    def is_numeric(self, token: str, dtype: ColumnType | None) -> bool:
        if dtype is not None:
            return dtype in NUMERIC_TYPES
        return _singular(_normalize(token)) in {
            "revenue", "sale", "amount", "profit", "quantity", "price",
            "cost", "total", "margin", "unit", "income", "value",
        }


# ---------------------------------------------------------------------------
# Rule-based planner
# ---------------------------------------------------------------------------


@dataclass
class _PlanContext:
    resolver: FieldResolver
    measures: dict[str, Measure] = dc_field(default_factory=dict)
    needs_date_table: bool = False
    visual_seq: int = 0

    def add_measure(self, measure: Measure) -> Measure:
        return self.measures.setdefault(measure.name, measure)

    def next_visual_id(self) -> str:
        self.visual_seq += 1
        return f"visual-{self.visual_seq:03d}"


class RuleBasedPlanner:
    """Deterministic keyword/pattern planner. Same input -> same spec."""

    def plan(
        self,
        request: str,
        schema: DatasetSchema | None = None,
        name: str = "Generated Report",
    ) -> ReportSpec:
        resolver = FieldResolver(schema)
        ctx = _PlanContext(resolver=resolver)

        report_filters, text = self._extract_report_filters(request, ctx)

        visuals: list[Visual] = []
        for clause in split_clauses(text):
            parsed = self._parse_clause(clause, ctx)
            if isinstance(parsed, Visual):
                visuals.append(parsed)
            elif isinstance(parsed, list):
                report_filters.extend(parsed)

        if not visuals:
            fallback = self._fallback_visual(text, ctx)
            if fallback:
                visuals.append(fallback)

        page_height = apply_layout(visuals)
        page = Page(name="page1", display_name="Page 1", height=page_height, visuals=visuals)
        return ReportSpec(
            name=name,
            pages=[page],
            measures=list(ctx.measures.values()),
            filters=report_filters,
        )

    # -- report-level filters -------------------------------------------------

    def _extract_report_filters(
        self, text: str, ctx: _PlanContext
    ) -> tuple[list[Filter], str]:
        filters: list[Filter] = []

        def year_repl(m: re.Match) -> str:
            start, end = m.group(1), m.group(2)
            ctx.needs_date_table = True
            year_field = FieldRef(kind=FieldKind.column, table=dax.DATE_TABLE, name="Year")
            if end:
                filters.append(
                    Filter(
                        field=year_field,
                        operator=FilterOperator.between,
                        values=[int(start), int(end)],
                    )
                )
            else:
                filters.append(
                    Filter(field=year_field, operator=FilterOperator.in_, values=[int(start)])
                )
            return " "

        text = _YEAR_FILTER_RE.sub(year_repl, text)

        def exclude_repl(m: re.Match) -> str:
            token = m.group(1).strip()
            ref, dtype = ctx.resolver.resolve(token)
            if dtype is not None:
                # Token names a real column (e.g. a "Returns" flag): exclude truthy rows.
                filters.append(
                    Filter(field=ref, operator=FilterOperator.not_in, values=[True])
                )
            else:
                # Treat as a categorical value on the guessed column.
                filters.append(
                    Filter(
                        field=ref,
                        operator=FilterOperator.not_in,
                        values=[" ".join(w.capitalize() for w in token.split())],
                    )
                )
            return " "

        text = _EXCLUDE_RE.sub(exclude_repl, text)
        return filters, text

    def _parse_top_n(self, clause: str, ctx: _PlanContext) -> tuple[list[Filter], str]:
        filters: list[Filter] = []

        def repl(m: re.Match) -> str:
            n = int(m.group(1))
            target_ref, _ = ctx.resolver.resolve(_clean_words(m.group(2)))
            order_token = _clean_words(m.group(3))
            order_ref, order_dtype = ctx.resolver.resolve(order_token)
            order_measure = ctx.add_measure(
                dax.base_measure(Aggregation.sum, order_ref, order_dtype)
            )
            filters.append(
                Filter(
                    field=target_ref,
                    operator=FilterOperator.top_n,
                    top_n=n,
                    order_by=FieldRef(kind=FieldKind.measure, name=order_measure.name),
                )
            )
            return " "

        cleaned = _TOP_N_RE.sub(repl, clause)
        return filters, cleaned

    # -- clause parsing --------------------------------------------------------

    def _parse_clause(self, clause: str, ctx: _PlanContext) -> Visual | list[Filter] | None:
        vt = _match_visual_type(clause)
        top_filters, clause = self._parse_top_n(clause, ctx)
        if vt is None:
            # A clause with no visual but a top-N pattern is a report-level filter.
            return top_filters or None

        hint = _match_hint(clause)
        rest = _VISUAL_RE.sub(" ", clause, count=1)

        if vt == VisualType.slicer:
            visual = self._build_slicer(clause, rest, ctx)
        elif vt == VisualType.card:
            visual = self._build_card(rest, ctx)
        elif vt in (VisualType.table, VisualType.matrix):
            visual = self._build_table(vt, rest, ctx)
        else:
            visual = self._build_chart(vt, rest, ctx)

        visual.layout_hint = hint
        visual.filters.extend(top_filters)
        return visual

    def _value_role(
        self, token: str, ctx: _PlanContext
    ) -> tuple[DataRole, Measure]:
        """Turn a value phrase into a measure-backed role, registering the DAX."""
        lowered = token.lower().strip()
        agg = Aggregation.sum
        if re.match(r"^(average|avg|mean)\b", lowered):
            agg = Aggregation.average
            lowered = re.sub(r"^(average|avg|mean)\b", "", lowered)
        elif re.match(r"^(distinct|unique)\b", lowered):
            agg = Aggregation.distinct_count
            lowered = re.sub(r"^(distinct|unique)(\s+count\s+of)?\b", "", lowered)
        elif re.match(r"^(count of|number of|count)\b", lowered):
            agg = Aggregation.count
            lowered = re.sub(r"^(count of|number of|count)\b", "", lowered)
        elif re.match(r"^(total|sum of|sum)\b", lowered):
            lowered = re.sub(r"^(total|sum of|sum)\b", "", lowered)
        elif re.match(r"^(max|maximum|highest)\b", lowered):
            agg = Aggregation.max
            lowered = re.sub(r"^(max|maximum|highest)\b", "", lowered)
        elif re.match(r"^(min|minimum|lowest)\b", lowered):
            agg = Aggregation.min
            lowered = re.sub(r"^(min|minimum|lowest)\b", "", lowered)

        base_token = _clean_words(lowered) or "Value"
        ref, dtype = ctx.resolver.resolve(base_token)
        if agg == Aggregation.sum and dtype is not None and dtype not in NUMERIC_TYPES:
            agg = Aggregation.count
        measure = ctx.add_measure(dax.base_measure(agg, ref, dtype))
        role = DataRole(field=FieldRef(kind=FieldKind.measure, name=measure.name))
        return role, measure

    def _build_chart(self, vt: VisualType, rest: str, ctx: _PlanContext) -> Visual:
        grain: DateGrain | None = None
        for word, g in _GRAINS.items():
            if re.search(rf"\b{word}\b", rest, re.IGNORECASE):
                grain = g
                rest = re.sub(rf"\b{word}\b", " ", rest, flags=re.IGNORECASE)
                break

        cleaned = _clean_words(rest, extra_fillers={"of", "showing", "for"})
        value_token = cleaned
        category_role: DataRole | None = None
        legend_role: DataRole | None = None

        m = _BY_RE.search(cleaned)
        if m:
            value_token = m.group(1)
            by_parts = [p for p in re.split(r",\s*|\s+and\s+", m.group(2)) if p.strip()]
            dims = []
            for part in by_parts[:2]:
                part_clean = _clean_words(part)
                if not part_clean:
                    continue
                if part_clean.lower() in _GRAINS or _normalize(part_clean) in (
                    "month", "year", "quarter", "week", "day", "date",
                ):
                    grain = grain or _grain_from_word(part_clean)
                    continue
                ref, _ = ctx.resolver.resolve(part_clean)
                dims.append(DataRole(field=ref))
            if dims:
                category_role = dims[0]
            if len(dims) > 1:
                legend_role = dims[1]

        values: list[DataRole] = []
        value_names: list[str] = []
        for piece in re.split(r",\s*|\s+and\s+", value_token):
            piece = piece.strip()
            if not piece or not _clean_words(piece):
                continue
            role, measure = self._value_role(piece, ctx)
            values.append(role)
            value_names.append(measure.name)
        if not values:
            role, measure = self._value_role("", ctx)
            values.append(role)
            value_names.append(measure.name)

        date_role: DataRole | None = None
        if grain:
            ctx.needs_date_table = True
            date_role = DataRole(
                field=FieldRef(
                    kind=FieldKind.column,
                    table=dax.DATE_TABLE,
                    name=_GRAIN_COLUMN[grain],
                ),
                date_grain=grain,
            )

        if vt in (VisualType.line_chart, VisualType.area_chart):
            # Time on the axis; an explicit dimension becomes the legend.
            if date_role is not None:
                legend_role = legend_role or category_role
                category_role = date_role
        else:
            category_role = category_role or date_role

        title_parts = [" & ".join(value_names)]
        if category_role:
            title_parts.append(f"by {category_role.label()}")
        title = " ".join(title_parts)

        return Visual(
            id=ctx.next_visual_id(),
            type=vt,
            title=title,
            fields=VisualFields(category=category_role, legend=legend_role, values=values),
        )

    def _build_card(self, rest: str, ctx: _PlanContext) -> Visual:
        lowered = rest.lower()
        measure: Measure
        if re.search(r"\byoy\b|\byear[- ]over[- ]year\b", lowered):
            base_token = _clean_words(
                re.sub(r"\byoy\b|\byear[- ]over[- ]year\b|\bgrowth\b|\bchange\b", " ", lowered),
                extra_fillers={"of", "for", "showing"},
            )
            base_ref, dtype = (
                ctx.resolver.resolve(base_token) if base_token else ctx.resolver.default_numeric()
            )
            base = ctx.add_measure(dax.base_measure(Aggregation.sum, base_ref, dtype))
            measure = ctx.add_measure(dax.yoy_measure(base, base_ref.name))
            ctx.needs_date_table = True
        elif re.search(r"\brunning total\b|\bcumulative\b", lowered):
            base_token = _clean_words(
                re.sub(r"\brunning total\b|\bcumulative\b", " ", lowered),
                extra_fillers={"of", "for", "showing"},
            )
            base_ref, dtype = (
                ctx.resolver.resolve(base_token) if base_token else ctx.resolver.default_numeric()
            )
            base = ctx.add_measure(dax.base_measure(Aggregation.sum, base_ref, dtype))
            measure = ctx.add_measure(dax.running_total_measure(base, base_ref.name))
            ctx.needs_date_table = True
        elif re.search(r"\b(%|percent(age)?) of total\b|\bshare of\b", lowered):
            base_token = _clean_words(
                re.sub(r"\b(%|percent(age)?) of total\b|\bshare of\b", " ", lowered),
                extra_fillers={"of", "for", "showing"},
            )
            base_ref, dtype = (
                ctx.resolver.resolve(base_token) if base_token else ctx.resolver.default_numeric()
            )
            base = ctx.add_measure(dax.base_measure(Aggregation.sum, base_ref, dtype))
            measure = ctx.add_measure(dax.pct_of_total_measure(base, base_ref.name))
        else:
            token = _clean_words(rest, extra_fillers={"of", "for", "showing"})
            if token:
                _, measure = self._value_role(token, ctx)
            else:
                base_ref, dtype = ctx.resolver.default_numeric()
                measure = ctx.add_measure(dax.base_measure(Aggregation.sum, base_ref, dtype))

        role = DataRole(field=FieldRef(kind=FieldKind.measure, name=measure.name))
        return Visual(
            id=ctx.next_visual_id(),
            type=VisualType.card,
            title=measure.name,
            fields=VisualFields(values=[role]),
        )

    def _build_slicer(self, clause: str, rest: str, ctx: _PlanContext) -> Visual:
        m = re.search(r"slicers?\s+(?:for|on|by|of)\s+([\w ]+)", clause, re.IGNORECASE)
        if not m:
            m = re.search(r"([\w ]+?)\s+slicers?\b", clause, re.IGNORECASE)
        token = _clean_words(m.group(1)) if m else _clean_words(rest, extra_fillers={"for", "on"})
        ref, _ = ctx.resolver.resolve(token or "Category")
        role = DataRole(field=ref)
        return Visual(
            id=ctx.next_visual_id(),
            type=VisualType.slicer,
            title=f"{ref.name} Slicer",
            fields=VisualFields(values=[role]),
        )

    def _build_table(self, vt: VisualType, rest: str, ctx: _PlanContext) -> Visual:
        m = re.search(r"\b(?:of|with|showing|listing|containing|for)\s+(.+)$", rest, re.IGNORECASE)
        items_text = m.group(1) if m else rest

        values: list[DataRole] = []
        seen: set[str] = set()

        by_match = _BY_RE.search(items_text)
        if by_match:
            items_text = f"{by_match.group(2)}, {by_match.group(1)}"

        for piece in re.split(r",\s*|\s+and\s+", items_text):
            token = _clean_words(piece, extra_fillers={"of", "showing"})
            if not token:
                continue
            has_agg_word = bool(
                re.match(r"^(average|avg|mean|count|number|total|sum|distinct|unique|min|max)\b",
                         piece.strip().lower())
            )
            ref, dtype = ctx.resolver.resolve(token)
            if has_agg_word or ctx.resolver.is_numeric(token, dtype):
                role, _ = self._value_role(piece.strip(), ctx)
            else:
                role = DataRole(field=ref)
            if role.field.qualified() in seen:
                continue
            seen.add(role.field.qualified())
            values.append(role)

        if not values and ctx.resolver.schema:
            table0 = ctx.resolver.schema.tables[0]
            for col in table0.columns[:6]:
                ref = FieldRef(kind=FieldKind.column, table=table0.name, name=col.name)
                if col.data_type in NUMERIC_TYPES:
                    measure = ctx.add_measure(dax.base_measure(Aggregation.sum, ref, col.data_type))
                    values.append(
                        DataRole(field=FieldRef(kind=FieldKind.measure, name=measure.name))
                    )
                else:
                    values.append(DataRole(field=ref))

        title = "Details" if vt == VisualType.table else "Matrix"
        return Visual(
            id=ctx.next_visual_id(),
            type=vt,
            title=title,
            fields=VisualFields(values=values),
        )

    def _fallback_visual(self, text: str, ctx: _PlanContext) -> Visual | None:
        """No visual keyword anywhere: default to a chart ('x by y') or table."""
        if not text.strip():
            return None
        if _BY_RE.search(text):
            return self._build_chart(VisualType.column_chart, text, ctx)
        return self._build_table(VisualType.table, text, ctx)


def _grain_from_word(word: str) -> DateGrain | None:
    key = _singular(_normalize(word))
    return {
        "month": DateGrain.month,
        "year": DateGrain.year,
        "quarter": DateGrain.quarter,
        "week": DateGrain.week,
        "day": DateGrain.day,
        "date": DateGrain.day,
    }.get(key) or _GRAINS.get(word.lower())


def _match_visual_type(clause: str) -> VisualType | None:
    m = _VISUAL_RE.search(clause)
    if not m:
        return None
    matched = m.group(0).lower()
    for phrase, vt in _VISUAL_PHRASES:
        if phrase == matched:
            return vt
    return None


def _match_hint(clause: str) -> str | None:
    lowered = clause.lower()
    for phrase, hint in _HINT_PHRASES:
        if phrase in lowered:
            return hint
    return None


def split_clauses(text: str) -> list[str]:
    """Split a request into one clause per visual mention.

    Sentences are split first; within a sentence, if several visual keywords
    appear, the text is cut at the delimiter (comma / "and" / "plus" / "with")
    closest before each subsequent keyword.
    """
    clauses: list[str] = []
    for sentence in re.split(r"(?<=[.;!?])\s+|\n+", text):
        sentence = sentence.strip().strip(".;!?")
        if not sentence:
            continue
        matches = list(_VISUAL_RE.finditer(sentence))
        if len(matches) <= 1:
            clauses.append(sentence)
            continue
        cuts = [0]
        delim_re = re.compile(r",\s*(?:and\s+|plus\s+)?|\s+and\s+|\s+plus\s+|\s+with\s+")
        for m in matches[1:]:
            window = sentence[cuts[-1]: m.start()]
            delims = list(delim_re.finditer(window))
            if delims:
                cuts.append(cuts[-1] + delims[-1].end())
            else:
                cuts.append(m.start())
        cuts.append(len(sentence))
        for i in range(len(cuts) - 1):
            piece = sentence[cuts[i]: cuts[i + 1]].strip(" ,;")
            if piece:
                clauses.append(piece)
    return clauses


# ---------------------------------------------------------------------------
# LLM-backed planner
# ---------------------------------------------------------------------------


def _coerce_json_payload(text: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError("LLM returned empty output")
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            candidate = text[start : end + 1]
            json.loads(candidate)
            return candidate
        raise


class LLMPlanner:
    """Planner that delegates report-spec generation to an LLM CLI.

    The default backend is Claude with the fable model, invoked through the
    `claude` command-line tool in print mode. The planner expects the model to
    return a ReportSpec JSON document.
    """

    def __init__(
        self,
        command: Sequence[str] | None = None,
    ) -> None:
        if command is None:
            command = os.environ.get("PBIGEN_LLM_COMMAND", "claude --model fable").split()
        self.command = list(command)

    def _build_prompt(self, request: str, schema: DatasetSchema | None, name: str) -> str:
        schema_payload = schema.model_dump(mode="json") if schema is not None else None
        return (
            "You are the LLM planner for a Power BI report generator. "
            "Return ONLY valid JSON that matches the ReportSpec schema. "
            "Do not wrap it in markdown or commentary. "
            "Use the provided dataset schema if present and keep field names exact. "
            "Leave layout coordinates to the host; set visuals, measures, and filters.\n\n"
            f"Report name: {name}\n"
            f"User request: {request}\n"
            f"Dataset schema JSON: {json.dumps(schema_payload, ensure_ascii=False)}\n"
            f"ReportSpec schema JSON: {json.dumps(report_spec_json_schema(), ensure_ascii=False)}"
        )

    def plan(
        self,
        request: str,
        schema: DatasetSchema | None = None,
        name: str = "Generated Report",
    ) -> ReportSpec:
        prompt = self._build_prompt(request, schema, name)
        command = [*self.command, "--print", "--json-schema", json.dumps(report_spec_json_schema()), prompt]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        payload = _coerce_json_payload(result.stdout)
        spec = ReportSpec.model_validate_json(payload)
        if name and spec.name in {"Generated Report", "Report"}:
            spec.name = name
        for page in spec.pages:
            page.height = max(page.height, apply_layout(page.visuals))
        return spec


register_planner("rules", RuleBasedPlanner)
register_planner("llm", LLMPlanner)
