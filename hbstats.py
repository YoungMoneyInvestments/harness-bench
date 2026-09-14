#!/usr/bin/env python3
"""Rich statistics over stored hardbench records. No model calls.

hardbench.py reports score, latency, steps, tool calls and cost-per-solve. This goes
further on the same stored records, because a single mean hides everything that matters
about a harness:

  * a Wilson 95% interval on the pass rate, so a difference can be read as real or noise
  * latency p50/p95/max, not just a mean -- harnesses differ most in the tail
  * step and tool-call distributions, including worst case
  * failed-call rate per call issued, not a raw count (arms issue different totals)
  * cache-hit rate and prompt tokens per step (how fast the harness grows context)
  * reasoning-token share of output
  * cost split into fresh / cached / output contributions
  * cost per attempt AND per solved item -- the gap between them is the retry tax

and, most importantly, a flaky-cell table: every (arm, item) whose pass rate is neither
0 nor 1. Those are the only cells where an inter-arm difference can mean anything.

Usage: python3 hbstats.py [results.jsonl ...]
"""
import json
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
DEFAULT = HERE / "results/hardbench_results_2026-09-13.jsonl"
ARMS = ["deepseek-bare", "deepseek-codex", "deepseek-claude", "deepseek-dsh"]
SHORT = {"deepseek-bare": "bare", "deepseek-codex": "codex",
         "deepseek-claude": "claude", "deepseek-dsh": "dsh"}

# DeepSeek published rates, USD per token (usage/deepseek-usage-summary-2026-09-12.csv)
R = dict(miss=0.00000015, hit=0.000000003, out=0.0000006)


