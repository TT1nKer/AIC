"""Zero-API unit tests for interlocutor_extractor."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from interlocutor_extractor import extract_user_name, update_interlocutor_facts


def run():
    # positive
    assert extract_user_name("我叫老王") == "老王", extract_user_name("我叫老王")
    assert extract_user_name("我叫做老王") == "老王", extract_user_name("我叫做老王")
    assert extract_user_name("我叫作李明") == "李明"
    assert extract_user_name("叫我小张") == "小张"
    assert extract_user_name("我是张三") == "张三"
    assert extract_user_name("Alice") is None  # no anchor
    assert extract_user_name("我叫 Alice") == "Alice"
    print("✓ positive extraction (7 cases)")

    # negative — questions
    assert extract_user_name("我叫什么") is None
    assert extract_user_name("我是谁") is None
    assert extract_user_name("你叫什么名字") is None
    # "我是 gay" — "gay" 在黑名单
    assert extract_user_name("我是gay") is None
    # "我是新来的" — "新" 在黑名单
    assert extract_user_name("我是新") is None
    print("✓ negative (questions / blacklist)")

    # negative — nothing matched
    assert extract_user_name("") is None
    assert extract_user_name("最近怎么样") is None
    assert extract_user_name("你在吗") is None
    print("✓ negative (no anchor)")

    # update_interlocutor_facts
    ctx = {}
    r = update_interlocutor_facts(ctx, "我叫老王")
    assert r == "老王"
    assert ctx["interlocutor_facts"]["user_name"] == "老王"
    assert "我叫老王" in ctx["interlocutor_facts"]["claims_made_this_session"]

    # same again — no duplicate claim
    r2 = update_interlocutor_facts(ctx, "我叫老王")
    assert r2 == "老王"
    assert ctx["interlocutor_facts"]["claims_made_this_session"].count("我叫老王") == 1
    print("✓ update_interlocutor_facts updates + dedups")

    # overwrite with new name
    r3 = update_interlocutor_facts(ctx, "叫我小李")
    assert r3 == "小李"
    assert ctx["interlocutor_facts"]["user_name"] == "小李"
    claims = ctx["interlocutor_facts"]["claims_made_this_session"]
    assert "我叫小李" in claims
    print("✓ overwrite: user_name changes, claim appended")

    print("\nAll interlocutor_extractor unit tests passed.")


if __name__ == "__main__":
    run()
