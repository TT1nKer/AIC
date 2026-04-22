"""
step_p3_runner — P3 relational_biases A/B experiment.

Frozen baselines:
  A 组: t014_B_P2_controlled / c01_B_P2_controlled (P1 sediment + P2 KB)
  B 组: t014_B_P3 / c01_B_P3 (P1 + P2 + P3 relational_biases)

4 personas × 6 questions = 24 calls, parallelized 4-way (~5-6 min).
"""

from __future__ import annotations
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

os.environ.setdefault("AICHAR_V2", "1")
os.environ.setdefault("AICHAR_P2", "1")
os.environ.setdefault("AICHAR_P3", "1")

from compiler import compile_phase_a, compile_phase_b
from speaker_reader import read_speaker, SpeakerReaderError
from decider import decide, DeciderError, validate_knowledge_boundary, validate_relational_biases
from expresser import express, ExpresserError
from association_gate import gate as association_gate
from schema_matcher import match as schema_match, apply_state_shifts, SchemaMatcherError

ROOT = Path(__file__).resolve().parent.parent
PERSONAS_DIR = ROOT / "tests" / "step_p3" / "personas"
P2_PERSONAS_DIR = ROOT / "tests" / "step_p2" / "personas"
QUESTIONS_PATH = ROOT / "tests" / "step_p3" / "questions.json"
RESULTS_DIR = ROOT / "tests" / "step_p3" / "results"

AICHAR_P2 = os.environ.get("AICHAR_P2", "0") == "1"
AICHAR_P3 = os.environ.get("AICHAR_P3", "0") == "1"

PERSONAS = [
    ("T-014 A (P1+P2)",    P2_PERSONAS_DIR / "t014_B_P2_controlled.json", "A"),
    ("T-014 B (P1+P2+P3)", PERSONAS_DIR    / "t014_B_P3.json",            "B"),
    ("C01   A (P1+P2)",    P2_PERSONAS_DIR / "c01_B_P2_controlled.json",  "A"),
    ("C01   B (P1+P2+P3)", PERSONAS_DIR    / "c01_B_P3.json",             "B"),
]


def _now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def load_json(p: Path):
    return json.loads(p.read_text("utf-8"))


def run_question(ctx: dict, rules: dict, redlines: dict, user_msg: str, variant: str) -> dict:
    ctx = json.loads(json.dumps(ctx))
    ctx["recent_turns"] = []
    ctx["situation"] = {"user_message": user_msg}
    ctx["now_iso"] = _now_iso()

    try:
        phase_a = compile_phase_a(ctx, rules)
        cr = read_speaker(phase_a)
    except SpeakerReaderError as e:
        return {"error": f"SpeakerReader: {e}"}

    # P1 v2 layer
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

    # P2: both A and B carry KB (P2 is the frozen baseline for this experiment)
    kb = ctx.get("character_state", {}).get("knowledge_boundary")
    if AICHAR_P2 and kb:
        kb_errs = validate_knowledge_boundary(kb)
        if kb_errs:
            return {"error": f"KB invalid: {kb_errs}"}
        cr["knowledge_boundary"] = kb

    # P3: only B variant and only when AICHAR_P3
    rb = ctx.get("character_state", {}).get("relational_biases")
    if AICHAR_P3 and variant == "B" and rb:
        rb_errs = validate_relational_biases(rb)
        if rb_errs:
            return {"error": f"RB invalid: {rb_errs}"}
        cr["relational_biases"] = rb

    phase_b = compile_phase_b(ctx, cr, rules)
    trace = phase_b["_trace"]

    try:
        dec = decide(
            phase_b["decider_payload"],
            current_read=cr, rules=rules,
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
        "gesture": exp["output"].get("gesture", ""),
        "facial_expression": exp["output"].get("facial_expression", ""),
        "resolved_mode": trace["resolved_mode"],
        "chosen_type": dec["output"]["chosen_candidate_type"],
        "gate_level": level,
        "schema_hits": cr.get("schema_hits", []),
        "internal_pressures": cr.get("internal_pressures", {}),
        "knowledge_boundary": cr.get("knowledge_boundary"),
        "relational_biases": cr.get("relational_biases"),
    }


def _run_persona_serial(plab, ppath, variant, questions, rules, redlines):
    persona = load_json(ppath)
    ctx = persona["context"]
    rows_local = []
    for q in questions:
        t0 = time.time()
        data = run_question(ctx, rules, redlines, q["text"], variant)
        dt = time.time() - t0
        if "error" in data:
            print(f"  [{plab} {q['id']}] {dt:.1f}s ERROR: {data['error'][:60]}", flush=True)
        else:
            rb_tag = "+RB" if data.get("relational_biases") else "no-RB"
            print(f"  [{plab} {q['id']}] {dt:.1f}s {data['chosen_type']:30s} {rb_tag}", flush=True)
        rows_local.append({"question_id": q["id"], "persona": plab, "data": data})
    return rows_local


def main():
    rules = load_json(ROOT / "rules" / "pose_rules.json")
    redlines = load_json(ROOT / "rules" / "verbal_redlines.json")
    questions_data = load_json(QUESTIONS_PATH)
    questions = questions_data["questions"]

    total = len(PERSONAS) * len(questions)
    print(f"Running {total} combinations  AICHAR_V2={os.environ.get('AICHAR_V2')} AICHAR_P2={os.environ.get('AICHAR_P2')} AICHAR_P3={os.environ.get('AICHAR_P3')}")
    print(f"Parallelism: {len(PERSONAS)} personas concurrent (each persona's Qs serial)")
    t0_all = time.time()

    rows = []
    with ThreadPoolExecutor(max_workers=len(PERSONAS)) as ex:
        futures = {
            ex.submit(_run_persona_serial, plab, ppath, variant, questions, rules, redlines): plab
            for plab, ppath, variant in PERSONAS
        }
        for fut in as_completed(futures):
            rows.extend(fut.result())

    print(f"\nTotal: {time.time() - t0_all:.1f}s for {total} calls")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = RESULTS_DIR / f"stepp3_{stamp}.raw.json"
    raw_path.write_text(
        json.dumps({"timestamp": stamp, "rows": rows, "questions": questions},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {raw_path}")


if __name__ == "__main__":
    main()
