#!/usr/bin/env python3
"""Re-score stored hardbench transcripts with the current graders (no model calls).

The tool tier is artifact-graded, so it cannot be re-scored from transcripts alone --
its scores in the stored records are final. The scored tier is text-graded and is fully
reproducible here. This is the script that turned 13 cells after the h2/h6/h10 grader
fixes; see HARDBENCH.md.
"""
import importlib.util
import json
import pathlib

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("hardbench", HERE / "hardbench.py")
hb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hb)

by_id = {i["id"]: i for i in hb.SCORED_ITEMS}
path = HERE / "results/hardbench_results_2026-09-13.jsonl"
records = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]

changed = 0
for r in records:
    if r["tier"] != "scored":
        continue
    old = r["score"]
    r["score"], r["why"] = hb.grade(by_id[r["item"]], r["text"], condition=r["condition"])
    if r["score"] != old:
        changed += 1
        print(f"  {r['condition']:16s} {r['item']:18s} r{r['rep']} {old} -> {r['score']}")

path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

for tier, items in (("tool", hb.TOOL_ITEMS), ("scored", hb.SCORED_ITEMS)):
    rows = [r for r in records if r["tier"] == tier]
    if not rows:
        continue
    summary = hb._summarise(rows, items)
    (HERE / f"scores/hardbench_{tier}_scores_2026-09-13.json").write_text(
        json.dumps(summary, indent=2))
    hb.report(summary, items, f"hard {tier} tier")

n_scored = len([r for r in records if r["tier"] == "scored"])
print(f"\nre-scored {n_scored} scored cells, {changed} changed")
