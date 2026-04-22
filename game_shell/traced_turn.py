"""
traced_turn — run_turn 的完整可见版本。

cli_demo.run_turn 只返回 summary + exp_out。traced_turn 重跑同一条链，
每个阶段的 input/output 都打印到 stdout。game_shell 默认使用这个。

阶段顺序（和 cli_demo.run_turn 保持一致）：
  [A] compile_phase_a(ctx, rules)        → phase_a payload
  [B] speaker_reader(phase_a)            → current_read (evidence_buckets /
                                           likely_mode / discourse_state)
  [C] association_gate(cr, ctx)          → level (off/light/deep)
  [D] schema_matcher(ctx, cr, level)     → hits  （gate != off 时）
  [E] apply_state_shifts(pressures, hits) → updated pressures
  [F] knowledge_boundary / relational_biases 合并到 current_read
  [G] compile_phase_b(ctx, cr, rules)    → decider_payload + _trace
  [H] decide(payload, cr, rules, mode)   → candidate_actions + chosen + compliance
  [I] express(payload, chosen, redlines) → utterance + thought + gesture + compliance

严格与 src/cli_demo.run_turn 同语义，只增 stdout 打印，不改 src。
"""

from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from compiler import compile_phase_a, compile_phase_b  # noqa: E402
from speaker_reader import read_speaker, SpeakerReaderError  # noqa: E402
from decider import decide, DeciderError  # noqa: E402
from expresser import express, ExpresserError  # noqa: E402
from association_gate import gate as association_gate  # noqa: E402
from schema_matcher import match as schema_match, apply_state_shifts, SchemaMatcherError  # noqa: E402

AICHAR_V2 = os.environ.get("AICHAR_V2", "0") == "1"

DIM = "\x1b[2m"
BOLD = "\x1b[1m"
CYAN = "\x1b[36m"
YELLOW = "\x1b[33m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"
RESET = "\x1b[0m"


def _hdr(label: str, elapsed: float | None = None):
    et = f" {DIM}({elapsed:.1f}s){RESET}" if elapsed is not None else ""
    print(f"\n{CYAN}{BOLD}── {label} ──{RESET}{et}")


def _kv(k: str, v, indent: int = 2):
    pad = " " * indent
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False, indent=2)
        lines = s.split("\n")
        print(f"{pad}{YELLOW}{k}{RESET}:")
        for ln in lines:
            print(f"{pad}  {ln}")
    else:
        print(f"{pad}{YELLOW}{k}{RESET}: {v}")


def _brief(obj, limit=220):
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + "…"


