"""
cli_demo — 最小交互 REPL。

你输入一句 → 跑完整条链 → 打印简短 trace + 角色 utterance。
只读联调：不写回状态、不晋升 lessons；会话内 recent_turns 滚动（最多 6 条）。

用法：
  python3 cli_demo.py                 # 默认 persona (A07 高玩笑基线)
  python3 cli_demo.py --debug         # 显示完整 trace
  python3 cli_demo.py --persona <key> # 从 PERSONAS 里选
  python3 cli_demo.py --reset         # 清空当前会话 recent_turns（REPL 内也可输 :reset）
REPL 命令：
  :q / :quit / :exit      退出
  :reset                   清空当前会话
  :debug                   切换 debug 输出
  :state                   打印当前 CharacterState / SpeakerModel 摘要
"""

from __future__ import annotations
import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from compiler import compile_phase_a, compile_phase_b
from speaker_reader import read_speaker, SpeakerReaderError
from decider import decide, DeciderError
from expresser import express, ExpresserError
from association_gate import gate as association_gate
from schema_matcher import match as schema_match, apply_state_shifts, SchemaMatcherError

import os as _os_v2
AICHAR_V2 = _os_v2.environ.get("AICHAR_V2", "0") == "1"

ROOT = Path(__file__).resolve().parent.parent


def load(rel: str):
    return json.loads((ROOT / rel).read_text("utf-8"))


# ── personas (keyed by name) ──

def _personas() -> dict[str, dict]:
    """Pull initial contexts from E2E fixtures + any single-persona files under tests/e2e/."""
    fx = load("tests/e2e/fixtures.json")
    personas = {}
    for sc in fx["scenarios"]:
        key = sc["scenario_id"].lower().replace("e2e-", "p")
        ctx = json.loads(json.dumps(sc["context"]))
        ctx["situation"] = {"user_message": ""}
        ctx["recent_turns"] = []
        personas[key] = {"name": sc["name"], "context": ctx}
    # single-persona JSON files (produced by from_doomsday.py etc.)
    for f in sorted((ROOT / "tests" / "e2e").glob("persona_*.json")):
        data = json.loads(f.read_text("utf-8"))
        key = f.stem.replace("persona_", "")  # e.g. doomsday_t014
        ctx = data.get("context", data)
        ctx["situation"] = {"user_message": ""}
        ctx["recent_turns"] = []
        personas[key] = {"name": data.get("scenario_id", key), "context": ctx}
    personas["default"] = personas.get("p1", personas[next(iter(personas))])
    return personas


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


# ── rendering ──

def _short(summary: dict, character_name: str, utterance: str) -> str:
    cr = summary["current_read"]
    return (
        f"[trace] likely={cr.get('likely_mode')}  mode={summary['resolved_mode']}  "
        f"chose={summary['chosen_type']}\n"
        f"{character_name}> {utterance}"
    )


def _debug(summary: dict, exp_out: dict, character_name: str) -> str:
    cr = summary["current_read"]
    lines = []
    lines.append(f"[buckets] {cr.get('evidence_buckets')}")
    lines.append(f"[read]    likely={cr.get('likely_mode')}  rec={cr.get('recommended_response_mode')}  conf={cr.get('confidence')}  dev={cr.get('deviation_from_baseline')}")
    if cr.get('evidence'):
        lines.append(f"[evidence] {cr.get('evidence')}")
    if summary.get("escalated_by"):
        lines.append(f"[escalate] {summary['escalated_by']}")
    lines.append(f"[resolve] mode={summary['resolved_mode']}")
    if AICHAR_V2:
        gl = cr.get('schema_gate_level')
        if gl:
            lines.append(f"[gate]    {gl}")
        hits = cr.get('schema_hits', [])
        if hits:
            for h in hits:
                lines.append(f"[schema]  {h['schema_id']} score={h['match_score']} mem={h['matched_memory_idxs']}  axes={h['match_axes']}")
        ip = cr.get('internal_pressures', {})
        if ip and any(v != 0 for v in ip.values()):
            lines.append(f"[pressures] {ip}")
    lines.append(f"[candidates] {summary['candidates']}")
    lines.append(f"[chose]   {summary['chosen_action']}  [{summary['chosen_type']}]")
    lines.append(f"[action]  {exp_out.get('action','')}")
    lines.append(f"[gesture] {exp_out.get('gesture','')}")
    lines.append(f"[facial]  {exp_out.get('facial_expression','')}")
    if exp_out.get('thought'):
        lines.append(f"[thought] {exp_out.get('thought')}")
    lines.append(f"{character_name}> {exp_out.get('utterance','')}")
    return "\n".join(lines)


# ── one turn ──

def _stage(label: str):
    """Print an inline progress tag; flush immediately so it shows before the API blocks."""
    sys.stdout.write(f"  \x1b[2m[{label}...]\x1b[0m ")
    sys.stdout.flush()
    return time.time()


def _stage_done(t0: float):
    dt = time.time() - t0
    sys.stdout.write(f"\x1b[2m{dt:.1f}s\x1b[0m  ")
    sys.stdout.flush()


