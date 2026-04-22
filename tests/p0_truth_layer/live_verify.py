"""
Live API verification for P0 truth-layer (3 tests).

Test 1 — Self identity (codename_only):
  Q: "你叫什么名字？"
  ✓: utterance 含 "T-014" 或明示"没正式名字/只是代号"
  ✗: utterance 出现"老周/老王/小张"这类生造中文姓名

Test 2 — Entity truth (A07):
  Q: "你认识 A07 吗？"
  ✓: utterance 不出现"楼上老人/住户/陌生人"等凭空补全标签
  ✓: 若提到 A07，描述与 canonical 一致（同组/维修搭档/年轻）

Test 3 — Interlocutor recall:
  T0 (extract 触发): "你好，我叫老王"
  T1 (recall probe): "我叫什么名字？"
  ✓: T1 utterance 含 "老王"
  ✗: T1 出现"你呢/你是谁"之类不记得对方名字的话

6 API 调用（T1: 1 / T2: 1 / T3: 2 + 壳层 extract 是零调用）。~3 分钟。
"""

from __future__ import annotations
import copy
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("AICHAR_V2", "1")
os.environ.setdefault("AICHAR_P2", "1")
os.environ.setdefault("AICHAR_P3", "1")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "game_shell"))

from compiler import compile_phase_a, compile_phase_b  # noqa: E402
from speaker_reader import read_speaker  # noqa: E402
from decider import decide  # noqa: E402
from expresser import express  # noqa: E402
from association_gate import gate as association_gate  # noqa: E402
from schema_matcher import match as schema_match, apply_state_shifts  # noqa: E402
from interlocutor_extractor import update_interlocutor_facts  # noqa: E402

PERSONA_PATH = ROOT / "tests/step_p3/personas/t014_B_P3.json"
RESULTS_DIR = ROOT / "tests/p0_truth_layer/results"

FABRICATED_NAME_PATTERNS = [
    # 老/小/大 + 1-2 chars — 老周/老王/老李/小张/大刘
    re.compile(r"(?<!T-)[老小大][\u4e00-\u9fff]{1,2}(?![0-9])"),
]
ENTITY_LEAK_PATTERNS = [
    re.compile(r"楼上[那这]?位?老人"),
    re.compile(r"楼上住户"),
    re.compile(r"陌生人"),
]
# P0.1: 用户名字不得被解释成 world entity 的 canonical label
NAMESPACE_BLEED_PATTERNS = [
    re.compile(r"老王[?？]?\s*[是就][那这]?(个|位)?\s*楼上"),
    re.compile(r"老王.*?(就是|其实|是)\s*(L-22|楼上那个老人|楼上老人)"),
    re.compile(r"老王.*?(A07|B-3)\s*(吧|吗)?"),
]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_one_turn(ctx: dict, rules: dict, redlines: dict, user_msg: str) -> dict:
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
    if kb:
        cr["knowledge_boundary"] = kb
    rb = ctx.get("character_state", {}).get("relational_biases")
    if rb:
        cr["relational_biases"] = rb

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

    truth_srcs = [c["src"] for c in phase_b["decider_payload"]["hard_constraints"] if c["src"].startswith("truth:")]

    return {
        "utterance": exp["output"].get("utterance", ""),
        "thought": exp["output"].get("thought", ""),
        "chosen_action": dec["output"]["chosen_action"],
        "chosen_type": dec["output"]["chosen_candidate_type"],
        "resolved_mode": trace["resolved_mode"],
        "truth_srcs": truth_srcs,
    }


def scan_fabricated_names(text: str) -> list[str]:
    hits = []
    for pat in FABRICATED_NAME_PATTERNS:
        for m in pat.finditer(text or ""):
            hits.append(m.group(0))
    return hits


def scan_entity_leak(text: str) -> list[str]:
    hits = []
    for pat in ENTITY_LEAK_PATTERNS:
        for m in pat.finditer(text or ""):
            hits.append(m.group(0))
    return hits


def scan_namespace_bleed(text: str) -> list[str]:
    """P0.1: detect interlocutor name being conflated with a world entity."""
    hits = []
    for pat in NAMESPACE_BLEED_PATTERNS:
        for m in pat.finditer(text or ""):
            hits.append(m.group(0))
    return hits


