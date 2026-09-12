#!/usr/bin/env python3
"""Render the benchmark figures as dependency-free SVG from the stored run records.

Reads results/*.jsonl and usage/*.csv, writes charts/*.svg. No plotting library.
"""
import csv
import json
import pathlib

HERE = pathlib.Path(__file__).parent
OUT = HERE / "charts"
FONT = "-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"

LABELS = {
    "codex-default": "codex default",
    "fable": "codex + Fable 5.1 prompt",
    "deepseek-flash-high": "DeepSeek Flash, bare API",
    "deepseek-fable": "DeepSeek Flash + Fable prompt, bare",
    "deepseek-bare": "DeepSeek Flash, bare API",
    "deepseek-codex": "DeepSeek Flash, codex harness",
    "codex-gpt": "GPT-5.6 Luna, codex harness",
    "codex-sol": "GPT-5.6 Sol, codex harness",
    "codex-astra": "GPT-6 Astra, codex harness",
}
COLORS = ["#4c78a8", "#f58518", "#54a24b", "#b279a2", "#9d755d", "#e45756", "#72b7b2"]


def load(path):
    return [json.loads(l) for l in (HERE / path).read_text().splitlines() if l.strip()]


def arm_stats(records):
    stats = {}
    for r in records:
        s = stats.setdefault(r["condition"], {"scores": [], "ms": []})
        s["scores"].append(r["score"])
        s["ms"].append(r["latency_ms"])
    return {k: {"score": sum(v["scores"]) / len(v["scores"]),
                "passed": sum(v["scores"]), "n": len(v["scores"]),
                "ms": sum(v["ms"]) / len(v["ms"])} for k, v in stats.items()}


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def frame(title, subtitle, width, height, body):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">
<rect width="{width}" height="{height}" fill="#ffffff"/>
<text x="32" y="44" font-family="{FONT}" font-size="22" font-weight="600" fill="#111111">{esc(title)}</text>
<text x="32" y="70" font-family="{FONT}" font-size="13" fill="#666666">{esc(subtitle)}</text>
{body}
<text x="32" y="{height - 16}" font-family="{FONT}" font-size="11" fill="#999999">harness-bench &#183; raw runs in results/ &#183; regenerate with python3 charts.py</text>
</svg>"""


def bars(title, subtitle, rows, width=900, scale_max=None, value_fmt="{:.3f}", unit=""):
    """rows: list of (label, value, color). Horizontal bars with value labels."""
    top, left, bar_h, gap = 96, 300, 30, 18
    height = top + len(rows) * (bar_h + gap) + 40
    vmax = scale_max or max(v for _, v, _ in rows)
    plot_w = width - left - 140
    body = [f'<rect x="{left}" y="{top - 6}" width="{plot_w}" height="{len(rows) * (bar_h + gap) - gap + 12}" fill="#fafafa"/>']
    for i, (label, value, color) in enumerate(rows):
        y = top + i * (bar_h + gap)
        w = max(2.0, plot_w * (value / vmax))
        body.append(f'<text x="{left - 12}" y="{y + bar_h * 0.68}" font-family="{FONT}" font-size="14" '
                    f'fill="#333333" text-anchor="end">{esc(label)}</text>')
        body.append(f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="3" fill="{color}"/>')
        text = esc(value_fmt.format(value)) + unit
        if w > 0.25 * plot_w:
            body.append(f'<text x="{left + w - 10:.1f}" y="{y + bar_h * 0.68}" font-family="{FONT}" font-size="14" '
                        f'fill="#ffffff" text-anchor="end">{text}</text>')
        else:
            body.append(f'<text x="{left + w + 10:.1f}" y="{y + bar_h * 0.68}" font-family="{FONT}" font-size="14" '
                        f'fill="#333333">{text}</text>')
    return frame(title, subtitle, width, height, "\n".join(body))


def main():
    OUT.mkdir(exist_ok=True)
    suite = arm_stats(load("results/suite_results_all.jsonl"))
    tools = arm_stats(load("results/toolbench_results.jsonl"))

    # 1. scored suite
    order = ["codex-default", "fable", "deepseek-flash-high", "deepseek-fable"]
    rows = [(f"{LABELS[k]}  ({suite[k]['passed']:.0f}/{suite[k]['n']})", suite[k]["score"], COLORS[i])
            for i, k in enumerate(order)]
    (OUT / "scored-suite.svg").write_text(bars(
        "Scored suite", "12 items x 2 reps. Every item machine graded.", rows, scale_max=1.0))

    # 2. tool suite, score beside latency
    order = ["deepseek-bare", "deepseek-codex", "codex-gpt", "codex-sol", "codex-astra"]
    rows = [(f"{LABELS[k]}  ({tools[k]['passed']:.0f}/{tools[k]['n']})", tools[k]["score"], COLORS[i])
            for i, k in enumerate(order)]
    (OUT / "tool-suite.svg").write_text(bars(
        "Tool suite: tool access decides it",
        "8 artifact-graded items x 2 reps. Graded by files on disk, never by prose.",
        rows, scale_max=1.0))
    rows = [(LABELS[k], tools[k]["ms"] / 1000, COLORS[i]) for i, k in enumerate(order)]
    (OUT / "tool-latency.svg").write_text(bars(
        "Mean latency per tool item", "Seconds per item, same suite.", rows,
        value_fmt="{:.1f}", unit="s"))

    # 2b. header banner
    price_rows = list(csv.DictReader((HERE / "usage/cross-model-cost-same-token-mix.csv").open()))
    cost_line = ", ".join(f'{r["model"].replace(" (off-peak actual)", "").replace("Claude ", "")} '
                          f'${float(r["cost_for_exported_mix_usd"]):,.2f}' for r in price_rows)
    W, H = 1280, 300
    stats = [
        ("Tool access", "0 of 16 bare  vs  16 of 16 inside a harness"),
        ("Speed, same suite", "7.4s DeepSeek  vs  25.6s Sol  vs  33.4s Astra"),
        ("Cost, same tokens", f'DeepSeek ${price_rows[0]["cost_for_exported_mix_usd"]}  vs  '
                              f'${price_rows[1]["cost_for_exported_mix_usd"]} to '
                              f'${price_rows[-1]["cost_for_exported_mix_usd"]} on the frontier'),
    ]
    body = [
        f'<rect width="{W}" height="{H}" fill="#0d1117"/>',
        f'<text x="56" y="104" font-family="{FONT}" font-size="52" font-weight="700" fill="#ffffff">harness-bench</text>',
        f'<text x="56" y="146" font-family="{FONT}" font-size="19" fill="#9aa4b2">'
        f'System prompts, tool harnesses, and cost per million tokens, measured.</text>',
    ]
    for i, (label, value) in enumerate(stats):
        y = 186 + i * 26
        body.append(f'<circle cx="62" cy="{y - 5}" r="4" fill="{COLORS[i]}"/>')
        body.append(f'<text x="82" y="{y}" font-family="{FONT}" font-size="16" fill="#e6edf3">'
                    f'<tspan fill="#9aa4b2">{esc(label)}: </tspan>{esc(value)}</text>')
    body.append(f'<line x1="56" y1="248" x2="1224" y2="248" stroke="#1f2937" stroke-width="1"/>')
    body.append(f'<text x="56" y="274" font-family="{FONT}" font-size="13" fill="#6e7681">'
                f'Scored suite, tool suite, and raw run records, reproducible with python3 charts.py</text>')
    (OUT / "header.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'role="img" aria-label="harness-bench">\n' + "\n".join(body) + "\n</svg>")

    # 3. cost comparison
    price_rows = list(csv.DictReader((HERE / "usage/cross-model-cost-same-token-mix.csv").open()))
    rows = [(r["model"], float(r["cost_for_exported_mix_usd"]), COLORS[i % len(COLORS)])
            for i, r in enumerate(price_rows)]
    (OUT / "cost-comparison.svg").write_text(bars(
        "Same token mix, vendor list prices",
        "One day of agent traffic: 1,367 requests, 154,507,769 tokens.",
        rows, value_fmt="${:,.2f}"))

    # 4. token mix
    usage = list(csv.DictReader((HERE / "usage/deepseek-usage-summary-2026-09-12.csv").open()))[0]
    hit = int(usage["input_cache_hit_tokens"]); miss = int(usage["input_cache_miss_tokens"])
    out = int(usage["output_tokens"]); total = hit + miss + out
    width, height, left, bar_w = 900, 330, 40, 820
    x = left
    body = []
    body.append(f'<text x="{left}" y="98" font-family="{FONT}" font-size="14" fill="#333333">'
                f'All tokens, one day. 97.4% of prompt tokens were cache hits.</text>')
    for label, val, color in (("cache hit", hit, "#4c78a8"), ("cache miss", miss, "#f58518"), ("output", out, "#54a24b")):
        w = bar_w * val / total
        body.append(f'<rect x="{x:.1f}" y="110" width="{max(w,1.2):.1f}" height="40" fill="{color}"/>')
        x += w
    x = left
    for label, val, color in (("cache hit 149,651,328", hit, "#4c78a8"),
                              ("cache miss 3,941,526", miss, "#f58518"),
                              ("output 914,915", out, "#54a24b")):
        body.append(f'<rect x="{x}" y="164" width="12" height="12" fill="{color}"/>')
        body.append(f'<text x="{x + 20}" y="174" font-family="{FONT}" font-size="13" fill="#333333">{esc(label)}</text>')
        x += 285
    body.append(f'<text x="{left}" y="212" font-family="{FONT}" font-size="14" fill="#333333">'
                f'Non-cache tokens, same day, zoomed in:</text>')
    x = left
    for val, color in ((miss, "#f58518"), (out, "#54a24b")):
        w = bar_w * val / (miss + out)
        body.append(f'<rect x="{x:.1f}" y="224" width="{w:.1f}" height="24" fill="{color}"/>')
        x += w
    body.append(f'<text x="{left}" y="272" font-family="{FONT}" font-size="14" fill="#333333">'
                f'154,507,769 tokens &#183; $1.59 &#183; about $0.0012 per request, off-peak rates</text>')
    (OUT / "token-mix.svg").write_text(frame(
        "Where the tokens went", "DeepSeek V4.1 Flash, 2026-09-12, provider usage export.", width, height,
        "\n".join(body)))

    print("wrote:", ", ".join(sorted(p.name for p in OUT.glob("*.svg"))))


if __name__ == "__main__":
    main()
