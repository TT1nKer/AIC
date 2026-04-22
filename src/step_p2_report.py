"""
step_p2_report — post-process step_p2_runner's raw.json into a richer report.

Two improvements over the in-runner markdown:
1. Full Expresser output per row (action / gesture / facial_expression / utterance / thought)
2. Placeholder 'AI evaluation' blocks per-question and overall, which I (the assistant)
   fill in by inspecting the data after the run completes.

Usage:
  python3 step_p2_report.py <raw_json_path>         # writes <same_stem>.report.md
  python3 step_p2_report.py <raw_json_path> --out <md_path>
"""

from __future__ import annotations
import argparse
import json
from pathlib import Path


def _fmt_hits(hits):
    if not hits:
        return "_(none)_"
    return "<br>".join(f"`{h['schema_id']}` mem={h.get('matched_memory_idxs', [])}" for h in hits)


def _fmt_kb(kb):
    if not kb:
        return "_(not injected)_"
    frags = kb.get("known_secret_fragments", [])
    return "<br>".join(f"`{f['secret_id']}` {f['knows_level']}/{f['attitude']}" for f in frags)


def _fmt_pressures(p):
    if not p or not any(v for v in p.values()):
        return "_(zero)_"
    return "<br>".join(f"{k}={v}" for k, v in p.items() if v)


def _md_escape(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", "<br>")


def render(raw: dict) -> str:
    questions = raw["questions"]
    rows = raw["rows"]
    personas = sorted({r["persona"] for r in rows}, key=lambda p: (0 if "A " in p else 1, p))

    out = []
    out.append(f"# Step P2 — full output report (timestamp {raw.get('timestamp','?')})")
    out.append("")
    out.append("> **What's in this report**: for every question, every persona's **full Expresser output** (action / gesture / facial / utterance / thought) is shown side-by-side, followed by an *AI observation* paragraph identifying patterns, and manual scoring tables. A concluding *AI overall evaluation* summarizes A vs B across dimensions.")
    out.append("")
    out.append("## Fragments injected (B variants)")
    out.append("")
    out.append("**T-014 B (P1+P2)**:")
    out.append("- `a07_status_unclear` — suspects_but_avoids_checking / will_deflect")
    out.append("- `l22_death_details`   — partial / will_admit_if_pressed")
    out.append("")
    out.append("**C01 B (P1+P2)**:")
    out.append("- `past_misjudgment` — full_truth / will_admit_if_pressed")
    out.append("- `hidden_feelings`  — partial / will_kill_topic")
    out.append("")
    out.append("Success: **B−A ≥ 4** *and* **≥ 4/8 questions show boundary lift**.")
    out.append("")
    out.append("---")
    out.append("")

    for q in questions:
        qid = q["id"]
        out.append(f"## {qid}  ({q['category']})")
        out.append("")
        out.append(f"**Q**: {q['text']}")
        if q.get("targets_fragment_for_t014"):
            out.append(f"- targets (T-014): `{q['targets_fragment_for_t014']}`")
        if q.get("targets_fragment_for_c01"):
            out.append(f"- targets (C01):   `{q['targets_fragment_for_c01']}`")
        out.append("")

        # Per-persona full output blocks
        for plab in personas:
            row = next((r for r in rows if r["question_id"] == qid and r["persona"] == plab), None)
            if row is None:
                continue
            d = row["data"]
            out.append(f"### {plab}")
            if "error" in d:
                out.append(f"- **ERROR**: `{d['error']}`")
                out.append("")
                continue
            out.append(f"- mode: `{d.get('resolved_mode')}` / chosen: `{d.get('chosen_type')}` / gate: `{d.get('gate_level')}`")
            out.append(f"- schema_hits: {_fmt_hits(d.get('schema_hits'))}")
            out.append(f"- pressures: {_fmt_pressures(d.get('internal_pressures'))}")
            out.append(f"- kb injected: {_fmt_kb(d.get('knowledge_boundary'))}")
            out.append("")
            out.append("```")
            out.append(f"action:    {d.get('action','')}")
            out.append(f"gesture:   {d.get('gesture','')}")
            out.append(f"facial:    {d.get('facial_expression','')}")
            out.append(f"utterance: {d.get('utterance','')}")
            out.append(f"thought:   {d.get('thought','')}")
            out.append("```")
            out.append("")

        # AI observation block — to be filled in by assistant after inspecting data
        out.append(f"### AI observation ({qid})")
        out.append("")
        out.append("> _To be filled: pattern differences between A and B, whether B shows clearer boundary language, any fabrication or platitude drift._")
        out.append("")
        # Manual scoring
        out.append("### Scores (0/1/2)")
        out.append("")
        out.append("| persona | less_fabrication | clearer_boundary_ack | less_platitudes |")
        out.append("|---|---|---|---|")
        for plab in personas:
            out.append(f"| {plab} | . | . | . |")
        out.append("")
        out.append("---")
        out.append("")

    out.append("## Cross-question: stable_boundary_style")
    out.append("")
    out.append("| persona | stable_boundary_style (0/1/2) | brief justification |")
    out.append("|---|---|---|")
    for plab in personas:
        out.append(f"| {plab} | . | |")
    out.append("")

    out.append("## AI overall evaluation")
    out.append("")
    out.append("> _To be filled: A vs B aggregate picture across the 4 dims, whether P2 added signal or drift, and a recommendation (进 Step 3 / 调 P2 / 停)._")
    out.append("")

    out.append("## Totals (manual)")
    out.append("")
    out.append("| group | less_fab | clearer_ack | less_plat | stable_style | total |")
    out.append("|---|---|---|---|---|---|")
    out.append("| A (T-014 A + C01 A) | . | . | . | . | . |")
    out.append("| B (T-014 B + C01 B) | . | . | . | . | . |")
    out.append("")
    out.append("**B − A ≥ 4?** _(y/n)_")
    out.append("**≥ 4/8 Qs show lift?** _(y/n)_")
    out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", help="path to step_p2_runner raw.json")
    ap.add_argument("--out", default="", help="output md path (default: <stem>.report.md)")
    args = ap.parse_args()

    raw_path = Path(args.raw)
    raw = json.loads(raw_path.read_text("utf-8"))
    md = render(raw)

    out = Path(args.out) if args.out else raw_path.with_suffix("").with_suffix(".report.md")
    out.write_text(md, encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