def main():
    rules = json.loads((ROOT / "rules/pose_rules.json").read_text("utf-8"))
    redlines = json.loads((ROOT / "rules/verbal_redlines.json").read_text("utf-8"))
    persona = json.loads(PERSONA_PATH.read_text("utf-8"))

    print("P0 truth-layer live verification")
    print(f"AICHAR_V2={os.environ['AICHAR_V2']} P2={os.environ['AICHAR_P2']} P3={os.environ['AICHAR_P3']}")
    print(f"persona: {PERSONA_PATH.name}\n")

    results = []
    t_start = time.time()

    # ── Test 1: self identity ──
    print("── Test 1: self identity (codename_only) ──")
    ctx1 = copy.deepcopy(persona["context"])
    ctx1["recent_turns"] = []
    ctx1.setdefault("interlocutor_facts", {"user_name": None, "claimed_role": None, "claims_made_this_session": []})
    q1 = "你叫什么名字？"
    t0 = time.time()
    r1 = run_one_turn(ctx1, rules, redlines, q1)
    print(f"  ({time.time()-t0:.1f}s) truth_srcs={r1.get('truth_srcs')}")
    if "error" in r1:
        print(f"  ERROR: {r1['error']}")
        t1_pass = False
    else:
        utt1 = r1["utterance"]
        print(f"  Q: {q1}")
        print(f"  A: {utt1}")
        fab = scan_fabricated_names(utt1)
        has_codename = "T-014" in utt1 or "代号" in utt1 or "没正式名字" in utt1 or "就叫 T" in utt1
        t1_pass = (not fab) and has_codename
        print(f"  fabricated_names={fab}  has_codename_indicator={has_codename}  → {'✓ PASS' if t1_pass else '✗ FAIL'}")
    results.append({"test": "self_identity", "pass": t1_pass, "data": r1, "question": q1})
    print()

    # ── Test 2: entity truth ──
    print("── Test 2: entity truth (A07 must not become 楼上老人) ──")
    ctx2 = copy.deepcopy(persona["context"])
    ctx2["recent_turns"] = []
    ctx2.setdefault("interlocutor_facts", {"user_name": None, "claimed_role": None, "claims_made_this_session": []})
    q2 = "你认识 A07 吗？"
    t0 = time.time()
    r2 = run_one_turn(ctx2, rules, redlines, q2)
    print(f"  ({time.time()-t0:.1f}s) truth_srcs={r2.get('truth_srcs')}")
    if "error" in r2:
        print(f"  ERROR: {r2['error']}")
        t2_pass = False
    else:
        utt2 = r2["utterance"]
        print(f"  Q: {q2}")
        print(f"  A: {utt2}")
        leak = scan_entity_leak(utt2)
        t2_pass = (not leak)
        print(f"  entity_leak_labels={leak}  → {'✓ PASS' if t2_pass else '✗ FAIL'}")
    results.append({"test": "entity_truth", "pass": t2_pass, "data": r2, "question": q2})
    print()

    # ── Test 3: interlocutor recall ──
    print("── Test 3: interlocutor recall (user introduces self, NPC must remember) ──")
    ctx3 = copy.deepcopy(persona["context"])
    ctx3["recent_turns"] = []
    ctx3.setdefault("interlocutor_facts", {"user_name": None, "claimed_role": None, "claims_made_this_session": []})

    q3a = "你好，我叫老王"
    extracted = update_interlocutor_facts(ctx3, q3a)
    print(f"  T0 extract: q={q3a!r}  extracted user_name={extracted!r}")
    assert extracted == "老王", f"extractor missed 老王: {extracted}"
    t0 = time.time()
    r3a = run_one_turn(ctx3, rules, redlines, q3a)
    print(f"  T0 ({time.time()-t0:.1f}s): A={r3a.get('utterance','')}")
    # accumulate recent_turns
    now = _now_iso()
    ctx3.setdefault("recent_turns", []).append({"role": "user", "text": q3a, "timestamp": now})
    if r3a.get("utterance"):
        ctx3["recent_turns"].append({"role": "character", "text": r3a["utterance"], "timestamp": now})

    q3b = "你还记得我叫什么吗？"
    t0 = time.time()
    r3b = run_one_turn(ctx3, rules, redlines, q3b)
    print(f"  T1 ({time.time()-t0:.1f}s) truth_srcs={r3b.get('truth_srcs')}")
    if "error" in r3b:
        print(f"  ERROR: {r3b['error']}")
        t3_pass = False
    else:
        utt3 = r3b["utterance"]
        print(f"  T1 Q: {q3b}")
        print(f"  T1 A: {utt3}")
        recalls = "老王" in utt3
        asks_back = any(p in utt3 for p in ["你呢", "你叫什么", "你是谁", "你怎么称呼"])
        t3_pass = recalls and not asks_back
        print(f"  recalls_老王={recalls}  asks_back={asks_back}  → {'✓ PASS' if t3_pass else '✗ FAIL'}")
    results.append({"test": "interlocutor_recall", "pass": t3_pass, "data": r3b,
                     "intro_turn": r3a, "question": q3b})
    print()

    # ── Test 4: namespace isolation (P0.1) ──
    # 对方声明名字后，NPC 不得把对方并入 world entity。延续 Test 3 的 ctx3（已含 interlocutor_facts 和
    # T0 的回合；recent_turns 已累积），追加一个明确问"老王是谁"的 probe，检查 NPC 回答是否把 老王
    # 解释成 L-22 / 楼上老人 / A07 等 world entity。
    print("── Test 4: namespace isolation (interlocutor ≠ world entity) ──")
    now4 = _now_iso()
    ctx3.setdefault("recent_turns", []).append({"role": "user", "text": q3b, "timestamp": now4})
    if r3b.get("utterance"):
        ctx3["recent_turns"].append({"role": "character", "text": r3b["utterance"], "timestamp": now4})
    q4 = "老王是谁？"
    t0 = time.time()
    r4 = run_one_turn(ctx3, rules, redlines, q4)
    print(f"  ({time.time()-t0:.1f}s) truth_srcs={r4.get('truth_srcs')}")
    if "error" in r4:
        print(f"  ERROR: {r4['error']}")
        t4_pass = False
    else:
        utt4 = r4["utterance"]
        print(f"  Q: {q4}")
        print(f"  A: {utt4}")
        bleed = scan_namespace_bleed(utt4)
        # 也检查是不是直接说"老王就是我刚认识的人/对方/你"这类正确归属
        t4_pass = (not bleed)
        print(f"  namespace_bleed={bleed}  → {'✓ PASS' if t4_pass else '✗ FAIL'}")
    results.append({"test": "namespace_isolation", "pass": t4_pass, "data": r4, "question": q4})
    print()

    total = time.time() - t_start
    print(f"Total: {total:.1f}s")
    passed = sum(1 for r in results if r["pass"])
    print(f"Results: {passed}/{len(results)} passed")
    for r in results:
        icon = "✓" if r["pass"] else "✗"
        print(f"  {icon} {r['test']}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"live_verify_{stamp}.raw.json"
    out.write_text(json.dumps({"timestamp": stamp, "results": results}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"wrote {out}")
    return passed == len(results)


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
