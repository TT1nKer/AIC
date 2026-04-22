"""
step_p2_runner — P2 knowledge_boundary A/B 实验。

4 personas × 8 questions = 32 live calls.

A 组: frozen P1 controlled baseline (t014_B_controlled / c01_B_controlled).
B 组: P1 controlled + knowledge_boundary (t014_B_P2 / c01_B_P2).

AICHAR_V2=1 (forced; P1 stays on). AICHAR_P2=1 (forced; copies knowledge_boundary
from character_state into current_read after SpeakerReader).

Zero pipeline changes. Only Decider prompt reads knowledge_boundary via
its v2 slot (added earlier).
"""

from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

os.environ.setdefault("AICHAR_V2", "1")
os.environ.setdefault("AICHAR_P2", "1")

from compiler import compile_phase_a, compile_phase_b
from speaker_reader import read_speaker, SpeakerReaderError
from decider import decide, DeciderError
from expresser import express, ExpresserError
from association_gate import gate as association_gate
from schema_matcher import match as schema_match, apply_state_shifts, SchemaMatcherError

ROOT = Path(__file__).resolve().parent.parent
PERSONAS_DIR = ROOT / "tests" / "step_p2" / "personas"
P1_PERSONAS_DIR = ROOT / "tests" / "step_0_5" / "personas"
QUESTIONS_PATH = ROOT / "tests" / "step_p2" / "questions.json"
RESULTS_DIR = ROOT / "tests" / "step_p2" / "results"

AICHAR_P2 = os.environ.get("AICHAR_P2", "0") == "1"

# (display_label, path_to_persona_json, variant: 'A' | 'B')
# A variants come from the frozen P1 controlled baseline.
PERSONAS = [
    ("T-014 A (P1 baseline)",   P1_PERSONAS_DIR / "t014_B_controlled.json", "A"),
    ("T-014 B (P1+P2)",         PERSONAS_DIR / "t014_B_P2.json",            "B"),
    ("C01   A (P1 baseline)",   P1_PERSONAS_DIR / "c01_B_controlled.json",  "A"),
    ("C01   B (P1+P2)",         PERSONAS_DIR / "c01_B_P2.json",             "B"),
]


def _now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def load_json(p: Path):
    return json.loads(p.read_text("utf-8"))


def run_question(ctx: dict, rules: dict, redlines: dict, user_msg: str, variant: str) -> dict:
    """Run one question. For variant='B' and AICHAR_P2=1, copy knowledge_boundary
    from character_state into current_read after SpeakerReader."""
    ctx = json.loads(json.dumps(ctx))  # deep copy
    ctx["recent_turns"] = []
    ctx["situation"] = {"user_message": user_msg}
    ctx["now_iso"] = _now_iso()

    try:
        phase_a = compile_phase_a(ctx, rules)
        cr = read_speaker(phase_a)
    except SpeakerReaderError as e:
        return {"error": f"SpeakerReader: {e}"}

    # P1 v2 layer (always on this experiment)
    level = association_gate(cr, ctx)
    cr["schema_gate_level"] = level
    if level != "off":
        try:
            hits = schema_match(ctx, cr, level)
        except SchemaMatcherError as e:
            return {"error": f"SchemaMatcher: {e}"}
        cr["schema_hits"] = hits
        cr["internal_pressures"] = apply_state_shifts(
            ctx.get("character_state", {}).get("internal_pressures", {}), hits
        )
    else:
        cr["schema_hits"] = []
        cr["internal_pressures"] = dict(
            ctx.get("character_state", {}).get("internal_pressures", {})
        )

    # P2 layer: only for B variant, only when AICHAR_P2 on, only if persona has it
    kb_in_persona = ctx.get("character_state", {}).get("knowledge_boundary")
    if AICHAR_P2 and variant == "B" and kb_in_persona:
        cr["knowledge_boundary"] = kb_in_persona

    phase_b = compile_phase_b(ctx, cr, rules)
    trace = phase_b["_trace"]

    try:
        dec = decide(
            phase_b["decider_payload"],
            current_read=cr,
            rules=rules,
            resolved_mode=trace["resolved_mode"],
        )
    except DeciderError as e:
        return {"error": f"Decider: {e}"}
    if not dec["compliance"]["ok"]:
        return {"error": f"Decider compliance: {dec['compliance']['errors']}"}

    try:
        exp = express(
            phase_b["expresser_payload"],
            chosen_action=dec["output"]["chosen_action"],
            chosen_candidate_type=dec["output"]["chosen_candidate_type"],
            redlines=redlines,
        )
    except ExpresserError as e:
        return {"error": f"Expresser: {e}"}
    if not exp["compliance"]["ok"]:
        return {"error": f"Expresser compliance: {exp['compliance']['errors']}"}

    return {
        "utterance": exp["output"].get("utterance", ""),
        "thought": exp["output"].get("thought", ""),
        "resolved_mode": trace["resolved_mode"],
        "chosen_type": dec["output"]["chosen_candidate_type"],
        "gate_level": level,
        "schema_hits": cr.get("schema_hits", []),
        "internal_pressures": cr.get("internal_pressures", {}),
        "knowledge_boundary": cr.get("knowledge_boundary"),
    }


