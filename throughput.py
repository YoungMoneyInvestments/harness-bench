#!/usr/bin/env python3
"""Provider-side throughput: time-to-first-token and decode tokens/sec, both surfaces.

Why this is separate from hbstats.py
------------------------------------
hbstats.py reports `tok/s` = output tokens / wall seconds for a whole agent run. That
number is confounded: it folds in tool execution, process startup, and for `dsh` an npx
package resolution. Worse, it rewards arms that fail -- bare produces 12,720 output
tokens of prose because it cannot act, and scores the highest effective tok/s in the set.
An arm that rambles is not a fast arm.

This measures the *model* instead, over one request:

  * TTFT      seconds until the first content token arrives
  * tok/s     decode rate AFTER the first token, so prompt processing is excluded

It runs against both of DeepSeek's compatible surfaces, because they are different code
paths and Claude Code uses the Anthropic one. If they serve at different speeds, that is
a real property of the harness choice, not noise.

Usage: python3 throughput.py [reps]
"""
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time

DS_ENV_FILE = pathlib.Path.home() / ".config/claude-deepseek/deepseek.env"
OPENAI_URL = "https://api.deepseek.com/chat/completions"
ANTHROPIC_URL = "https://api.deepseek.com/anthropic/v1/messages"
PROMPT = ("Write the integers from 1 to 120, one per line, with no other text "
          "whatsoever. Do not stop early.")


def key():
    for line in DS_ENV_FILE.read_text().splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no DeepSeek key")


def curl_stream(url, headers, body, timeout=180):
    """Stream a completion; return (ttft_s, total_s, text)."""
    cmd = ["curl", "-sS", "-N", "-m", str(timeout), url]
    for h in headers:
        cmd += ["-H", h]
    # -N only stops CLIENT buffering; the request must also ask for a stream, or the
    # server returns one JSON document and no SSE frames ever arrive.
    cmd += ["-d", json.dumps({**body, "stream": True})]
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    ttft, text = None, ""
    for line in proc.stdout:
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            break
        try:
            ev = json.loads(payload)
        except json.JSONDecodeError:
            continue
        piece = ""
        if ev.get("type") == "content_block_delta":                  # Anthropic surface
            d = ev.get("delta") or {}
            piece = d.get("text") or d.get("thinking") or ""
        elif ev.get("choices"):                                       # OpenAI surface
            d = ev["choices"][0].get("delta") or {}
            # DeepSeek streams chain-of-thought as reasoning_content, separately from
            # content. Time the first token of either kind, or a reasoning-heavy reply
            # looks like it never started.
            piece = d.get("content") or d.get("reasoning_content") or ""
        if piece:
            if ttft is None:
                ttft = time.time() - t0
            text += piece
    proc.wait()
    return ttft, time.time() - t0, text


def count_tokens(url, headers, body):
    """Non-streaming call to get the provider's own completion_tokens count."""
    cmd = ["curl", "-sS", "-m", "180", url]
    for h in headers:
        cmd += ["-H", h]
    cmd += ["-d", json.dumps({**body, "stream": False})]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        return None
    if "usage" in d:
        return d["usage"].get("output_tokens") or d["usage"].get("completion_tokens")
    return None


def main(reps=3):
    k = key()
    surfaces = [
        ("openai  (codex / bare)", OPENAI_URL, "deepseek-flash", "openai"),
        ("anthropic (Claude Code)", ANTHROPIC_URL, "claude-sonnet-4-6", "anthropic"),
        ("anthropic (opus alias -> v4-pro)", ANTHROPIC_URL, "claude-opus-4-6", "anthropic"),
    ]
    print(f"{'surface':36s}{'out tok':>9s}{'TTFT s':>9s}{'decode tok/s':>14s}{'total s':>9s}")
    for label, url, model, style in surfaces:
        if style == "openai":
            headers = ["Content-Type: application/json", f"Authorization: Bearer {k}"]
            body = {"model": model, "max_tokens": 700,
                    "messages": [{"role": "user", "content": PROMPT}]}
        else:
            headers = ["Content-Type: application/json", "anthropic-version: 2023-06-01",
                       f"Authorization: Bearer {k}"]
            body = {"model": model, "max_tokens": 700,
                    "messages": [{"role": "user", "content": PROMPT}]}
        ttfts, rates, tots, ntok = [], [], [], []
        for _ in range(reps):
            ttft, total, text = curl_stream(url, headers, body)
            n = count_tokens(url, headers, body)
            if ttft is None or not n:
                continue
            decode_s = max(total - ttft, 1e-6)
            ttfts.append(ttft); tots.append(total)
            rates.append(n / decode_s); ntok.append(n)
        if not rates:
            print(f"{label:36s}   (no usable samples)")
            continue
        print(f"{label:36s}{statistics.median(ntok):>9.0f}{statistics.median(ttfts):>9.2f}"
              f"{statistics.median(rates):>14.1f}{statistics.median(tots):>9.2f}")
    print(f"\nmedian of {reps} samples. TTFT = seconds to first content token; decode tok/s "
          f"excludes\nprompt processing. Compare this to the effective tok/s in hbstats.py, "
          f"which is a\npipeline number and rewards arms that ramble instead of acting.")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
