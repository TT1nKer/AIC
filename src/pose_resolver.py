"""
pose_resolver — 姿态冲突裁判器

职责：给定 SpeakerReader 推荐的 mode + 当前 context，
经过 safety_hooks 抬升后，输出最终生效的 mode。

不含 LLM 调用。纯函数、确定性。
"""

from __future__ import annotations
from typing import Any


def _get_path(ctx: dict, path: str) -> Any:
    cur: Any = ctx
    for seg in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
    return cur


def _cmp(op: str, actual: Any, expected: Any) -> bool:
    if actual is None:
        return False
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op in ("gte", "lte", "gt", "lt"):
        if not (isinstance(actual, (int, float)) and isinstance(expected, (int, float))):
            return False
        if op == "gte":
            return actual >= expected
        if op == "lte":
            return actual <= expected
        if op == "gt":
            return actual > expected
        if op == "lt":
            return actual < expected
    return False


def eval_trigger(expr: dict, ctx: dict) -> bool:
    for op in ("eq", "ne", "gte", "lte", "gt", "lt"):
        if op in expr:
            return _cmp(op, _get_path(ctx, expr[op]["path"]), expr[op]["value"])
    if "any_of" in expr:
        return any(eval_trigger(e, ctx) for e in expr["any_of"])
    if "all_of" in expr:
        return all(eval_trigger(e, ctx) for e in expr["all_of"])
    if "not" in expr:
        return not eval_trigger(expr["not"], ctx)
    return False


def resolve(rules: dict, recommended: str, ctx: dict) -> dict:
    modes = rules["modes"]
    if recommended not in modes:
        raise ValueError(f"UNKNOWN_MODE: {recommended}")

    final_mode = recommended
    escalated_by = None

    for hook in rules["global"]["safety_hooks"]:
        forced = hook["forced_mode"]
        if forced not in modes:
            raise ValueError(f"UNKNOWN_MODE: {forced}")
        if eval_trigger(hook["trigger"], ctx):
            if modes[forced]["priority"] > modes[final_mode]["priority"]:
                final_mode = forced
                escalated_by = hook["name"]

    return {"mode": final_mode, "escalated_by": escalated_by}
