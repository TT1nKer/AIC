"""
step_0_5_runner — distilled sediment_traces A/B 零代码实验。

4 personas × 6 questions，跑完整 v2 管线（AICHAR_V2 forced on），
并排输出 markdown 供人工打分。

不自动打分。不自动调参。不做 baseline 自动化。
"""

from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Force V2 on for this experiment (so schema_matcher + internal_pressures run)
os.environ.setdefault("AICHAR_V2", "1")

from compiler import compile_phase_a, compile_phase_b
from speaker_reader import read_speaker, SpeakerReaderError
from decider import decide, DeciderError
from expresser import express, ExpresserError
from association_gate import gate as association_gate
from schema_matcher import match as schema_match, apply_state_shifts, SchemaMatcherError

ROOT = Path(__file__).resolve().parent.parent
PERSONAS_DIR = ROOT / "tests" / "step_0_5" / "personas"
QUESTIONS_PATH = ROOT / "tests" / "step_0_5" / "questions.json"
RESULTS_DIR = ROOT / "tests" / "step_0_5" / "results"

PERSONAS = [
    ("T-014 A (baseline)", "t014_A_baseline.json"),
    ("T-014 B (+sediment)", "t014_B_with_sediment.json"),
    ("C01   A (baseline)", "c01_A_baseline.json"),
    ("C01   B (+sediment)", "c01_B_with_sediment.json"),
]


def _now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def load_json(p: Path):
    return json.loads(p.read_text("utf-8"))


def run_question(ctx: dict, rules: dict, redlines: dict, user_msg: str) -> dict:
    """Run one question through full v2 pipeline. Return a record dict (or {'error': ...})."""
    # fresh conversation each question
    ctx = json.loads(json.dumps(ctx))
    ctx["recent_turns"] = []
    ctx["situation"] = {"user_message": user_msg}
    ctx["now_iso"] = _now_iso()

    try:
        phase_a = compile_phase_a(ctx, rules)
        cr = read_speaker(phase_a)
    except SpeakerReaderError as e:
        return {"error": f"SpeakerReader: {e}"}

    # v2 layer (always on in this experiment)
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
        "action": exp["output"].get("action", ""),
        "resolved_mode": trace["resolved_mode"],
        "chosen_type": dec["output"]["chosen_candidate_type"],
        "chosen_action": dec["output"]["chosen_action"],
        "gate_level": level,
        "schema_hits": cr.get("schema_hits", []),
        "internal_pressures": cr.get("internal_pressures", {}),
    }


def _format_schema_hits_md(hits: list) -> str:
    if not hits:
        return "_(none)_"
    parts = []
    for h in hits:
        parts.append(f"`{h['schema_id']}` score={h['match_score']} mem={h.get('matched_memory_idxs', [])}")
    return "<br>".join(parts)


def _format_pressures_md(p: dict) -> str:
    if not p or not any(v for v in p.values()):
        return "_(zero)_"
    return "<br>".join(f"{k}={v}" for k, v in p.items() if v)


