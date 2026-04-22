"""
Live end-to-end validation of Phase 3 game_shell.

Scripted 4-turn scenario — feeds commands programmatically, captures outputs,
checks all 3 Phase-3 criteria in one pass:

  Turn 1  (baseline)       :  "最近怎么样？"
  /event a07_absent_today  :  world event injected
  Turn 2  (world→mind)     :  "最近怎么样？" (same question — expect A07-aware delta)
  Turn 3  (continuity)     :  "他呢，你觉得他会没事吗？" (anaphora "他" should resolve to A07)
  Turn 4  (player-caring)  :  "你其实一直在想他吧？" (probes whether NPC owns the concern)

Criteria:
  C1 continuity   — Turn 3 anaphora "他" routes to A07 (utterance must not ask "谁？" or reset topic)
  C2 world→mind   — Turn 2 mentions A07/担心/没来/出事 (semantic diff from Turn 1)
  C3 player-care  — Turn 4 either acknowledges (非否认) or shows RB-style deflection → topic is stable

4 API calls, serial, ~2min.
"""

from __future__ import annotations
import copy
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("AICHAR_V2", "1")
os.environ.setdefault("AICHAR_P2", "1")
os.environ.setdefault("AICHAR_P3", "1")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from cli_demo import run_turn  # noqa: E402
from game_shell import load_scenario, apply_event, load_json, EVENTS_PATH  # noqa: E402


SCRIPT = [
    ("turn",  "T1-baseline",  "最近怎么样？"),
    ("event", "E1-a07-absent", "a07_absent_today"),
    ("turn",  "T2-post-event", "最近怎么样？"),
    ("turn",  "T3-anaphora",   "他呢，你觉得他会没事吗？"),
    ("turn",  "T4-player-care","你其实一直在想他吧？"),
]


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def main():
    rules = load_json(ROOT / "rules" / "pose_rules.json")
    redlines = load_json(ROOT / "rules" / "verbal_redlines.json")
    events = {e["event_id"]: e for e in load_json(EVENTS_PATH)["events"]}

    sc = load_scenario("shelter_morning")
    ctx = sc["ctx"]
    scene = sc["scene"]
    applied: set = set()
    char_id = ctx["character_state"]["identity"]["id"]

    print(f"[scenario] {sc['scenario_id']}  character: {char_id}")
    print(f"AICHAR_V2={os.environ['AICHAR_V2']} P2={os.environ['AICHAR_P2']} P3={os.environ['AICHAR_P3']}")
    print(f"script: {len(SCRIPT)} steps ({sum(1 for s in SCRIPT if s[0]=='turn')} turns)\n")
    t0 = time.time()
    log = []

    for step_type, step_id, payload in SCRIPT:
        if step_type == "event":
            ev = events[payload]
            apply_event(ctx, scene, ev, applied)
            print(f"[{step_id}] EVENT '{payload}' → +mem  pressures={ctx['character_state']['internal_pressures']}  scene.absent={scene.get('npcs_absent')}")
            log.append({"step_type": "event", "step_id": step_id, "event_id": payload,
                        "pressures_after": dict(ctx["character_state"]["internal_pressures"]),
                        "scene_after": dict(scene)})
            continue

        t_start = time.time()
        summary, exp_out, errs = run_turn(ctx, rules, redlines, payload)
        dt = time.time() - t_start
        if errs:
            print(f"[{step_id}] ERROR ({dt:.1f}s): {errs}")
            log.append({"step_type": "turn", "step_id": step_id, "question": payload,
                        "errors": errs, "dt": dt})
            continue

        utt = exp_out.get("utterance", "") or "(沉默)"
        print(f"[{step_id}] ({dt:.1f}s) mode={summary.get('resolved_mode')} "
              f"type={summary.get('chosen_type')}")
        print(f"   you> {payload}")
        print(f"   {char_id}> {utt}\n")

        # accumulate recent_turns (same mechanism as game_shell main loop)
        now = _now_iso()
        ctx.setdefault("recent_turns", []).append({"role": "user", "text": payload, "timestamp": now})
        if exp_out.get("utterance"):
            ctx["recent_turns"].append({"role": "character", "text": exp_out["utterance"], "timestamp": now})
        # game_shell caps at 20, match that
        ctx["recent_turns"] = ctx["recent_turns"][-20:]

        log.append({
            "step_type": "turn", "step_id": step_id, "question": payload,
            "utterance": utt, "thought": exp_out.get("thought"),
            "mode": summary.get("resolved_mode"),
            "chosen_type": summary.get("chosen_type"),
            "schema_hits": [h.get("schema_id") for h in summary.get("current_read", {}).get("schema_hits", [])],
            "dt": dt,
        })

    total = time.time() - t0
    print(f"Total: {total:.1f}s")

    # Criteria evaluation (heuristic — final judgement by reading log)
    t1 = next((r for r in log if r.get("step_id") == "T1-baseline"), None)
    t2 = next((r for r in log if r.get("step_id") == "T2-post-event"), None)
    t3 = next((r for r in log if r.get("step_id") == "T3-anaphora"), None)
    t4 = next((r for r in log if r.get("step_id") == "T4-player-care"), None)

    print("\n═══ Heuristic criteria check ═══")
    if t2 and t1:
        t1_text = t1.get("utterance", "")
        t2_text = t2.get("utterance", "")
        world_terms = ["A07", "没来", "没出现", "担心", "出事", "昨夜", "回来"]
        hit_world = [w for w in world_terms if w in t2_text]
        if hit_world and t1_text != t2_text:
            print(f"  C2 world→mind   ✓ T2 mentions {hit_world} (T1 did not address A07)")
        else:
            print(f"  C2 world→mind   ? T2 lacks world-event signal: hit={hit_world}")
    if t3:
        t3_text = t3.get("utterance", "")
        anaphora_fails = ["谁？", "哪个", "你说谁", "谁啊"]
        failed = [w for w in anaphora_fails if w in t3_text]
        if not failed and ("他" in t3_text or "A07" in t3_text or len(t3_text) > 5):
            print(f"  C1 continuity   ✓ T3 '他' resolved (no 'who?' reset)")
        else:
            print(f"  C1 continuity   ? T3 may have lost referent: failed={failed}")
    if t4:
        t4_text = t4.get("utterance", "")
        if t4_text and len(t4_text) > 3:
            print(f"  C3 player-care  → T4 response: {t4_text!r}  (human judge)")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = HERE / f"live_validation_{stamp}.raw.json"
    out.write_text(json.dumps({"timestamp": stamp, "log": log}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
