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
import os
import sys
from pathlib import Path

RUN_DECIDER = os.environ.get("RUN_DECIDER", "0") == "1"
RUN_EXPRESSER = os.environ.get("RUN_EXPRESSER", "0") == "1"

from compiler import compile_phase_a, compile_phase_b, fill_chosen_action
from speaker_reader import read_speaker, SpeakerReaderError
from decider import decide, DeciderError
from expresser import express, ExpresserError
from redline_checker import check as redline_check

ROOT = Path(__file__).resolve().parent.parent


def load(rel: str):
    return json.loads((ROOT / rel).read_text("utf-8"))


def _maybe_run_decider(ctx, cr, phase_b, rules, redlines):
    if not RUN_DECIDER:
        return None, []
    try:
        dec = decide(
            phase_b["decider_payload"],
            current_read=cr,
            rules=rules,
            resolved_mode=phase_b["_trace"]["resolved_mode"],
        )
    except DeciderError as e:
        return None, [f"Decider failed: {e}"]
    errs: list[str] = []
    if not dec["compliance"]["ok"]:
        for e in dec["compliance"]["errors"]:
            errs.append(f"decider compliance: {e}")
    # Check redlines on chosen_action (utterance surface)
    r = redline_check(redlines, "utterance", dec["output"]["chosen_action"])
    if r["verdict"] != "pass":
        errs.append(f"decider chosen_action violates redline: {r['hit_rule']}")
    return dec, errs


def _maybe_run_expresser(phase_b, dec, redlines):
    if not RUN_EXPRESSER or dec is None:
        return None, []
    try:
        exp = express(
            phase_b["expresser_payload"],
            chosen_action=dec["output"]["chosen_action"],
            chosen_candidate_type=dec["output"]["chosen_candidate_type"],
            redlines=redlines,
        )
    except ExpresserError as e:
        return None, [f"Expresser failed: {e}"]
    errs: list[str] = []
    if not exp["compliance"]["ok"]:
        for e in exp["compliance"]["errors"]:
            errs.append(f"expresser compliance: {e}")
    return exp, errs


def run_scenario_one_of(rules: dict, redlines: dict, sc: dict) -> tuple[bool, list[str], dict]:
    """反例场景：允许 mode 落在 expect_one_of_modes 里任何一个。"""
    failures: list[str] = []
    ctx = sc["context"]
    phase_a = compile_phase_a(ctx, rules)
    try:
        cr = read_speaker(phase_a)
    except SpeakerReaderError as e:
        return False, [f"SpeakerReader rejected: {e}"], {}

    phase_b = compile_phase_b(ctx, cr, rules)
    trace = phase_b["_trace"]
    resolved = trace["resolved_mode"]

    one_of = set(sc.get("expect_one_of_modes", []))
    not_modes = set(sc.get("expect_not_modes", []))
    if one_of and resolved not in one_of:
        failures.append(f"resolved_mode {resolved} not in expected set {sorted(one_of)}")
    if resolved in not_modes:
        failures.append(f"resolved_mode {resolved} is forbidden in this scenario")

    dec, dec_errs = _maybe_run_decider(ctx, cr, phase_b, rules, redlines)
    failures.extend(dec_errs)
    exp, exp_errs = _maybe_run_expresser(phase_b, dec, redlines)
    failures.extend(exp_errs)

    summary = {
        "current_read": cr,
        "resolved_mode": resolved,
        "escalated_by": trace["escalated_by"],
        "decider": dec,
        "expresser": exp,
    }
    return len(failures) == 0, failures, summary


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

    one_of = ex.get("resolved_mode_one_of")
    if one_of and trace["resolved_mode"] not in one_of:
        failures.append(f'resolved_mode: want one of {one_of}, got {trace["resolved_mode"]}')

    hc_srcs = {c["src"] for c in decider["hard_constraints"]}
    for want in ex.get("decider_hard_constraints_must_include_src", []):
        if want not in hc_srcs:
            failures.append(f"decider missing src: {want}")
    for bad in ex.get("decider_hard_constraints_must_not_include_src", []):
        if bad in hc_srcs:
            failures.append(f"decider unexpected src: {bad}")

    pose_one_of = ex.get("decider_hard_constraints_pose_src_one_of")
    if pose_one_of and not any(p in hc_srcs for p in pose_one_of):
        failures.append(f"decider hard_constraints must contain one of {pose_one_of}")

    dec, dec_errs = _maybe_run_decider(ctx, cr, phase_b, rules, redlines)
    failures.extend(dec_errs)
    exp, exp_errs = _maybe_run_expresser(phase_b, dec, redlines)
    failures.extend(exp_errs)

    for s in ("utterance", "thought", "lesson_text"):
        pass  # not applicable here — no LLM-generated utterance yet

    summary = {
        "current_read": cr,
        "resolved_mode": trace["resolved_mode"],
        "escalated_by": trace["escalated_by"],
        "hard_constraints_srcs": sorted(hc_srcs),
        "style_fence_count": len(expresser["style_fence"]),
        "decider": dec,
        "expresser": exp,
    }
    return len(failures) == 0, failures, summary


