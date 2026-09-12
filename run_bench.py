#!/usr/bin/env python3
"""Fable 5.1 harness benchmark: codex+fable prompt vs codex default vs raw deepseek-flash high."""
import json
import os
import pathlib
import subprocess
import time

HERE = pathlib.Path(__file__).parent
WORK = pathlib.Path("/tmp/fable-bench")
FABLE_PROMPT = WORK / "claude-fable-5.1.md"
CODEX_HOME = WORK / "ch"
MODEL = "gpt-5.6-luna"
DS_MODEL = "deepseek-flash"
RESULTS = HERE / "results.jsonl"


def codex_run(prompt, instructions_file=None):
    cmd = [
        "codex", "exec", "--json", "--skip-git-repo-check", "--ephemeral",
        "-C", str(WORK), "-m", MODEL, "-c", "model_reasoning_effort=high",
        "-s", "read-only",
    ]
    if instructions_file:
        cmd += ["-c", f"model_instructions_file={instructions_file}"]
    cmd.append(prompt)
    env = dict(os.environ, CODEX_HOME=str(CODEX_HOME))
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900)
    ms = int((time.time() - t0) * 1000)
    text, usage, err = "", {}, None
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "item.completed":
            item = ev.get("item", {})
            if item.get("type") == "agent_message":
                text += item.get("text", "")
            elif item.get("type") == "error":
                err = item.get("message")
        elif ev.get("type") == "turn.completed":
            usage = ev.get("usage", {})
    if proc.returncode != 0 and not text:
        err = (err or "") + proc.stderr[-2000:]
    return dict(text=text, usage=usage, error=err, latency_ms=ms, exit=proc.returncode)


def deepseek_run(prompt):
    key = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-a", os.environ.get("USER", ""),
         "-s", "codex-deepseek", "-w"],
        capture_output=True, text=True, check=True).stdout.strip()
    body = json.dumps({
        "model": DS_MODEL,
        "reasoning_effort": "high",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2048,
    })
    t0 = time.time()
    proc = subprocess.run(
        ["curl", "-sS", "-m", "600", "https://api.deepseek.com/chat/completions",
         "-H", "Content-Type: application/json",
         "-H", f"Authorization: Bearer {key}",
         "-d", body],
        capture_output=True, text=True, timeout=900)
    ms = int((time.time() - t0) * 1000)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return dict(text="", usage={}, error=proc.stdout[:2000], latency_ms=ms, exit=1)
    if "error" in data:
        return dict(text="", usage={}, error=str(data["error"])[:2000], latency_ms=ms, exit=1)
    msg = data["choices"][0]["message"]
    usage = data.get("usage", {})
    return dict(text=msg.get("content") or "",
                reasoning=msg.get("reasoning_content") or "",
                usage=usage, error=None, latency_ms=ms, exit=0)


def main():
    prompts = sorted((HERE / "prompts").glob("*.txt"))
    conditions = [
        ("fable", lambda p: codex_run(p, FABLE_PROMPT)),
        ("codex-default", lambda p: codex_run(p)),
        ("deepseek-flash-high", lambda p: deepseek_run(p)),
    ]
    with RESULTS.open("w") as fh:
        for name, fn in conditions:
            for path in prompts:
                prompt = path.read_text().strip()
                res = fn(prompt)
                rec = dict(condition=name, prompt_id=path.stem, prompt=prompt, **res)
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                print(f"{name:22s} {path.stem:22s} {res['latency_ms']:>7}ms "
                      f"in={res['usage'].get('input_tokens', res['usage'].get('prompt_tokens', '-'))} "
                      f"out={res['usage'].get('output_tokens', res['usage'].get('completion_tokens', '-'))} "
                      f"chars={len(res['text'])} err={str(res['error'])[:60]}")


if __name__ == "__main__":
    main()
