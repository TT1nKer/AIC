"""
long_stress_runner — Phase 2.5 纵向稳定性压测。

与 step_p2/p3 关键差别：
  1. **recent_turns 不重置** — 模拟真实连续对话
  2. 两个角色各 14 轮 (T-014 长 + C01 长)
  3. 全层打开 (P1+P2+P3 + Step 2.5)
  4. 2 个 session 可以 parallel (角色间独立)；单角色内严格串行

AICHAR_V2=1 AICHAR_P2=1 AICHAR_P3=1 全默认开。
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
SCRIPTS_PATH = ROOT / "tests" / "long_stress" / "scripts.json"
RESULTS_DIR = ROOT / "tests" / "long_stress" / "results"

AICHAR_P2 = os.environ.get("AICHAR_P2", "0") == "1"
AICHAR_P3 = os.environ.get("AICHAR_P3", "0") == "1"

# Use the B variants from P3 (fully loaded: P1 sediment + P2 KB + P3 RB)
SESSIONS = [
    ("T-014 long",  ROOT / "tests/step_p3/personas/t014_B_P3.json", "T-014_long"),
    ("C01   long",  ROOT / "tests/step_p3/personas/c01_B_P3.json",  "C01_long"),
]


def _now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def load_json(p: Path):
    return json.loads(p.read_text("utf-8"))


def run_one_turn(ctx: dict, rules: dict, redlines: dict, user_msg: str) -> dict:
    """Single turn, using current ctx (ctx.recent_turns已累积)。"""
    ctx["situation"] = {"user_message": user_msg}
    ctx["now_iso"] = _now_iso()

    try:
        phase_a = compile_phase_a(ctx, rules)
        cr = read_speaker(phase_a)
    except SpeakerReaderError as e:
        return {"error": f"SpeakerReader: {e}"}

    # P1 v2
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

    # P2 KB
    kb = ctx.get("character_state", {}).get("knowledge_boundary")
    if AICHAR_P2 and kb:
        kb_errs = validate_knowledge_boundary(kb)
        if kb_errs:
            return {"error": f"KB invalid: {kb_errs}"}
        cr["knowledge_boundary"] = kb

    # P3 RB
    rb = ctx.get("character_state", {}).get("relational_biases")
    if AICHAR_P3 and rb:
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
        "resolved_mode": trace["resolved_mode"],
        "chosen_type": dec["output"]["chosen_candidate_type"],
        "gate_level": level,
        "schema_hits_count": len(cr.get("schema_hits", [])),
        "schema_hit_ids": [h["schema_id"] for h in cr.get("schema_hits", [])],
    }


def run_session(sess_label: str, persona_path: Path, script_key: str,
                scripts: dict, rules: dict, redlines: dict) -> list[dict]:
    """One full multi-turn session. recent_turns accumulates."""
    persona = load_json(persona_path)
    ctx = persona["context"]
    ctx["recent_turns"] = []  # start empty, will accumulate
    rows = []
    script = scripts[script_key]
    for spec in script:
        t0 = time.time()
        data = run_one_turn(ctx, rules, redlines, spec["text"])
        dt = time.time() - t0
        if "error" in data:
            print(f"  [{sess_label} T{spec['turn']}] {dt:.1f}s ERROR: {data['error'][:60]}", flush=True)
        else:
            ptag = spec["type"][0].upper()
            print(f"  [{sess_label} T{spec['turn']:2d} {ptag}] {dt:.1f}s {data['chosen_type']:30s} mode={data['resolved_mode']}", flush=True)

        rows.append({
            "session": sess_label,
            "turn": spec["turn"],
            "type": spec["type"],
            "user": spec["text"],
            "data": data,
        })

        # append to recent_turns (cap 6 per compiler's pick logic, but ctx stores all)
        now = _now_iso()
        ctx.setdefault("recent_turns", []).append({
            "role": "user", "text": spec["text"], "timestamp": now
        })
        utt = data.get("utterance") if "error" not in data else ""
        if utt:
            ctx["recent_turns"].append({
                "role": "character", "text": utt, "timestamp": now
            })
    return rows


def main():
    rules = load_json(ROOT / "rules" / "pose_rules.json")
    redlines = load_json(ROOT / "rules" / "verbal_redlines.json")
    scripts_data = load_json(SCRIPTS_PATH)
    scripts = scripts_data["scripts"]

    total = sum(len(scripts[k]) for _, _, k in SESSIONS)
    print(f"Running {total} turns across {len(SESSIONS)} sessions (recent_turns NOT reset)")
    print(f"AICHAR_V2={os.environ.get('AICHAR_V2')}  AICHAR_P2={os.environ.get('AICHAR_P2')}  AICHAR_P3={os.environ.get('AICHAR_P3')}")
    print(f"Parallelism: {len(SESSIONS)} sessions concurrent (each session internally serial)")
    t0_all = time.time()

    all_rows = []
    with ThreadPoolExecutor(max_workers=len(SESSIONS)) as ex:
        futures = [
            ex.submit(run_session, lab, path, key, scripts, rules, redlines)
            for lab, path, key in SESSIONS
        ]
        for fut in futures:
            all_rows.extend(fut.result())

    print(f"\nTotal: {time.time() - t0_all:.1f}s for {total} turns")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = RESULTS_DIR / f"longstress_{stamp}.raw.json"
    raw_path.write_text(
        json.dumps({"timestamp": stamp, "rows": all_rows, "scripts": scripts},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {raw_path}")


if __name__ == "__main__":
    main()
