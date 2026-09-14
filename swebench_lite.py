#!/usr/bin/env python3
"""swebench_lite: SWE-bench-shaped tasks, four harnesses, reliability not just accuracy.

Why this file exists
--------------------
Every suite in this repo measures **single-shot correctness on a prompt**. That is why
they all tie: a one-shot answer either matches an expected string or it doesn't, and the
harness around it barely matters. The literature's answer to that is to stop grading
prose and start grading *executable state*.

This implements the two contracts that carry the most weight:

**SWE-bench's FAIL_TO_PASS / PASS_TO_PASS.** A task ships a small repo with a real test
suite. Grading is all-or-nothing: `resolved` iff every FAIL_TO_PASS test now passes AND
every PASS_TO_PASS test still passes. Two mechanics make it hold up:

  * **Test files are reset from the canonical copy before grading**, so a task cannot be
    "passed" by editing or deleting its tests. Whatever the agent did to the test files
    during the run is overwritten.
  * **An oracle gate.** F2P and P2P are not hand-written -- they are *derived* by running
    the broken snapshot (must fail F2P) and the gold patch (must pass F2P and P2P). A task
    with no FAIL_TO_PASS tests, or one the gold patch does not resolve, is invalid and is
    rejected at build time. This is the invariant that catches a broken task before it
    silently scores every arm zero.

**tau-bench's pass^k.** pass@k (did it ever succeed) rises with k; pass^k (did it succeed
*every* time) falls. Reliability is what a user experiences, so both are reported, and the
gap between them is the point.

Two known SWE-bench failure modes are deliberately guarded: issues are generated from the
same spec the tests are generated from (so nothing is underspecified), and each task ships
a **hidden** extended test set alongside the visible one, so passing by fitting the visible
tests alone is not enough.

Usage:
  python3 swebench_lite.py --validate        # oracle-gate every task, no model calls
  python3 swebench_lite.py                   # run all arms
"""
import importlib.util
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).parent
WORK = pathlib.Path("/tmp/fable-bench")
RUN_ROOT = WORK / "swebench_lite" / time.strftime("%Y%m%d-%H%M%S")
SEEDS = int(os.environ.get("SEEDS", "3"))   # distinct task instances per family
REPS = int(os.environ.get("REPS", "5"))     # repeats of the SAME instance, per arm

_spec = importlib.util.spec_from_file_location("hardbench", HERE / "hardbench.py")
hb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hb)


# ------------------------------------------------------------------ task families
# Each family returns a broken repo, a visible test suite, a hidden extended suite,
# the gold patch, and an issue written from the same spec the tests come from.

INVENTORY_ISSUE = """\
`inventory.py` has two defects, both reported by users:

1. `remove()` will drive a SKU's quantity below zero when asked to remove more than is
   on hand. Stock levels must never go negative; removing more than available leaves the
   SKU at 0. The function still returns the resulting quantity.
2. `low_stock()` is documented to return SKUs whose quantity is *at or below* the
   threshold. It currently excludes SKUs sitting exactly at the threshold.

Fix `inventory.py` so the tests pass. Do not change the public function signatures.
"""

INVENTORY_BROKEN = '''"""Tiny in-memory inventory store."""


def add(store, sku, qty):
    """Add qty of sku. Returns the new quantity."""
    store[sku] = store.get(sku, 0) + qty
    return store[sku]


def remove(store, sku, qty):
    """Remove qty of sku. Returns the new quantity."""
    store[sku] = store.get(sku, 0) - qty
    return store[sku]


def total(store):
    """Sum of all quantities."""
    return sum(store.values())


def low_stock(store, threshold):
    """Sorted SKUs at or below threshold."""
    return sorted(k for k, v in store.items() if v < threshold)
'''

INVENTORY_GOLD = INVENTORY_BROKEN.replace(
    "    store[sku] = store.get(sku, 0) - qty\n    return store[sku]",
    "    store[sku] = max(0, store.get(sku, 0) - qty)\n    return store[sku]"
).replace("if v < threshold", "if v <= threshold")

