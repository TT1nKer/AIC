"""
interlocutor_extractor — P0-C: 壳层抽取对方声明的事实。

每轮用户输入后，regex 提取以下模式并写回 ctx.interlocutor_facts：
  - "我叫 X" / "我叫做 X" / "我叫作 X"
  - "我是 X"
  - "叫我 X"

刻意保守：抽到的候选名字必须通过反 filter（长度、标点、疑问词）才写入。
抽不到就不写，不做猜测。这是壳层 deterministic 抽取，零 LLM 调用。

限制（v0.1）：
  - 只抽 user_name
  - claimed_role / claims_made_this_session 先留字段，不自动抽
  - 再覆盖新值时覆盖（最新优先）；不做冲突判定
"""

from __future__ import annotations
import re
from typing import Optional

# 抽名字的正则。关键设计：
#   - 锚在"我叫/我是/叫我"开头或其后
#   - name 段 1-6 汉字或 1-12 拉丁字符
#   - 后面允许标点或结束
#   - 排除明显非名字的内容

NAME_PATTERNS = [
    re.compile(r"我叫(?:做|作)?\s*([A-Za-z\u4e00-\u9fff]{1,8})"),
    re.compile(r"叫我\s*([A-Za-z\u4e00-\u9fff]{1,8})"),
    re.compile(r"我是\s*([A-Za-z\u4e00-\u9fff]{1,8})"),
]

# 抽到的候选必须不能是这些词
NAME_BLACKLIST = {
    "什么", "谁", "哪个", "哪位", "那位", "这位",
    "你", "他", "她", "它", "我们", "你们",
    "gay", "同性恋", "直的", "弯的",
    "新", "旧", "对", "错", "是", "不是",
    "人", "的", "在", "有", "没", "没有",
    "来", "走",
}


def extract_user_name(user_msg: str) -> Optional[str]:
    """
    Return extracted user_name or None.
    """
    m = (user_msg or "").strip()
    if not m:
        return None
    # "我是什么意思" / "你是谁" 这类问句不抽
    if "?" in m or "？" in m:
        # 短句允许（"我叫老王?"），但含疑问词要拒
        if any(w in m for w in ["什么", "谁", "哪个", "吗", "呢"]):
            return None

    for pat in NAME_PATTERNS:
        match = pat.search(m)
        if not match:
            continue
        name = match.group(1).strip()
        if not name:
            continue
        if name in NAME_BLACKLIST:
            continue
        # 最少 1 字，最多 8 字
        if not (1 <= len(name) <= 8):
            continue
        # 含疑问/否定语义词的剔除
        if any(w in name for w in ["什么", "谁", "哪"]):
            continue
        return name
    return None


def update_interlocutor_facts(ctx: dict, user_msg: str) -> Optional[str]:
    """
    Mutates ctx.interlocutor_facts in place if a new user_name is extracted.
    Returns the extracted name (new) or None.
    """
    name = extract_user_name(user_msg)
    if name is None:
        return None
    facts = ctx.setdefault("interlocutor_facts", {})
    prev = facts.get("user_name")
    facts["user_name"] = name
    claims = facts.setdefault("claims_made_this_session", [])
    claim_text = f"我叫{name}" if prev != name else None
    if claim_text and claim_text not in claims:
        claims.append(claim_text)
    return name
