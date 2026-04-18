"""
decider — 调用 LLM 产出 candidate_actions / chosen_action。
只读联调；不写回任何状态。

验证维度（机械校验，不靠 NLP）：
  V1. JSON schema：字段类型、数量、范围
  V2. candidate_type 必须是 taxonomy 枚举
  V3. 任何候选 candidate_type 不得落在 forbidden_candidate_types
  V4. required_candidate_types 必须被候选覆盖
  V5. chosen_candidate_type 在约束要求 chosen 取某类时必须命中
  V6. fit_score_caps（含 any_other 通配）必须被尊重
  V7. chosen_action 必须是候选之一
"""

from __future__ import annotations
import json
import re
from pathlib import Path

from llm_client import chat_json, LLMError

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "prompts" / "decider.v1.md"


class DeciderError(Exception):
    pass


def _load_template() -> tuple[str, str]:
    text = PROMPT_PATH.read_text("utf-8")
    m = re.search(r"# SYSTEM\s*\n(.*?)\n# USER\s*\n(.*)", text, re.DOTALL)
    if not m:
        raise DeciderError("prompt template malformed")
    return m.group(1).strip(), m.group(2).strip()


def _render(template: str, slots: dict[str, str]) -> str:
    out = template
    for k, v in slots.items():
        out = out.replace(f"{{{{{k}}}}}", v)
    return out