INVENTORY_TESTS = '''import unittest

from inventory import add, low_stock, remove, total


class TestInventory(unittest.TestCase):
    def test_add_new_sku(self):
        s = {}
        self.assertEqual(add(s, "A", 5), 5)
        self.assertEqual(s["A"], 5)

    def test_add_existing_sku(self):
        s = {"A": 5}
        self.assertEqual(add(s, "A", 3), 8)

    def test_remove_partial(self):
        s = {"A": 5}
        self.assertEqual(remove(s, "A", 2), 3)

    def test_total_sums(self):
        self.assertEqual(total({"A": 5, "B": 7}), 12)

    def test_total_empty(self):
        self.assertEqual(total({}), 0)


if __name__ == "__main__":
    unittest.main()
'''

INVENTORY_HIDDEN = '''import unittest

from inventory import add, low_stock, remove, total


class TestInventoryContract(unittest.TestCase):
    def test_remove_never_goes_negative(self):
        s = {"A": 2}
        self.assertEqual(remove(s, "A", 5), 0)
        self.assertEqual(s["A"], 0)

    def test_remove_exact(self):
        s = {"A": 3}
        self.assertEqual(remove(s, "A", 3), 0)

    def test_low_stock_includes_threshold(self):
        s = {"A": 3, "B": 4}
        self.assertEqual(low_stock(s, 3), ["A"])

    def test_low_stock_boundary_and_below(self):
        s = {"A": 1, "B": 3, "C": 9}
        self.assertEqual(low_stock(s, 3), ["A", "B"])

    def test_low_stock_empty(self):
        self.assertEqual(low_stock({}, 5), [])

    def test_negative_after_repeated_removal(self):
        s = {"A": 4}
        remove(s, "A", 3)
        self.assertEqual(remove(s, "A", 3), 0)
        self.assertEqual(total(s), 0)

    def test_low_stock_threshold_zero(self):
        s = {"A": 0, "B": 1}
        self.assertEqual(low_stock(s, 0), ["A"])


if __name__ == "__main__":
    unittest.main()
'''


LEDGER_ISSUE = """\
`ledger.py` has two defects:

1. `daily_balances()` must report, for each day, the balance after that day's entries,
   processing days in chronological order. It currently walks the list in insertion
   order, so a back-dated entry written into the list later produces wrong per-day
   balances.
2. `overdrawn_days()` counts a day once per transaction that dips below zero. It must
   count DISTINCT days on which the balance was ever negative.

Fix `ledger.py`. Signatures stay as they are. Do not change `balance()`: a sum is
order-independent and it is not defective.
"""

LEDGER_BROKEN = '''"""Running-balance ledger."""


def balance(entries):
    """Final balance after applying all entries."""
    total = 0
    for e in entries:
        total += e["amount"]
    return total


def daily_balances(entries):
    """Map day -> balance at end of that day."""
    out = {}
    total = 0
    for e in entries:
        total += e["amount"]
        out[e["day"]] = total
    return out


def overdrawn_days(entries):
    """Number of distinct days on which the balance went below zero."""
    count = 0
    total = 0
    for e in entries:
        total += e["amount"]
        if total < 0:
            count += 1
    return count
'''

LEDGER_GOLD = '''"""Running-balance ledger."""


def balance(entries):
    """Final balance after applying all entries. A sum is order-independent."""
    total = 0
    for e in entries:
        total += e["amount"]
    return total


def daily_balances(entries):
    """Map day -> balance at end of that day."""
    out = {}
    total = 0
    for e in sorted(entries, key=lambda x: x["day"]):
        total += e["amount"]
        out[e["day"]] = total
    return out


def overdrawn_days(entries):
    """Number of distinct days on which the balance went below zero."""
    seen = set()
    total = 0
    for e in sorted(entries, key=lambda x: x["day"]):
        total += e["amount"]
        if total < 0:
            seen.add(e["day"])
    return len(seen)
'''

