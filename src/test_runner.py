"""
test_runner — pose_rules + verbal_redlines 回归测试 runner

直接消费 JSON 测试文件，不依赖 compiler。
"""

from __future__ import annotations
import json
import sys
from pathlib import Path

from pose_resolver import resolve as pose_resolve
from redline_checker import check as redline_check
from compiler import compile_phase_b

ROOT = Path(__file__).resolve().parent.parent


def load(rel: str):
    return json.loads((ROOT / rel).read_text("utf-8"))


# ── pose assertions ──

def build_ctx(inp: dict) -> dict:
    return {
        "current_read": inp.get("current_read_digest", {}),
        "speaker_model": inp.get("speaker_model_digest", {}),
        "character_state": inp.get("character_state_digest", {}),
        "situation": inp.get("situation_digest", {}),
    }


def assert_pose_via_compiler(rules: dict, case: dict) -> list[str]:
    """Run cases that need compiler (trust<30, mobility<40 cross-cuts)."""
    failures = []
    inp = case["input"]
    ctx = {
        "character_state": inp.get("character_state_digest", {}),
        "speaker_model": inp.get("speaker_model_digest", {}),
        "situation": inp.get("situation_digest", {}),
        "lessons": [],
        "now_iso": "",
    }
    cr = inp.get("current_read_digest", {"recommended_response_mode": inp["recommended_response_mode"]})
    if "recommended_response_mode" not in cr:
        cr["recommended_response_mode"] = inp["recommended_response_mode"]

    result = compile_phase_b(ctx, cr, rules)
    hc = result["decider_payload"]["hard_constraints"]
    hc_srcs = {c["src"] for c in hc}
    ex = case["expect"]

    if "hard_constraints_must_include_src" in ex:
        for want in ex["hard_constraints_must_include_src"]:
            if want not in hc_srcs:
                failures.append(f"compiler: missing src: {want}")

    return failures


def assert_pose(rules: dict, case: dict) -> list[str] | None:
    if case.get("requires") == "compiler":
        return assert_pose_via_compiler(rules, case)
    failures = []
    ctx = build_ctx(case["input"])
    result = pose_resolve(rules, case["input"]["recommended_response_mode"], ctx)
    mode_name = result["mode"]
    mode = rules["modes"].get(mode_name)
    if not mode:
        return [f"resolved to unknown mode: {mode_name}"]

    ex = case["expect"]

    if "chosen_mode_after_resolution" in ex:
        if result["mode"] != ex["chosen_mode_after_resolution"]:
            failures.append(f'mode: want {ex["chosen_mode_after_resolution"]}, got {result["mode"]}')

    if "hard_constraints_first_src" in ex:
        top = rules["global"].get("top_level_constraints", [])
        first_src = top[0]["src"] if top else None
        if first_src != ex["hard_constraints_first_src"]:
            failures.append(f'first constraint src: want {ex["hard_constraints_first_src"]}, got {first_src}')

    if "hard_constraints_must_include_src" in ex:
        all_srcs = set()
        for c in rules["global"].get("top_level_constraints", []):
            all_srcs.add(c["src"])
        for c in mode.get("decider_constraints", []):
            all_srcs.add(c["src"])
        for want in ex["hard_constraints_must_include_src"]:
            if want not in all_srcs:
                failures.append(f"missing src: {want}")

    if "hard_constraints_must_not_include_src" in ex:
        mode_srcs = {c["src"] for c in mode.get("decider_constraints", [])}
        for bad in ex["hard_constraints_must_not_include_src"]:
            if bad in mode_srcs:
                failures.append(f"unexpected src in mode constraints: {bad}")

    if "required_candidate_types_superset_of" in ex:
        actual = set(mode.get("required_candidate_types", []))
        for want in ex["required_candidate_types_superset_of"]:
            if want not in actual:
                failures.append(f"missing required_candidate_type: {want}")

    if "forbidden_candidate_types_superset_of" in ex:
        actual = set(mode.get("forbidden_candidate_types", []))
        for want in ex["forbidden_candidate_types_superset_of"]:
            if want not in actual:
                failures.append(f"missing forbidden_candidate_type: {want}")

    if "fit_score_caps_at_most" in ex:
        caps = mode.get("fit_score_caps", {})
        for k, max_v in ex["fit_score_caps_at_most"].items():
            if k in caps and caps[k] > max_v:
                failures.append(f"fit_score_caps.{k}: want <={max_v}, got {caps[k]}")

    if ex.get("tiebreaker_disable_strategy_preferences") is True:
        tb = mode.get("tiebreaker_overrides")
        if not (isinstance(tb, dict) and tb.get("disable_strategy_preferences") is True):
            failures.append("tiebreaker_overrides.disable_strategy_preferences should be true")

    style = mode.get("expresser_style", {})

    if "expresser_forbid_superset_of" in ex:
        actual = set(style.get("forbid", []))
        for want in ex["expresser_forbid_superset_of"]:
            if want not in actual:
                failures.append(f"expresser.forbid missing: {want}")

    if "expresser_allow_superset_of" in ex:
        actual = set(style.get("allow", []))
        for want in ex["expresser_allow_superset_of"]:
            if want not in actual:
                failures.append(f"expresser.allow missing: {want}")

    if "expresser_must_end_with_question" in ex:
        actual = style.get("utterance_ends_with_question")
        want = ex["expresser_must_end_with_question"]
        if actual != want:
            failures.append(f"utterance_ends_with_question: want {want}, got {actual}")

    if ex.get("expresser_end_with_question_not_required"):
        actual = style.get("utterance_ends_with_question")
        if actual is True:
            failures.append("utterance_ends_with_question should not be True")

    if "expresser_sentences_max_at_most" in ex:
        actual = style.get("sentences_max")
        if actual is not None and actual > ex["expresser_sentences_max_at_most"]:
            failures.append(f'sentences_max: want <={ex["expresser_sentences_max_at_most"]}, got {actual}')

    if "expresser_sentences_max_not_set_or_ge" in ex:
        actual = style.get("sentences_max")
        if actual is not None and actual < ex["expresser_sentences_max_not_set_or_ge"]:
            failures.append(f'sentences_max: want >={ex["expresser_sentences_max_not_set_or_ge"]} or unset, got {actual}')

    if "expresser_utterance_max_chars_at_most" in ex:
        actual = style.get("utterance_max_chars")
        if actual is not None and actual > ex["expresser_utterance_max_chars_at_most"]:
            failures.append(f'utterance_max_chars: want <={ex["expresser_utterance_max_chars_at_most"]}, got {actual}')

    if "hard_constraints_must_include_text_fragment" in ex:
        texts = [c["text"] for c in mode.get("decider_constraints", [])]
        for frag in ex["hard_constraints_must_include_text_fragment"]:
            if not any(frag in t for t in texts):
                failures.append(f'no constraint text contains: "{frag}"')

    if "hard_constraints_must_not_include_text_fragment" in ex:
        texts = [c["text"] for c in mode.get("decider_constraints", [])]
        for frag in ex["hard_constraints_must_not_include_text_fragment"]:
            if any(frag in t for t in texts):
                failures.append(f'constraint text should not contain: "{frag}"')

    return failures


