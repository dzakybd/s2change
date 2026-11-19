"""Analyst app tests: SQL guard, collector, API with a fake agent. No network, no LLM, no database."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("deepagents")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from app import agent as agent_mod  # noqa: E402
from app import collector, main, tools  # noqa: E402
from app.guard import SqlRejected, guard_sql  # noqa: E402

# --- SQL guard ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM change_features",
        "select likely_cause, sum(area_m2) / 1e4 from change_features f join change_context c using (id) group by 1",
        "WITH t AS (SELECT id FROM change_features) SELECT * FROM t",
        "SELECT ST_Area(geometry), ST_AsText(ST_Transform(geometry, 4326)) FROM change_features WHERE area_m2 > 1000",
        "SELECT * FROM timeline_months ORDER BY month",
        "SELECT run_id FROM change_runs WHERE run_id LIKE '%cva%'",
    ],
)
def test_guard_accepts(sql):
    assert guard_sql(sql).upper().startswith(("SELECT", "WITH"))


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE change_features",
        "DELETE FROM change_runs",
        "INSERT INTO change_runs (run_id) VALUES ('x')",
        "UPDATE change_features SET area_m2 = 0",
        "SELECT 1; DROP TABLE change_features",
        "SELECT * FROM change_features; SELECT 1",
        "SELECT * INTO newtable FROM change_features",
        "SELECT * FROM pg_shadow",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM spatial_ref_sys",
        "SELECT pg_sleep(10)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT * FROM change_features FOR UPDATE",
        "WITH x AS (DELETE FROM change_runs RETURNING *) SELECT * FROM x",
        "SELECT * FROM public.change_features",
        "SELECT FROM WHERE",
        "",
        "COPY change_features TO '/tmp/x'",
    ],
)
def test_guard_rejects(sql):
    with pytest.raises(SqlRejected):
        guard_sql(sql)


def test_guard_caps_rows():
    assert guard_sql("SELECT id FROM change_features").upper().endswith("LIMIT 200")
    assert guard_sql("SELECT id FROM change_features LIMIT 5").upper().endswith("LIMIT 5")
    assert guard_sql("SELECT id FROM change_features LIMIT 100000").upper().endswith("LIMIT 200")


def test_readonly_sql_tool_reports_rejection_as_json():
    out = json.loads(tools.readonly_sql.invoke({"query": "DROP TABLE change_features"}))
    assert "error" in out and "SELECT" in out["error"]


# --- collector ----------------------------------------------------------------------------------------------------


def test_collector_validates_actions():
    c = collector.Collector()
    assert c.add_action("fly_to", bbox=[25.8, -12.3, 25.9, -12.2])["bbox"] == [25.8, -12.3, 25.9, -12.2]
    assert c.add_action("highlight", run_id="r", ids=[3, 1, 3])["ids"] == [1, 3]
    assert c.add_action("show_run", run_id="r") == {"type": "show_run", "run_id": "r"}
    assert c.add_action("set_month", month="2024-05")["month"] == "2024-05"
    for bad in (
        {"type": "fly_to", "bbox": [1, 2, 3]},
        {"type": "fly_to", "bbox": [30, -12, 25, -11]},
        {"type": "highlight", "run_id": "r", "ids": []},
        {"type": "set_month", "month": "2024-13"},
        {"type": "teleport"},
    ):
        with pytest.raises(ValueError):
            c.add_action(**bad)
    assert len(c.actions) == 4


def test_collector_charts_and_tables():
    c = collector.Collector()
    assert c.add_chart("bar", "t", ["a", "b"], [{"name": "s", "y": [1, 2]}])["series"][0]["y"] == [1.0, 2.0]
    with pytest.raises(ValueError):
        c.add_chart("pie", "t", ["a"], [{"name": "s", "y": [1]}])
    with pytest.raises(ValueError):
        c.add_chart("line", "t", ["a", "b"], [{"name": "s", "y": [1]}])
    c.add_table("t", ["a", "b"], [[1, "x"]])
    with pytest.raises(ValueError):
        c.add_table("t", ["a", "b"], [[1]])


def test_collector_is_per_context_not_global():
    with pytest.raises(RuntimeError):
        collector.current()
    seen = {}

    def worker(name: str):
        c = collector.start()
        tools.make_table.invoke({"title": name, "columns": ["a"], "rows": [[name]]})
        seen[name] = [t["title"] for t in collector.current().tables] == [name] and c.tables[0]["rows"] == [[name]]

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("one", "two")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert seen == {"one": True, "two": True}


def test_chart_tool_appends_and_reports_errors():
    c = collector.start()
    ok = json.loads(
        tools.make_chart.invoke({"type": "bar", "title": "T", "x": ["a", "b"], "series": [{"name": "s", "y": [1, 2]}]})
    )
    assert ok["ok"] and len(c.charts) == 1
    bad = json.loads(tools.make_chart.invoke({"type": "bar", "title": "T", "x": ["a", "b"], "series": [{"name": "s", "y": [1]}]}))
    assert "error" in bad and len(c.charts) == 1


# --- API ----------------------------------------------------------------------------------------------------------


class FakeAgent:
    def __init__(self):
        self.seen: list = []

    def invoke(self, inputs, config=None):
        self.seen.append(inputs["messages"])
        collector.current().add_action("show_run", run_id="r1")
        call = {"name": "list_runs", "args": {}, "id": "1", "type": "tool_call"}
        return {"messages": [HumanMessage("q"), AIMessage("", tool_calls=[call]), AIMessage("**Done**: 1,345 ha")]}


@pytest.fixture
def client(monkeypatch):
    fake = FakeAgent()
    monkeypatch.setattr(agent_mod, "get_agent", lambda: fake)
    monkeypatch.setattr(main, "daily", main.DailyCap(300))
    monkeypatch.setattr(main, "RATE", "6/minute")
    main.limiter.reset()
    c = TestClient(main.app)
    c.fake = fake
    return c


def chat(client, text="hello", ip="1.1.1.1", **kw):
    return client.post(
        "/api/agent/chat", json={"messages": [{"role": "user", "content": text}]}, headers={"X-Forwarded-For": ip}, **kw
    )


def test_health(client, monkeypatch):
    monkeypatch.setattr(main.db, "ping", lambda: False)
    monkeypatch.setenv("GEMINI_MODEL", "m-test")
    assert client.get("/api/health").json() == {"ok": True, "db": False, "model": "m-test"}


def test_chat_contract(client):
    r = chat(client)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"answer", "actions", "charts", "tables", "tool_calls"}
    assert body["answer"] == "**Done**: 1,345 ha"
    assert body["actions"] == [{"type": "show_run", "run_id": "r1"}]
    assert body["tool_calls"] == [{"name": "list_runs", "args": {}}]


def test_chat_limits_input(client):
    assert chat(client, "x" * 2001).status_code == 422
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(24)] + [
        {"role": "user", "content": "last"}
    ]
    assert client.post("/api/agent/chat", json={"messages": history}).status_code == 200
    sent = client.fake.seen[-1]
    assert len(sent) == 10 and sent[-1]["content"] == "last"
    assert client.post("/api/agent/chat", json={"messages": [{"role": "assistant", "content": "hi"}]}).status_code == 422
    assert client.post("/api/agent/chat", json={"messages": []}).status_code == 422


def test_rate_limit_per_ip(client):
    assert [chat(client, ip="2.2.2.2").status_code for _ in range(6)] == [200] * 6
    blocked = chat(client, ip="2.2.2.2")
    assert blocked.status_code == 429 and "detail" in blocked.json()
    assert chat(client, ip="3.3.3.3").status_code == 200  # another client is unaffected
    assert chat(client, ip="9.9.9.9, 4.4.4.4").status_code == 200  # keyed on the last (proxy-appended) entry
    assert chat(client, ip="8.8.8.8, 4.4.4.4").status_code == 200  # same key as above, 2 of 6


def test_daily_cap(client, monkeypatch):
    monkeypatch.setattr(main, "daily", main.DailyCap(2))
    assert [chat(client, ip=f"5.5.5.{i}").status_code for i in range(2)] == [200, 200]
    r = chat(client, ip="5.5.5.9")
    assert r.status_code == 429 and "daily" in r.json()["detail"]


def test_agent_failure_is_a_friendly_502(client, monkeypatch):
    def boom():
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(agent_mod, "get_agent", boom)
    r = chat(client)
    assert r.status_code == 502 and "secret" not in r.text


def test_cors_allows_viewer_origins(client):
    for origin in ("http://localhost:5173", "https://s2change.dzaky.ai.id"):
        r = client.get("/api/health", headers={"Origin": origin})
        assert r.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-origin" not in client.get("/api/health", headers={"Origin": "https://evil.example"}).headers