LEDGER_TESTS = '''import unittest

from ledger import balance, daily_balances


class TestLedger(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(balance([]), 0)

    def test_single_entry(self):
        self.assertEqual(balance([{"day": 1, "amount": 10}]), 10)

    def test_sum_regardless_of_order(self):
        entries = [{"day": 2, "amount": -5}, {"day": 1, "amount": 10}]
        self.assertEqual(balance(entries), 5)

    def test_daily_balances_last_day(self):
        entries = [{"day": 1, "amount": 10}, {"day": 2, "amount": -3}]
        self.assertEqual(daily_balances(entries)[2], 7)


if __name__ == "__main__":
    unittest.main()
'''

LEDGER_HIDDEN = '''import unittest

from ledger import balance, daily_balances, overdrawn_days


class TestLedgerContract(unittest.TestCase):
    def test_chronological_not_insertion_order(self):
        entries = [{"day": 3, "amount": -100}, {"day": 1, "amount": 50}, {"day": 2, "amount": 5}]
        self.assertEqual(balance(entries), -45)

    def test_daily_balances_chronological(self):
        entries = [{"day": 3, "amount": -100}, {"day": 1, "amount": 50}, {"day": 2, "amount": 5}]
        db = daily_balances(entries)
        self.assertEqual(db[1], 50)
        self.assertEqual(db[2], 55)
        self.assertEqual(db[3], -45)

    def test_overdrawn_counts_distinct_days(self):
        entries = [{"day": 1, "amount": -10}, {"day": 1, "amount": -10}, {"day": 2, "amount": 100}]
        self.assertEqual(overdrawn_days(entries), 1)

    def test_overdrawn_two_days(self):
        entries = [{"day": 1, "amount": -10}, {"day": 2, "amount": -10}, {"day": 3, "amount": 100}]
        self.assertEqual(overdrawn_days(entries), 2)

    def test_overdrawn_none(self):
        self.assertEqual(overdrawn_days([{"day": 1, "amount": 10}]), 0)

    def test_overdrawn_after_recovery(self):
        entries = [{"day": 1, "amount": -5}, {"day": 2, "amount": 50}, {"day": 3, "amount": -60}]
        self.assertEqual(overdrawn_days(entries), 2)


if __name__ == "__main__":
    unittest.main()
'''


FAMILIES = {
    "inventory": dict(files={"inventory.py": INVENTORY_BROKEN}, gold={"inventory.py": INVENTORY_GOLD},
                      visible={"test_inventory.py": INVENTORY_TESTS},
                      hidden={"test_inventory_contract.py": INVENTORY_HIDDEN},
                      issue=INVENTORY_ISSUE),
    "ledger": dict(files={"ledger.py": LEDGER_BROKEN}, gold={"ledger.py": LEDGER_GOLD},
                   visible={"test_ledger.py": LEDGER_TESTS},
                   hidden={"test_ledger_contract.py": LEDGER_HIDDEN},
                   issue=LEDGER_ISSUE),
}

# ------------------------------------------- generated hidden cases, one set per instance
# Expected values come from a reference implementation of the spec, not from one
# hand-written example, so each seed is a fresh problem and an arm cannot pass by
# memorising a fixed instance.

def _ref_balance(entries):
    return sum(e["amount"] for e in entries)


def _ref_daily(entries):
    out, total = {}, 0
    for e in sorted(entries, key=lambda x: x["day"]):
        total += e["amount"]
        out[e["day"]] = total
    return out


def _ref_overdrawn(entries):
    seen, total = set(), 0
    for e in sorted(entries, key=lambda x: x["day"]):
        total += e["amount"]
        if total < 0:
            seen.add(e["day"])
    return len(seen)


def _ref_low_stock(store, threshold):
    return sorted(k for k, v in store.items() if v <= threshold)