def _ser(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _validate_schema(d: dict) -> list[str]:
    errs: list[str] = []
    for k in ("candidate_actions", "chosen_action", "chosen_candidate_type",
              "why_this_action", "why_not_others"):
        if k not in d:
            errs.append(f"missing: {k}")
    if errs:
        return errs

    cands = d["candidate_actions"]
    if not isinstance(cands, list) or not (3 <= len(cands) <= 5):
        errs.append(f"candidate_actions: need 3..5 items, got {len(cands) if isinstance(cands, list) else type(cands)}")
        return errs

    for i, c in enumerate(cands):
        for k in ("action", "candidate_type", "motivation", "risk", "fit_score"):
            if k not in c:
                errs.append(f"candidate[{i}].{k}: missing")
        if errs:
            continue
        if not isinstance(c["action"], str) or not c["action"]:
            errs.append(f"candidate[{i}].action: need non-empty string")
        if not isinstance(c["candidate_type"], str) or not c["candidate_type"]:
            errs.append(f"candidate[{i}].candidate_type: need non-empty string")
        if not isinstance(c["motivation"], list) or not (1 <= len(c["motivation"]) <= 3):
            errs.append(f"candidate[{i}].motivation: need 1..3 strings")
        if not isinstance(c["risk"], list) or not (1 <= len(c["risk"]) <= 3):
            errs.append(f"candidate[{i}].risk: need 1..3 strings")
        fs = c["fit_score"]
        if not isinstance(fs, int) or not (0 <= fs <= 100):
            errs.append(f"candidate[{i}].fit_score: need int 0..100, got {fs!r}")

    if not isinstance(d["chosen_action"], str) or not d["chosen_action"]:
        errs.append("chosen_action: need non-empty string")

    return errs


def _validate_constraints(
    d: dict,
    *,
    taxonomy_keys: set[str],
    required_types: list[str],
    forbidden_types: list[str],
    fit_score_caps: dict[str, int],
    mandatory_chosen_types: list[str],
) -> list[str]:
    """Return list of compliance errors; empty = fully compliant."""
    errs: list[str] = []
    cands = d["candidate_actions"]

    # V2. candidate_type must be in taxonomy
    for i, c in enumerate(cands):
        if c["candidate_type"] not in taxonomy_keys:
            errs.append(f"candidate[{i}].candidate_type '{c['candidate_type']}' not in taxonomy")

    # V3. no forbidden types among candidates
    forbidden = set(forbidden_types)
    for i, c in enumerate(cands):
        if c["candidate_type"] in forbidden:
            errs.append(f"candidate[{i}] is forbidden type: {c['candidate_type']}")

    # V4. required types must be covered
    present = {c["candidate_type"] for c in cands}
    for rt in required_types:
        if rt not in present:
            errs.append(f"required candidate_type missing from candidates: {rt}")

    # V5. chosen_candidate_type must match chosen_action's declared type
    chosen_action = d["chosen_action"]
    chosen_type = d["chosen_candidate_type"]
    chosen_cand = next((c for c in cands if c["action"] == chosen_action), None)
    if chosen_cand is None:
        errs.append("chosen_action does not match any candidate.action verbatim")
    elif chosen_cand["candidate_type"] != chosen_type:
        errs.append(
            f"chosen_candidate_type ({chosen_type}) != matched candidate's "
            f"candidate_type ({chosen_cand['candidate_type']})"
        )

    # V5b. mandatory_chosen_types: chosen must be one of these (if list non-empty)
    if mandatory_chosen_types and chosen_type not in mandatory_chosen_types:
        errs.append(
            f"chosen_candidate_type '{chosen_type}' not in mandatory chosen types {mandatory_chosen_types}"
        )

    # V6. fit_score caps (including any_other wildcard)
    # any_other applies to types that are NOT:
    #   (a) explicitly listed in fit_score_caps, or
    #   (b) in required_candidate_types (those are privileged winners)
    explicit_keys = {k for k in fit_score_caps if k != "any_other"}
    any_other_cap = fit_score_caps.get("any_other")
    privileged = set(required_types)
    for i, c in enumerate(cands):
        t = c["candidate_type"]
        fs = c.get("fit_score")
        if not isinstance(fs, int):
            continue
        if t in explicit_keys:
            if fs > fit_score_caps[t]:
                errs.append(f"candidate[{i}] ({t}): fit_score {fs} > cap {fit_score_caps[t]}")
        elif t in privileged:
            continue  # required types exempt from any_other cap
        elif any_other_cap is not None:
            if fs > any_other_cap:
                errs.append(f"candidate[{i}] ({t}): fit_score {fs} > any_other cap {any_other_cap}")

    return errs


def _extract_mandatory_chosen_types(hard_constraints: list[dict]) -> list[str]:
    """
    Scan decider_constraints text for the pattern 'chosen 取之' preceded by a
    candidate_type inside (...). This is how pose_rules encodes
    'chosen_action must be of this type'.
    """
    types: list[str] = []
    for c in hard_constraints:
        text = c.get("text", "")
        if "chosen 取之" not in text and "chosen_取之" not in text:
            continue
        m = re.search(r"\(([a-z_]+)\)", text)
        if m:
            types.append(m.group(1))
    return types


def decide(
    payload: dict,
    *,
    current_read: dict,
    rules: dict,
    resolved_mode: str,
    model: str = "deepseek-chat",
) -> dict:
    """
    payload = compile_phase_b(...)['decider_payload']
    Returns {"output": <decider JSON>, "compliance": {"errors": [...], "ok": bool}}.
    Does NOT write back to any state.
    """
    if payload.get("template_id") != "decider.v1":
        raise DeciderError(f"wrong template_id: {payload.get('template_id')}")

    mode_def = rules["modes"][resolved_mode]
    taxonomy = rules["candidate_type_taxonomy"]
    required = list(mode_def.get("required_candidate_types", []))
    forbidden = list(mode_def.get("forbidden_candidate_types", []))
    fit_caps = dict(mode_def.get("fit_score_caps", {}))
    mandatory_chosen = _extract_mandatory_chosen_types(payload["hard_constraints"])

    system, user_template = _load_template()
    slots = dict(payload["slots"])
    slots["HARD_CONSTRAINTS"] = "\n".join(
        f"- {c['text']} (src: {c['src']})" for c in payload["hard_constraints"]
    )
    slots["CANDIDATE_TYPE_TAXONOMY"] = _ser(taxonomy)
    slots["REQUIRED_TYPES"] = _ser(required)
    slots["FORBIDDEN_TYPES"] = _ser(forbidden)
    slots["FIT_SCORE_CAPS"] = _ser(fit_caps)
    slots["TIEBREAKERS"] = _ser(payload.get("tiebreakers"))
    user = _render(user_template, slots)

    try:
        out = chat_json(system, user, model=model, temperature=0.3, max_tokens=1500)
    except LLMError as e:
        raise DeciderError(f"llm failed: {e}") from e

    schema_errs = _validate_schema(out)
    if schema_errs:
        return {
            "output": out,
            "compliance": {"ok": False, "errors": schema_errs, "phase": "schema"},
            "meta": {"resolved_mode": resolved_mode},
        }

    comp_errs = _validate_constraints(
        out,
        taxonomy_keys=set(taxonomy.keys()),
        required_types=required,
        forbidden_types=forbidden,
        fit_score_caps=fit_caps,
        mandatory_chosen_types=mandatory_chosen,
    )
    return {
        "output": out,
        "compliance": {"ok": not comp_errs, "errors": comp_errs, "phase": "constraints"},
        "meta": {
            "resolved_mode": resolved_mode,
            "required_types": required,
            "forbidden_types": forbidden,
            "fit_score_caps": fit_caps,
            "mandatory_chosen_types": mandatory_chosen,
        },
    }
