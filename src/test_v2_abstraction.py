"""
V2 抽象工作台测试 runner。

- 第一部分：gate_cases.json 单元测试（纯代码，断言 gate() 返回档位）
- 第二部分：abstraction_test.json live 测试（调 DeepSeek schema_matcher，
  断言 hits 中至少命中一个期望 schema_id）

用法：
  python3 test_v2_abstraction.py            # 跑全部
  python3 test_v2_abstraction.py --unit     # 只跑 gate 单元
  python3 test_v2_abstraction.py --live     # 只跑 abstraction 10 题（花 API 成本）
"""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from association_gate import gate
from schema_matcher import match as schema_match, SchemaMatcherError

ROOT = Path(__file__).resolve().parent.parent
GATE_PATH = ROOT / "tests" / "v2_abstraction" / "gate_cases.json"
ABS_PATH = ROOT / "tests" / "v2_abstraction" / "abstraction_test.json"


def run_gate_unit() -> tuple[int, int]:
    data = json.loads(GATE_PATH.read_text("utf-8"))
    total = passed = 0
    print("\n── GATE UNIT ──")
    for c in data["cases"]:
        total += 1
        want = c["expect"]
        got = gate(c["current_read"], c["ctx"])
        if got == want:
            passed += 1
            print(f"  PASS  {c['case_id']}")
        else:
            print(f"  FAIL  {c['case_id']}  want={want} got={got}")
    print(f"  {passed}/{total}")
    return passed, total


def run_abstraction_live() -> tuple[int, int]:
    data = json.loads(ABS_PATH.read_text("utf-8"))
    total = passed = 0
    print("\n── ABSTRACTION LIVE (calls DeepSeek) ──")
    for t in data["tests"]:
        total += 1
        ctx = {
            "character_state": {"memories": t["character_memories"]},
            "recent_turns": t.get("recent_turns", []),
            "situation": {"user_message": t["user_message"]},
        }
        # Simulate a current_read that would trigger light gate
        cr_fake = {"evidence_buckets": {"baseline_deviation_signals": 50}}
        try:
            hits = schema_match(ctx, cr_fake, "light")
        except SchemaMatcherError as e:
            print(f"  FAIL  {t['test_id']}  matcher error: {e}")
            continue
        hit_ids = {h["schema_id"] for h in hits}
        expected = set(t["expected_schema_ids_any_of"])
        if hit_ids & expected:
            passed += 1
            matched = hit_ids & expected
            print(f"  PASS  {t['test_id']}  hit={matched}")
        else:
            print(f"  FAIL  {t['test_id']}  want any of {sorted(expected)}  got {sorted(hit_ids)}")
    print(f"  {passed}/{total}  (threshold ≥7/10)")
    return passed, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", action="store_true")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    run_unit = args.unit or not args.live
    run_live = args.live or not args.unit

    unit_pass = unit_total = 0
    live_pass = live_total = 0

    if run_unit:
        unit_pass, unit_total = run_gate_unit()
    if run_live:
        live_pass, live_total = run_abstraction_live()

    print(f"\n{'='*30}")
    if run_unit:
        print(f"Gate unit:    {unit_pass}/{unit_total}")
    if run_live:
        print(f"Abstraction:  {live_pass}/{live_total} (pass if ≥7)")

    failed = False
    if run_unit and unit_pass < unit_total:
        failed = True
    if run_live and live_pass < 7:
        failed = True
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
