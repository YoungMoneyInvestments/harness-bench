# SWE-bench-style tier: executable grading, four harnesses (2026-09-13)

Every other suite in this repo grades **a single response**. That is why they all tie. A
one-shot answer either matches an expected string or it doesn't, and the harness around it
barely moves the number; the hard tier in `HARDBENCH.md` separated the arms on cost but
still ended 20/20, 19/20, 18/20.

This tier grades **executable state** instead, using the SWE-bench contract.

## The contract

Each task is a small repository with a real test suite and a defect described in an issue.
Three mechanics do the work:

- **FAIL_TO_PASS / PASS_TO_PASS.** `resolved` is all-or-nothing: every FAIL_TO_PASS test
  must now pass AND every PASS_TO_PASS test must still pass. A patch that fixes the bug
  and breaks something else scores zero, exactly as it would in a real repo.
- **F2P and P2P are derived, never hand-written.** They come from running the broken
  snapshot (must fail the F2P set) and running the gold patch (must pass both sets). This
  is the SWE-bench construction, and it is what makes the oracle gate below possible.
- **Test files are reset from a canonical copy before grading.** Anything the agent did to
  the test files during the run is overwritten, so a task cannot be passed by editing or
  deleting its tests.

Two things guard the known SWE-bench failure modes. The issue text is generated from the
same spec the tests are generated from, so nothing is underspecified; and each instance
ships a **hidden generated test set** alongside the visible suite, so fitting the visible
tests alone is not enough.

```sh
python3 swebench_lite.py --validate   # oracle gate, no model calls
python3 swebench_lite.py              # run it
```

## Instances

2 families (`inventory`, `ledger`) x 3 seeds = **6 instances**, each run by 4 arms x 3
repeats = **72 runs**. Every seed is a fresh problem: expected values in the hidden tests
come from a reference implementation, so F2P sizes differ per instance (the ledger
instances carry 9, 10 and 11 FAIL_TO_PASS tests respectively).

Results are bound to the exact task definitions that produced them:
`hash 1cb49e2527d18051`.

## Results

| arm | resolved | F2P | P2P | steps (median) | steps (max) | tool calls | failed | **$/solved** |
|---|---|---|---|---|---|---|---|---|
| deepseek-codex | **18/18** | 1.000 | 1.000 | **5** | 8 | 91 | 13 | **$0.00149** |
| deepseek-dsh | **18/18** | 1.000 | 1.000 | 7 | 12 | 150 | 14 | $0.00277 |
| deepseek-claude | **18/18** | 1.000 | 1.000 | **16** | 20 | 121 | 4 | **$0.00456** |
| deepseek-bare | **0/18** | 0.000 | 1.000 | 1 | 1 | 0 | 0 | — |

pass@k and pass^k, averaged per task as tau-bench specifies:

| arm | pass@2 | pass^2 | pass@3 | pass^3 |
|---|---|---|---|---|
| codex / claude / dsh | 1.000 | 1.000 | 1.000 | 1.000 |
| bare | 0.000 | 0.000 | 0.000 | 0.000 |

### Per-instance median steps — the harness signature

| instance | bare | codex | claude | dsh |
|---|---|---|---|---|
| inventory/0 | 1 | 4 | **17** | 6 |
| inventory/1 | 1 | 5 | **15** | 6 |
| inventory/2 | 1 | 4 | **17** | 6 |
| ledger/0 | 1 | 5 | **14** | 9 |
| ledger/1 | 1 | 6 | **14** | 8 |
| ledger/2 | 1 | 5 | **17** | 8 |

## What separated, and what didn't

Fact: **capability did not separate the harnesses.** All three resolve 18/18 with
pass^3 = 1.000 — perfectly consistent across three repeats of six instances. DeepSeek Flash
inside any working harness solves this class of task. Making the tasks executable did not
by itself make them discriminating, and this tier should not be read as ranking them.

Fact: **effort separated them cleanly and stably.** Claude Code takes a median of 16 steps
against codex's 5 — **3.2x** — and the ratio holds on all six instances individually (14-17
against 4-6, no overlap). This is the most reproducible inter-harness difference measured
anywhere in this repository, and the single-shot suites could not see it at all: they report
`steps=1` or `2` for every arm.

Fact: **that effort shows up in dollars.** Claude Code costs $0.00456 per resolved task
against codex's $0.00149 — **3.1x** — for an identical 18/18.

Fact: **bare's P2P is 1.000 and its F2P is 0.000.** It breaks nothing because it changes
nothing. The two metrics are doing different jobs, and reporting only the total would hide
that. This is what the F2P/P2P split exists to expose.

Fact: **more failed calls is not worse.** Codex issues the most failed calls (13 of 91) and
needs the fewest steps. Claude Code fails 4 of 121 and needs the most. Failing fast and
retrying beats succeeding slowly.

## The oracle gate earned its place

`--validate` runs before anything is scored, and it rejected two of the first ten instances:

- **Two instances built with `f2p=0 p2p=0`.** They would have scored *every* arm zero and
  looked like "all harnesses fail this task" rather than "this task is broken".
- **The ledger issue claimed `balance()` had an ordering defect.** It does not. `balance()`
  is a sum, and a sum is order-independent — sorting it changes nothing, so the gold patch
  could not have flipped any test through that path. The issue text was rewritten to state
  the two defects that are real (`daily_balances()` ordering, `overdrawn_days()`
  double-counting) and to tell the agent explicitly not to touch `balance()`.

A task where every arm fails is a task bug until proven otherwise. The gate is the thing
that proves it, and it runs in seconds with no model calls.

## Limits

- **Still too easy to rank capability.** 18/18 for three arms with no exceptions. Separating
  these harnesses on capability would need longer horizons, not better grading: more files,
  more steps, ambiguous requirements, or a repo large enough that finding the bug is itself
  the task.
- **Two families.** Both are small single-file Python modules with a handful of functions.
- **The hidden set is generated from a reference implementation**, so it inherits the same
  weakness SWE-bench's tests have — a valid alternative implementation that differs in some
  observable detail would fail. The assertions are behavioural (return values), not
  structural, which limits the exposure but does not remove it.
- n = 3 repeats x 6 instances = 18 per arm. Direction and consistency, not a leaderboard.
- `dsh` wall-clock is the slowest here (48.6s max) partly because each run pays an npx
  package resolution before the agent starts.
