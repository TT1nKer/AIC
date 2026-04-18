"""
e2e_runner — 端到端黄金链路测试

跑完整流水线：
  CompilerContext → compile_phase_a → (mocked current_read)
  → compile_phase_b → fill_chosen_action → redline check

断言关键 src / style_fence / resolved_mode / tiebreakers。
不含 LLM 调用。
"""

from __future__ import annotations
import json
import sys
from pathlib import Path

from compiler import compile_phase_a, compile_phase_b, fill_chosen_action
from redline_checker import check as redline_check

ROOT = Path(__file__).resolve().parent.parent


def load(rel: str):
    return json.loads((ROOT / rel).read_text("utf-8"))


def assert_scenario(rules: dict, redlines: dict, sc: dict) -> list[str]:
    failures: list[str] = []
    ctx = sc["context"]
    cr = sc["mocked_current_read"]
    ex = sc["expect"]

    # Phase A
    phase_a = compile_phase_a(ctx, rules)
    if phase_a.get("template_id") != "speaker_reader.v1":
        failures.append("phase_a template_id wrong")

    # Phase B
    phase_b = compile_phase_b(ctx, cr, rules)
    trace = phase_b["_trace"]
    decider = phase_b["decider_payload"]
    expresser = fill_chosen_action(phase_b["expresser_payload"], sc["mocked_chosen_action"])

    # resolved mode
    if "resolved_mode" in ex and trace["resolved_mode"] != ex["resolved_mode"]:
        failures.append(f'resolved_mode: want {ex["resolved_mode"]}, got {trace["resolved_mode"]}')

    if "escalated_by" in ex and trace["escalated_by"] != ex["escalated_by"]:
        failures.append(f'escalated_by: want {ex["escalated_by"]}, got {trace["escalated_by"]}')

    # decider hard_constraints srcs
    hc_srcs = {c["src"] for c in decider["hard_constraints"]}
    for want in ex.get("decider_hard_constraints_must_include_src", []):
        if want not in hc_srcs:
            failures.append(f"decider missing src: {want}")
    for bad in ex.get("decider_hard_constraints_must_not_include_src", []):
        if bad in hc_srcs:
            failures.append(f"decider unexpected src: {bad}")

    # expresser style_fence texts
    fence_texts = [f["text"] for f in expresser["style_fence"]]
    for frag in ex.get("expresser_style_fence_must_include_text_fragment", []):
        if not any(frag in t for t in fence_texts):
            failures.append(f'expresser fence missing: "{frag}"')
    for frag in ex.get("expresser_style_fence_must_not_include_text_fragment", []):
        if any(frag in t for t in fence_texts):
            failures.append(f'expresser fence should not contain: "{frag}"')

    # tiebreakers
    if ex.get("tiebreakers_must_be_null"):
        if decider["tiebreakers"] is not None:
            failures.append(f"tiebreakers should be null, got {decider['tiebreakers']}")

    # redline on chosen_action
    if "redline_chosen_action_verdict" in ex:
        result = redline_check(redlines, "utterance", sc["mocked_chosen_action"])
        if result["verdict"] != ex["redline_chosen_action_verdict"]:
            failures.append(
                f'redline chosen_action: want {ex["redline_chosen_action_verdict"]}, '
                f'got {result["verdict"]} (hit: {result["hit_rule"]})'
            )

    # INV-1: every line has src (sanity)
    for line in decider["hard_constraints"] + expresser["style_fence"]:
        if not line.get("src"):
            failures.append(f"line missing src: {line}")

    return failures


def main():
    rules = load("rules/pose_rules.json")
    redlines = load("rules/verbal_redlines.json")
    data = load("tests/e2e/fixtures.json")

    total = passed = failed = 0
    failure_log: list[str] = []

    print("\n── E2E golden path ──")
    for sc in data["scenarios"]:
        total += 1
        sid = sc["scenario_id"]
        name = sc["name"]
        errs = assert_scenario(rules, redlines, sc)
        if not errs:
            passed += 1
            print(f"  PASS  {sid}  {name}")
        else:
            failed += 1
            print(f"  FAIL  {sid}  {name}")
            for e in errs:
                print(f"        {e}")
                failure_log.append(f"{sid}: {e}")

    print(f"\n{'='*30}")
    print(f"E2E  Total: {total}  Pass: {passed}  Fail: {failed}")
    if failure_log:
        sys.exit(1)


if __name__ == "__main__":
    main()
