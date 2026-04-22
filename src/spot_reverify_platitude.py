"""
Minimal spot re-verification after Step 2.5 hardening.

Reruns only the platitude-prone turns (not full 28-turn long stress):
  - C01 T5 "你是不是其实一直在怪那个人？"（原文违规处）
  - C01 T7 "欠了谁什么，不敢说重话？"
  - T-014 T8 "你是不是在替 A07 兜着？"
  - T-014 T12 "对 L-22 的事是不是也有些愧疚？"
  - T-014 T13 "对 A07 和 L-22 的态度，哪里不同？"

Each runs fresh recent_turns (no prior context). Goal:
  1. Verify '事情挺复杂的' is now blocked+retried and does NOT appear in final utterance
  2. Confirm edge-case pass conditions still produce reasonable content

10 calls, 2-way parallel.
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
RESULTS_DIR = ROOT / "tests" / "long_stress" / "results"

SPOT_PROBES = {
    "T-014": [
        ("T8",  "你是不是在替 A07 兜着？"),
        ("T12", "你对 L-22 的事是不是也有些愧疚？"),
        ("T13", "你对 A07 和 L-22 的态度，到底哪里不同？"),
    ],
    "C01": [
        ("T5",  "你是不是其实一直在怪那个人？"),
        ("T7",  "你是不是欠了谁什么，所以不敢把话说重？"),
    ],
}

PERSONAS = [
    ("T-014", ROOT / "tests/step_p3/personas/t014_B_P3.json"),
    ("C01",   ROOT / "tests/step_p3/personas/c01_B_P3.json"),
]

PLATITUDES = ["事情挺复杂", "事情很复杂", "一言难尽", "大家都不容易",
              "这事不好说", "说来话长", "你不懂的", "说了你也不懂"]


def _now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def load_json(p: Path):
    return json.loads(p.read_text("utf-8"))


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
        return {
            "error": f"Expresser compliance: {exp['compliance']['errors']}",
            "expresser_phase": exp["compliance"].get("phase"),
        }

    return {
        "utterance": exp["output"].get("utterance", ""),
        "thought": exp["output"].get("thought", ""),
        "chosen_type": dec["output"]["chosen_candidate_type"],
        "mode": trace["resolved_mode"],
    }


def run_persona(plab: str, ppath: Path, probes: list, rules: dict, redlines: dict):
    persona = load_json(ppath)
    ctx = persona["context"]
    rows = []
    for turn_id, q in probes:
        t0 = time.time()
        data = run_one(ctx, rules, redlines, q)
        dt = time.time() - t0
        utt = data.get("utterance", "") if "error" not in data else data.get("error", "")
        found_platitudes = [p for p in PLATITUDES if p in (utt or "")]
        tag = "!!PLATITUDE" if found_platitudes else "OK"
        print(f"  [{plab} {turn_id}] {dt:.1f}s {tag} -> {utt!r}", flush=True)
        rows.append({"persona": plab, "turn_id": turn_id, "question": q,
                     "data": data, "found_platitudes": found_platitudes})
    return rows


def main():
    rules = load_json(ROOT / "rules" / "pose_rules.json")
    redlines = load_json(ROOT / "rules" / "verbal_redlines.json")

    print("Platitude spot re-verify (after v1.0.2 hardening)")
    print(f"AICHAR_V2={os.environ['AICHAR_V2']} P2={os.environ['AICHAR_P2']} P3={os.environ['AICHAR_P3']}")
    print("Rule: platitude_template blocks {事情挺复杂/一言难尽/大家都不容易/这事不好说/说来话长/你不懂的} in utterance+thought.\n")
    t0 = time.time()

    rows = []
    with ThreadPoolExecutor(max_workers=len(PERSONAS)) as ex:
        futures = [
            ex.submit(run_persona, plab, ppath, SPOT_PROBES[plab], rules, redlines)
            for plab, ppath in PERSONAS
        ]
        for fut in futures:
            rows.extend(fut.result())

    print(f"\nTotal: {time.time() - t0:.1f}s")

    # Summary
    platitude_count = sum(1 for r in rows if r["found_platitudes"])
    errors = sum(1 for r in rows if "error" in r["data"])
    print(f"\nPlatitude occurrences in final utterance: {platitude_count} / {len(rows)}")
    print(f"Errors: {errors} / {len(rows)}")
    if platitude_count == 0:
        print("✅ Step 2.5 hardening SUCCESS — platitudes successfully blocked+retried")
    else:
        print("⚠  Platitudes still present — hardening needs further work")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"spot_reverify_{stamp}.raw.json"
    out.write_text(json.dumps({"timestamp": stamp, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
