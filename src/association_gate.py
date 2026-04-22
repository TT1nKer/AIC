"""
association_gate — 纯代码门控器

决定本轮是否打开 schema_matcher。三档：off / light / deep。

规则（阈值硬编码，规则稳定后可抽成 JSON）：
  - answer_obligation=high 且无追问线索 且 deviation<30 → off
  - 追问线索 + deviation>=60 → deep
  - deviation>=40 或 追问线索 → light
  - 其它 → off
"""

from __future__ import annotations
from typing import Literal


FOLLOWUP_CUES = ("为什么", "具体点", "具体", "细节", "再说说", "详细", "继续说", "然后呢")


def _has_followup_cue(ctx: dict) -> bool:
    recent = ctx.get("recent_turns", [])
    # 只看最近 3 轮用户消息（避免回溯太远）
    user_texts = [t.get("text", "") for t in recent[-3:] if t.get("role") == "user"]
    # 也要看本轮用户消息（situation）
    cur = ctx.get("situation", {}).get("user_message", "")
    user_texts.append(cur)
    blob = " ".join(user_texts)
    return any(cue in blob for cue in FOLLOWUP_CUES)


def gate(current_read: dict, ctx: dict) -> Literal["off", "light", "deep"]:
    """Decide whether to run schema_matcher this turn.

    Returns 'off' / 'light' / 'deep'.

    Step 1 control: if discourse_state.unresolved_self_reference is non-empty,
    the user is asking about something the character just said (referent
    resolution). Running schema_matcher risks pulling attention toward a
    sediment memory that has nothing to do with the referent. Force off.
    Rule 8 (reference_resolution) still handles the referent via Decider.
    """
    ds = (current_read or {}).get("discourse_state", {}) or {}
    ev = (current_read or {}).get("evidence_buckets", {}) or {}

    # Guard A: explicit referent in play → gate off so matcher does not compete
    if ds.get("unresolved_self_reference"):
        return "off"

    oblig = ds.get("answer_obligation")
    dev = ev.get("baseline_deviation_signals", 0)
    followup = _has_followup_cue(ctx)

    if oblig == "high" and not followup and dev < 30:
        return "off"

    if followup and dev >= 60:
        return "deep"

    if dev >= 40 or followup:
        return "light"

    return "off"
