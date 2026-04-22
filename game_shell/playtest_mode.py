"""
playtest_mode — 试玩模式壳层增强（v0.1）。

两个 deterministic 壳层函数，零二次 LLM 调用：

  1. quick_ack_check(user_msg, ctx) → str | None
     极轻问候检测。命中时返回一个预设短句作为 NPC 回复，跳过全链路。
     规则（严格 AND）：
       - recent_turns 长度 ≤ 1（只允许在会话最开头快通道）
       - 输入长度 ≤ 8 字
       - 匹配问候白名单
       - 不含解释型/关系型/状态型/指代型/问号/专有名词/角色 ID
     否则返回 None（走原全链路）。

  2. deterministic_hook(utterance, ctx, scene) → str
     如果 utterance 过短且无具体锚点名词，从 scene / recent events /
     relational_biases targets 里挑一个最具体的名词，deterministic 拼一句
     follow-up hook 附在后面。不调用模型。

这两个函数是壳层增强，严格在 game_shell 范围内。不改 src/。
"""

from __future__ import annotations
import re
from typing import Optional

# ── 1. 快通道 ──

# 白名单：极短问候（全半角空格+标点都兼容）
GREETING_WHITELIST = [
    r"^嗨$", r"^你好$", r"^在吗$", r"^有人吗$", r"^喂$",
    r"^hi$", r"^hey$", r"^hello$",
    r"^你在$", r"^你在吗$",
    r"^你在干(嘛|啥|什么)$",
    r"^你在(忙|做)什么$",
    r"^在吗\.?$",
]

# 黑名单：一旦命中任何一个，就走全链路（不走快通道）
# 覆盖解释型/关系型/状态型/指代型触发词
BLACKLIST_TERMS = [
    "为什么", "怎么回事", "怎么了",
    "你觉得", "你是不是", "是不是在",
    "担心", "害怕", "后悔", "欠", "怪",
    "谁", "哪个", "想什么", "还好吗",
    "替", "兜", "瞒", "藏",
]

# 专有名词（会随 persona 变；第一版硬编码常见 doomsday id 前缀）
PROPER_NOUN_PATTERN = re.compile(r"[A-Z]-\d{1,3}|T-\d{1,3}|A\d{2}|L-\d{1,3}|K-\d{1,3}|B-\d")

# 快通道命中时的预设短回复池（按 character 风格粗分）
QUICK_ACK_REPLIES = {
    "greeting": "在。门没锁。",
    "whatdoing": "修东西。你有事？",
}


def _classify_greeting(msg: str) -> Optional[str]:
    """
    返回 'greeting' / 'whatdoing' / None。
    None 表示不属于任何快通道问候。
    """
    m = msg.strip()
    if not m:
        return None
    for pat in GREETING_WHITELIST:
        if re.match(pat, m, re.IGNORECASE):
            # 再细分
            if "干" in m or "忙" in m or "做" in m:
                return "whatdoing"
            return "greeting"
    return None


def quick_ack_check(user_msg: str, ctx: dict) -> Optional[str]:
    """
    返回预设短回复（命中快通道）或 None（走全链路）。
    """
    # 只允许在会话极早期快通道
    if len(ctx.get("recent_turns", [])) > 1:
        return None

    m = user_msg.strip()
    if len(m) > 8:
        return None
    if "?" in m or "？" in m:
        return None
    if PROPER_NOUN_PATTERN.search(m):
        return None
    for bad in BLACKLIST_TERMS:
        if bad in m:
            return None
    kind = _classify_greeting(m)
    if kind is None:
        return None
    return QUICK_ACK_REPLIES.get(kind)


# ── 2. deterministic hook 补齐 ──

HOOK_MIN_CHARS = 8
HOOK_PROPER_PATTERN = re.compile(r"[A-Z]-\d{1,3}|T-\d{1,3}|A\d{2}|L-\d{1,3}|K-\d{1,3}|B-\d")

# 角色风格固定短语（避免和 quick_ack_check 的回复池重复）
HOOK_TEMPLATES = {
    "recent_event": "{noun} 这会儿正烦着呢。",
    "absent_person": "{noun} 今天没来。",
    "biased_target": "{noun} 那边的事，我还没想好怎么说。",
    "scene_default": "{noun} 刚出故障，得先看着。",
}


def _extract_hook_noun(ctx: dict, scene: dict) -> Optional[tuple[str, str]]:
    """
    从最近事件/场景/relational_biases 里挑一个最具体的名词。
    返回 (noun, template_key) 或 None。
    优先级：最近 memory → scene.npcs_absent → relational_biases target → None。
    """
    cs = ctx.get("character_state", {})
    mems = cs.get("memories") or []
    # 最新一条 memory 的文本里挖专有名词
    for m in reversed(mems[-3:]):
        text = m.get("text", "")
        match = HOOK_PROPER_PATTERN.search(text)
        if match:
            return match.group(0), "recent_event"

    absent = scene.get("npcs_absent") or []
    if absent:
        return absent[0], "absent_person"

    rb = cs.get("relational_biases") or []
    if rb:
        return rb[0].get("target_id", ""), "biased_target"

    # 最后 fallback：scene.location 里如果有专有名词
    loc = scene.get("location", "")
    match = HOOK_PROPER_PATTERN.search(loc)
    if match:
        return match.group(0), "scene_default"

    return None


def needs_hook(utterance: str) -> bool:
    """
    判断回复是否过短或无锚点，需要 deterministic 补钩子。
    """
    if not utterance:
        return False
    if HOOK_PROPER_PATTERN.search(utterance):
        return False
    if len(utterance) >= HOOK_MIN_CHARS:
        return False
    return True


def deterministic_hook(utterance: str, ctx: dict, scene: dict) -> str:
    """
    若 utterance 过短且无专有名词，壳层拼接一个具体 hook。
    不调用模型。若找不到合适名词，原样返回。
    """
    if not needs_hook(utterance):
        return utterance
    picked = _extract_hook_noun(ctx, scene)
    if picked is None:
        return utterance
    noun, key = picked
    tmpl = HOOK_TEMPLATES.get(key, HOOK_TEMPLATES["recent_event"])
    hook = tmpl.format(noun=noun)
    # 拼接方式：保留原句标点，在后面加一个空格或换行 + hook
    sep = "" if utterance.endswith(("。", "！", "？", ".", "!", "?")) else "。"
    return f"{utterance}{sep}{hook}"
