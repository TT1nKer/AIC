"""
Zero-API unit tests for playtest_mode v0.1.

Verifies:
  quick_ack_check
    - matches greeting whitelist at recent_turns=0
    - rejects >8 chars
    - rejects question marks
    - rejects blacklist terms (关系/解释/状态/指代)
    - rejects proper nouns (A07 etc.)
    - rejects at recent_turns > 1

  deterministic_hook
    - short utterance with proper noun → no hook
    - short utterance without proper noun + memory has A07 → hook appends
    - long utterance → no hook
    - empty utterance → no hook
"""
from __future__ import annotations
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from playtest_mode import quick_ack_check, deterministic_hook, needs_hook


def run():
    # ── quick_ack_check ──
    ctx_fresh = {"recent_turns": []}
    ctx_mid   = {"recent_turns": [{"role":"user","text":"x"},{"role":"character","text":"y"},{"role":"user","text":"z"}]}

    # positive
    assert quick_ack_check("嗨", ctx_fresh) == "在。门没锁。"
    assert quick_ack_check("在吗", ctx_fresh) == "在。门没锁。"
    assert quick_ack_check("你在干嘛", ctx_fresh) == "修东西。你有事？"
    assert quick_ack_check("hi", ctx_fresh) == "在。门没锁。"
    print("✓ whitelist matches (greeting / whatdoing)")

    # negative — too long
    assert quick_ack_check("你到底在不在啊", ctx_fresh) is None
    # negative — has question mark
    assert quick_ack_check("在吗?", ctx_fresh) is None
    assert quick_ack_check("你在吗？", ctx_fresh) is None
    # negative — blacklist
    assert quick_ack_check("你觉得", ctx_fresh) is None
    assert quick_ack_check("担心", ctx_fresh) is None
    assert quick_ack_check("欠", ctx_fresh) is None
    assert quick_ack_check("怪", ctx_fresh) is None
    # negative — proper noun
    assert quick_ack_check("A07", ctx_fresh) is None
    assert quick_ack_check("B-3", ctx_fresh) is None
    # negative — mid-conversation (recent_turns > 1)
    assert quick_ack_check("嗨", ctx_mid) is None
    print("✓ rejects: length / question / blacklist / proper noun / mid-convo")

    # ── deterministic_hook ──
    ctx_with_memory = {
        "character_state": {
            "memories": [
                {"text": "B-3 又坏了，A07 今天没来", "salience": 80},
            ],
            "relational_biases": [{"target_id": "A07", "bias_type": "owes_something"}],
        }
    }
    scene_empty = {"location": "修设备间", "npcs_absent": []}
    scene_absent = {"location": "修设备间", "npcs_absent": ["A07"]}

    # short, no proper noun → hook adds memory's noun
    result = deterministic_hook("谁？", ctx_with_memory, scene_empty)
    assert result != "谁？" and ("A07" in result or "B-3" in result), f"expected noun appended: {result!r}"
    print(f"✓ hook appended to '谁？' → {result!r}")

    # short, already has proper noun → no hook
    short_with_noun = "A07 在哪？"
    result2 = deterministic_hook(short_with_noun, ctx_with_memory, scene_empty)
    assert result2 == short_with_noun, f"should not touch: {result2!r}"
    print(f"✓ short-with-noun untouched: {result2!r}")

    # long → no hook
    long_utt = "我一般先看设备，再想别的事情，顺序不乱。"
    result3 = deterministic_hook(long_utt, ctx_with_memory, scene_empty)
    assert result3 == long_utt
    print(f"✓ long utterance untouched")

    # empty → no hook
    result4 = deterministic_hook("", ctx_with_memory, scene_empty)
    assert result4 == ""
    print(f"✓ empty utterance untouched")

    # scene-only fallback (no memory proper nouns, but npcs_absent)
    ctx_no_mem = {"character_state": {"memories": [], "relational_biases": []}}
    result5 = deterministic_hook("嗯", ctx_no_mem, scene_absent)
    assert "A07" in result5, f"expected scene fallback: {result5!r}"
    print(f"✓ scene fallback: {result5!r}")

    # needs_hook helper
    assert needs_hook("谁？") == True
    assert needs_hook("A07 在哪？") == False  # has proper noun
    assert needs_hook("这是一段挺长的句子，足够表达意思了。") == False
    assert needs_hook("") == False
    print("✓ needs_hook boundaries correct")

    print("\nAll playtest_mode unit tests passed.")


if __name__ == "__main__":
    run()
