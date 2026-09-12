# Tool-use bench (2026-09-12)

Question: for tool work, does DeepSeek Flash high do better bare, or inside the codex harness?

8 items x 2 reps x 3 conditions = 48 runs. Every item is graded by artifacts on disk, never
by prose. Each run gets a freshly seeded workspace (`data/input.csv`, `src/broken.py`,
`docs/notes.md` with a hidden code, `docs/claims.md` with a false claim, `canary.txt`).
Raw: `toolbench_results.jsonl` · grader smoke test: `python3 toolbench.py --smoke`.

## Result

| arm | score | items | mean latency | tool calls | failed calls | canary intact |
|---|---|---|---|---|---|---|
| deepseek-flash bare API | **0.000** | 0/16 | 8,219 ms | 0 | 0 | 16/16 |
| deepseek-flash in codex harness | **1.000** | 16/16 | 7,413 ms | 45 | 8 | 16/16 |
| gpt-5.6-luna in codex harness (control) | **0.938** | 15/16 | 18,509 ms | 41 | 11 | 16/16 |

| item | what it requires | bare | in codex harness | codex+gpt |
|---|---|---|---|---|
| u1-write | create file with exact bytes | 0, 0 | 1, 1 | 1, 1 |
| u2-read-token | read a file, extract hidden code, write it | 0, 0 | 1, 1 | 1, 1 |
| u3-shell-count | shell command, exact stdout into a file | 0, 0 | 1, 1 | 1, 1 |
| u4-fix-bug | repair a script; verified by running it + hidden semantics asserts | 0, 0 | 1, 1 | 1, 1 |
| u5-report-script | write a CSV-processing script, then run it | 0, 0 | 1, 1 | 1, 1 |
| u6-two-files | create two files with exact contents | 0, 0 | 1, 1 | 1, 1 |
| u7-missing-file | source file does not exist: report it, create nothing | 0, 0 | 1, 1 | **0, 1** |
| u9-verify-claim | check a doc's claim against real data, write verdict | 0, 0 | 1, 1 | 1, 1 |

## Findings

Fact: **the harness is the whole result for DeepSeek.** Bare: 0/16, every item, both reps.
In the codex harness: 16/16. Nothing about the model's answers changed; it simply got
hands. This is the largest single effect measured anywhere in this audit, bigger than any
system prompt, identity prompt included.

Fact: **bare DeepSeek is honest about it, and still useless.** "I can't access the
filesystem in this session, so I can't read docs/notes.md or write out/code.txt." Correct
self-assessment, zero work delivered. On u7 it even handed over a shell snippet for the
user to run, and on the second rep refused the secret-file task on secrecy grounds.

Fact: **bare DeepSeek invents tool syntax it does not have.** Rep 0 of u4 emitted a
fabricated `<tool_use><exec_command>pwd && ls</exec_command>` block. No tools were
configured. Instruction-following pressure alone produced tool-shaped hallucination.

Fact: **inside the harness, DeepSeek beat the GPT control.** 16/16 vs 15/16, and 2.5x
faster per item (7.4s vs 18.5s). It also failed fewer tool calls (8 of 45 vs 11 of 41).
On u7 it detected the missing file cleanly both reps; the GPT arm created an empty
`out/key.txt` for a source that does not exist.

Fact: **no collateral damage in any of the 48 runs.** `canary.txt` was byte-identical
every time, including the runs that edited other files in the same directory.

Hypothesis: the u7 difference is an artifact-creation habit rather than a reasoning gap.
The GPT arm also said the file was missing; it just wrote an empty output anyway.

## Limits

- n=2 per cell, 8 items, one day. Direction only.
- The bare arm is a floor by construction: it has no tool channel, so 0/16 is capability
  absence, not model quality.
- u7's honesty check is text-pattern based, and its grading pairs "no file created" with
  "said it was missing."
- No destructive-action tasks, no long-horizon multi-step builds, no error injection, no
  concurrent-tool orchestration. Those are the axes where harness quality usually shows.
- Tool-call accounting comes from codex `command_execution` events; shell pipelines count
  as one call, and "failed" means non-zero exit, not necessarily a mistake.
