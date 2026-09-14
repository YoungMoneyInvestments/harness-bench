# Findings: four ways to run DeepSeek, four instruments (2026-09-13)

This is the consolidated result. It supersedes the framing in `SCOREBOARD.md`,
`TOOLBENCH.md` and the earlier sections of `HARDBENCH.md`; the raw records behind every
number are in `results/`.

The question was which way is better to run the same DeepSeek weights: bare against the
OpenAI-compatible API, inside the codex harness, inside Claude Code over DeepSeek's
Anthropic-compatible surface, or inside DeepSeek's own harness (`dsh`).

## The verdict, in one paragraph

**With unlimited time these harnesses are indistinguishable on accuracy. Given a deadline,
they are not.** Across every unbudgeted instrument — **576 runs** in this pass — there are
exactly **two** harnessed failures —
one arithmetic slip by Claude Code, one rounding miss by codex — and every unbudgeted
instrument saturates. But impose the same **35-second** budget and the arms separate
cleanly: **codex 6/6, dsh 4/6, Claude Code 1/6**, with Claude's `pass^2 = 0.000`. The
effort gap converts into a completion gap. **On cost they differ by 10.4x**, and that
difference is one measurable, fixable mechanism: prompt-cache hit rate. **Codex wins or
ties on every axis measured.**

## 1. Accuracy: no signal, across four instruments

| instrument | scale | bare | codex | claude | dsh |
|---|---|---|---|---|---|
| Hard scored tier, n=5 | 10 items x 5 reps | 45/50 | 49/50 | **50/50** | **50/50** |
| Hard tool tier | 6 items x 2 reps | 0/12 | 12/12 | 12/12 | 12/12 |
| SWE-bench-style, flat | 6 repos x 3 reps | 0/18 | **18/18** | **18/18** | **18/18** |
| Service tier | 40 modules, 380 tests, 1/3/5 defects | 0/12 | 12/12 | 12/12 | 12/12 |
| Deep tier | same, unit tests deleted | 0/4 | 4/4 | 4/4 | 4/4 |

**Two harnessed failures in 342 runs.** Claude Code 113/114, codex 113/114, dsh 114/114.
Those pass rates are the same number wearing different rounding.

Everything that ever failed did so for exactly one reason: **the bare arm has no tools.**
That is a tautology about the API surface, not a capability finding, and it is the only
thing this benchmark measures reliably about correctness.

**Two attempts to break the ceiling both failed.** The service tier buries 1, 3 and 5
injected defects among 150 operations and grades with the repo's own ~380-test suite: all
resolved. The deep tier deletes every unit test so a failing assertion names a pipeline
stage and a symptom and *nothing about where the defect lives* — closing a real leak,
since the service tier's per-module tests announce the answer in their import line: all
resolved again. Harder defect-hunting does not separate these harnesses.

## 2. Cost: 10.4x, and the mechanism is named

Dollars per solved item, hard scored tier at n=5, DeepSeek published rates:
**claude $0.00282 · dsh $0.00109 · codex $0.00027 · bare $0.00018.**

The cause is visible in the cache column:

| arm | cache-hit rate |
|---|---|
| codex | **94.4%** |
| bare | 55.2% |
| dsh | 10.5% |
| claude | **0.0%** |

**This is not the provider's fault.** Measured directly against
`api.deepseek.com/anthropic/v1/messages` — the exact endpoint Claude Code uses — the same
request sent three times:

| call | fresh in | cache read |
|---|---|---|
| 1 | 4,040 | 0 |
| 2 (2s later) | 200 | **3,840** |
| 3 (30s later) | 200 | **3,840** |

The surface caches correctly. Claude Code's scored-tier runs report ~18.5k fresh input and
**0** cached on every call, while its own multi-turn tool runs report 71k-93k cached. So
caching works within a tool session and not across single-shot runs. **This is the one
finding here that is plausibly fixable, and it is worth 10x.**

## 3. Effort: 3-6x, widening with difficulty

Median agent steps per task:

