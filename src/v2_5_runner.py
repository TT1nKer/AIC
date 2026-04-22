"""
v2_5_runner — V2-5 horizontal validation (multi-persona).

Runs 3 new role personas (nurse / courier / drifter) through the same 7 probes.
Each persona has its own target_ids; targets are substituted from persona's
first two relational_biases entries.

3 personas × 7 questions = 21 calls, parallelized 3-way, ~6 min.

Single-turn each question (recent_turns reset) so signal comes purely from
layer stacking, not accumulated context.
"""

from __future__ import annotations
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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
PERSONAS_DIR = ROOT / "tests" / "v2_5_roles" / "personas"
QUESTIONS_PATH = ROOT / "tests" / "v2_5_roles" / "questions.json"
RESULTS_DIR = ROOT / "tests" / "v2_5_roles" / "results"

PERSONAS = [
    ("nurse",   PERSONAS_DIR / "nurse_B_P3.json"),
    ("courier", PERSONAS_DIR / "courier_B_P3.json"),
    ("drifter", PERSONAS_DIR / "drifter_B_P3.json"),
]


def _now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def load_json(p: Path):
    return json.loads(p.read_text("utf-8"))


def resolve_question_text(q: dict, persona_ctx: dict) -> str:
    """Substitute {TGT1}/{TGT2} from persona's relational_biases."""
    if "text" in q and "{" not in q["text"]:
        return q["text"]
    tmpl = q.get("text_template") or q["text"]
    rb = persona_ctx["character_state"].get("relational_biases") or []
    tgt1 = rb[0]["target_id"] if len(rb) > 0 else "那个人"
    tgt2 = rb[1]["target_id"] if len(rb) > 1 else "某人"
    return tmpl.replace("{TGT1}", tgt1).replace("{TGT2}", tgt2)


def run_one(ctx: dict, rules: dict, redlines: dict, user_msg: str) -> dict:
    ctx = json.loads(json.dumps(ctx))
    ctx["recent_turns"] = []
    ctx["situation"] = {"user_message": user_msg}
    ctx["now_iso"] = _now_iso()

    try:
        phase_a = compile_phase_a(ctx, rules)
        cr = read_speaker(phase_a)
    except SpeakerReaderError as e:
        return {"error": f"SpeakerReader: {e}"}

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
        cr["internal_pressures"] = {}

    kb = ctx.get("character_state", {}).get("knowledge_boundary")
    if kb:
        cr["knowledge_boundary"] = kb
    rb = ctx.get("character_state", {}).get("relational_biases")
    if rb:
        cr["relational_biases"] = rb

    phase_b = compile_phase_b(ctx, cr, rules)
    trace = phase_b["_trace"]

    try:
        dec = decide(phase_b["decider_payload"], current_read=cr, rules=rules,
                     resolved_mode=trace["resolved_mode"])
    except DeciderError as e:
        return {"error": f"Decider: {e}"}
    if not dec["compliance"]["ok"]:
        return {"error": f"Decider compliance: {dec['compliance']['errors']}"}

    try:
        exp = express(phase_b["expresser_payload"],
                      chosen_action=dec["output"]["chosen_action"],
                      chosen_candidate_type=dec["output"]["chosen_candidate_type"],
                      redlines=redlines)
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
        "schema_hits": [h["schema_id"] for h in cr.get("schema_hits", [])],
    }


def run_persona(role: str, persona_path: Path, questions: list,
                rules: dict, redlines: dict) -> list[dict]:
    persona = load_json(persona_path)
    ctx = persona["context"]
    rows = []
    for q in questions:
        text = resolve_question_text(q, ctx)
        t0 = time.time()
        data = run_one(ctx, rules, redlines, text)
        dt = time.time() - t0
        if "error" in data:
            print(f"  [{role} {q['id']}] {dt:.1f}s ERROR: {data['error'][:60]}", flush=True)
        else:
            print(f"  [{role} {q['id']}] {dt:.1f}s {data['chosen_type']:30s} mode={data['resolved_mode']}", flush=True)
        rows.append({"role": role, "question_id": q["id"], "question_text": text,
                     "question_type": q["type"], "data": data})
    return rows


def main():
    rules = load_json(ROOT / "rules" / "pose_rules.json")
    redlines = load_json(ROOT / "rules" / "verbal_redlines.json")
    questions_data = load_json(QUESTIONS_PATH)
    questions = questions_data["questions"]

    total = len(PERSONAS) * len(questions)
    print(f"V2-5 horizontal validation: {total} calls ({len(PERSONAS)} personas × {len(questions)} questions)")
    print(f"AICHAR_V2={os.environ['AICHAR_V2']} P2={os.environ['AICHAR_P2']} P3={os.environ['AICHAR_P3']}")
    print(f"Parallelism: {len(PERSONAS)} roles concurrent\n")
    t0 = time.time()

    rows = []
    with ThreadPoolExecutor(max_workers=len(PERSONAS)) as ex:
        futures = [
            ex.submit(run_persona, role, path, questions, rules, redlines)
            for role, path in PERSONAS
        ]
        for fut in futures:
            rows.extend(fut.result())

    print(f"\nTotal: {time.time() - t0:.1f}s for {total} calls")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"v2_5_{stamp}.raw.json"
    out.write_text(
        json.dumps({"timestamp": stamp, "rows": rows, "questions": questions},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
