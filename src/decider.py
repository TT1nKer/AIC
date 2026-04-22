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


def _strip_v2_blocks(text: str) -> str:
    """Remove the 【schema_hits】/【internal_pressures】/【knowledge_boundary】 sections
    to keep v1 prompt bit-for-bit identical when no v2/p2 data is present. Each block
    starts at the marker line and ends at the next blank line following its payload."""
    import re
    pattern = re.compile(
        r"\n\n【(?:schema_hits|internal_pressures|knowledge_boundary)[^\n]*】.*?(?=\n\n【|\n\n按|\Z)",
        re.DOTALL,
    )
    return pattern.sub("", text)


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


ANSWER_SATISFYING_TYPES = {
    "direct_self_answer", "partial_answer_with_uncertainty", "reference_resolution"
}


def _validate_discourse(d: dict, discourse: dict, mode_required_types: list[str] | None = None) -> list[str]:
    """Rule 7/8/9 discourse validation.

    Rule 7: answer_obligation=high -> chosen must be a direct-answer type OR
    the resolved mode's required_candidate_type (because choosing the mode's
    required type IS the correct answer for that pose, e.g. check_on_state ->
    stop_joke_ask_state).
    """
    errs: list[str] = []
    cands = d.get("candidate_actions", [])
    chosen_type = d.get("chosen_candidate_type")
    present_types = {c.get("candidate_type") for c in cands}
    satisfying = set(ANSWER_SATISFYING_TYPES) | set(mode_required_types or [])

    usr = (discourse or {}).get("unresolved_self_reference")
    # Rule 8 first — more specific. When there's an unresolved reference, the
    # chosen must resolve it: reference_resolution explicitly, a direct answer
    # that implicitly resolves it, or the mode's required type when that
    # required type itself can serve as resolution (e.g., direct_self_answer
    # of "为啥喜欢辣" IS the resolution).
    if usr:
        if not (present_types & satisfying):
            errs.append("discourse rule 8: unresolved_self_reference present but no resolution-capable candidate")
        elif chosen_type not in satisfying:
            errs.append(f"discourse rule 8: unresolved_self_reference present but chose {chosen_type} (need reference_resolution / direct answer / mode's required type)")
    else:
        # Rule 7 only fires when there's no outstanding reference
        oblig = (discourse or {}).get("answer_obligation")
        if oblig == "high":
            if not (present_types & satisfying):
                errs.append("discourse rule 7: answer_obligation=high but no answer-satisfying candidate")
            elif chosen_type not in satisfying:
                reasons = d.get("why_this_action", [])
                joined = " ".join(reasons) if isinstance(reasons, list) else str(reasons)
                if chosen_type == "clarifying_probe" and not any(k in joined for k in ("信息", "不足", "不够", "无法", "无从")):
                    errs.append("discourse rule 7: chose clarifying_probe without info-insufficiency justification")
                elif chosen_type != "clarifying_probe":
                    errs.append(f"discourse rule 7: answer_obligation=high but chose {chosen_type}")

    pressure = (discourse or {}).get("topic_pressure")
    if pressure == "must_answer_before_pivot" and chosen_type == "abstract_pivot":
        errs.append("discourse rule 9: topic_pressure=must_answer_before_pivot but chose abstract_pivot")

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
    slots["DISCOURSE_STATE"] = _ser(current_read.get("discourse_state", {}))
    hits = current_read.get("schema_hits")
    pressures = current_read.get("internal_pressures")
    kb = current_read.get("knowledge_boundary") or {}
    kb_fragments = kb.get("known_secret_fragments") if isinstance(kb, dict) else None
    has_v2_data = (
        bool(hits)
        or (pressures and any(v != 0 for v in pressures.values()))
        or bool(kb_fragments)
    )
    slots["SCHEMA_HITS"] = _ser(hits or [])
    slots["INTERNAL_PRESSURES"] = _ser(pressures or {})
    slots["KNOWLEDGE_BOUNDARY"] = _ser(kb if kb_fragments else {})
    user = _render(user_template, slots)
    if not has_v2_data:
        # Strip 【schema_hits】/【internal_pressures】/【knowledge_boundary】 blocks so
        # v1 prompt stays bit-for-bit identical when no v2/p2 data is present.
        user = _strip_v2_blocks(user)

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
    discourse_errs = _validate_discourse(
        out,
        current_read.get("discourse_state", {}),
        mode_required_types=required,
    )
    all_errs = comp_errs + discourse_errs
    return {
        "output": out,
        "compliance": {"ok": not all_errs, "errors": all_errs, "phase": "constraints"},
        "meta": {
            "resolved_mode": resolved_mode,
            "required_types": required,
            "forbidden_types": forbidden,
            "fit_score_caps": fit_caps,
            "mandatory_chosen_types": mandatory_chosen,
        },
    }