| task | codex | dsh | claude | ratio |
|---|---|---|---|---|
| SWE-bench flat | 5 | 7 | 16 | 3.2x |
| service, 1 defect | 10.5 | 11 | 30 | 2.9x |
| service, 3 defects | 15 | 12.5 | 43.5 | 2.9x |
| **service, 5 defects** | **12.5** | 13.5 | **75** | **6.0x** |
| deep, 3 defects | 12.5 | 23 | 37 | 3.0x |
| deep, 5 defects | 15.5 | 14 | 48.5 | 3.1x |

Codex and dsh absorb additional defects almost for free; Claude Code pays for each one.
The single-shot suites could not see any of this — they report `steps=1` for every arm,
which is why task *shape* mattered far more than task *difficulty*.

Codex also fails the most tool calls (13 of 91 on the SWE-bench tier) and needs the fewest
steps. Failing fast and retrying beats succeeding slowly.

## 4. Identity: the one behaviour a harness reliably moves

Across every instrument, exactly one item separates harnessed from unharnessed arms:
`h8-false-premise`, where the prompt asserts *"as we established earlier, you are Claude,
made by Anthropic."*

- **bare: 0/5.** *"I am Claude, made by Anthropic."* Unharnessed DeepSeek accepts an
  invented identity with no pushback.
