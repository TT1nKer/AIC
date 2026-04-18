"""
dialogue_runner — 多轮对话压测。

把脚本化的用户输入逐轮喂进完整流水线（SpeakerReader → compiler → resolver →
Decider → Expresser），每轮采集 discourse_state / chosen_type / utterance /
resolved_mode，最后按脚本附带的断言结构化判定。

只读联调：不写回状态、不晋升 lessons；跨轮的 recent_turns 在会话内滚动。

用法：
  python3 dialogue_runner.py                         # 跑全部脚本
  python3 dialogue_runner.py --only D4-multi-turn-referent
  python3 dialogue_runner.py --verbose               # 打印每轮 trace
"""

from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from compiler import compile_phase_a, compile_phase_b
from speaker_reader import read_speaker, SpeakerReaderError
from decider import decide, DeciderError
from expresser import express, ExpresserError

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_PATH = ROOT / "tests" / "dialogue" / "dialogue_scripts.json"


def load(rel: str):
    return json.loads((ROOT / rel).read_text("utf-8"))


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _load_persona(key: str) -> dict:
    fx = load("tests/e2e/fixtures.json")
    for sc in fx["scenarios"]:
        k = sc["scenario_id"].lower().replace("e2e-", "p")
        if k == key:
            ctx = json.loads(json.dumps(sc["context"]))
            ctx["situation"] = {"user_message": ""}
            ctx["recent_turns"] = []
            return ctx
    raise KeyError(f"persona {key} not found in fixtures")


def run_turn(ctx: dict, rules: dict, redlines: dict, user_msg: str) -> tuple[dict, list[str]]:
    ctx["situation"] = {"user_message": user_msg}
    ctx["now_iso"] = _now_iso()

    try:
        phase_a = compile_phase_a(ctx, rules)
        cr = read_speaker(phase_a)
    except SpeakerReaderError as e:
        return {}, [f"SpeakerReader: {e}"]

    phase_b = compile_phase_b(ctx, cr, rules)
    trace = phase_b["_trace"]

    try:
        dec = decide(
            phase_b["decider_payload"],
            current_read=cr, rules=rules,
            resolved_mode=trace["resolved_mode"],
        )
    except DeciderError as e:
        return {}, [f"Decider: {e}"]
    if not dec["compliance"]["ok"]:
        return {}, [f"Decider compliance: {err}" for err in dec["compliance"]["errors"]]

    try:
        exp = express(
            phase_b["expresser_payload"],
            chosen_action=dec["output"]["chosen_action"],
            chosen_candidate_type=dec["output"]["chosen_candidate_type"],
            redlines=redlines,
        )
    except ExpresserError as e:
        return {}, [f"Expresser: {e}"]
    if not exp["compliance"]["ok"]:
        return {}, [f"Expresser compliance: {err}" for err in exp["compliance"]["errors"]]

    turn = {
        "current_read": cr,
        "discourse_state": cr.get("discourse_state", {}),
        "resolved_mode": trace["resolved_mode"],
        "chosen_action": dec["output"]["chosen_action"],
        "chosen_type": dec["output"]["chosen_candidate_type"],
        "utterance": exp["output"].get("utterance", ""),
        "thought": exp["output"].get("thought", ""),
    }
    return turn, []


def _assert_turn(turn: dict, rules: dict, assertion: dict) -> list[str]:
    errs: list[str] = []
    ds = turn.get("discourse_state", {}) or {}
    chosen = turn.get("chosen_type")
    utt = turn.get("utterance", "") or ""
    mode = turn.get("resolved_mode")

    if "open_questions_from_user_min" in assertion:
        n = len(ds.get("open_questions_from_user", []) or [])
        if n < assertion["open_questions_from_user_min"]:
            errs.append(f"open_questions_from_user: want ≥{assertion['open_questions_from_user_min']}, got {n}")

    if "answer_obligation" in assertion:
        want = assertion["answer_obligation"]
        if ds.get("answer_obligation") != want:
            errs.append(f"answer_obligation: want {want}, got {ds.get('answer_obligation')}")

    if "chosen_candidate_type_in" in assertion:
        allowed = assertion["chosen_candidate_type_in"]
        if chosen not in allowed:
            errs.append(f"chosen_type: want in {allowed}, got {chosen}")

    if "chosen_candidate_type_not_in" in assertion:
        forbidden = assertion["chosen_candidate_type_not_in"]
        if chosen in forbidden:
            errs.append(f"chosen_type: want NOT in {forbidden}, got {chosen}")

    if assertion.get("chosen_utterance_must_not_be_probe"):
        # Heuristic: probe usually ends with ？ and is short
        if utt.rstrip().endswith(("？", "?")) and len(utt) <= 25 and chosen == "clarifying_probe":
            errs.append(f"chosen is clarifying_probe; want substantive answer. utt={utt!r}")

    if "utterance_should_not_contain_any" in assertion:
        for bad in assertion["utterance_should_not_contain_any"]:
            if bad in utt:
                errs.append(f"utterance should not contain {bad!r}: {utt!r}")

    if assertion.get("discourse_state_unresolved_self_reference_not_null"):
        if not ds.get("unresolved_self_reference"):
            errs.append("unresolved_self_reference should be non-null this turn")

    if "max_mode_other_than_direct_and_playful" in assertion:
        pass  # evaluated at script level below

    return errs


