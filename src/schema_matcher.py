"""
schema_matcher — LLM 抽象结构匹配。

只在 association_gate 返回 light/deep 时调用。
输出 schema_hits: list[dict]，每条带 schema_id / match_score / match_axes /
matched_memory_idxs / rationale_one_line / proposed_state_shift。

硬校验：schema_id 必须在白名单；match_axes ≥ 1；memory_idxs 合法；
match_score ≥ 0.5；hits ≤ 3；state_shift 字段在白名单且 |value| ≤ 25。
无效直接 reject，不兜底。
"""

from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

from llm_client import chat_json, LLMError

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "prompts" / "schema_matcher.v1.md"
SCHEMAS_PATH = ROOT / "rules" / "memory_schemas.v1.json"


class SchemaMatcherError(Exception):
    pass


_cache: dict[str, Any] = {}


def _load_schemas() -> dict:
    if "defs" not in _cache:
        _cache["defs"] = json.loads(SCHEMAS_PATH.read_text("utf-8"))
    return _cache["defs"]


def _load_prompt_template() -> tuple[str, str]:
    text = PROMPT_PATH.read_text("utf-8")
    m = re.search(r"# SYSTEM\s*\n(.*?)\n# USER\s*\n(.*)", text, re.DOTALL)
    if not m:
        raise SchemaMatcherError("prompt template malformed")
    return m.group(1).strip(), m.group(2).strip()


def _render(template: str, slots: dict[str, str]) -> str:
    out = template
    for k, v in slots.items():
        out = out.replace(f"{{{{{k}}}}}", v)
    return out


def _ser(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ── validation ──

def _validate_hits(
    hits: list,
    *,
    valid_schema_ids: set[str],
    memory_count: int,
    allowed_shift_fields: set[str],
) -> list[str]:
    errs: list[str] = []
    if not isinstance(hits, list):
        return [f"hits must be array, got {type(hits).__name__}"]
    if len(hits) > 3:
        errs.append(f"too many hits ({len(hits)} > 3)")

    for i, h in enumerate(hits):
        if not isinstance(h, dict):
            errs.append(f"hit[{i}] must be object")
            continue

        for req in ("schema_id", "match_score", "match_axes",
                    "matched_memory_idxs", "rationale_one_line",
                    "proposed_state_shift"):
            if req not in h:
                errs.append(f"hit[{i}].{req}: missing")

        if errs and any(f"hit[{i}]" in e for e in errs):
            continue

        sid = h.get("schema_id")
        if sid not in valid_schema_ids:
            errs.append(f"hit[{i}].schema_id '{sid}' not in whitelist")

        score = h.get("match_score")
        if not isinstance(score, (int, float)):
            errs.append(f"hit[{i}].match_score: need number, got {type(score).__name__}")
        elif not (0 <= score <= 1):
            errs.append(f"hit[{i}].match_score out of [0,1]: {score}")
        elif score < 0.5:
            errs.append(f"hit[{i}].match_score below threshold (0.5): {score}")

        axes = h.get("match_axes")
        if not isinstance(axes, list) or not axes:
            errs.append(f"hit[{i}].match_axes must be non-empty array")
        else:
            for j, a in enumerate(axes):
                if not isinstance(a, str) or not a.strip():
                    errs.append(f"hit[{i}].match_axes[{j}] must be non-empty string")

        idxs = h.get("matched_memory_idxs")
        if not isinstance(idxs, list) or not idxs:
            errs.append(f"hit[{i}].matched_memory_idxs must be non-empty array")
        else:
            for j, idx in enumerate(idxs):
                if not isinstance(idx, int):
                    errs.append(f"hit[{i}].matched_memory_idxs[{j}] must be integer")
                elif idx < 0 or idx >= memory_count:
                    errs.append(f"hit[{i}].matched_memory_idxs[{j}]={idx} out of range [0,{memory_count-1}]")

        rationale = h.get("rationale_one_line")
        if not isinstance(rationale, str) or not rationale.strip():
            errs.append(f"hit[{i}].rationale_one_line must be non-empty string")
        elif len(rationale) > 50:
            errs.append(f"hit[{i}].rationale_one_line too long ({len(rationale)} > 50)")

        shift = h.get("proposed_state_shift")
        if not isinstance(shift, dict):
            errs.append(f"hit[{i}].proposed_state_shift must be object")
        else:
            for k, v in shift.items():
                if k not in allowed_shift_fields:
                    errs.append(f"hit[{i}].proposed_state_shift.{k} not in allowed fields")
                if not isinstance(v, int):
                    errs.append(f"hit[{i}].proposed_state_shift.{k} must be integer")
                elif abs(v) > 25:
                    errs.append(f"hit[{i}].proposed_state_shift.{k}={v} outside [-25,25]")

    return errs


# ── main entry ──

def match(ctx: dict, current_read: dict, gate_level: str,
          *, model: str = "deepseek-chat") -> list[dict]:
    """
    Called only when gate_level in {'light', 'deep'}.
    Returns list of validated schema hits, or raises SchemaMatcherError.
    """
    if gate_level not in ("light", "deep"):
        return []

    defs = _load_schemas()
    valid_ids = {s["schema_id"] for s in defs["schemas"]}
    allowed_fields = set(defs["state_shift_fields"])

    memories = ((ctx or {}).get("character_state", {}) or {}).get("memories", [])
    memory_count = len(memories)
    if memory_count == 0:
        return []  # no memories to match against; gracefully empty

    system, user_template = _load_prompt_template()
    slots = {
        "SCHEMA_DEFS_JSON": _ser(defs["schemas"]),
        "STATE_SHIFT_FIELDS_JSON": _ser(defs["state_shift_fields"]),
        "CHARACTER_MEMORIES_JSON": _ser([
            {"idx": i, **m} for i, m in enumerate(memories)
        ]),
        "RECENT_TURNS": _ser(ctx.get("recent_turns", [])),
        "USER_MESSAGE": ctx.get("situation", {}).get("user_message", ""),
    }
    user = _render(user_template, slots)

    try:
        # DeepSeek JSON mode guarantees object; wrap an envelope in the prompt
        # by asking for {"hits": [...]} shape and extracting.
        raw = chat_json(
            system + "\n\n重要: 把结果包在 {\"hits\": [...]} 里返回；hits 为空就返回 {\"hits\": []}。",
            user,
            model=model,
            temperature=0.1,
            max_tokens=800,
        )
    except LLMError as e:
        raise SchemaMatcherError(f"llm failed: {e}") from e

    if not isinstance(raw, dict) or "hits" not in raw:
        raise SchemaMatcherError(f"expected object with 'hits', got {raw!r}")
    hits = raw["hits"]

    errs = _validate_hits(
        hits,
        valid_schema_ids=valid_ids,
        memory_count=memory_count,
        allowed_shift_fields=allowed_fields,
    )
    if errs:
        raise SchemaMatcherError(f"invalid hits: {errs}; raw={hits!r}")

    # sort by match_score descending
    hits.sort(key=lambda h: -h["match_score"])
    return hits


def apply_state_shifts(
    internal_pressures: dict,
    hits: list[dict],
    *,
    per_field_cap: int = 40,
) -> dict:
    """Accumulate proposed_state_shift from all hits into internal_pressures.
    Each field clamped to [0, per_field_cap] this turn. Pure function — returns new dict."""
    out = dict(internal_pressures or {})
    for h in hits or []:
        shift = h.get("proposed_state_shift", {})
        for k, v in shift.items():
            cur = out.get(k, 0)
            out[k] = max(0, min(per_field_cap, cur + int(v)))
    return out