def wilson(k, n, z=1.96):
    """Wilson score interval -- correct at the extremes where normal approx isn't."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (c - m) / d), min(1.0, (c + m) / d)


def pct(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, int(round(q * (len(s) - 1))))
    return s[i]


def cost_parts(u):
    return (u["fresh"] + u["cache_write"]) * R["miss"], u["cache_read"] * R["hit"], u["out"] * R["out"]


def load(paths):
    recs = []
    for p in paths:
        recs += [json.loads(l) for l in pathlib.Path(p).read_text().splitlines() if l.strip()]
    return recs


def main(paths):
    recs = load(paths)
    if not recs:
        print("no records"); return
    tiers = sorted({r["tier"] for r in recs})
    print(f"{len(recs)} records from {', '.join(str(p) for p in paths)}")
    print(f"tiers: {', '.join(tiers)}   reps per cell: "
          f"{ {t: max(r['rep'] for r in recs if r['tier'] == t) + 1 for t in tiers} }")

    for tier in tiers:
        rows = [r for r in recs if r["tier"] == tier]
        arms = [a for a in ARMS if any(r["condition"] == a for r in rows)]
        print(f"\n{'=' * 118}\n=== {tier.upper()} TIER ===\n{'=' * 118}")

        print(f"{'arm':8s}{'n':>5s}{'solved':>8s}{'rate':>7s}{'95% CI':>17s}"
              f"{'p50ms':>8s}{'p95ms':>8s}{'maxms':>8s}{'ms/step':>9s}{'steps':>7s}{'p95st':>7s}"
              f"{'tools':>7s}{'fail%':>7s}{'cache%':>8s}{'ctx/step':>10s}{'tok/s':>8s}")
        tot = {}
        for a in arms:
            rs = [r for r in rows if r["condition"] == a]
            n = len(rs); k = sum(r["score"] for r in rs)
            lo, hi = wilson(k, n)
            lat = [r["latency_ms"] for r in rs]
            st = [r["steps"] for r in rs]
            tc = sum(r["tool_calls"] for r in rs)
            fc = sum(r["failed_tool_calls"] for r in rs)
            fr = sum(r["norm"]["fresh"] for r in rs)
            ca = sum(r["norm"]["cache_read"] for r in rs)
            ou = sum(r["norm"]["out"] for r in rs)
            steps_all = max(sum(st), 1)
            wall_s = max(sum(lat), 1) / 1000.0
            tot[a] = dict(n=n, k=k, rs=rs)
            print(f"{SHORT[a]:8s}{n:>5d}{k:>8.0f}{k / n:>7.3f}{f'[{lo:.3f},{hi:.3f}]':>17s}"
                  f"{pct(lat, .5):>8,d}{pct(lat, .95):>8,d}{max(lat):>8,d}"
                  f"{sum(lat) / steps_all:>9,.0f}{sum(st) / n:>7.2f}{pct(st, .95):>7.0f}{tc:>7d}"
                  f"{(100 * fc / tc if tc else 0):>6.1f}%"
                  f"{(100 * ca / max(fr + ca, 1)):>7.1f}%{int((fr + ca) / steps_all):>10,d}"
                  f"{ou / wall_s:>8.1f}")
        print("  tok/s is EFFECTIVE pipeline throughput (output tokens / wall seconds), not "
              "model decode speed:\nit  includes tool execution, process startup and npx "
              "resolution. Do not read it as provider speed.")

        print(f"\n--- cost, USD at DeepSeek published rates ---")
        print(f"{'arm':8s}{'fresh$':>10s}{'cached$':>10s}{'output$':>10s}{'total$':>10s}"
              f"{'$/attempt':>11s}{'$/solved':>11s}{'retry tax':>11s}")
        for a in arms:
            rs = tot[a]["rs"]; k = tot[a]["k"]; n = tot[a]["n"]
            f = c = o = 0.0
            for r in rs:
                p = cost_parts(r["norm"]); f += p[0]; c += p[1]; o += p[2]
            t = f + c + o
            per_att = t / n
            per_sol = t / k if k else float("nan")
            tax = (per_sol / per_att - 1) * 100 if k and per_att else float("nan")
            print(f"{SHORT[a]:8s}{f:>10.4f}{c:>10.4f}{o:>10.4f}{t:>10.4f}"
                  f"{per_att:>11.5f}{per_sol:>11.5f}{tax:>10.0f}%")

        print(f"\n--- reasoning share of output, and tokens per step ---")
        print(f"{'arm':8s}{'out tok':>10s}{'reason tok':>12s}{'reason%':>9s}"
              f"{'fresh/step':>12s}{'cached/step':>13s}{'out/step':>10s}")
        for a in arms:
            rs = tot[a]["rs"]
            o = sum(r["norm"]["out"] for r in rs); rn = sum(r["norm"]["reasoning"] for r in rs)
            fr = sum(r["norm"]["fresh"] for r in rs); ca = sum(r["norm"]["cache_read"] for r in rs)
            st = max(sum(r["steps"] for r in rs), 1)
            print(f"{SHORT[a]:8s}{o:>10,d}{rn:>12,d}{(100 * rn / max(o, 1)):>8.1f}%"
                  f"{int(fr / st):>12,d}{int(ca / st):>13,d}{int(o / st):>10,d}")

        items = sorted({r["item"] for r in rows})
        print(f"\n--- per-item pass rate (n={max(r['rep'] for r in rows if r['tier'] == tier) + 1} per cell) ---")
        print(f"{'item':24s}" + "".join(f"{SHORT[a]:>10s}" for a in arms) + "   verdict")
        flaky = []
        for it in items:
            cells = {}
            for a in arms:
                rs = [r for r in rows if r["condition"] == a and r["item"] == it]
                cells[a] = (sum(r["score"] for r in rs) / len(rs), len(rs)) if rs else (0, 0)
            rates = [v[0] for v in cells.values()]
            spread = max(rates) - min(rates)
            for a in arms:
                if 0 < cells[a][0] < 1:
                    flaky.append((it, a, cells[a][0], cells[a][1]))
            verdict = "ALL PASS" if min(rates) == 1 else ("ALL FAIL" if max(rates) == 0 else
                                                          f"spread {spread:.2f}")
            print(f"{it:24s}" + "".join(f"{cells[a][0]:>10.2f}" for a in arms) + f"   {verdict}")

        print(f"\n--- flaky cells (0 < rate < 1): the only cells where a gap is meaningful ---")
        if not flaky:
            print("  none -- every cell is deterministic over these reps")
        for it, a, rate, n in sorted(flaky, key=lambda x: -x[2]):
            lo, hi = wilson(rate * n, n)
            print(f"  {it:24s} {SHORT[a]:8s} {rate:.2f}  ({rate * n:.0f}/{n})  95% CI [{lo:.2f},{hi:.2f}]")


if __name__ == "__main__":
    main([pathlib.Path(p) for p in sys.argv[1:]] or [DEFAULT])