def run_turn_traced(ctx: dict, rules: dict, redlines: dict, user_msg: str,
                    *, now_iso: str) -> tuple[dict, dict, list[str]]:
    """
    返回同 cli_demo.run_turn：(summary, exp_out, errs)。但每阶段打印 trace。
    """
    ctx["situation"] = {"user_message": user_msg, "scene": ctx.get("situation", {}).get("scene", {})}
    ctx["now_iso"] = now_iso

    _hdr("[IN] user_message")
    print(f"  {BOLD}{user_msg}{RESET}")
    print(f"  {DIM}recent_turns={len(ctx.get('recent_turns', []))}  "
          f"memories={len(ctx['character_state'].get('memories', []))}  "
          f"pressures={ctx['character_state'].get('internal_pressures', {})}{RESET}")
    facts = ctx.get("interlocutor_facts") or {}
    if facts.get("user_name") or facts.get("claimed_role") or facts.get("claims_made_this_session"):
        print(f"  {DIM}interlocutor_facts={facts}{RESET}")
    ident = ctx["character_state"].get("identity", {})
    if ident.get("name_policy"):
        print(f"  {DIM}identity.name_policy={ident.get('name_policy')}  display_name={ident.get('display_name')}  id={ident.get('id')}{RESET}")

    # ── [A] compile_phase_a ──
    t0 = time.time()
    try:
        phase_a = compile_phase_a(ctx, rules)
    except Exception as e:
        print(f"{RED}compile_phase_a failed: {e}{RESET}")
        return {}, {}, [f"compile_phase_a: {e}"]
    _hdr("[A] compile_phase_a → phase_a_payload", time.time() - t0)
    _kv("template_id", phase_a.get("template_id"))
    _kv("hard_constraints (count)", len(phase_a.get("hard_constraints", [])))
    for hc in phase_a.get("hard_constraints", [])[:4]:
        print(f"    {DIM}- {hc.get('text','')[:100]}  (src: {hc.get('src','')}){RESET}")
    if len(phase_a.get("hard_constraints", [])) > 4:
        print(f"    {DIM}... +{len(phase_a['hard_constraints'])-4} more{RESET}")

    # ── [B] speaker_reader ──
    t0 = time.time()
    try:
        cr = read_speaker(phase_a)
    except SpeakerReaderError as e:
        print(f"{RED}speaker_reader failed: {e}{RESET}")
        return {}, {}, [f"SpeakerReader: {e}"]
    _hdr("[B] speaker_reader → current_read", time.time() - t0)
    _kv("likely_mode", cr.get("likely_mode"))
    _kv("recommended_response_mode", cr.get("recommended_response_mode"))
    _kv("confidence", cr.get("confidence"))
    _kv("deviation_from_baseline", cr.get("deviation_from_baseline"))
    _kv("evidence_buckets", cr.get("evidence_buckets"))
    if cr.get("bucket_evidence"):
        _kv("bucket_evidence", cr.get("bucket_evidence"))
    _kv("discourse_state", cr.get("discourse_state", {}))

    # ── [C] association_gate ──
    if AICHAR_V2:
        level = association_gate(cr, ctx)
        cr["schema_gate_level"] = level
        _hdr("[C] association_gate → schema_gate_level")
        _kv("level", level)

        # ── [D] schema_matcher ──
        if level != "off":
            t0 = time.time()
            try:
                hits = schema_match(ctx, cr, level)
            except SchemaMatcherError as e:
                print(f"{RED}schema_matcher failed: {e}{RESET}")
                return {}, {}, [f"SchemaMatcher: {e}"]
            _hdr(f"[D] schema_matcher({level}) → hits", time.time() - t0)
            if not hits:
                print(f"  {DIM}(no schema hits){RESET}")
            for h in hits:
                print(f"  - {GREEN}{h.get('schema_id')}{RESET}  score={h.get('match_score')}  "
                      f"mem_idxs={h.get('matched_memory_idxs')}")
                print(f"    {DIM}axes={h.get('match_axes')}{RESET}")
                if h.get("state_shift"):
                    print(f"    {DIM}state_shift={h.get('state_shift')}{RESET}")
            cr["schema_hits"] = hits

            # ── [E] apply_state_shifts ──
            base_ip = ctx.get("character_state", {}).get("internal_pressures", {})
            new_ip = apply_state_shifts(base_ip, hits)
            cr["internal_pressures"] = new_ip
            _hdr("[E] apply_state_shifts → internal_pressures")
            print(f"  base:  {base_ip}")
            print(f"  after: {new_ip}")
        else:
            cr["schema_hits"] = []
            cr["internal_pressures"] = dict(
                ctx.get("character_state", {}).get("internal_pressures", {})
            )
            _hdr("[D/E] skipped (gate=off)")

    # ── [F] knowledge_boundary / relational_biases 合并 ──
    kb = ctx.get("character_state", {}).get("knowledge_boundary")
    rb = ctx.get("character_state", {}).get("relational_biases")
    if kb or rb:
        _hdr("[F] merge knowledge_boundary + relational_biases")
        if kb:
            cr["knowledge_boundary"] = kb
            _kv("knowledge_boundary", kb)
        if rb:
            cr["relational_biases"] = rb
            _kv("relational_biases", rb)

    # ── [G] compile_phase_b ──
    t0 = time.time()
    phase_b = compile_phase_b(ctx, cr, rules)
    trace = phase_b["_trace"]
    _hdr("[G] compile_phase_b → decider_payload + trace", time.time() - t0)
    _kv("resolved_mode", trace.get("resolved_mode"))
    if trace.get("escalated_by"):
        _kv("escalated_by", trace.get("escalated_by"))
    hc = phase_b["decider_payload"].get("hard_constraints", [])
    _kv("decider hard_constraints (count)", len(hc))
    truth_hc = [c for c in hc if str(c.get("src", "")).startswith("truth:")]
    if truth_hc:
        print(f"  {GREEN}truth-layer constraints active:{RESET}")
        for c in truth_hc:
            snippet = (c.get("text", "") or "").replace("\n", " ")[:120]
            print(f"    {DIM}[{c['src']}]{RESET} {snippet}…")

    # ── [H] decide ──
    t0 = time.time()
    try:
        dec = decide(phase_b["decider_payload"], current_read=cr, rules=rules,
                     resolved_mode=trace["resolved_mode"])
    except DeciderError as e:
        print(f"{RED}decider failed: {e}{RESET}")
        return {}, {}, [f"Decider: {e}"]
    _hdr("[H] decide → candidate_actions + chosen", time.time() - t0)
    out = dec.get("output", {})
    cands = out.get("candidate_actions", [])
    print(f"  {YELLOW}candidates{RESET} ({len(cands)}):")
    for i, c in enumerate(cands):
        marker = f"{GREEN}★{RESET}" if c.get("action") == out.get("chosen_action") else " "
        print(f"  {marker} [{i}] type={c.get('candidate_type'):35s} fit={c.get('fit_score')}")
        print(f"      {DIM}action: {c.get('action')}{RESET}")
        if c.get("why"):
            print(f"      {DIM}why:    {c.get('why')}{RESET}")
    print(f"  {YELLOW}chosen{RESET}: {out.get('chosen_action')}")
    print(f"  {YELLOW}chosen_type{RESET}: {out.get('chosen_candidate_type')}")
    comp = dec.get("compliance", {})
    color = GREEN if comp.get("ok") else RED
    print(f"  {color}compliance: ok={comp.get('ok')}{RESET}")
    if not comp.get("ok"):
        for err in comp.get("errors", []):
            print(f"    {RED}- {err}{RESET}")
        return {}, {}, [f"Decider compliance: {err}" for err in comp.get("errors", [])]

    # ── [I] express ──
    t0 = time.time()
    try:
        exp = express(phase_b["expresser_payload"],
                      chosen_action=out["chosen_action"],
                      chosen_candidate_type=out["chosen_candidate_type"],
                      redlines=redlines)
    except ExpresserError as e:
        print(f"{RED}expresser failed: {e}{RESET}")
        return {}, {}, [f"Expresser: {e}"]
    _hdr("[I] express → utterance / thought / gesture", time.time() - t0)
    ex_out = exp.get("output", {})
    _kv("utterance", ex_out.get("utterance", ""))
    if ex_out.get("thought"):
        _kv("thought", ex_out.get("thought", ""))
    if ex_out.get("action"):
        _kv("action", ex_out.get("action", ""))
    if ex_out.get("gesture"):
        _kv("gesture", ex_out.get("gesture", ""))
    if ex_out.get("facial_expression"):
        _kv("facial", ex_out.get("facial_expression", ""))
    exp_comp = exp.get("compliance", {})
    color = GREEN if exp_comp.get("ok") else RED
    print(f"  {color}compliance: ok={exp_comp.get('ok')}{RESET}")
    if not exp_comp.get("ok"):
        for err in exp_comp.get("errors", []):
            print(f"    {RED}- {err}{RESET}")
        return {}, {}, [f"Expresser compliance: {err}" for err in exp_comp.get("errors", [])]

    summary = {
        "current_read": cr,
        "resolved_mode": trace["resolved_mode"],
        "escalated_by": trace.get("escalated_by"),
        "chosen_action": out["chosen_action"],
        "chosen_type": out["chosen_candidate_type"],
        "candidates": [(c["candidate_type"], c["fit_score"]) for c in cands],
    }
    return summary, ex_out, []