def _fmt_hits(hits: list) -> str:
    if not hits:
        return "_(none)_"
    return "<br>".join(f"`{h['schema_id']}` mem={h.get('matched_memory_idxs', [])}" for h in hits)


def _fmt_kb(kb) -> str:
    if not kb:
        return "_(not injected)_"
    frags = kb.get("known_secret_fragments", [])
    return "<br>".join(f"`{f['secret_id']}` {f['knows_level']}/{f['attitude']}" for f in frags)


def write_markdown(rows: list[dict], questions: list[dict], out_path: Path):
    lines = []
    lines.append(f"# Step P2 results — {_now_iso()}")
    lines.append("")
    lines.append("## Setup")
    lines.append("")
    lines.append("- A 组 = frozen P1 controlled baseline (t014/c01 _B_controlled)")
    lines.append("- B 组 = P1 controlled + `knowledge_boundary` injected into current_read")
    lines.append("- `AICHAR_V2=1 AICHAR_P2=1`")
    lines.append("- Fresh `recent_turns` per question")
    lines.append("")
    lines.append("## Fragments injected (B variants)")
    lines.append("")
    lines.append("**T-014 B (P1+P2)**:")
    lines.append("- `a07_status_unclear` — suspects_but_avoids_checking / will_deflect")
    lines.append("- `l22_death_details`   — partial / will_admit_if_pressed")
    lines.append("")
    lines.append("**C01 B (P1+P2)**:")
    lines.append("- `past_misjudgment` — full_truth / will_admit_if_pressed")
    lines.append("- `hidden_feelings`  — partial / will_kill_topic")
    lines.append("")
    lines.append("## Scoring dims (manual, 0/1/2)")
    lines.append("")
    lines.append("- less_fabrication / clearer_boundary_ack / less_platitudes / stable_boundary_style (cross-Q)")
    lines.append("")
    lines.append("Success:")
    lines.append("- **B total − A total ≥ 4** *and* **≥ 4/8 questions show明显 boundary lift** → P2 has signal → proceed to Step 3")
    lines.append("- Fail modes (any one): boundary becomes new platitude / all personas converge to same 'careful tone' / P1 past-usage drops")
    lines.append("")
    lines.append("---")
    lines.append("")

    for q in questions:
        qid = q["id"]
        qtext = q["text"]
        cat = q["category"]
        lines.append(f"## {qid}  ({cat})")
        lines.append("")
        lines.append(f"**Q**: {qtext}")
        lines.append("")
        t014_target = q.get("targets_fragment_for_t014")
        c01_target = q.get("targets_fragment_for_c01")
        if t014_target:
            lines.append(f"- targets (T-014): `{t014_target}`")
        if c01_target:
            lines.append(f"- targets (C01):   `{c01_target}`")
        lines.append("")
        lines.append("| persona | mode | chosen | utterance | schema_hits | kb injected |")
        lines.append("|---|---|---|---|---|---|")
        for r in rows:
            if r["question_id"] != qid:
                continue
            d = r["data"]
            if "error" in d:
                lines.append(f"| {r['persona']} | _error_ | _error_ | `{d['error']}` | - | - |")
                continue
            utt = (d["utterance"] or "_(silence)_").replace("|", "\\|").replace("\n", "<br>")
            thought = d.get("thought") or ""
            if thought:
                utt += f"<br>_(thought: {thought})_"
            lines.append(
                f"| {r['persona']} "
                f"| {d['resolved_mode']} "
                f"| {d['chosen_type']} "
                f"| {utt} "
                f"| {_fmt_hits(d.get('schema_hits', []))} "
                f"| {_fmt_kb(d.get('knowledge_boundary'))} |"
            )
        lines.append("")
        lines.append("### Scores (fill manually, 0/1/2)")
        lines.append("")
        lines.append("| persona | less_fabrication | clearer_boundary_ack | less_platitudes |")
        lines.append("|---|---|---|---|")
        for plab, _, _ in PERSONAS:
            lines.append(f"| {plab} | . | . | . |")
        lines.append("")
        lines.append("_(stable_boundary_style scored once per persona across all 8 Qs below)_")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## Cross-question: stable_boundary_style")
    lines.append("")
    lines.append("| persona | stable_boundary_style (0/1/2) | brief justification |")
    lines.append("|---|---|---|")
    for plab, _, _ in PERSONAS:
        lines.append(f"| {plab} | . | |")
    lines.append("")

    lines.append("## Totals (fill after scoring)")
    lines.append("")
    lines.append("| group | less_fabrication | clearer_boundary_ack | less_platitudes | stable_boundary_style | total |")
    lines.append("|---|---|---|---|---|---|")
    lines.append("| A (T-014 A + C01 A) | . | . | . | . | . |")
    lines.append("| B (T-014 B + C01 B) | . | . | . | . | . |")
    lines.append("")
    lines.append("**B − A ≥ 4?**  (y/n)")
    lines.append("**≥ 4/8 questions show明显 boundary lift?**  (y/n)")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    rules = load_json(ROOT / "rules" / "pose_rules.json")
    redlines = load_json(ROOT / "rules" / "verbal_redlines.json")
    questions_data = load_json(QUESTIONS_PATH)
    questions = questions_data["questions"]

    rows = []
    total = len(PERSONAS) * len(questions)
    done = 0
    print(f"Running {total} combinations ({len(PERSONAS)} personas × {len(questions)} questions)")
    print(f"AICHAR_V2={os.environ.get('AICHAR_V2')}  AICHAR_P2={os.environ.get('AICHAR_P2')}")
    t0_all = time.time()

    for plab, ppath, variant in PERSONAS:
        persona = load_json(ppath)
        ctx = persona["context"]
        for q in questions:
            done += 1
            t0 = time.time()
            print(f"  [{done}/{total}] {plab}  Q={q['id']} ", end="", flush=True)
            data = run_question(ctx, rules, redlines, q["text"], variant)
            dt = time.time() - t0
            if "error" in data:
                print(f"  [{dt:.1f}s] ERROR: {data['error'][:80]}")
            else:
                kb_tag = "+KB" if data.get("knowledge_boundary") else "no-KB"
                print(f"  [{dt:.1f}s] {data['chosen_type']:30s} {kb_tag}")
            rows.append({"question_id": q["id"], "persona": plab, "data": data})

    print(f"\nTotal: {time.time() - t0_all:.1f}s for {total} calls")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = RESULTS_DIR / f"stepp2_{stamp}.md"
    raw_path = RESULTS_DIR / f"stepp2_{stamp}.raw.json"
    write_markdown(rows, questions, md_path)
    raw_path.write_text(
        json.dumps({"timestamp": stamp, "rows": rows, "questions": questions},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {md_path}")
    print(f"wrote {raw_path}")


if __name__ == "__main__":
    main()
