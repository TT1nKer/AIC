"""
redline_checker — verbal_redlines 执行器

检查一段文字（utterance / thought / lesson_text）是否违反 S/L/V 红线。
纯函数、确定性、不含 LLM 调用。
"""

from __future__ import annotations
import re


def check(redlines: dict, surface: str, text: str) -> dict:
    if not text or not text.strip():
        return {"verdict": "pass", "hit_rule": None}

    lower = text.lower()

    for term in redlines["global_blacklist_terms"]:
        if term.lower() in lower:
            return {"verdict": "block", "hit_rule": f"blacklist:{term}"}

    for block in redlines["global_regex_blocks"]:
        if surface not in block["applies_to"]:
            continue
        if re.search(block["pattern"], text, re.IGNORECASE):
            return {"verdict": "block", "hit_rule": f"regex:{block['name']}"}

    return {"verdict": "pass", "hit_rule": None}
