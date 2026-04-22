"""
compiler — 四层状态 → 三模块 payload 的确定性装配器

Phase A: compile_phase_a(ctx) → SpeakerReaderPayload  (不依赖 current_read)
Phase B: compile_phase_b(ctx, current_read) → DeciderPayload + ExpresserPayload
fill_chosen_action(payload, action) → 回填 Expresser 的 CHOSEN_ACTION_TEXT

纯函数。不含 LLM 调用。
"""

from __future__ import annotations
import json
from typing import Any

from pose_resolver import resolve as pose_resolve


# ── constants ──

SPEAKER_READER_TOKEN_BUDGET = 3000
DECIDER_TOKEN_BUDGET = 3500
EXPRESSER_TOKEN_BUDGET = 1500

LESSON_MAX_PER_DEST = 3
LESSON_MIN_CONFIDENCE_SPEAKER = 0.7
LESSON_MIN_CONFIDENCE_GENERAL = 0.8


# ── errors ──

class CompileError(Exception):
    def __init__(self, code: str, **kw):
        self.code = code
        self.detail = kw
        super().__init__(f"{code}: {kw}")


# ── helpers ──

def _est_tokens(s: str) -> int:
    return len(s) // 2  # rough CJK estimate


def _ser(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _top_emotion(emotion: dict) -> dict:
    if not emotion:
        return {"name": "neutral", "value": 0}
    name = max(emotion, key=lambda k: emotion[k])
    return {"name": name, "value": emotion[name]}


def _route_lessons(lessons: list[dict], kind: str, max_n: int, min_conf: float) -> list[dict]:
    matched = [l for l in lessons if l.get("kind") == kind and l.get("confidence", 0) >= min_conf]
    matched.sort(key=lambda l: (-l.get("confidence", 0), l.get("created_at", "")))
    return matched[:max_n]


def _pick_recent_turns(turns: list[dict], max_n: int = 6) -> list[dict]:
    if len(turns) <= max_n:
        return turns

    kept = set()
    # P1: last 2
    for i in range(max(0, len(turns) - 2), len(turns)):
        kept.add(i)
    # P3: last user turn not in P1
    for i in range(len(turns) - 1, -1, -1):
        if i not in kept and turns[i].get("role") == "user":
            kept.add(i)
            break
    # P5: fill remaining by recency
    for i in range(len(turns) - 1, -1, -1):
        if len(kept) >= max_n:
            break
        kept.add(i)

    return [turns[i] for i in sorted(kept)]


# ── constraint builders ──

def _build_top_level(rules: dict) -> list[dict]:
    return list(rules["global"]["top_level_constraints"])


def _build_mode_constraints(mode_def: dict) -> list[dict]:
    return list(mode_def.get("decider_constraints", []))


def _build_crosscut_constraints(ctx: dict) -> list[dict]:
    lines = []
    trust = (ctx.get("speaker_model") or {}).get("trust_and_familiarity", {}).get("trust")
    if trust is not None and trust < 30:
        lines.append({
            "text": "候选中至少 1 项为保守动作 (conservative_action 或 clarifying_probe)",
            "src": "trust<30"
        })

    mobility = (ctx.get("character_state") or {}).get("constraints", {}).get("mobility")
    if mobility is not None and mobility < 40:
        lines.append({
            "text": "所有候选动作须符合 mobility<40 限制",
            "src": "state:mobility"
        })

    return lines


# ── P0 truth-layer: canonical identity / entities / interlocutor facts ──

def _build_self_identity_constraint(character_state: dict) -> dict | None:
    """
    Self identity truth slot (P0-A).
      name_policy="codename_only" → 禁止生造人名；自称必须用 id/codename
      name_policy="explicit_name" → 自称必须用 display_name
    """
    ident = character_state.get("identity", {}) or {}
    policy = ident.get("name_policy")
    if not policy:
        return None
    cid = ident.get("id") or ""
    disp = ident.get("display_name")

    if policy == "codename_only":
        return {
            "text": (
                f"你的身份是代号 {cid}，没有正式姓名。"
                f"回答中所有对'你叫什么/怎么称呼你'类问题的自称必须严格等于 {cid}；"
                "禁止为自己生造任何人名（如'老周/老王/小张'等）；"
                "禁止把自己的代号说成别的代号。"
            ),
            "src": "truth:self_identity:codename_only",
        }
    if policy == "explicit_name":
        if not disp:
            return None
        return {
            "text": (
                f"你的名字是 {disp}（{cid}）。自称必须严格使用 {disp}；"
                "禁止临时改名或使用未在 identity 中定义的别名。"
            ),
            "src": "truth:self_identity:explicit_name",
        }
    return None


def _entity_ids_mentioned(ctx: dict, entities: list[dict]) -> set:
    """Which entity ids appear in user_message or last 3 recent_turns or last 3 memories."""
    if not entities:
        return set()
    ids = {e.get("id") for e in entities if e.get("id")}
    surface = [ctx.get("situation", {}).get("user_message", "") or ""]
    for t in (ctx.get("recent_turns", []) or [])[-3:]:
        surface.append(t.get("text", "") or "")
    mems = (ctx.get("character_state", {}).get("memories", []) or [])[-5:]
    for m in mems:
        surface.append(m.get("text", "") or "")
    blob = "\n".join(surface)
    hit = set()
    for eid in ids:
        if eid and eid in blob:
            hit.add(eid)
    return hit


def _build_entity_truth_constraint(ctx: dict) -> dict | None:
    """
    World entities truth slot (P0-B).
    只把当前对话中出现过的 entity 注入（避免 persona entities[] 膨胀时超 budget）。
    """
    entities = (ctx.get("character_state", {}) or {}).get("entities") or []
    if not entities:
        return None
    mentioned = _entity_ids_mentioned(ctx, entities)
    if not mentioned:
        return None
    lines = []
    for e in entities:
        eid = e.get("id")
        if eid not in mentioned:
            continue
        desc = e.get("canonical_description", "") or ""
        aliases = e.get("aliases") or []
        alias_text = f"（别名: {', '.join(aliases)}）" if aliases else ""
        lines.append(f"  - {eid}{alias_text}: {desc}")
    if not lines:
        return None
    body = "\n".join(lines)
    return {
        "text": (
            "以下实体的定义是硬真值，不得用社会常识补全或改写："
            f"\n{body}\n"
            "当对话涉及这些 id 时，你的回答必须与上表一致；"
            "禁止把 A07/L-22/B-3 这类 id 描述成上表之外的身份（如'楼上那位老人'这种凭空标签）。"
        ),
        "src": "truth:entities",
    }


def _build_interlocutor_fact_constraint(ctx: dict) -> dict | None:
    """
    Interlocutor facts truth slot (P0-C).
    对方已声明事实必须视为已知；含中文"我"字歧义消歧。
    """
    facts = ctx.get("interlocutor_facts") or {}
    user_name = facts.get("user_name")
    claimed_role = facts.get("claimed_role")
    declared = []
    if user_name:
        declared.append(f"对方（即用户，你说话时称'你'）的名字/代号是：{user_name}")
    if claimed_role:
        declared.append(f"对方自称的身份：{claimed_role}")
    claims = facts.get("claims_made_this_session") or []
    for c in claims[-3:]:
        if c:
            declared.append(f"对方本次会话中明确声明过：{c}")
    if not declared:
        return None
    body = "\n  - ".join([""] + declared)
    parts = [f"对方（用户）已声明的事实，必须视为你已知：{body}"]
    if user_name:
        parts.append(
            f"\n注意中文'我'的指代歧义：当用户发问'我叫什么/我叫什么名字/我是谁'一类问题时，"
            f"'我'指的是用户自己（即 {user_name}），不是你自己。"
            f"正确答案是'你叫{user_name}'或类似的回忆式回应；"
            f"不要答自己的代号，也不要反问'你呢/你叫什么/你是谁'。"
        )
    parts.append("不得再向对方询问已在上面声明过的事实（如姓名），那是失忆表现。")
    return {
        "text": "".join(parts),
        "src": "truth:interlocutor",
    }


def _build_truth_layer_constraints(ctx: dict) -> list[dict]:
    """Return all applicable P0 truth-layer constraints (may be 0-3)."""
    cs = ctx.get("character_state", {}) or {}
    out = []
    si = _build_self_identity_constraint(cs)
    if si:
        out.append(si)
    ent = _build_entity_truth_constraint(ctx)
    if ent:
        out.append(ent)
    il = _build_interlocutor_fact_constraint(ctx)
    if il:
        out.append(il)
    return out


def _build_style_fence(mode_def: dict, character_state: dict) -> list[dict]:
    fence = []
    style = mode_def.get("expresser_style", {})

    for item in style.get("forbid", []):
        fence.append({"text": f"禁止: {item}", "src": f"pose:style:{item}"})

    emo = _top_emotion(character_state.get("emotion", {}))
    fence.append({
        "text": f"语气须与主导情绪 {emo['name']}={emo['value']} 一致",
        "src": "state:emotion"
    })

    phys = character_state.get("physiology", {})
    if phys.get("fatigue", 0) >= 70 or phys.get("pain", 0) >= 70:
        fence.append({"text": "utterance 使用短句/断句", "src": "state:physiology"})

    if style.get("utterance_ends_with_question"):
        fence.append({"text": "utterance 须以问句结尾", "src": "pose:style:question"})

    if style.get("utterance_may_be_empty"):
        fence.append({"text": "utterance 可为空", "src": "pose:style:empty_ok"})

    max_chars = style.get("utterance_max_chars")
    if max_chars is not None:
        fence.append({"text": f"utterance ≤ {max_chars} 字", "src": "pose:style:max_chars"})

    max_sent = style.get("sentences_max")
    if max_sent is not None:
        fence.append({"text": f"sentences ≤ {max_sent}", "src": "pose:style:max_sentences"})

    return fence


def _build_tiebreakers(mode_def: dict, speaker_model: dict) -> dict | None:
    tb = mode_def.get("tiebreaker_overrides")
    if isinstance(tb, dict) and tb.get("disable_strategy_preferences"):
        return None

    prefs = speaker_model.get("strategy_preferences", {})
    if not prefs:
        return None

    return {
        "strategy_preferences": prefs,
        "note": "仅在 fit_score 差 ≤5 且均满足硬约束时生效"
    }


# ── invariant checks ──

def _check_invariants(hard_constraints: list[dict], style_fence: list[dict],
                      rules: dict, resolved_mode: str):
    # INV-1: every line has src
    for line in hard_constraints + style_fence:
        if not line.get("src"):
            raise CompileError("INVARIANT_FAIL", inv="INV-1", detail="missing src")

    # INV-2: first constraint src == fixed_core:top_level
    if not hard_constraints or hard_constraints[0].get("src") != "fixed_core:top_level":
        raise CompileError("INVARIANT_FAIL", inv="INV-2")

    # INV-3: check_on_state must have fit_cap
    if resolved_mode == "check_on_state":
        srcs = {c["src"] for c in hard_constraints}
        if "pose:check_on_state:fit_cap" not in srcs:
            raise CompileError("INVARIANT_FAIL", inv="INV-3")

    # INV-8: count limits
    max_hc = rules["global"].get("hard_constraints_max", 12)
    max_sf = rules["global"].get("style_fence_max", 8)
    if len(hard_constraints) > max_hc:
        raise CompileError("INVARIANT_FAIL", inv="INV-8", detail=f"hard_constraints={len(hard_constraints)}")
    if len(style_fence) > max_sf:
        raise CompileError("INVARIANT_FAIL", inv="INV-8", detail=f"style_fence={len(style_fence)}")


# ── Phase A ──

def compile_phase_a(ctx: dict, rules: dict) -> dict:
    speaker_model = ctx.get("speaker_model", {})
    recent = _pick_recent_turns(ctx.get("recent_turns", []))
    user_msg = ctx.get("situation", {}).get("user_message", "")
    now = ctx.get("now_iso", "")

    lessons_speaker = _route_lessons(
        ctx.get("lessons", []), "speaker_model", LESSON_MAX_PER_DEST, LESSON_MIN_CONFIDENCE_SPEAKER)
    lessons_misread = _route_lessons(
        ctx.get("lessons", []), "self_misread", LESSON_MAX_PER_DEST, LESSON_MIN_CONFIDENCE_SPEAKER)
    all_lessons = (lessons_speaker + lessons_misread)[:LESSON_MAX_PER_DEST]

    constraints = []
    constraints.append({
        "text": "distress 迹象优先级高于 baseline 玩笑判断",
        "src": "fixed_core:safety"
    })
    constraints.append({
        "text": "若 deviation_from_baseline ≥ 50 不得沿用基线默认模式",
        "src": "rule:deviation"
    })

    trace = {
        "phase": "A",
        "lessons_selected": [l.get("text", "")[:30] for l in all_lessons],
        "recent_turns_kept": len(recent),
        "constraints_added": [c["src"] for c in constraints],
    }

    return {
        "template_id": "speaker_reader.v1",
        "slots": {
            "SPEAKER_MODEL_JSON": _ser(speaker_model),
            "RECENT_TURNS": _ser(recent),
            "USER_MESSAGE": user_msg,
            "NOW_ISO": now,
            "LESSONS": _ser(all_lessons),
            "INTERLOCUTOR_FACTS": _ser(ctx.get("interlocutor_facts") or {}),
        },
        "constraints": constraints,
        "_trace": trace,
    }


# ── Phase B ──

def compile_phase_b(ctx: dict, current_read: dict, rules: dict) -> dict:
    recommended = current_read.get("recommended_response_mode", "half_serious_probe")
    result = pose_resolve(rules, recommended, {
        "current_read": current_read,
        "situation": ctx.get("situation", {}),
        "speaker_model": ctx.get("speaker_model", {}),
        "character_state": ctx.get("character_state", {}),
    })
    resolved_mode = result["mode"]
    mode_def = rules["modes"][resolved_mode]

    # ── Decider payload ──

    hard_constraints = []
    hard_constraints.extend(_build_top_level(rules))
    hard_constraints.extend(_build_mode_constraints(mode_def))
    hard_constraints.extend(_build_crosscut_constraints(ctx))
    hard_constraints.extend(_build_truth_layer_constraints(ctx))

    # strategy_preferences line
    sp = ctx.get("speaker_model", {}).get("strategy_preferences", {})
    tb_override = mode_def.get("tiebreaker_overrides")
    if not (isinstance(tb_override, dict) and tb_override.get("disable_strategy_preferences")):
        if sp:
            hard_constraints.append({
                "text": "strategy_preferences 仅在 fit_score 差 ≤5 且均满足硬约束时生效",
                "src": "strategy_layer"
            })

    # lessons for decider
    lessons_strategy = _route_lessons(
        ctx.get("lessons", []), "strategy", LESSON_MAX_PER_DEST, LESSON_MIN_CONFIDENCE_SPEAKER)
    lessons_general = _route_lessons(
        ctx.get("lessons", []), "general", 2, LESSON_MIN_CONFIDENCE_GENERAL)
    lessons_misread = _route_lessons(
        ctx.get("lessons", []), "self_misread", LESSON_MAX_PER_DEST, LESSON_MIN_CONFIDENCE_SPEAKER)
    decider_lessons = (lessons_strategy + lessons_general + lessons_misread)[:LESSON_MAX_PER_DEST]

    tiebreakers = _build_tiebreakers(mode_def, ctx.get("speaker_model", {}))

    # ── Expresser payload ──

    character_state = ctx.get("character_state", {})
    style_fence = _build_style_fence(mode_def, character_state)

    # ── invariant checks ──

    _check_invariants(hard_constraints, style_fence, rules, resolved_mode)

    # ── trace ──

    trace = {
        "phase": "B",
        "recommended_mode": recommended,
        "resolved_mode": resolved_mode,
        "escalated_by": result["escalated_by"],
        "hard_constraints_count": len(hard_constraints),
        "hard_constraints_srcs": [c["src"] for c in hard_constraints],
        "style_fence_count": len(style_fence),
        "crosscut_triggered": [
            c["src"] for c in hard_constraints
            if c["src"] in ("trust<30", "state:mobility")
        ],
        "lessons_selected": [l.get("text", "")[:30] for l in decider_lessons],
        "tiebreakers_active": tiebreakers is not None,
    }

    decider_payload = {
        "template_id": "decider.v1",
        "slots": {
            "STATE_JSON": _ser(character_state),
            "SITUATION_TEXT": ctx.get("situation", {}).get("user_message", ""),
            "CURRENT_READ_JSON": _ser(current_read),
            "LESSONS": _ser(decider_lessons),
        },
        "hard_constraints": hard_constraints,
        "tiebreakers": tiebreakers,
    }

    expresser_payload = {
        "template_id": "expresser.v1",
        "slots": {
            "STATE_JSON": _ser(character_state),
            "CHOSEN_ACTION_TEXT": "",  # filled by fill_chosen_action
        },
        "style_fence": style_fence,
    }

    return {
        "decider_payload": decider_payload,
        "expresser_payload": expresser_payload,
        "_trace": trace,
    }


def fill_chosen_action(expresser_payload: dict, chosen_action: str) -> dict:
    out = dict(expresser_payload)
    out["slots"] = dict(out["slots"])
    out["slots"]["CHOSEN_ACTION_TEXT"] = chosen_action
    return out
