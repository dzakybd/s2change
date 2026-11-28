"""Analyst app tests: SQL guard, collector, API with a fake agent. No network, no LLM, no database."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("deepagents")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))

from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from api import agent as agent_mod  # noqa: E402
from api import analyst, auth, collector, main, tools  # noqa: E402
from api.guard import SqlRejected, guard_sql  # noqa: E402

# SQL guard


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM change_features",
        "select change_type, sum(area_m2) / 1e4 from change_features f join facilities c using (facility_id) group by 1",
        "WITH t AS (SELECT id FROM change_features) SELECT * FROM t",
        "SELECT ST_Area(geometry), ST_AsText(ST_Transform(geometry, 4326)) FROM change_features WHERE area_m2 > 1000",
        "SELECT * FROM facility_changes ORDER BY changed_ha DESC",
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
        "SELECT * FROM timeline_months",
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


# Collector


def test_collector_validates_actions():
    c = collector.Collector()
    assert c.add_action("fly_to", bbox=[25.8, -12.3, 25.9, -12.2])["bbox"] == [25.8, -12.3, 25.9, -12.2]
    assert c.add_action("highlight", run_id="r", ids=[3, 1, 3])["ids"] == [1, 3]
    assert c.add_action("show_run", run_id="r") == {"type": "show_run", "run_id": "r"}
    assert c.add_action("set_view", oblique=True) == {"type": "set_view", "oblique": True}
    for bad in (
        {"type": "set_view"},
        {"type": "fly_to", "bbox": [1, 2, 3]},
        {"type": "fly_to", "bbox": [30, -12, 25, -11]},
        {"type": "highlight", "run_id": "r", "ids": []},
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


# API


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
    monkeypatch.delenv("ACCESS_CODE_HASH", raising=False)  # a developer's .env must not close the gate under test
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
    assert len(sent) == main.KEEP_MESSAGES and sent[-1]["content"] == "last"
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


# Access code

CODE = "open-sesame-2024"


@pytest.fixture
def locked(client, monkeypatch):
    """The same app with the gate closed; https base URL because the session cookie is Secure."""
    monkeypatch.setenv("ACCESS_CODE_HASH", auth.hash_code(CODE, iterations=1000))
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setattr(main, "LOGIN_RATE", "5/minute")
    c = TestClient(main.app, base_url="https://testserver")
    c.fake = client.fake
    return c


def login(c, code=CODE, ip="7.7.7.7"):
    return c.post("/api/auth/login", json={"code": code}, headers={"X-Forwarded-For": ip})


def test_hash_roundtrip_and_malformed_hashes():
    stored = auth.hash_code("correct horse", iterations=1000)
    assert stored.startswith("pbkdf2_sha256$1000$") and "correct horse" not in stored
    assert auth.verify_code("correct horse", stored) and not auth.verify_code("wrong", stored)
    assert auth.hash_code("correct horse", iterations=1000) != stored  # random salt
    for bad in ("", "x", "a$b$c$d", "pbkdf2_sha256$x$AA==$AA==", "pbkdf2_sha256$1000$***$AA==", "scrypt$1000$AA==$AA=="):
        assert not auth.verify_code("correct horse", bad)


def test_gate_is_open_when_no_hash_is_set(client):
    assert client.get("/api/auth/status").json() == {"enabled": False, "authenticated": True}
    assert chat(client).status_code == 200


def test_status_and_login(locked):
    assert locked.get("/api/auth/status").json() == {"enabled": True, "authenticated": False}
    bad = login(locked, "nope")
    assert bad.status_code == 401 and "set-cookie" not in bad.headers and CODE not in bad.text
    ok = login(locked)
    assert ok.status_code == 200 and ok.json() == {"enabled": True, "authenticated": True}
    assert locked.get("/api/auth/status").json() == {"enabled": True, "authenticated": True}


def test_session_cookie_flags(locked):
    cookie = login(locked).headers["set-cookie"].lower()
    assert cookie.startswith("s2c_session=")
    for flag in ("httponly", "secure", "samesite=strict", "path=/api", f"max-age={7 * 24 * 3600}"):
        assert flag in cookie


def test_logout_clears_the_session(locked):
    login(locked)
    r = locked.post("/api/auth/logout")
    assert r.status_code == 200 and "max-age=0" in r.headers["set-cookie"].lower()
    assert locked.get("/api/auth/status").json()["authenticated"] is False


def gated_calls(c):
    return {
        "chat": lambda: chat(c),
        "db uri": lambda: c.get("/api/db/connection/uri"),
    }


def test_gated_endpoints_need_the_cookie(locked):
    for name, call in gated_calls(locked).items():
        r = call()
        assert r.status_code == 401 and r.json() == {"detail": "This feature needs the access code."}, name
    assert locked.fake.seen == []  # the agent was never reached


def test_gated_endpoints_work_with_the_cookie(locked, monkeypatch):
    monkeypatch.setattr(main.explorer, "full_uri", lambda: "postgresql://reader@db/x")
    login(locked)
    assert chat(locked).status_code == 200
    assert locked.get("/api/db/connection/uri").json() == {"uri": "postgresql://reader@db/x"}


def test_public_endpoints_stay_open(locked, monkeypatch):
    monkeypatch.setattr(main.db, "ping", lambda: True)
    assert locked.get("/api/health").status_code == 200


def test_tampered_foreign_and_expired_cookies_are_refused(locked, monkeypatch):
    monkeypatch.setattr(main.explorer, "full_uri", lambda: "postgresql://reader@db/x")
    good = auth.make_session()
    issued, sig = good.split(".")
    forged = {
        "tampered signature": f"{issued}.{'0' * len(sig)}",
        "tampered timestamp": f"{int(issued) + 1}.{sig}",
        "other secret": good.replace(sig, auth.hmac.new(b"x" * 40, f"s2c1.{issued}".encode(), "sha256").hexdigest()),
        "expired": auth.make_session(time.time() - auth.MAX_AGE - 5),
        "future": auth.make_session(time.time() + 3600),
        "garbage": "not-a-cookie",
        "empty": "",
    }
    for why, value in forged.items():
        r = locked.get("/api/db/connection/uri", headers={"Cookie": f"s2c_session={value}"})
        assert r.status_code == 401, why
    assert locked.get("/api/db/connection/uri", headers={"Cookie": f"s2c_session={good}"}).status_code == 200
    assert not auth.session_valid("\u00b2.\u00e9")  # non-ASCII digits and signature: refused, not a 500


def test_login_is_rate_limited_per_ip(locked):
    assert [login(locked, "nope", ip="6.6.6.6").status_code for _ in range(5)] == [401] * 5
    assert login(locked, ip="6.6.6.6").status_code == 429  # even the right code, until the minute is over
    assert login(locked, ip="6.6.6.7").status_code == 200


@pytest.mark.parametrize(
    "env",
    [{"ACCESS_CODE_HASH": "plaintext", "SESSION_SECRET": "s" * 40}, {"ACCESS_CODE_HASH": auth.hash_code("x", iterations=1)}],
)
def test_half_configured_gate_refuses_to_start(monkeypatch, env):
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(RuntimeError):
        auth.check_config()


def test_missing_hash_warns_and_starts(monkeypatch, caplog):
    monkeypatch.delenv("ACCESS_CODE_HASH", raising=False)
    auth.check_config()
    assert "DISABLED" in caplog.text


def test_explorer_refuses_names_outside_the_schema(locked, monkeypatch):
    monkeypatch.setattr(
        main.explorer, "_columns", lambda: {t: [{"name": "id", "type": "bigint", "not_null": True}] for t in main.explorer.TABLES}
    )
    monkeypatch.setattr(main.explorer, "_constraints", lambda: [])
    assert locked.get("/api/db/tables/pg_user").status_code == 422
    assert locked.get("/api/db/tables/change_runs", params={"sort": "id; DROP TABLE x"}).status_code == 422
    assert locked.get("/api/db/tables/change_runs", params={"column": "nope", "value": "1"}).status_code == 422
    assert locked.get("/api/db/tables/change_runs", params={"size": 5000}).status_code == 422
    assert locked.get("/api/db/export.sql", params={"table": "pg_user"}).status_code == 422


def test_out_of_credit_is_reported_plainly(client, monkeypatch):
    class PaymentRequired(Exception):
        status_code = 402

    def boom(messages):
        try:
            raise PaymentRequired("402: requires more credits")
        except PaymentRequired as e:
            raise RuntimeError("agent graph failed") from e

    monkeypatch.setattr(main.analyst, "run_chat", boom)
    r = chat(client)
    assert r.status_code == 503 and "credit is used up" in r.json()["detail"]


@pytest.fixture
def starters(monkeypatch, tmp_path):
    """A fresh process: empty memory cache, disk cache in tmp_path, fixed fingerprint."""

    def restart(fp="fp1"):
        monkeypatch.setattr(analyst, "_cache", {})
        monkeypatch.setattr(analyst, "_fingerprint", None)
        monkeypatch.setattr(analyst, "fingerprint", lambda: fp)

    monkeypatch.setenv("ANALYST_CACHE", str(tmp_path / "starters.json"))
    restart()
    return restart


def test_starter_answers_survive_a_restart(client, starters):
    q = analyst.STARTERS[0]
    assert chat(client, q).status_code == 200 and len(client.fake.seen) == 1
    starters()  # deploy: new process, same code and data
    assert chat(client, q, ip="6.6.6.6").json()["answer"] == "**Done**: 1,345 ha"
    assert len(client.fake.seen) == 1  # served from disk, no LLM call
    starters(fp="fp2")  # republished results or a new model: the old answers are dropped
    chat(client, q, ip="7.7.7.7")
    assert len(client.fake.seen) == 2


def test_warm_stops_at_the_first_failure(client, starters, monkeypatch):
    calls = []

    def boom(messages):
        calls.append(messages)
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(agent_mod, "run", boom)
    analyst.warm()
    assert len(calls) == 1
