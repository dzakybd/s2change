"""Run the agent evals against a running API: ``python -m api.evals`` (env: EVAL_API_URL, READER_DATABASE_URL)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import statistics
import time
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

HERE = Path(__file__).parent
REFUSAL = re.compile(
    r"can(?:no|')t|cannot|unable|not able|won't|will not|decline|outside|out of scope|not allowed|read-only|not permitted|sorry"
    r"|specializ|specialis|only (?:discuss|answer|help|work|assist|query|access)|restrict|block|allow-?list"
    r"|i am (?:a|an|designed)"
    r"|not (?:available|included|contain)|no (?:data|information)|does not|doesn't",
    re.I,
)
NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")


def numbers_in(*texts) -> list[float]:
    out = []
    for t in texts:
        for m in NUMBER.findall(str(t)):
            with contextlib.suppress(ValueError):
                out.append(float(m.replace(",", "")))
    return out


def close(expected: float, found: list[float], tol: float = 0.02) -> bool:
    return any(abs(v - expected) <= tol * abs(expected) + (0.011 if expected == 0 else 0) for v in found)


def run_case(case: dict, defs: dict, conn, client: httpx.Client) -> dict:
    def sql(q: str):
        for k, v in {**defs, **values}.items():
            q = q.replace("{" + k + "}", str(v))
        v = conn.execute(text(q)).scalar()
        return float(v) if hasattr(v, "as_tuple") else v

    values: dict = {}
    for k, q in case.get("vars", {}).items():
        values[k] = sql(q)
    truth = {k: sql(q) for k, q in case.get("truth", {}).items()}
    question = case["question"].format(**values)

    t0 = time.perf_counter()
    try:
        r = client.post("/api/agent/chat", json={"messages": [{"role": "user", "content": question}]})
        r.raise_for_status()
        resp = r.json()
    except Exception as e:
        return {
            "id": case["id"],
            "status": "fail",
            "checks": {"http": f"{type(e).__name__}: {e}"[:200]},
            "latency_s": round(time.perf_counter() - t0, 1),
        }
    latency = round(time.perf_counter() - t0, 1)

    answer = resp["answer"]
    low = answer.lower().replace("_", " ")
    called = [t["name"] for t in resp["tool_calls"]]
    found = numbers_in(answer, resp["tables"], resp["charts"])
    checks: dict[str, bool] = {}
    for name in case.get("numbers", []):
        checks[f"number:{name}={truth[name]}"] = truth[name] is not None and close(float(truth[name]), found)
    for item in case.get("contains", []):
        needle = str(truth.get(item, item)).lower().replace("_", " ")
        checks[f"contains:{needle}"] = needle in low
    if case.get("contains_any"):
        checks["contains_any"] = any(s.lower() in low for s in case["contains_any"])
    if case.get("tools_any"):
        checks["tool:" + "|".join(case["tools_any"])] = bool(set(case["tools_any"]) & set(called))
    if case.get("tools_all"):
        checks["tools_all:" + "+".join(case["tools_all"])] = set(case["tools_all"]) <= set(called)
    if case.get("declines"):
        checks["declines_wording"] = bool(REFUSAL.search(answer))
    types = [a["type"] for a in resp["actions"]]
    if case.get("actions_any"):
        checks["action_any:" + "|".join(case["actions_any"])] = bool(set(case["actions_any"]) & set(types))
    for t in case.get("actions_all", []):
        checks[f"action:{t}"] = t in types
    if case.get("action_ids"):
        got = {i for a in resp["actions"] if a["type"] == "highlight" for i in a["ids"]}
        checks["action_ids"] = got == set(truth[case["action_ids"]])
    if case.get("charts"):
        checks["charts"] = len(resp["charts"]) >= case["charts"]
    if case.get("tables"):
        checks["tables"] = len(resp["tables"]) >= case["tables"]
    if case.get("refusal"):
        checks["refusal_wording"] = bool(REFUSAL.search(answer))
        checks["no_tools"] = not called
    if case.get("blocked"):
        checks["refusal_wording"] = bool(REFUSAL.search(answer))
        after = {k: sql(q) for k, q in case["truth"].items()}
        checks["data_unchanged"] = after == truth
    return {
        "id": case["id"], "status": "pass" if all(checks.values()) else "fail", "question": question, "checks": checks,
        "latency_s": latency, "tools": called, "answer": answer[:600],
    }  # fmt: skip


def write_reports(results: list[dict], model: str) -> None:
    ran = [r for r in results if r["status"] != "skipped"]
    passed = [r for r in ran if r["status"] == "pass"]
    lat = sorted(r["latency_s"] for r in ran)
    stats = {
        "model": model, "n_run": len(ran), "n_pass": len(passed), "n_skipped": len(results) - len(ran),
        "pass_rate": round(len(passed) / len(ran), 3) if ran else None,
        "latency_mean_s": round(statistics.mean(lat), 1) if lat else None,
        "latency_p50_s": round(statistics.median(lat), 1) if lat else None,
        "latency_max_s": lat[-1] if lat else None,
    }  # fmt: skip
    (HERE / "results.json").write_text(json.dumps({"summary": stats, "cases": results}, indent=2))
    lines = [
        "# Agent eval results", "",
        f"Model `{model}`: **{stats['n_pass']}/{stats['n_run']} passed** ({(stats['pass_rate'] or 0):.0%}), "
        f"{stats['n_skipped']} skipped. Latency mean {stats['latency_mean_s']} s, median {stats['latency_p50_s']} s, "
        f"max {stats['latency_max_s']} s.", "",
        "| Case | Result | Latency (s) | Tools | Failed checks |", "|---|---|---:|---|---|",
    ]  # fmt: skip
    for r in results:
        failed = ", ".join(k for k, v in r.get("checks", {}).items() if v is not True) or r.get("reason", "")
        tools = ", ".join(dict.fromkeys(r.get("tools", [])))
        lines.append(f"| {r['id']} | {r['status']} | {r.get('latency_s', '')} | {tools} | {failed} |")
    (HERE / "results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> None:
    load_dotenv()
    spec = yaml.safe_load((HERE / "cases.yaml").read_text())
    engine = create_engine(os.environ["READER_DATABASE_URL"])
    with (
        engine.connect() as conn,
        httpx.Client(base_url=os.environ.get("EVAL_API_URL", "http://127.0.0.1:8000"), timeout=180) as client,
    ):
        model = client.get("/api/health").json()["model"]
        results = []
        for case in spec["cases"]:
            try:
                res = run_case(case, spec["defs"], conn, client)
            except Exception as e:  # a broken truth query must not hide the other cases
                conn.rollback()
                res = {
                    "id": case["id"],
                    "status": "fail",
                    "checks": {"harness": f"{type(e).__name__}: {str(e)[:150]}"},
                    "latency_s": 0,
                }
            print(f"{res['id']}: {res['status']} {res.get('latency_s', '')}", flush=True)
            results.append(res)
    write_reports(results, model)


if __name__ == "__main__":
    main()
