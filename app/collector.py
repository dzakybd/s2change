"""Per-request collector for what the UI should render (map actions, charts, tables).

A contextvar holds one Collector per chat request, so concurrent requests never share state. Tools append to it and
the endpoint returns it next to the answer text. Inputs are validated here because they come from the model.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field

MAX_IDS, MAX_POINTS, MAX_ROWS, MAX_ITEMS = 500, 200, 100, 6
# Layer ids of the viewer's layer menu (viewer/src/map/layers.ts).
LAYERS = ("types", "after", "before", "intensity", "facilities")


@dataclass
class Collector:
    actions: list[dict] = field(default_factory=list)
    charts: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)

    def add_action(self, type: str, bbox=None, run_id=None, ids=None, **kw) -> dict:
        if len(self.actions) >= MAX_ITEMS:
            raise ValueError("Too many map actions in one answer.")
        if type == "fly_to":
            box = [float(v) for v in bbox or []]
            if len(box) != 4 or not (-180 <= box[0] < box[2] <= 180 and -90 <= box[1] < box[3] <= 90):
                raise ValueError("fly_to needs bbox=[west, south, east, north] in lon/lat degrees (west < east, south < north).")
            action = {"type": type, "bbox": [round(v, 5) for v in box]}
        elif type == "highlight":
            clean = sorted({int(i) for i in ids or []})[:MAX_IDS]
            if not run_id or not clean:
                raise ValueError("highlight needs run_id and a non-empty list of integer ids.")
            action = {"type": type, "run_id": str(run_id), "ids": clean}
        elif type == "show_run":
            if not run_id:
                raise ValueError("show_run needs run_id.")
            action = {"type": type, "run_id": str(run_id)}
        elif type == "set_layer":
            if kw.get("layer") not in LAYERS:
                raise ValueError(f"set_layer needs layer, one of {', '.join(LAYERS)}.")
            action = {"type": type, "layer": kw["layer"]}
        elif type == "set_view":
            if kw.get("swipe") is None:
                raise ValueError("set_view needs swipe (true or false).")
            action = {"type": type, "swipe": bool(kw["swipe"])}
        elif type == "show_facility":
            if not kw.get("facility_id"):
                raise ValueError("show_facility needs facility_id.")
            action = {"type": type, "facility_id": str(kw["facility_id"])}
        elif type == "compare_dates":
            if not kw.get("before") or not kw.get("after"):
                raise ValueError("compare_dates needs before and after.")
            action = {"type": type, "before": str(kw["before"]), "after": str(kw["after"])}
        else:
            types = "fly_to, highlight, show_run, set_layer, set_view, show_facility, compare_dates"
            raise ValueError(f"type must be one of {types}.")
        self.actions.append(action)
        return action

    def add_chart(self, type: str, title: str, x: list, series: list[dict]) -> dict:
        if type not in ("bar", "line"):
            raise ValueError("chart type must be 'bar' or 'line'.")
        if len(self.charts) >= MAX_ITEMS or not x or not series:
            raise ValueError("A chart needs x values and at least one series (at most 6 charts per answer).")
        x = list(x)[:MAX_POINTS]
        out = []
        for s in series:
            y = [None if v is None else float(v) for v in list(s["y"])[: len(x)]]
            if len(y) != len(x):
                raise ValueError(f"Series '{s['name']}' has {len(y)} values but x has {len(x)}.")
            out.append({"name": str(s["name"]), "y": y})
        chart = {"type": type, "title": str(title), "x": x, "series": out}
        self.charts.append(chart)
        return chart

    def add_table(self, title: str, columns: list[str], rows: list[list]) -> dict:
        if len(self.tables) >= MAX_ITEMS or not columns:
            raise ValueError("A table needs columns (at most 6 tables per answer).")
        if any(len(r) != len(columns) for r in rows):
            raise ValueError("Every row must have one value per column.")
        table = {"title": str(title), "columns": [str(c) for c in columns], "rows": [list(r) for r in rows[:MAX_ROWS]]}
        self.tables.append(table)
        return table


_current: ContextVar[Collector | None] = ContextVar("s2cd_collector", default=None)


def start() -> Collector:
    c = Collector()
    _current.set(c)
    return c


def current() -> Collector:
    c = _current.get()
    if c is None:
        raise RuntimeError("No active request collector")
    return c