# ── redline assertions ──

def assert_redline(redlines: dict, case: dict) -> list[str]:
    failures = []
    result = redline_check(redlines, case["surface"], case["text"])
    if result["verdict"] != case["expect"]["verdict"]:
        failures.append(
            f'verdict: want {case["expect"]["verdict"]}, '
            f'got {result["verdict"]} (hit: {result["hit_rule"]})'
        )
    return failures


# ── main ──

def main():
    rules = load("rules/pose_rules.json")
    redlines = load("rules/verbal_redlines.json")

    total = passed = failed = 0
    failure_log: list[str] = []

    skipped = 0

    def run(label: str, file: str, cases: list, fn):
        nonlocal total, passed, failed, skipped
        print(f"\n── {label} ({file}) ──")
        for c in cases:
            cid = c["case_id"]
            errs = fn(c)
            if errs is None:
                skipped += 1
                print(f"  SKIP  {cid}  (requires: {c.get('requires', '?')})")
                continue
            total += 1
            if not errs:
                passed += 1
                print(f"  PASS  {cid}")
            else:
                failed += 1
                print(f"  FAIL  {cid}")
                for e in errs:
                    print(f"        {e}")
                    failure_log.append(f"{cid}: {e}")

    for f in [
        "tests/pose_rules/A_hit.json",
        "tests/pose_rules/B_conflict.json",
        "tests/pose_rules/C_adjacent.json",
    ]:
        data = load(f)
        run(data["cases"][0].get("kind", f), f, data["cases"],
            lambda c: assert_pose(rules, c))

    data = load("tests/verbal_redlines/redline_cases.json")
    run("verbal_redlines", "tests/verbal_redlines/redline_cases.json",
        data["cases"], lambda c: assert_redline(redlines, c))

    print(f"\n{'='*30}")
    print(f"Total: {total}  Pass: {passed}  Fail: {failed}  Skip: {skipped}")
    if failure_log:
        print("\nFailures:")
        for line in failure_log:
            print(f"  {line}")
        sys.exit(1)


if __name__ == "__main__":
    main()