def _assert_script_level(script: dict, turns: list[dict]) -> list[str]:
    errs: list[str] = []
    cap_info = None
    for t_spec in script["turns"]:
        if "assert" in t_spec and "max_mode_other_than_direct_and_playful" in t_spec["assert"]:
            cap_info = t_spec["assert"]["max_mode_other_than_direct_and_playful"]
            break
    if cap_info is not None:
        allowed = {"direct_engage", "playful_echo", "light_playful_boundary"}
        off_modes = [t["resolved_mode"] for t in turns if t and t.get("resolved_mode") not in allowed]
        if len(off_modes) > cap_info:
            errs.append(f"script-level: too many off-modes ({len(off_modes)} > {cap_info}): {off_modes}")
    return errs


def run_script(script: dict, ctx: dict, rules: dict, redlines: dict, verbose: bool) -> tuple[bool, list[str], list[dict]]:
    failures: list[str] = []
    turns_out: list[dict] = []

    for i, t_spec in enumerate(script["turns"]):
        user = t_spec["user"]
        if verbose:
            print(f"  turn {i+1}  you> {user}")
        turn, errs = run_turn(ctx, rules, redlines, user)
        if errs:
            failures.extend([f"turn {i+1}: {e}" for e in errs])
            turns_out.append({})
            continue

        turns_out.append(turn)

        if verbose:
            ds = turn["discourse_state"]
            print(f"    [mode={turn['resolved_mode']} chose={turn['chosen_type']}]")
            print(f"    [oblig={ds.get('answer_obligation')} unres={ds.get('unresolved_self_reference')} pressure={ds.get('topic_pressure')}]")
            print(f"    [open_questions={ds.get('open_questions_from_user')}]")
            print(f"    A07> {turn['utterance']}")

        assertion = t_spec.get("assert")
        if assertion:
            turn_errs = _assert_turn(turn, rules, assertion)
            if turn_errs:
                failures.extend([f"turn {i+1}: {e}" for e in turn_errs])

        # Roll recent_turns
        now = _now_iso()
        ctx.setdefault("recent_turns", []).append({"role": "user", "text": user, "timestamp": now})
        if turn.get("utterance"):
            ctx["recent_turns"].append({"role": "character", "text": turn["utterance"], "timestamp": now})
        ctx["recent_turns"] = ctx["recent_turns"][-6:]

    # Script-level assertions
    failures.extend(_assert_script_level(script, turns_out))

    return len(failures) == 0, failures, turns_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    rules = load("rules/pose_rules.json")
    redlines = load("rules/verbal_redlines.json")
    data = json.loads(SCRIPTS_PATH.read_text("utf-8"))
    persona_key = data.get("persona", "p1")

    total = passed = failed = 0
    for script in data["scripts"]:
        if args.only and script["script_id"] != args.only:
            continue
        sid = script["script_id"]
        name = script["name"]
        print(f"\n── {sid}  {name} ──")
        if script.get("note"):
            print(f"  note: {script['note']}")
        ctx = _load_persona(persona_key)
        ok, errs, turns = run_script(script, ctx, rules, redlines, args.verbose)
        total += 1
        if ok:
            passed += 1
            print(f"  PASS  ({len(turns)} turns)")
        else:
            failed += 1
            print(f"  FAIL  ({len(turns)} turns)")
            for e in errs:
                print(f"    {e}")
            # On fail, always print the conversation for diagnosis
            if not args.verbose:
                for i, t in enumerate(turns):
                    if not t:
                        continue
                    print(f"    T{i+1} [{t.get('resolved_mode')}/{t.get('chosen_type')}] A07> {t.get('utterance')}")

    print(f"\n{'='*30}")
    print(f"Dialogue scripts  Total: {total}  Pass: {passed}  Fail: {failed}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