def _stage_end():
    sys.stdout.write("\n")
    sys.stdout.flush()


def run_turn(ctx: dict, rules: dict, redlines: dict, user_msg: str) -> tuple[dict, dict, list[str]]:
    ctx["situation"] = {"user_message": user_msg}
    ctx["now_iso"] = _now_iso()

    t0 = _stage("read")
    try:
        phase_a = compile_phase_a(ctx, rules)
        cr = read_speaker(phase_a)
    except SpeakerReaderError as e:
        _stage_end()
        return {}, {}, [f"SpeakerReader: {e}"]
    _stage_done(t0)

    # v2 抽象工作台: gate + schema_matcher (env-gated; default off = v1 behavior unchanged)
    if AICHAR_V2:
        level = association_gate(cr, ctx)
        cr["schema_gate_level"] = level
        if level != "off":
            t0 = _stage(f"match:{level}")
            try:
                hits = schema_match(ctx, cr, level)
            except SchemaMatcherError as e:
                _stage_end()
                return {}, {}, [f"SchemaMatcher: {e}"]
            _stage_done(t0)
            cr["schema_hits"] = hits
            cr["internal_pressures"] = apply_state_shifts(
                ctx.get("character_state", {}).get("internal_pressures", {}), hits
            )
        else:
            cr["schema_hits"] = []
            cr["internal_pressures"] = dict(
                ctx.get("character_state", {}).get("internal_pressures", {})
            )

    phase_b = compile_phase_b(ctx, cr, rules)
    trace = phase_b["_trace"]

    t0 = _stage("decide")
    try:
        dec = decide(
            phase_b["decider_payload"],
            current_read=cr, rules=rules,
            resolved_mode=trace["resolved_mode"],
        )
    except DeciderError as e:
        _stage_end()
        return {}, {}, [f"Decider: {e}"]
    if not dec["compliance"]["ok"]:
        _stage_end()
        return {}, {}, [f"Decider compliance: {err}" for err in dec["compliance"]["errors"]]
    _stage_done(t0)

    t0 = _stage("express")
    try:
        exp = express(
            phase_b["expresser_payload"],
            chosen_action=dec["output"]["chosen_action"],
            chosen_candidate_type=dec["output"]["chosen_candidate_type"],
            redlines=redlines,
        )
    except ExpresserError as e:
        _stage_end()
        return {}, {}, [f"Expresser: {e}"]
    if not exp["compliance"]["ok"]:
        _stage_end()
        return {}, {}, [f"Expresser compliance: {err}" for err in exp["compliance"]["errors"]]
    _stage_done(t0)
    _stage_end()

    summary = {
        "current_read": cr,
        "resolved_mode": trace["resolved_mode"],
        "escalated_by": trace.get("escalated_by"),
        "chosen_action": dec["output"]["chosen_action"],
        "chosen_type": dec["output"]["chosen_candidate_type"],
        "candidates": [(c["candidate_type"], c["fit_score"]) for c in dec["output"]["candidate_actions"]],
    }
    return summary, exp["output"], []


# ── REPL ──

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", default="default")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    rules = load("rules/pose_rules.json")
    redlines = load("rules/verbal_redlines.json")
    personas = _personas()

    if args.persona not in personas:
        print(f"persona '{args.persona}' not found. available: {sorted(personas)}")
        sys.exit(1)

    persona = personas[args.persona]
    character_name = persona["context"]["character_state"]["identity"].get("name") or persona["context"]["character_state"]["identity"]["id"]

    print(f"persona: {args.persona}  ({persona['name']})")
    print(f"character: {character_name}  (debug={args.debug})")
    print("commands: :q  :reset  :debug  :state\n")

    ctx = persona["context"]
    debug = args.debug

    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue

        if line in (":q", ":quit", ":exit"):
            break
        if line == ":reset":
            ctx["recent_turns"] = []
            print("[session reset]\n")
            continue
        if line == ":debug":
            debug = not debug
            print(f"[debug={debug}]\n")
            continue
        if line == ":state":
            ident = ctx["character_state"]["identity"]
            sm = ctx["speaker_model"]["baseline_style"]
            turns = ctx.get("recent_turns", [])
            print(f"[state] character={ident.get('id')} age={ident.get('age')} bg={ident.get('background')}")
            print(f"[state] baseline={sm}")
            print(f"[state] recent_turns={len(turns)}\n")
            continue

        summary, exp_out, errs = run_turn(ctx, rules, redlines, line)
        if errs:
            print("[error]")
            for e in errs:
                print(f"  {e}")
            print()
            continue

        if debug:
            print(_debug(summary, exp_out, character_name))
        else:
            utt = exp_out.get("utterance", "") or "(沉默)"
            print(_short(summary, character_name, utt))
        print()

        # update session recent_turns (cap 6)
        now = _now_iso()
        ctx.setdefault("recent_turns", []).append({"role": "user", "text": line, "timestamp": now})
        if exp_out.get("utterance"):
            ctx["recent_turns"].append({"role": "character", "text": exp_out["utterance"], "timestamp": now})
        ctx["recent_turns"] = ctx["recent_turns"][-6:]


if __name__ == "__main__":
    main()
