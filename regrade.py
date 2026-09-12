#!/usr/bin/env python3
"""Re-score suite_results.jsonl with the current graders (no model calls)."""
import importlib.util
import json
import pathlib
import re

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("suite", HERE / "suite.py")
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)

files = sorted(p for p in HERE.glob("suite_results*.jsonl") if p.name != "suite_results_all.jsonl")
records = [json.loads(l) for p in files for l in p.read_text().splitlines() if l.strip()]
by_id = {i["id"]: i for i in suite.ITEMS}
for r in records:
    file_ok = r.get("file_ok")
    if file_ok is None and r["item"] == "t11-file":
        file_ok = "file_ok=True" in r["why"]
    r["score"], r["why"] = suite.grade(by_id[r["item"]], r["text"], file_ok=bool(file_ok),
                                       condition=r["condition"])

summary = {}
order = ["fable", "codex-default", "deepseek-flash-high", "deepseek-fable"]
present = [n for n in order if any(r["condition"] == n for r in records)]
for name in present:
    rows = [r for r in records if r["condition"] == name]
    summary[name] = dict(
        score=round(sum(r["score"] for r in rows) / len(rows), 4),
        items_passed=round(sum(r["score"] for r in rows), 1),
        items_total=len(rows),
        mean_latency_ms=int(sum(r["latency_ms"] for r in rows) / len(rows)),
        total_input_tokens=sum(r["usage"].get("input_tokens", r["usage"].get("prompt_tokens", 0)) for r in rows),
        total_output_tokens=sum(r["usage"].get("output_tokens", r["usage"].get("completion_tokens", 0)) for r in rows),
        per_item={i: [r["score"] for r in rows if r["item"] == i] for i in by_id})

(HERE / "suite_results_all.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
(HERE / "suite_scores.json").write_text(json.dumps(summary, indent=2))

hdr = f"{'item':14s}" + "".join(f"{c:>22s}" for c in present)
print(hdr)
for item in by_id:
    print(f"{item:14s}" + "".join(f"{str(summary[c]['per_item'][item]):>22s}" for c in present))
print()
for c, v in summary.items():
    print(f"{c:22s} score={v['score']:.3f} ({v['items_passed']}/{v['items_total']}) "
          f"mean_latency={v['mean_latency_ms']}ms in_tokens={v['total_input_tokens']} out_tokens={v['total_output_tokens']}")
