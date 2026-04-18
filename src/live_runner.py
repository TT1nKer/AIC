"""
live_runner — 只读联调：用真实 SpeakerReader 替换 mocked_current_read，跑完 3 个 E2E 场景。

不写回任何状态。不更新 lessons。仅：
- 调 DeepSeek 产出 current_read
- 校验 schema
- 跑 compile_phase_b + fill_chosen_action
- 断言 resolved_mode 与场景预期一致（容许模型在其它字段上浮动）
- 打印完整 trace
"""

from __future__ import annotations
import json
import sys
from pathlib import Path

from compiler import compile_phase_a, compile_phase_b, fill_chosen_action
from speaker_reader import read_speaker, SpeakerReaderError
from redline_checker import check as redline_check

ROOT = Path(__file__).resolve().parent.parent


def load(rel: str):
    return json.loads((ROOT / rel).read_text("utf-8"))


def run_scenario(rules: dict, redlines: dict, sc: dict) -> tuple[bool, list[str], dict]:
    failures: list[str] = []
    ctx = sc["context"]
    ex = sc["expect"]

    phase_a = compile_phase_a(ctx, rules)

    try:
        cr = read_speaker(phase_a)
    except SpeakerReaderError as e:
        return False, [f"SpeakerReader rejected: {e}"], {}

    phase_b = compile_phase_b(ctx, cr, rules)
    trace = phase_b["_trace"]
    decider = phase_b["decider_payload"]
    expresser = fill_chosen_action(phase_b["expresser_payload"], sc.get("mocked_chosen_action", ""))

    expected_mode = ex.get("resolved_mode")
    if expected_mode and trace["resolved_mode"] != expected_mode:
        failures.append(f'resolved_mode: want {expected_mode}, got {trace["resolved_mode"]}')

    hc_srcs = {c["src"] for c in decider["hard_constraints"]}
    for want in ex.get("decider_hard_constraints_must_include_src", []):
        if want not in hc_srcs:
            failures.append(f"decider missing src: {want}")
    for bad in ex.get("decider_hard_constraints_must_not_include_src", []):
        if bad in hc_srcs:
            failures.append(f"decider unexpected src: {bad}")

    for s in ("utterance", "thought", "lesson_text"):
        pass  # not applicable here — no LLM-generated utterance yet

    summary = {
        "current_read": cr,
        "resolved_mode": trace["resolved_mode"],
        "escalated_by": trace["escalated_by"],
        "hard_constraints_srcs": sorted(hc_srcs),
        "style_fence_count": len(expresser["style_fence"]),
    }
    return len(failures) == 0, failures, summary


def main():
    rules = load("rules/pose_rules.json")
    redlines = load("rules/verbal_redlines.json")
    data = load("tests/e2e/fixtures.json")

    total = passed = failed = 0
    print("\n── LIVE E2E (SpeakerReader real, Decider/Expresser mocked downstream) ──\n")

    for sc in data["scenarios"]:
        total += 1
        sid = sc["scenario_id"]
        name = sc["name"]
        print(f"── {sid}  {name} ──")
        ok, errs, summary = run_scenario(rules, redlines, sc)
        if summary:
            print(f"  current_read.likely_mode     = {summary['current_read'].get('likely_mode')}")
            print(f"  current_read.rec_response    = {summary['current_read'].get('recommended_response_mode')}")
            print(f"  current_read.deviation       = {summary['current_read'].get('deviation_from_baseline')}")
            print(f"  current_read.appears_distress= {summary['current_read'].get('appears_distressed')}")
            print(f"  current_read.confidence      = {summary['current_read'].get('confidence')}")
            print(f"  current_read.evidence        = {summary['current_read'].get('evidence')}")
            print(f"  resolved_mode                = {summary['resolved_mode']}")
            print(f"  escalated_by                 = {summary['escalated_by']}")
            print(f"  hard_constraints_srcs        = {summary['hard_constraints_srcs']}")
        if ok:
            passed += 1
            print(f"  PASS\n")
        else:
            failed += 1
            print(f"  FAIL")
            for e in errs:
                print(f"    {e}")
            print()

    print(f"{'='*30}")
    print(f"LIVE  Total: {total}  Pass: {passed}  Fail: {failed}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