def _gen_ledger_hidden(seed):
    rng = random.Random(f"ledger:{seed}")
    # case 0 is fixed and guaranteed to exercise both defects
    cases = [[{"day": 3, "amount": -10}, {"day": 1, "amount": 5},
              {"day": 3, "amount": -10}, {"day": 2, "amount": 100}]]
    cases += [[{"day": rng.randint(1, 4), "amount": rng.randint(-40, 40)}
               for _ in range(rng.randint(3, 6))] for _ in range(4)]
    out = ["import unittest", "",
           "from ledger import balance, daily_balances, overdrawn_days", "", "",
           "class TestLedgerGenerated(unittest.TestCase):"]
    for i, entries in enumerate(cases):
        out += [f"    def test_case{i}_balance(self):",
                f"        entries = {entries!r}",
                f"        self.assertEqual(balance(entries), {_ref_balance(entries)})",
                f"    def test_case{i}_daily(self):",
                f"        entries = {entries!r}",
                f"        self.assertEqual(daily_balances(entries), {_ref_daily(entries)!r})",
                f"    def test_case{i}_overdrawn(self):",
                f"        entries = {entries!r}",
                f"        self.assertEqual(overdrawn_days(entries), {_ref_overdrawn(entries)})",
                ""]
    out += ["", 'if __name__ == "__main__":', "    unittest.main()", ""]
    return "\n".join(out)


def _gen_inventory_hidden(seed):
    rng = random.Random(f"inventory:{seed}")
    stores = [{"A": rng.randint(0, 6), "B": rng.randint(0, 6), "C": rng.randint(0, 6)}
              for _ in range(3)]
    # case 0's threshold equals a stored quantity, guaranteeing the boundary is hit
    thresholds = [stores[0]["A"]] + [rng.randint(0, 6) for _ in range(2)]
    out = ["import unittest", "",
           "from inventory import add, low_stock, remove, total", "", "",
           "class TestInventoryGenerated(unittest.TestCase):"]
    for i, (store, th) in enumerate(zip(stores, thresholds)):
        out += [f"    def test_case{i}_low_stock_boundary(self):",
                f"        store = {store!r}",
                f"        self.assertEqual(low_stock(store, {th}), {_ref_low_stock(store, th)!r})",
                ""]
    for i, store in enumerate(stores):
        qty = store["A"] + rng.randint(1, 5)
        out += [f"    def test_case{i}_remove_never_negative(self):",
                f"        store = {store!r}",
                f"        self.assertEqual(remove(store, 'A', {qty}), 0)",
                f"        self.assertEqual(store['A'], 0)",
                ""]
    out += ["", 'if __name__ == "__main__":', "    unittest.main()", ""]
    return "\n".join(out)


GENERATED_HIDDEN = {"ledger": _gen_ledger_hidden, "inventory": _gen_inventory_hidden}


def build_task(name, seed):
    """Materialise a task instance, then DERIVE its F2P/P2P lists by running the oracle.

    F2P and P2P are never hand-written: they come from running the broken snapshot and
    running the gold patch. A task that yields no FAIL_TO_PASS tests, or that its own
    gold patch does not resolve, is invalid -- see validate().
    """
    fam = FAMILIES[name]
    files = dict(fam["files"])
    gold = dict(fam["gold"])
    visible = dict(fam["visible"])
    hidden = dict(fam["hidden"])
    hidden[f"test_{name}_generated.py"] = GENERATED_HIDDEN[name](seed)

    task = dict(name=name, seed=seed, files=files, gold=gold,
                tests={**visible, **hidden}, visible=visible, hidden=hidden,
                issue=fam["issue"])
    broken = run_tests(task, files)
    fixed = run_tests(task, {**files, **gold})
    task["f2p"] = sorted(k for k in fixed if fixed[k] == "ok" and broken.get(k) != "ok")
    task["p2p"] = sorted(k for k in fixed if fixed[k] == "ok" and broken.get(k) == "ok")
    task["valid"] = bool(task["f2p"]) and bool(task["p2p"])
    task["broken_failures"] = sorted(k for k, v in broken.items() if v != "ok")
    return task


# --------------------------------------------------------------------- execution

LINE = re.compile(r"^\S+ \((?P<id>[\w\.]+)\) \.\.\. (?P<out>ok|FAIL|ERROR|skipped|"
                  r"expected failure|unexpected success)")


def parse_unittest(text):
    """Map dotted test id -> outcome. Stdlib only, no pytest dependency."""
    out = {}
    for line in text.splitlines():
        m = LINE.match(line.strip())
        if m:
            out[m.group("id")] = m.group("out")
    return out


