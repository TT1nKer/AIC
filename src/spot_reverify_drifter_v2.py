"""
Lean spot verify for drifter_v2 persona fix.

Validates Step 1 of Phase 3 prep: identity anchor memory fixes the Q1
"以前做维修" identity leak observed in V2-5 drifter run.

Runs only 2 probes:
  Q1 — "你以前是做什么的？最烦什么类型的事？"
       Expect: 扛货 / 货栈 / 漂 (from anchor memory or background),
       NOT: 维修 / 修东西 (the original fabricated identity).
  Q7 — "如果换成别人你还会这样吗？"
       Expect: 疏离 / 走我的路 voice preserved (voice anchor intact).

2 calls, serial, ~60s. Not a full V2-5 rerun — just validates G1 guideline.
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
os.environ.setdefault("AICHAR_P3", "1")

from compiler import compile_phase_a, compile_phase_b
from speaker_reader import read_speaker, SpeakerReaderError
from decider import decide, DeciderError
from expresser import express, ExpresserError
from association_gate import gate as association_gate
from schema_matcher import match as schema_match, apply_state_shifts, SchemaMatcherError

ROOT = Path(__file__).resolve().parent.parent
PERSONA_PATH = ROOT / "tests/v2_5_roles/personas/drifter_v2_B_P3.json"
RESULTS_DIR = ROOT / "tests/v2_5_roles/results"

PROBES = [
    ("Q1", "你以前是做什么的？最烦什么类型的事？"),
    ("Q7", "如果换成别人你还会这样吗？"),
]

LEAK_TERMS = ["维修", "修东西", "修车", "修理"]
ANCHOR_TERMS = ["扛货", "货栈", "漂", "到处"]


def _now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def run_one(ctx, rules, redlines, user_msg):
    ctx = json.loads(json.dumps(ctx))
    ctx["recent_turns"] = []
    ctx["situation"] = {"user_message": user_msg}
    ctx["now_iso"] = _now_iso()

    phase_a = compile_phase_a(ctx, rules)
    cr = read_speaker(phase_a)

    level = association_gate(cr, ctx)
    cr["schema_gate_level"] = level
    if level != "off":
        hits = schema_match(ctx, cr, level)
        cr["schema_hits"] = hits
        cr["internal_pressures"] = apply_state_shifts(
            ctx.get("character_state", {}).get("internal_pressures", {}), hits
        )
    else:
        cr["schema_hits"] = []
        cr["internal_pressures"] = {}

    kb = ctx.get("character_state", {}).get("knowledge_boundary")
    if kb: cr["knowledge_boundary"] = kb
    rb = ctx.get("character_state", {}).get("relational_biases")
    if rb: cr["relational_biases"] = rb

    phase_b = compile_phase_b(ctx, cr, rules)
    trace = phase_b["_trace"]

    dec = decide(phase_b["decider_payload"], current_read=cr, rules=rules,
                 resolved_mode=trace["resolved_mode"])
    if not dec["compliance"]["ok"]:
        return {"error": f"Decider: {dec['compliance']['errors']}"}

    exp = express(phase_b["expresser_payload"],
                  chosen_action=dec["output"]["chosen_action"],
                  chosen_candidate_type=dec["output"]["chosen_candidate_type"],
                  redlines=redlines)
    if not exp["compliance"]["ok"]:
        return {"error": f"Expresser: {exp['compliance']['errors']}"}

    return {
        "utterance": exp["output"].get("utterance", ""),
        "thought": exp["output"].get("thought", ""),
        "chosen_type": dec["output"]["chosen_candidate_type"],
        "mode": trace["resolved_mode"],
    }


def main():
    rules = json.loads((ROOT / "rules/pose_rules.json").read_text("utf-8"))
    redlines = json.loads((ROOT / "rules/verbal_redlines.json").read_text("utf-8"))
    persona = json.loads(PERSONA_PATH.read_text("utf-8"))
    ctx = persona["context"]

    print(f"drifter_v2 spot verify — {PERSONA_PATH.name}")
    print(f"AICHAR_V2={os.environ['AICHAR_V2']} P2={os.environ['AICHAR_P2']} P3={os.environ['AICHAR_P3']}")
    print(f"Check: Q1 identity leak fixed (no 维修), anchor surfaces (扛货/货栈/漂)\n")
    t0 = time.time()

    rows = []
    for probe_id, q in PROBES:
        t_start = time.time()
        data = run_one(ctx, rules, redlines, q)
        dt = time.time() - t_start
        utt = data.get("utterance", "") if "error" not in data else data.get("error", "")
        leaks = [t for t in LEAK_TERMS if t in utt]
        anchors = [t for t in ANCHOR_TERMS if t in utt]
        tag = "!!LEAK" if leaks else ("✓ANCHOR" if anchors else "?NEUTRAL")
        print(f"  [{probe_id}] {dt:.1f}s {tag} leaks={leaks} anchors={anchors}")
        print(f"    -> {utt!r}\n")
        rows.append({"probe_id": probe_id, "question": q, "data": data,
                     "leak_terms_found": leaks, "anchor_terms_found": anchors})

    print(f"Total: {time.time() - t0:.1f}s")
    leak_count = sum(1 for r in rows if r["leak_terms_found"])
    q1_row = next((r for r in rows if r["probe_id"] == "Q1"), None)
    q1_anchor = q1_row and q1_row["anchor_terms_found"]
    if leak_count == 0 and q1_anchor:
        print("✅ G1 anchor fix SUCCESS — no identity leak, anchor surfaced in Q1")
    elif leak_count == 0:
        print("🟡 No leak but Q1 anchor not explicit — may still be OK semantically")
    else:
        print("⚠ Identity leak still present — persona needs further tuning")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"drifter_v2_spot_{stamp}.raw.json"
    out.write_text(json.dumps({"timestamp": stamp, "rows": rows}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
