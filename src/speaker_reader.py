"""
speaker_reader — 调用 LLM，把 Phase A payload 转成 current_read。

失败策略：schema 校验不过即 reject，不兜底、不重写。
"""

from __future__ import annotations
import re
from pathlib import Path

from llm_client import chat_json, LLMError

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "prompts" / "speaker_reader.v1.md"


VALID_LIKELY_MODES = {
    "serious_inquiry", "curiosity", "joking", "meme_play",
    "boundary_test", "provocation", "venting",
    "distress_signal", "malicious", "ambiguous",
}
VALID_SECONDARY = VALID_LIKELY_MODES | {"none"}
VALID_RESPONSE_MODES = {
    "playful_echo", "light_playful_boundary", "curious_pivot",
    "half_serious_probe", "soft_boundary", "hard_boundary",
    "check_on_state", "disengage",
}


class SpeakerReaderError(Exception):
    pass


def _load_template() -> tuple[str, str]:
    text = PROMPT_PATH.read_text("utf-8")
    m = re.search(r"# SYSTEM\s*\n(.*?)\n# USER\s*\n(.*)", text, re.DOTALL)
    if not m:
        raise SpeakerReaderError("prompt template malformed")
    return m.group(1).strip(), m.group(2).strip()


def _render(template: str, slots: dict[str, str]) -> str:
    out = template
    for k, v in slots.items():
        out = out.replace(f"{{{{{k}}}}}", v)
    return out


BUCKET_KEYS = [
    "playfulness_signals", "distress_signals", "seriousness_signals",
    "baseline_deviation_signals", "operational_risk_signals", "trust_risk_signals",
]


def _validate(cr: dict) -> list[str]:
    errs: list[str] = []
    req = ["evidence_buckets", "bucket_evidence",
           "likely_mode", "secondary_mode", "appears_playful", "appears_serious",
           "appears_distressed", "deviation_from_baseline", "confidence",
           "evidence", "recommended_response_mode"]
    for k in req:
        if k not in cr:
            errs.append(f"missing: {k}")
    if errs:
        return errs

    buckets = cr.get("evidence_buckets", {})
    for bk in BUCKET_KEYS:
        v = buckets.get(bk)
        if not isinstance(v, int) or not (0 <= v <= 100):
            errs.append(f"evidence_buckets.{bk}: need int 0..100, got {v!r}")
    ev_map = cr.get("bucket_evidence", {})
    for bk in BUCKET_KEYS:
        if bk not in ev_map or not isinstance(ev_map[bk], str) or not ev_map[bk]:
            errs.append(f"bucket_evidence.{bk}: need non-empty string")

    if cr["likely_mode"] not in VALID_LIKELY_MODES:
        errs.append(f"likely_mode not in enum: {cr['likely_mode']}")
    if cr["secondary_mode"] not in VALID_SECONDARY:
        errs.append(f"secondary_mode not in enum: {cr['secondary_mode']}")
    if cr["recommended_response_mode"] not in VALID_RESPONSE_MODES:
        errs.append(f"recommended_response_mode not in enum: {cr['recommended_response_mode']}")

    for k in ("appears_playful", "appears_serious", "appears_distressed", "deviation_from_baseline"):
        v = cr.get(k)
        if not isinstance(v, int) or not (0 <= v <= 100):
            errs.append(f"{k}: need int 0..100, got {v!r}")

    c = cr.get("confidence")
    if not isinstance(c, (int, float)) or not (0 <= c <= 1):
        errs.append(f"confidence: need 0..1, got {c!r}")

    ev = cr.get("evidence")
    if not isinstance(ev, list) or not (1 <= len(ev) <= 5) or not all(isinstance(x, str) and x for x in ev):
        errs.append("evidence: need 1..5 non-empty strings")

    return errs


def read_speaker(payload: dict, *, model: str = "deepseek-chat") -> dict:
    """payload = compile_phase_a(ctx, rules) 的输出。返回 current_read dict。"""
    if payload.get("template_id") != "speaker_reader.v1":
        raise SpeakerReaderError(f"wrong template_id: {payload.get('template_id')}")

    system, user_template = _load_template()
    slots = dict(payload["slots"])
    slots["CONSTRAINTS"] = "\n".join(f"- {c['text']} ({c['src']})" for c in payload.get("constraints", []))
    user = _render(user_template, slots)

    try:
        cr = chat_json(system, user, model=model, temperature=0.2)
    except LLMError as e:
        raise SpeakerReaderError(f"llm failed: {e}") from e

    errs = _validate(cr)
    if errs:
        raise SpeakerReaderError(f"current_read invalid: {errs}; raw={cr}")

    return cr