def run_tests(task, files):
    """Write files into a scratch dir with canonical tests and run the suite."""
    import hashlib
    key = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:10]
    scratch = RUN_ROOT / "_oracle" / f"{task['name']}-{task['seed']}-{key}"
    scratch.mkdir(parents=True, exist_ok=True)
    for rel, content in {**files, **task["tests"]}.items():
        p = scratch / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-v", "-s", "."],
                          cwd=scratch, capture_output=True, text=True, timeout=300)
    return parse_unittest(proc.stderr + proc.stdout)


def grade_run(workdir, task):
    """SWE-bench grading: reset test files from canonical, run, apply the F2P/P2P contract."""
    for rel, content in task["tests"].items():
        p = workdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)                      # agent edits to tests are discarded
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-v", "-s", "."],
                          cwd=workdir, capture_output=True, text=True, timeout=300)
    outcomes = parse_unittest(proc.stderr + proc.stdout)
    f2p_pass = [t for t in task["f2p"] if outcomes.get(t) == "ok"]
    p2p_pass = [t for t in task["p2p"] if outcomes.get(t) == "ok"]
    f2p_rate = len(f2p_pass) / len(task["f2p"]) if task["f2p"] else 1.0
    p2p_rate = len(p2p_pass) / len(task["p2p"]) if task["p2p"] else 1.0
    resolved = f2p_rate == 1.0 and p2p_rate == 1.0
    missing = [t for t in task["f2p"] + task["p2p"] if t not in outcomes]
    return dict(resolved=resolved, f2p_rate=f2p_rate, p2p_rate=p2p_rate,
                f2p_pass=len(f2p_pass), f2p_total=len(task["f2p"]),
                p2p_pass=len(p2p_pass), p2p_total=len(task["p2p"]),
                missing_tests=missing)


ARMS = {
    "deepseek-bare": lambda p, w: hb.bare_run(p, w),
    "deepseek-codex": lambda p, w: hb.codex_run(p, w),
    "deepseek-claude": lambda p, w: hb.claude_run(p, w),
    "deepseek-dsh": lambda p, w: hb.dsh_run(p, w),
}

PROMPT = """\
You are working in the repository at the current directory.

{issue}

Run the test suite to check your work. Grading re-applies the canonical test files, so
editing or deleting tests will not help.
"""


# -------------------------------------------------------------------------- stats

def pass_at_k(outcomes, k):
    """Probability at least one of k trials succeeds (capability)."""
    trials = len(outcomes)
    if trials < k:
        return None
    c = sum(outcomes)
    if trials - c < k:
        return 1.0
    p = 1.0
    for i in range(k):
        p *= (trials - c - i) / (trials - i)
    return 1 - p


def pass_hat_k(outcomes, k):
    """Probability ALL k trials succeed (reliability). tau-bench's metric."""
    trials = len(outcomes)
    if trials < k:
        return None
    c = sum(outcomes)
    if c < k:
        return 0.0
    p = 1.0
    for i in range(k):
        p *= (c - i) / (trials - i)
    return p