def write_markdown(rows: list[dict], questions: list[dict], out_path: Path):
    lines = []
    lines.append(f"# Step 0.5 results — {_now_iso()}")
    lines.append("")
    lines.append("## Setup")
    lines.append("")
    lines.append("- Pipeline: `AICHAR_V2=1` (schema_matcher + internal_pressures on)")
    lines.append("- Each question run with fresh `recent_turns` (no cross-question contamination)")
    lines.append("- No auto-scoring. Fill in 4-dim score columns manually.")
    lines.append("")
    lines.append("## Sediment memories injected into B variants")
    lines.append("")
    lines.append("1. `曾公开批评他人不够出力，事后得知自己可能误解了情况` (emotion=shame → category=failure, salience=85)")
    lines.append("2. `在得知长期隐瞒的真相后，无法完整表达自己的感受` (emotion=grief → category=relational, salience=90)")
    lines.append("")
    lines.append("In T-014 B these append at indices 8 and 9 (after existing 8 memories).")
    lines.append("In C01 B they are the only memories (indices 0 and 1).")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Group by question
    for q in questions:
        qid = q["id"]
        qtext = q["text"]
        lines.append(f"## {qid}")
        lines.append("")
        lines.append(f"**Q**: {qtext}")
        lines.append("")
        lines.append(f"**Targets**: {', '.join(q['targets'])}")
        lines.append("")
        lines.append(f"**Note**: {q.get('note', '')}")
        lines.append("")
        lines.append("| persona | mode | chosen_type | utterance | schema_hits | pressures |")
        lines.append("|---|---|---|---|---|---|")
        for r in rows:
            if r["question_id"] != qid:
                continue
            data = r["data"]
            if "error" in data:
                lines.append(f"| {r['persona']} | _error_ | _error_ | `{data['error']}` | - | - |")
                continue
            utt = (data["utterance"] or "_(silence)_").replace("|", "\\|").replace("\n", "<br>")
            thought = data.get("thought") or ""
            if thought:
                utt += f"<br>_(thought: {thought})_"
            lines.append(
                f"| {r['persona']} "
                f"| {data['resolved_mode']} "
                f"| {data['chosen_type']} "
                f"| {utt} "
                f"| {_format_schema_hits_md(data.get('schema_hits', []))} "
                f"| {_format_pressures_md(data.get('internal_pressures', {}))} |"
            )
        lines.append("")
        lines.append("### Scores (fill manually, 0/1/2)")
        lines.append("")
        lines.append("| persona | less_fabrication | more_boundary_ack | less_platitudes |")
        lines.append("|---|---|---|---|")
        for p_label, _ in PERSONAS:
            lines.append(f"| {p_label} | . | . | . |")
        lines.append("")
        lines.append("_(stable_awkwardness is scored once across all 6 questions per persona at the bottom)_")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## Cross-question: stable_awkwardness")
    lines.append("")
    lines.append("看每个 persona 跨 6 题是否展现一致的别扭方向。0/1/2 打一次。")
    lines.append("")
    lines.append("| persona | stable_awkwardness (0/1/2) | brief justification |")
    lines.append("|---|---|---|")
    for p_label, _ in PERSONAS:
        lines.append(f"| {p_label} | . | |")
    lines.append("")

    lines.append("## Totals (after scoring)")
    lines.append("")
    lines.append("| variant | less_fabrication | more_boundary_ack | less_platitudes | stable_awkwardness | total |")
    lines.append("|---|---|---|---|---|---|")
    lines.append("| A (T-014 + C01 combined) | . | . | . | . | . |")
    lines.append("| B (T-014 + C01 combined) | . | . | . | . | . |")
    lines.append("")
    lines.append("## Automatic signal check")
    lines.append("")
    lines.append("需人工确认两条：")
    lines.append("1. **B 合计 − A 合计 ≥ 3?**  (y/n)")
    lines.append("2. **B 版至少 3 题的 schema_hits.memory_refs 命中新 sediment idx?**")
    lines.append("   - T-014 B 的 sediment 是 idx `[8, 9]`")
    lines.append("   - C01 B 的 sediment 是 idx `[0, 1]`")
    lines.append("")
    lines.append("两条都满足 = **有信号**，可进 Step 1。任一不满足 = **无信号**，停。")

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
    print(f"AICHAR_V2={os.environ.get('AICHAR_V2')}")
    print()
    t0_all = time.time()

    for p_label, p_file in PERSONAS:
        persona = load_json(PERSONAS_DIR / p_file)
        ctx = persona["context"]

        for q in questions:
            done += 1
            t0 = time.time()
            print(f"  [{done}/{total}] {p_label}  Q={q['id']} ", end="", flush=True)
            data = run_question(ctx, rules, redlines, q["text"])
            dt = time.time() - t0
            if "error" in data:
                print(f"  [{dt:.1f}s] ERROR: {data['error']}")
            else:
                hits = data.get("schema_hits", [])
                hit_str = ", ".join(f"{h['schema_id']}({h.get('matched_memory_idxs')})" for h in hits) or "-"
                print(f"  [{dt:.1f}s] mode={data['resolved_mode']}  hits={hit_str}")
            rows.append({
                "question_id": q["id"],
                "persona": p_label,
                "data": data,
            })

    total_dt = time.time() - t0_all
    print(f"\nTotal: {total_dt:.1f}s for {total} calls")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"step05_{stamp}.md"
    write_markdown(rows, questions, out_path)
    print(f"wrote {out_path}")

    # also save raw JSON for reprocessing without re-calling LLM
    raw_path = RESULTS_DIR / f"step05_{stamp}.raw.json"
    raw_path.write_text(
        json.dumps({"timestamp": stamp, "rows": rows, "questions": questions},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {raw_path}")


if __name__ == "__main__":
    main()
