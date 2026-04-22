"""
Zero-API structural tests for P0 truth-layer compiler emission.

Verifies:
  - persona with name_policy='codename_only' → hard_constraint src='truth:self_identity:codename_only'
  - persona with entities[] + entity id mentioned in user_message → src='truth:entities'
  - entity NOT mentioned → src='truth:entities' absent
  - ctx.interlocutor_facts.user_name → src='truth:interlocutor'
  - phase_a slot INTERLOCUTOR_FACTS injected
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from compiler import compile_phase_a, compile_phase_b  # noqa: E402


def load_persona():
    p = ROOT / "tests/step_p3/personas/t014_B_P3.json"
    return json.loads(p.read_text("utf-8"))["context"]


def load_rules():
    return json.loads((ROOT / "rules/pose_rules.json").read_text("utf-8"))


def make_fake_current_read(user_msg: str):
    """Minimum viable current_read so compile_phase_b can resolve mode."""
    return {
        "likely_mode": "serious_inquiry",
        "recommended_response_mode": "direct_engage",
        "confidence": 0.9,
        "deviation_from_baseline": 20,
        "evidence_buckets": {
            "playfulness_signals": 10,
            "distress_signals": 0,
            "seriousness_signals": 50,
            "baseline_deviation_signals": 20,
            "operational_risk_signals": 0,
            "trust_risk_signals": 20,
        },
        "bucket_evidence": {k: "stub" for k in [
            "playfulness_signals", "distress_signals", "seriousness_signals",
            "baseline_deviation_signals", "operational_risk_signals", "trust_risk_signals"
        ]},
        "discourse_state": {
            "open_questions_from_user": [],
            "unresolved_self_reference": None,
            "answer_obligation": "low",
            "topic_pressure": "free",
        },
    }


def run():
    rules = load_rules()
    ctx_base = load_persona()

    # ── 1. self_identity constraint ──
    ctx = json.loads(json.dumps(ctx_base))
    ctx["situation"] = {"user_message": "你好"}
    pa = compile_phase_a(ctx, rules)
    assert "INTERLOCUTOR_FACTS" in pa["slots"], "phase_a must inject INTERLOCUTOR_FACTS slot"
    pb = compile_phase_b(ctx, make_fake_current_read("你好"), rules)
    hc_srcs = [c["src"] for c in pb["decider_payload"]["hard_constraints"]]
    assert "truth:self_identity:codename_only" in hc_srcs, \
        f"missing self_identity constraint; srcs={hc_srcs}"
    print("✓ [1] self_identity:codename_only constraint emitted")

    # ── 2. entity constraint appears when id mentioned ──
    ctx2 = json.loads(json.dumps(ctx_base))
    ctx2["situation"] = {"user_message": "你认识 A07 吗"}
    pb2 = compile_phase_b(ctx2, make_fake_current_read("你认识 A07 吗"), rules)
    hc_srcs2 = [c["src"] for c in pb2["decider_payload"]["hard_constraints"]]
    assert "truth:entities" in hc_srcs2, \
        f"entity constraint missing when A07 mentioned; srcs={hc_srcs2}"
    truth_line = next(c for c in pb2["decider_payload"]["hard_constraints"] if c["src"] == "truth:entities")
    assert "A07" in truth_line["text"], f"A07 must be in entity constraint text: {truth_line}"
    assert "楼上" in truth_line["text"] or "同组" in truth_line["text"], \
        f"canonical_description must flow through: {truth_line}"
    print("✓ [2] entity constraint emitted when A07 mentioned, canonical desc present")

    # ── 3. entity constraint absent when no id mentioned ──
    ctx3 = json.loads(json.dumps(ctx_base))
    ctx3["situation"] = {"user_message": "你今天怎么样"}
    # strip memories to remove A07/B-3/L-22 mentions from the last-5 window
    ctx3["character_state"]["memories"] = []
    ctx3["recent_turns"] = []
    pb3 = compile_phase_b(ctx3, make_fake_current_read("你今天怎么样"), rules)
    hc_srcs3 = [c["src"] for c in pb3["decider_payload"]["hard_constraints"]]
    assert "truth:entities" not in hc_srcs3, \
        f"entity constraint should be absent when no id mentioned; srcs={hc_srcs3}"
    print("✓ [3] entity constraint absent when no id in scope")

    # ── 4. interlocutor_facts constraint ──
    ctx4 = json.loads(json.dumps(ctx_base))
    ctx4["situation"] = {"user_message": "我叫什么名字"}
    ctx4["interlocutor_facts"] = {
        "user_name": "老王",
        "claimed_role": None,
        "claims_made_this_session": ["我叫老王"],
    }
    pa4 = compile_phase_a(ctx4, rules)
    il_json = pa4["slots"]["INTERLOCUTOR_FACTS"]
    assert "老王" in il_json, f"INTERLOCUTOR_FACTS slot must contain 老王: {il_json}"
    pb4 = compile_phase_b(ctx4, make_fake_current_read("我叫什么名字"), rules)
    hc_srcs4 = [c["src"] for c in pb4["decider_payload"]["hard_constraints"]]
    assert "truth:interlocutor" in hc_srcs4, \
        f"interlocutor constraint missing; srcs={hc_srcs4}"
    truth_line4 = next(c for c in pb4["decider_payload"]["hard_constraints"] if c["src"] == "truth:interlocutor")
    assert "老王" in truth_line4["text"]
    print("✓ [4] interlocutor:user_name=老王 constraint emitted")

    # ── 5. hard_constraints count stays within budget ──
    for pb_ in (pb, pb2, pb3, pb4):
        count = len(pb_["decider_payload"]["hard_constraints"])
        assert count <= 12, f"hard_constraints budget exceeded: {count}"
    print(f"✓ [5] all scenarios stay within hard_constraints_max=12")

    # ── 6. INV-2 still holds: first constraint is fixed_core:top_level ──
    for pb_ in (pb, pb2, pb3, pb4):
        first_src = pb_["decider_payload"]["hard_constraints"][0]["src"]
        assert first_src == "fixed_core:top_level", \
            f"INV-2 broken: first src={first_src}"
    print("✓ [6] INV-2 preserved (first src = fixed_core:top_level)")

    print("\nAll compiler truth-layer structural tests passed.")


if __name__ == "__main__":
    run()