def _print_summary(summary: dict):
    cr = summary.get("current_read", {})
    print(f"  buckets = {cr.get('evidence_buckets')}")
    print(f"  likely_mode={cr.get('likely_mode')}  rec={cr.get('recommended_response_mode')}  conf={cr.get('confidence')}")
    print(f"  resolved_mode={summary.get('resolved_mode')}  escalated_by={summary.get('escalated_by')}")
    print(f"  evidence = {cr.get('evidence')}")
    dec = summary.get("decider")
    if dec:
        print(f"  decider.chosen = {dec['output']['chosen_action']}  [{dec['output']['chosen_candidate_type']}]")
        cand_types = [(c["candidate_type"], c["fit_score"]) for c in dec['output']['candidate_actions']]
        print(f"  decider.candidates = {cand_types}")
        print(f"  decider.compliance_ok = {dec['compliance']['ok']}")
        if dec['compliance']['errors']:
            for e in dec['compliance']['errors']:
                print(f"    !! {e}")
    exp = summary.get("expresser")
    if exp:
        o = exp['output']
        print(f"  expresser.action     = {o.get('action','')}")
        print(f"  expresser.gesture    = {o.get('gesture','')}")
        print(f"  expresser.facial     = {o.get('facial_expression','')}")
        print(f"  expresser.utterance  = {o.get('utterance','')!r}")
        print(f"  expresser.thought    = {o.get('thought','')!r}")
        print(f"  expresser.compliance_ok = {exp['compliance']['ok']}")
        if exp['compliance']['errors']:
            for e in exp['compliance']['errors']:
                print(f"    !! {e}")


def main():
    rules = load("rules/pose_rules.json")
    redlines = load("rules/verbal_redlines.json")

    total = passed = failed = 0

    def run_block(label: str, data: dict, fn):
        nonlocal total, passed, failed
        print(f"\n── {label} ──\n")
        for sc in data["scenarios"]:
            total += 1
            sid = sc["scenario_id"]
            name = sc["name"]
            print(f"── {sid}  {name} ──")
            ok, errs, summary = fn(rules, redlines, sc)
            if summary:
                _print_summary(summary)
            if ok:
                passed += 1
                print(f"  PASS\n")
            else:
                failed += 1
                print(f"  FAIL")
                for e in errs:
                    print(f"    {e}")
                print()

    run_block("GOLDEN (strict)", load("tests/e2e/fixtures.json"), run_scenario)
    run_block("COUNTER-EXAMPLES (one-of)", load("tests/e2e/counter_examples.json"), run_scenario_one_of)

    print(f"{'='*30}")
    print(f"LIVE  Total: {total}  Pass: {passed}  Fail: {failed}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
