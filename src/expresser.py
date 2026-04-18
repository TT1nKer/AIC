"""
expresser — chosen_action → 外显 (action/gesture/facial/utterance/thought)

不兜底。以下任何一项不满足即 reject：
  E1 JSON schema
  E2 redline_checker: utterance + thought 必须 pass
  E3 style_fence: 长度、句数、问号结尾、可空规则
  E4 禁止字段泄漏（由 redline_checker 已覆盖大部分；此处不再重复）
"""

from __future__ import annotations
import json
import re
from pathlib import Path

from llm_client import chat_json, LLMError
from redline_checker import check as redline_check

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "prompts" / "expresser.v1.md"


class ExpresserError(Exception):
    pass


def _load_template() -> tuple[str, str]:
    text = PROMPT_PATH.read_text("utf-8")
    m = re.search(r"# SYSTEM\s*\n(.*?)\n# USER\s*\n(.*)", text, re.DOTALL)
    if not m:
        raise ExpresserError("prompt template malformed")
    return m.group(1).strip(), m.group(2).strip()


def _render(template: str, slots: dict[str, str]) -> str:
    out = template
    for k, v in slots.items():
        out = out.replace(f"{{{{{k}}}}}", v)
    return out


def _ser(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _validate_schema(e: dict) -> list[str]:
    errs: list[str] = []
    for k in ("action", "gesture", "facial_expression", "utterance", "thought"):
        if k not in e:
            errs.append(f"missing: {k}")
    if errs:
        return errs
    for k in ("action", "gesture", "facial_expression", "utterance", "thought"):
        if not isinstance(e[k], str):
            errs.append(f"{k}: need string, got {type(e[k])}")
    # default length sanity (style_fence may tighten)
    if len(e.get("action", "")) > 60:
        errs.append(f"action too long: {len(e['action'])}")
    if len(e.get("gesture", "")) > 50:
        errs.append(f"gesture too long: {len(e['gesture'])}")
    if len(e.get("facial_expression", "")) > 40:
        errs.append(f"facial_expression too long: {len(e['facial_expression'])}")
    if len(e.get("thought", "")) > 50:
        errs.append(f"thought too long: {len(e['thought'])}")
    return errs


def _count_sentences_zh(s: str) -> int:
    if not s:
        return 0
    parts = re.split(r"[。！？!?…]+", s.strip())
    return len([p for p in parts if p.strip()])


def _parse_fence(fence: list[dict]) -> dict:
    """Extract machine-checkable style constraints from style_fence rows."""
    out = {
        "utterance_max_chars": None,
        "sentences_max": None,
        "utterance_ends_with_question": False,
        "utterance_may_be_empty": False,
    }
    for row in fence:
        t = row.get("text", "")
        m = re.search(r"utterance\s*≤\s*(\d+)\s*字", t)
        if m:
            out["utterance_max_chars"] = int(m.group(1))
        m = re.search(r"sentences\s*≤\s*(\d+)", t)
        if m:
            out["sentences_max"] = int(m.group(1))
        if "问句结尾" in t:
            out["utterance_ends_with_question"] = True
        if "utterance 可为空" in t:
            out["utterance_may_be_empty"] = True
    return out


def _validate_fence(e: dict, fence: list[dict]) -> list[str]:
    errs: list[str] = []
    cfg = _parse_fence(fence)
    utt = e.get("utterance", "") or ""

    if cfg["utterance_max_chars"] is not None and len(utt) > cfg["utterance_max_chars"]:
        errs.append(f"utterance {len(utt)} chars > max {cfg['utterance_max_chars']}")

    if cfg["sentences_max"] is not None:
        n = _count_sentences_zh(utt)
        if n > cfg["sentences_max"]:
            errs.append(f"utterance {n} sentences > max {cfg['sentences_max']}")

    if cfg["utterance_ends_with_question"] and utt:
        if not re.search(r"[？?]\s*$", utt):
            errs.append("utterance must end with question mark")

    return errs


def _validate_redlines(e: dict, redlines: dict) -> list[str]:
    errs: list[str] = []
    for surface, field in (("utterance", "utterance"), ("thought", "thought")):
        r = redline_check(redlines, surface, e.get(field, "") or "")
        if r["verdict"] != "pass":
            errs.append(f"redline block on {field}: {r['hit_rule']}")
    return errs


def express(
    payload: dict,
    *,
    chosen_action: str,
    chosen_candidate_type: str,
    redlines: dict,
    model: str = "deepseek-chat",
    max_attempts: int = 2,
) -> dict:
    """
    payload = compile_phase_b(...)['expresser_payload']
    Returns {"output": <JSON>, "compliance": {"ok": bool, "errors": [...], "phase": ...}}.
    On first-pass failure, retries once with the validation errors appended as a correction hint.
    No silent fallback: if second attempt also fails, returns compliance.ok=False with errors.
    """
    if payload.get("template_id") != "expresser.v1":
        raise ExpresserError(f"wrong template_id: {payload.get('template_id')}")

    system, user_template = _load_template()
    base_slots = dict(payload["slots"])
    base_slots["CHOSEN_ACTION_TEXT"] = chosen_action
    base_slots["CHOSEN_CANDIDATE_TYPE"] = chosen_candidate_type
    fence_lines = payload.get("style_fence", [])
    base_slots["STYLE_FENCE"] = "\n".join(f"- {c['text']} (src: {c['src']})" for c in fence_lines)

    last_errs: list[str] = []
    last_out: dict | None = None

    for attempt in range(max_attempts):
        user = _render(user_template, base_slots)
        if attempt > 0 and last_errs:
            user += "\n\n【上次输出违反了以下规则，请修正后重新输出，禁止兜底或省略】\n"
            user += "\n".join(f"- {e}" for e in last_errs)

        try:
            out = chat_json(system, user, model=model, temperature=0.5, max_tokens=600)
        except LLMError as e:
            raise ExpresserError(f"llm failed: {e}") from e
        last_out = out

        schema_errs = _validate_schema(out)
        if schema_errs:
            last_errs = schema_errs
            continue
        fence_errs = _validate_fence(out, fence_lines)
        redline_errs = _validate_redlines(out, redlines)
        all_errs = fence_errs + redline_errs
        if not all_errs:
            return {"output": out, "compliance": {"ok": True, "errors": [], "phase": "passed"}}
        last_errs = all_errs

    return {
        "output": last_out or {},
        "compliance": {"ok": False, "errors": last_errs, "phase": "rejected_after_retry"},
    }