def main():
    seeds = list(range(SEEDS))
    tasks = []
    for name in FAMILIES:
        for s in seeds:
            t = build_task(name, s)
            if not t["valid"]:
                print(f"REJECTED invalid task {name}/{s}: f2p={len(t['f2p'])} p2p={len(t['p2p'])}")
                continue
            tasks.append(t)
    print(f"{len(tasks)} valid task instances "
          f"({len(FAMILIES)} families x {len(seeds)} seeds) x {REPS} repeats per arm; "
          f"f2p sizes {[len(t['f2p']) for t in tasks]}")
    print(f"hash-bound: {task_fingerprint(tasks)}\n")

    arms = {a: f for a, f in ARMS.items() if a in os.environ.get("CONDS", "").split(",")} \
        if os.environ.get("CONDS") else ARMS
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    out_path = HERE / os.environ.get("OUTFILE", "swebench_results.jsonl")
    records = []
    with out_path.open("w") as fh:
        for task in tasks:
            for arm, fn in arms.items():
                for rep in range(REPS):
                    # the SAME instance is re-seeded and re-run each repeat, so pass^k
                    # measures consistency on one problem rather than luck across problems
                    work = RUN_ROOT / f"{arm}__{task['name']}__s{task['seed']}__r{rep}"
                    work.mkdir(parents=True, exist_ok=True)
                    for rel, content in task["files"].items():
                        (work / rel).write_text(content)
                    for rel, content in task["visible"].items():
                        (work / rel).write_text(content)
                    res = fn(PROMPT.format(issue=task["issue"]), work)
                    g = grade_run(work, task)
                    rec = dict(task=task["name"], seed=task["seed"], rep=rep, arm=arm, **g,
                               latency_ms=res["latency_ms"], steps=res["steps"],
                               tool_calls=res["tool_calls"],
                               failed_tool_calls=res["failed_tool_calls"],
                               norm=res["norm"], text=res["text"][:4000])
                    records.append(rec)
                    fh.write(json.dumps(rec) + "\n")
                    fh.flush()
                    print(f"  {arm:16s} {task['name']:10s} s{task['seed']} r{rep} "
                          f"resolved={g['resolved']} f2p={g['f2p_pass']}/{g['f2p_total']} "
                          f"p2p={g['p2p_pass']}/{g['p2p_total']} {res['latency_ms']:>7,d}ms "
                          f"steps={res['steps']}", flush=True)

    print()
    for arm in arms:
        rs = [r for r in records if r["arm"] == arm]
        if not rs:
            continue
        n = len(rs)
        resolved = sum(r["resolved"] for r in rs)
        lo, hi = wilson(resolved, n)
        by_task = {}
        for r in rs:
            by_task.setdefault((r["task"], r["seed"]), []).append(1 if r["resolved"] else 0)
        trials = list(by_task.values())
        print(f"{arm:16s} resolved {resolved}/{n} = {resolved/n:.3f}  "
              f"95% CI [{lo:.3f},{hi:.3f}]  "
              f"mean f2p={sum(r['f2p_rate'] for r in rs)/n:.3f} "
              f"p2p={sum(r['p2p_rate'] for r in rs)/n:.3f}")
        # tau-bench estimator: average the per-task C(c,k)/C(n,k), not over a pooled list
        line = []
        for k in (2, 3, 4):
            a = [v for v in (pass_at_k(t, k) for t in trials) if v is not None]
            h = [v for v in (pass_hat_k(t, k) for t in trials) if v is not None]
            if a:
                line.append(f"pass@{k}={sum(a)/len(a):.3f}  pass^{k}={sum(h)/len(h):.3f}")
        if line:
            print("    per-task averaged:  " + "   ".join(line))
        print("    instances:  " + "  ".join(
            f"{t[0][:4]}/{t[1]}={sum(v)}/{len(v)}" for t, v in sorted(by_task.items())))
    print("\nruns:", RUN_ROOT)


def wilson(k, n, z=1.96):
    import math
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (c - m) / d), min(1.0, (c + m) / d)


def task_fingerprint(tasks):
    """Bind a result set to the exact task definitions that produced it."""
    import hashlib
    blob = json.dumps([[t["name"], t["seed"], t["f2p"], t["p2p"],
                        t["files"], t["tests"]] for t in tasks], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def validate():
    """Oracle gate: every task must have F2P tests and be resolved by its own gold patch."""
    bad = 0
    for name in FAMILIES:
        for s in range(max(REPS, 2)):
            t = build_task(name, s)
            fixed = run_tests(t, {**t["files"], **t["gold"]})
            broken = run_tests(t, t["files"])
            gold_ok = all(fixed.get(x) == "ok" for x in t["f2p"] + t["p2p"])
            broken_fails_f2p = all(broken.get(x) != "ok" for x in t["f2p"])
            status = "OK" if (t["valid"] and gold_ok and broken_fails_f2p) else "BROKEN"
            bad += status == "BROKEN"
            print(f"  {status:6s} {name}/{s}  f2p={len(t['f2p'])} p2p={len(t['p2p'])} "
                  f"gold_resolves={gold_ok} broken_fails_f2p={broken_fails_f2p}")
    print("oracle gate:", "PASS" if bad == 0 else f"FAIL {bad}")
    return bad


if __name__ == "__main__":
    if "--validate" in sys.argv:
        validate()
    else:
        main()
