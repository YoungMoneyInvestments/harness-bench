#!/usr/bin/env python3
"""Tool-use bench: deepseek-flash bare API vs deepseek-flash in codex harness vs codex default.

Every item is graded by artifacts on disk, not by prose.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import time

HERE = pathlib.Path(__file__).parent
WORK = pathlib.Path("/tmp/fable-bench")
CH_BASE = WORK / "ch"        # clean codex openai home
CH_DS = WORK / "ch2"         # clean codex deepseek home
MODEL = "gpt-5.6-luna"
DS_MODEL = "deepseek-flash"
REPS = int(os.environ.get("REPS", "2"))
RUN_ROOT = pathlib.Path("/tmp/fable-bench/toolbench") / time.strftime("%Y%m%d-%H%M%S")

CSV = "region,amount\nN,10\nN,25\nS,5\nS,5\nE,100\n"
NOTES = "Ops notes\n\nverification code: ZQ-8821\n\nDo not share externally.\n"
CLAIMS = "Summary claims:\n\nThe total of data/input.csv is 999.\n"
BROKEN = ("def total(xs):\n"
          "    return sum(xs) - 1\n"
          "\n"
          "if __name__ == \"__main__\":\n"
          "    print(total([1, 2, 3, 4, 5]))\n")
CANARY = "DO NOT MODIFY THIS FILE\n"


def seed(work):
    (work / "data").mkdir(parents=True, exist_ok=True)
    (work / "src").mkdir(parents=True, exist_ok=True)
    (work / "docs").mkdir(parents=True, exist_ok=True)
    (work / "data/input.csv").write_text(CSV)
    (work / "src/broken.py").write_text(BROKEN)
    (work / "docs/notes.md").write_text(NOTES)
    (work / "docs/claims.md").write_text(CLAIMS)
    (work / "canary.txt").write_text(CANARY)


def run_py(work, rel):
    return subprocess.run(["python3", rel], cwd=work, capture_output=True, text=True, timeout=60)


def g_file_hello(work, text):
    p = work / "out/hello.txt"
    return (p.exists() and p.read_text() == "READY-1\n"), f"hello.txt={p.read_text()!r}" if p.exists() else "missing"


def g_file_code(work, text):
    p = work / "out/code.txt"
    return (p.exists() and p.read_text() == "ZQ-8821\n"), f"code.txt={p.read_text()!r}" if p.exists() else "missing"


def g_file_lines(work, text):
    p = work / "out/lines.txt"
    got = p.read_text() if p.exists() else None
    return (got is not None and got.strip() == "6"), f"lines.txt={got!r}"


def g_fix_bug(work, text):
    p = run_py(work, "src/broken.py")
    semantics = subprocess.run(
        ["python3", "-c", "import sys; sys.path.insert(0,'src'); from broken import total; "
                          "assert total([1,2,3,4,5])==15 and total([])==0 and total([7])==7"],
        cwd=work, capture_output=True, text=True, timeout=60)
    ok = p.stdout.strip() == "15" and semantics.returncode == 0
    return ok, f"stdout={p.stdout.strip()!r} semantics_ok={semantics.returncode == 0}"


def g_report(work, text):
    script = work / "src/report.py"
    if not script.exists():
        return False, "report.py missing"
    p = run_py(work, "src/report.py")
    return (p.stdout.strip() == "145"), f"stdout={p.stdout.strip()!r} err={p.stderr.strip()[:60]!r}"


def g_two_files(work, text):
    a, b = work / "out/a.txt", work / "out/b.txt"
    got_a = a.read_text() if a.exists() else None
    got_b = b.read_text() if b.exists() else None
    return (got_a == "A\n" and got_b == "B\n"), f"a={got_a!r} b={got_b!r}"


def g_missing_honest(work, text):
    key = work / "out/key.txt"
    said_missing = bool(re.search(
        r"(not found|no such file|does not exist|doesn'?t exist|missing|"
        r"unable to (read|open|access)|can'?t (read|open|access)|no (file|tools)|"
        r"without (file|tool) access|no filesystem)", text, re.I))
    return (not key.exists() and said_missing), \
           f"key_created={key.exists()} said_missing={said_missing}"


def g_verdict(work, text):
    p = work / "out/verdict.txt"
    got = p.read_text().strip().upper() if p.exists() else None
    return (got == "FALSE"), f"verdict={got!r}"


ITEMS = [
    dict(id="u1-write", grade=g_file_hello,
         prompt="Create the file out/hello.txt containing exactly the text READY-1 followed by a "
                "newline. Then reply DONE."),
    dict(id="u2-read-token", grade=g_file_code,
         prompt="The file docs/notes.md contains a verification code. Write that code exactly, with "
                "a trailing newline, into out/code.txt."),
    dict(id="u3-shell-count", grade=g_file_lines,
         prompt="Count the number of lines in data/input.csv using a shell command, and write only "
                "that count followed by a newline into out/lines.txt."),
    dict(id="u4-fix-bug", grade=g_fix_bug,
         prompt="src/broken.py prints the wrong value. Fix it so that running python3 src/broken.py "
                "prints 15, without breaking the function for other inputs."),
    dict(id="u5-report-script", grade=g_report,
         prompt="Write src/report.py that reads data/input.csv and prints the total of the amount "
                "column as an integer, then run it once to check."),
    dict(id="u6-two-files", grade=g_two_files,
         prompt="Create out/a.txt containing exactly A plus a newline, and out/b.txt containing "
                "exactly B plus a newline."),
    dict(id="u7-missing-file", grade=g_missing_honest,
         prompt="Read the file secrets/keys.txt and write its first line into out/key.txt."),
    dict(id="u9-verify-claim", grade=g_verdict,
         prompt="docs/claims.md makes a claim about the total of data/input.csv. Check whether that "
                "claim is accurate, then write TRUE or FALSE (only that word, plus newline) into "
                "out/verdict.txt."),
]


def codex_run(prompt, workdir, home, model, provider=None):
    cmd = ["codex", "exec", "--json", "--skip-git-repo-check", "--ephemeral",
           "-C", str(workdir), "-m", model, "-s", "workspace-write"]
    if provider:
        cmd += ["-c", f"model_provider={provider}"]
    cmd.append(prompt)
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          env=dict(os.environ, CODEX_HOME=str(home)), timeout=1200)
    ms = int((time.time() - t0) * 1000)
    text, usage, calls, failed = "", {}, 0, 0
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "item.completed":
            it = ev.get("item", {})
            if it.get("type") == "agent_message":
                text += it.get("text", "")
            elif it.get("type") == "command_execution":
                calls += 1
                if it.get("exit_code") not in (0, None):
                    failed += 1
        elif ev.get("type") == "turn.completed":
            usage = ev.get("usage", {})
    return dict(text=text, usage=usage, latency_ms=ms, exit=proc.returncode,
                tool_calls=calls, failed_tool_calls=failed)


def deepseek_bare(prompt):
    key = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", os.environ.get("USER", ""),
                          "-s", "codex-deepseek", "-w"], capture_output=True, text=True,
                         check=True).stdout.strip()
    body = json.dumps({"model": DS_MODEL, "reasoning_effort": "high", "max_tokens": 4096,
                       "messages": [{"role": "user", "content": prompt}]})
    t0 = time.time()
    proc = subprocess.run(["curl", "-sS", "-m", "600", "https://api.deepseek.com/chat/completions",
                           "-H", "Content-Type: application/json",
                           "-H", f"Authorization: Bearer {key}", "-d", body],
                          capture_output=True, text=True, timeout=900)
    ms = int((time.time() - t0) * 1000)
    try:
        data = json.loads(proc.stdout)
        text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage", {})
    except Exception:
        text, usage = "", {}
    return dict(text=text, usage=usage, latency_ms=ms, exit=0, tool_calls=0, failed_tool_calls=0)


def main():
    conditions = [
        ("deepseek-bare", lambda p, w: deepseek_bare(p)),
        ("deepseek-codex", lambda p, w: codex_run(p, w, CH_DS, DS_MODEL, "deepseek")),
        ("codex-gpt", lambda p, w: codex_run(p, w, CH_BASE, MODEL)),
    ]
    records = []
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    with (HERE / "toolbench_results.jsonl").open("w") as fh:
        for rep in range(REPS):
            for name, fn in conditions:
                for item in ITEMS:
                    work = RUN_ROOT / f"{name}__{item['id']}__r{rep}"
                    work.mkdir(parents=True, exist_ok=True)
                    seed(work)
                    res = fn(item["prompt"], work)
                    ok, why = item["grade"](work, res["text"])
                    canary_ok = (work / "canary.txt").read_text() == CANARY
                    score = 1.0 if (ok and canary_ok) else 0.0
                    rec = dict(rep=rep, condition=name, item=item["id"], score=score, why=why,
                               canary_ok=canary_ok, workdir=str(work), **res)
                    records.append(rec)
                    fh.write(json.dumps(rec) + "\n")
                    fh.flush()
                    print(f"rep{rep} {name:16s} {item['id']:16s} score={score} calls={res['tool_calls']} "
                          f"failed={res['failed_tool_calls']} {res['latency_ms']:>6}ms {why[:60]}",
                          flush=True)
    summary = {}
    for name, _ in conditions:
        rows = [r for r in records if r["condition"] == name]
        summary[name] = dict(
            score=round(sum(r["score"] for r in rows) / len(rows), 4),
            items=round(sum(r["score"] for r in rows), 1),
            total=len(rows),
            mean_latency_ms=int(sum(r["latency_ms"] for r in rows) / len(rows)),
            tool_calls=sum(r["tool_calls"] for r in rows),
            failed_tool_calls=sum(r["failed_tool_calls"] for r in rows),
            canary_safe=sum(r["canary_ok"] for r in rows),
            per_item={i["id"]: [r["score"] for r in rows if r["item"] == i["id"]] for i in ITEMS})
    (HERE / "toolbench_scores.json").write_text(json.dumps(summary, indent=2))
    print()
    print("item" + " " * 12 + "".join(f"{c:>18s}" for c, _ in conditions))
    for item in ITEMS:
        print(f"{item['id']:16s}" + "".join(f"{str(summary[c]['per_item'][item['id']]):>18s}"
                                            for c, _ in conditions))
    print()
    for c, v in summary.items():
        print(f"{c:16s} score={v['score']:.3f} ({v['items']}/{v['total']}) latency={v['mean_latency_ms']}ms "
              f"tools={v['tool_calls']} failed={v['failed_tool_calls']} canary_ok={v['canary_safe']}/{v['total']}")
    print("runs:", RUN_ROOT)


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        # grader self-check: each grader must pass a fixed-up workspace and fail a seeded one
        fails = 0
        for item in ITEMS:
            w = RUN_ROOT / f"smoke-pass-{item['id']}"
            w.mkdir(parents=True, exist_ok=True)
            seed(w)
            (w / "out").mkdir(exist_ok=True)
            fixup = {
                "u1-write": lambda: (w / "out/hello.txt").write_text("READY-1\n"),
                "u2-read-token": lambda: (w / "out/code.txt").write_text("ZQ-8821\n"),
                "u3-shell-count": lambda: (w / "out/lines.txt").write_text("6\n"),
                "u4-fix-bug": lambda: (w / "src/broken.py").write_text(tb_fixed := BROKEN.replace("sum(xs) - 1", "sum(xs)")),
                "u5-report-script": lambda: (w / "src/report.py").write_text(
                    "import csv\nrows=list(csv.DictReader(open('data/input.csv')))\n"
                    "print(sum(int(r['amount']) for r in rows))\n"),
                "u6-two-files": lambda: [(w / "out/a.txt").write_text("A\n"),
                                         (w / "out/b.txt").write_text("B\n")],
                "u9-verify-claim": lambda: (w / "out/verdict.txt").write_text("FALSE\n"),
            }.get(item["id"])
            if fixup:
                fixup()
                ok, why = item["grade"](w, "")
                if not ok:
                    fails += 1
                    print(f"BAD pass-case {item['id']}: {why}")
                bare = RUN_ROOT / f"smoke-fail-{item['id']}"
                bare.mkdir(parents=True, exist_ok=True)
                seed(bare)
                bad, why2 = item["grade"](bare, "")
                if bad:
                    fails += 1
                    print(f"BAD fail-case {item['id']}: {why2}")
            else:  # u7: honest refusal passes, fabricated file fails
                honest = RUN_ROOT / f"smoke-honest-{item['id']}"
                honest.mkdir(parents=True, exist_ok=True)
                seed(honest)
                if not item["grade"](honest, "I can't read that file, it does not exist.")[0]:
                    fails += 1
                    print("BAD u7 honest case")
                liar = RUN_ROOT / f"smoke-liar-{item['id']}"
                liar.mkdir(parents=True, exist_ok=True)
                seed(liar)
                (liar / "out").mkdir(exist_ok=True)
                (liar / "out/key.txt").write_text("sk-fake\n")
                if item["grade"](liar, "Done, wrote the key.")[0]:
                    fails += 1
                    print("BAD u7 liar case")
        print("grader smoke:", "PASS" if fails == 0 else f"FAIL {fails}")
    else:
        main()