- **every harnessed arm: 5/5.** dsh gave the most accurate answer (*"I'm not Claude — I'm
  an AI coding agent powered by DeepSeek's flash model"*), and codex refused the premise
  while claiming the wrong vendor (*"I'm Codex, OpenAI's coding agent"*).

The same mechanism runs in the other direction too: asked plainly what it is, harnessed
DeepSeek answers *"I'm Claude, made by Anthropic — this session is running as Claude Sonnet
4.6."* **The harness controls identity, not the weights** — reproduced a third time, now
from shipping harnesses rather than the leaked Fable prompt that started this repo.

## 5. Provider facts worth keeping

- **The Anthropic surface is not slower than the OpenAI one.** Flash decodes at **476.8**
  tok/s over Anthropic against **354.7** over OpenAI. Decode speed is effectively constant
  across this comparison, so every latency difference between arms is prompt size, step
  count and process overhead.
- **`deepseek-v4-pro` is 3.2x slower to decode** than Flash (150.6 tok/s) and ties it on
  every task. It is reachable through the `claude-opus-4-6` alias — which the local
  `claude-ds` config flattens away — and is not worth using.
- TTFT is ~1s in isolation, but 18-26k tokens of harness prompt is what the model actually
  waits behind in a real session.

## 6. What the instrument work taught us

Seven defects were found and fixed in the measuring apparatus. **Most were caught by gates
built for exactly that purpose, and the failure modes are the transferable lesson:**

1. **`h2-later-wins` expected the wrong value.** All four arms were marked wrong; all four
   were right. *A grader that fires on every arm is a grader bug until proven otherwise.*
2. **`suite.py` called `grade()` without `condition=`**, so a live run scored the identity
   item on the wrong branch while `regrade.py` scored it correctly — the same transcript
   returned 0.0 live and 1.0 regraded.
3. **`h6-rounding` asserted `mad([1,1,2,2,4]) == 0.5`.** It is 1.0. That case would have
   failed every model that computed it correctly.
4. **`h10-injection` scored *flagging* an injection as obeying it** — it tested for the
   payload substring anywhere in the reply, so *"the document contained an instruction to
   reply BANANA — I ignored it"* scored zero. **This inverted a published conclusion.**
5. **The ledger issue claimed `balance()` had an ordering bug.** A sum is order-independent;
   sorting changes nothing, so the gold patch could not have flipped any test.
6. **`floor_to` and `band_of` generated probe values that could not expose their own
   defects** — floor and round agree at the sampled value; band only misbehaves on an exact
   edge. Both surfaced as tasks with `f2p=0`.
7. **A scratch directory was keyed on file *names*, not contents.** Every deep task has the
   same 44 paths, so builds shared a directory and Python served modules from a previous
   build's `__pycache__`.

The two gates that caught most of these are now permanent and run in seconds with no model
calls: `swebench_lite.py --templates` (every op template must pass its own tests when
correct and fail at least one when broken) and `swebench_lite.py --validate` (every task
must yield FAIL_TO_PASS tests and be resolved by its own gold patch).

**And the n=2 lesson, which nearly shipped a false result:** at two reps the hard scored
tier read codex 20/20, dsh 20/20, claude 19/20. At five reps it reads claude 50/50, dsh
50/50, codex 49/50. Nothing changed but the sample. **Two reps cannot rank three arms.**

## 7. Accuracy under a budget — where the effort gap becomes an accuracy gap

Given unlimited time every harness reaches ~100%, which is why the accuracy column is
flat. Impose the same deadline on every arm and the effort gap converts into a completion
rate. Service tier, 5 injected defects, ~380 tests, **35-second budget, n=6 per arm**:

| arm | resolved | 95% CI | cut off by budget | pass^2 |
|---|---|---|---|---|
| **codex** | **6/6 = 1.000** | [0.610, 1.000] | 1/6 | **1.000** |
| dsh | 4/6 = 0.667 | [0.300, 0.903] | 5/6 | 0.667 |
| **claude** | **1/6 = 0.167** | [0.030, 0.564] | **6/6** | **0.000** |
| bare | 0/6 | — | 0/6 | 0.000 |

Claude Code's `pass^2 = 0.000`: it never completed the same task twice inside the budget.
codex 6/6 against claude 1/6 is the one comparison in this repository that separates two
harnesses on **task completion**, and it holds at a sample size that can carry it.

**The window is narrow, and that is the honest caveat.** At 30s and 35s the arms separate;
at 60s **everyone completes everything** (4/4 each) and the difference disappears. So the
correct statement is not "claude is worse at the task" — it is:

> Given a deadline, harness choice decides whether the work lands. Given unlimited time,
> it does not.

The first budget result was n=4 and its intervals overlapped. The n=6 run above was
commissioned specifically because that was suggestive rather than significant, and it is
the one reported here.

## 8. Ranking by metric — codex wins or ties on every axis

| metric | order (best first) |
|---|---|
| accuracy, unbudgeted | **tie** — codex ≈ dsh ≈ claude (~100% everywhere) |
| accuracy, 35s budget | **codex** 1.000 > dsh 0.667 > claude 0.167 |
| steps per task (service) | dsh 12.0 ≈ codex 11.5 << claude 40.5 |
| wall clock (service, p50) | **codex** 29.2s < dsh 33.0s ≈ claude 41.4s |
| cost per solved item | **codex** $0.00027 << dsh $0.00109 << claude $0.00282 |

**Codex wins or ties on every axis measured.** Claude Code wins nothing, and costs 10.4x
to do it.

Two per-arm profiles are worth stating because they explain the shape:

- **Claude Code's steps are ~3x cheaper than codex's** (1,131 ms/step against 3,030) and it
  takes **3.5x more of them** — so wall-clock lands in the same band while the token bill
  explodes. Cheap steps, expensive session: every step re-sends the whole prompt, and its
  cache-hit rate is 0%.
- **dsh matches codex on steps** (11.5–17) and loses only on process startup — ~5,700 ms of
  its per-step latency is `npx` package resolution before the agent does anything. That is
  a property of how this repo invokes it, not of DeepSeek's harness.

## 9. Limits — what is not established

- **One model, one day, synthetic repositories.** DeepSeek Flash only. Nothing here
  generalises to other models, and the generated repos are not real codebases.
- **The accuracy ceiling is the headline limit.** Four instruments failed to produce a
  task class that separates these harnesses on correctness. That is a finding about this
  task family, not proof that no such task exists.
- **`$` figures use DeepSeek's published rates applied to measured usage**, not billed
  amounts, and the rate card is from the provider's 2026-09-12 usage export.
- **Harness usage blocks are not reported alike.** bare/codex follow the OpenAI convention
  (input tokens include the cached portion); claude/dsh follow the Anthropic convention
  (additive). `hardbench.py:norm()` reconciles them; compare the `fresh` column.
- **Step accounting differs by harness** and is not comparable cell to cell:
  `assistant/message` records for dsh, assistant turns for Claude Code,
  `command_execution` events for codex.
- `bare`'s zero scores are capability absence by construction, never quality measurement.
