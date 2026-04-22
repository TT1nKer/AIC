"""
distiller — 调 LLM 把一段叙事蒸成 distilled_package JSON。

v0.1：
  - 输入：一段文本（文件或 --text）
  - 调 DeepSeek，要求 JSON 输出
  - 对输出做强 schema 校验（使用 distilled_package.v0.1）
  - 对输出做**抗泄漏校验**：扫描是否含常见可识别特征（见 _leak_check）
  - 失败直接 reject，不兜底

用法：
  python3 distiller.py --in <file.txt>
  python3 distiller.py --in <file.txt> --hint "重点关注 A 和 B 的关系"
  python3 distiller.py --text "..." --out <path>.json

依赖：复用 AICharacter 的 src/llm_client.py（DEEPSEEK_API_KEY）。
"""

from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

AICHAR_SRC = Path(__file__).resolve().parent.parent.parent / "src"
sys.path.insert(0, str(AICHAR_SRC))

from llm_client import chat_json, LLMError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "prompts" / "distiller.v0.1.md"
SCHEMA_PATH = ROOT / "schemas" / "distilled_package.v0.1.json"


class DistillerError(Exception):
    pass


DEFENSE_STYLES = {
    "hard_deflect", "minimize_own_suffering", "joke_to_escape", "change_topic",
    "self_blame_to_shut_down", "attack_others", "silent_withdrawal",
    "overcompensate_with_competence", "intellectualize", "deny_then_partial_admit",
}
EMOTIONS = {
    "shame", "fear", "anger", "sadness", "grief", "pride",
    "guilt", "attachment_loss", "betrayal_residue",
}
TENSION_TYPES = {
    "owes_something", "suspects", "depends_on", "avoids", "sees_as_replacement",
    "cooperates_on_surface_opposes_underneath", "protects_from_truth",
    "envies", "blames", "is_blamed_by",
}
KNOWS_LEVELS = {
    "full_truth", "partial", "wrong_version", "unaware", "suspects_but_avoids_checking",
}
ATTITUDES = {
    "will_volunteer", "will_admit_if_pressed", "will_deflect",
    "will_deny", "will_kill_topic",
}


def _load_prompt() -> tuple[str, str]:
    text = PROMPT_PATH.read_text("utf-8")
    m = re.search(r"# SYSTEM\s*\n(.*?)\n# USER\s*\n(.*)", text, re.DOTALL)
    if not m:
        raise DistillerError("prompt template malformed")
    return m.group(1).strip(), m.group(2).strip()


def _render(template: str, slots: dict[str, str]) -> str:
    out = template
    for k, v in slots.items():
        out = out.replace(f"{{{{{k}}}}}", v)
    return out


# ── schema validation ──

def _validate_package(pkg: dict) -> list[str]:
    errs: list[str] = []
    if not isinstance(pkg, dict):
        return ["top-level must be object"]

    if pkg.get("version") != "0.1.0":
        errs.append(f"version must be '0.1.0', got {pkg.get('version')!r}")

    chars = pkg.get("characters")
    if not isinstance(chars, list):
        errs.append("characters must be array")
        return errs
    if len(chars) > 8:
        errs.append(f"too many characters: {len(chars)} > 8")

    char_ids = set()
    for i, c in enumerate(chars):
        errs.extend(_validate_character(c, i, char_ids))

    for i, r in enumerate(pkg.get("relationships", []) or []):
        errs.extend(_validate_relationship(r, i, char_ids))

    for i, s in enumerate(pkg.get("secrets", []) or []):
        errs.extend(_validate_secret(s, i, char_ids))

    for i, t in enumerate(pkg.get("triggers", []) or []):
        errs.extend(_validate_trigger(t, i, char_ids))

    return errs


def _validate_character(c: dict, i: int, char_ids: set) -> list[str]:
    errs = []
    prefix = f"character[{i}]"
    if not isinstance(c, dict):
        return [f"{prefix} must be object"]

    for req in ("character_id", "core_drive", "defense_style", "trigger_patterns", "sediment_traces"):
        if req not in c:
            errs.append(f"{prefix}.{req}: missing")
    if errs:
        return errs

    cid = c["character_id"]
    if not re.match(r"^[A-Za-z0-9_]+$", cid) or len(cid) > 20:
        errs.append(f"{prefix}.character_id invalid: {cid!r}")
    elif cid in char_ids:
        errs.append(f"{prefix}.character_id duplicate: {cid}")
    else:
        char_ids.add(cid)

    for field, maxn in [("core_drive", 3), ("hidden_fact", 2),
                        ("trigger_patterns", 5), ("cannot_say", 4)]:
        v = c.get(field, [])
        if not isinstance(v, list) or len(v) > maxn:
            errs.append(f"{prefix}.{field} invalid list (got {type(v).__name__}, need ≤{maxn})")
        else:
            for j, x in enumerate(v):
                if not isinstance(x, str) or len(x) > 80:
                    errs.append(f"{prefix}.{field}[{j}] must be string ≤80")

    ds = c.get("defense_style", [])
    if not isinstance(ds, list) or not (1 <= len(ds) <= 4):
        errs.append(f"{prefix}.defense_style need 1..4 items")
    else:
        for j, d in enumerate(ds):
            if d not in DEFENSE_STYLES:
                errs.append(f"{prefix}.defense_style[{j}] not in enum: {d}")

    st = c.get("sediment_traces", [])
    if not isinstance(st, list) or not (1 <= len(st) <= 3):
        errs.append(f"{prefix}.sediment_traces need 1..3 items")
    else:
        for j, s in enumerate(st):
            if not isinstance(s, dict):
                errs.append(f"{prefix}.sediment_traces[{j}] must be object")
                continue
            for k in ("text", "emotion", "salience"):
                if k not in s:
                    errs.append(f"{prefix}.sediment_traces[{j}].{k} missing")
            if isinstance(s.get("text"), str) and len(s["text"]) > 60:
                errs.append(f"{prefix}.sediment_traces[{j}].text too long")
            if s.get("emotion") not in EMOTIONS:
                errs.append(f"{prefix}.sediment_traces[{j}].emotion invalid: {s.get('emotion')}")
            sal = s.get("salience")
            if not isinstance(sal, int) or not (40 <= sal <= 100):
                errs.append(f"{prefix}.sediment_traces[{j}].salience invalid: {sal}")

    return errs


def _validate_relationship(r: dict, i: int, char_ids: set) -> list[str]:
    errs = []
    prefix = f"relationship[{i}]"
    if not isinstance(r, dict):
        return [f"{prefix} must be object"]
    for req in ("from_id", "to_id", "tension_type", "surface_vs_underneath"):
        if req not in r:
            errs.append(f"{prefix}.{req} missing")
    if errs:
        return errs
    if r["from_id"] not in char_ids:
        errs.append(f"{prefix}.from_id unknown: {r['from_id']}")
    if r["to_id"] not in char_ids:
        errs.append(f"{prefix}.to_id unknown: {r['to_id']}")
    if r["tension_type"] not in TENSION_TYPES:
        errs.append(f"{prefix}.tension_type invalid: {r['tension_type']}")
    sv = r.get("surface_vs_underneath", {})
    if not isinstance(sv, dict) or "surface" not in sv or "underneath" not in sv:
        errs.append(f"{prefix}.surface_vs_underneath needs surface+underneath")
    return errs


def _validate_secret(s: dict, i: int, char_ids: set) -> list[str]:
    errs = []
    prefix = f"secret[{i}]"
    if not isinstance(s, dict):
        return [f"{prefix} must be object"]
    for req in ("secret_id", "truth_abstracted", "knowledge_map"):
        if req not in s:
            errs.append(f"{prefix}.{req} missing")
    if errs:
        return errs
    if not re.match(r"^[a-z0-9_]+$", s["secret_id"]):
        errs.append(f"{prefix}.secret_id invalid: {s['secret_id']}")
    km = s.get("knowledge_map", [])
    if not isinstance(km, list) or not km:
        errs.append(f"{prefix}.knowledge_map must be non-empty array")
    else:
        for j, e in enumerate(km):
            if not isinstance(e, dict):
                errs.append(f"{prefix}.knowledge_map[{j}] must be object")
                continue
            if e.get("character_id") not in char_ids:
                errs.append(f"{prefix}.knowledge_map[{j}].character_id unknown: {e.get('character_id')}")
            if e.get("knows_level") not in KNOWS_LEVELS:
                errs.append(f"{prefix}.knowledge_map[{j}].knows_level invalid: {e.get('knows_level')}")
            att = e.get("attitude")
            if att is not None and att not in ATTITUDES:
                errs.append(f"{prefix}.knowledge_map[{j}].attitude invalid: {att}")
    return errs


def _validate_trigger(t: dict, i: int, char_ids: set) -> list[str]:
    errs = []
    prefix = f"trigger[{i}]"
    if not isinstance(t, dict):
        return [f"{prefix} must be object"]
    for req in ("trigger_id", "pattern", "likely_effects"):
        if req not in t:
            errs.append(f"{prefix}.{req} missing")
    if errs:
        return errs
    if not re.match(r"^[a-z0-9_]+$", t["trigger_id"]):
        errs.append(f"{prefix}.trigger_id invalid: {t['trigger_id']}")
    eff = t.get("likely_effects", [])
    if not isinstance(eff, list) or not eff:
        errs.append(f"{prefix}.likely_effects must be non-empty array")
    else:
        for j, e in enumerate(eff):
            if not isinstance(e, dict):
                errs.append(f"{prefix}.likely_effects[{j}] must be object")
                continue
            if e.get("character_id") not in char_ids:
                errs.append(f"{prefix}.likely_effects[{j}].character_id unknown: {e.get('character_id')}")
            if not isinstance(e.get("change"), str) or len(e["change"]) > 60:
                errs.append(f"{prefix}.likely_effects[{j}].change invalid")
    return errs


# ── anti-leak check ──

# Words that very strongly suggest the distiller leaked source-specific content.
# Not exhaustive; meant as a smoke check. Curate to your use-case.
_LEAK_HINTS_DEFAULT = [
    "原文", "原著", "小说里", "作品中", "电影里",
]


def _leak_check(pkg: dict, extra_hints: list[str] | None = None) -> list[str]:
    hints = list(_LEAK_HINTS_DEFAULT) + list(extra_hints or [])
    errs: list[str] = []
    blob = json.dumps(pkg, ensure_ascii=False)
    for h in hints:
        if h in blob:
            errs.append(f"leak suspicion: phrase {h!r} appears in output")
    return errs


# ── main ──

def distill(source_text: str, user_hint: str = "",
            *, model: str = "deepseek-chat", extra_leak_hints: list[str] | None = None) -> dict:
    """Return validated distilled package dict, or raise DistillerError."""
    if len(source_text.strip()) < 200:
        raise DistillerError("input text too short (<200 chars)")

    system, user_tmpl = _load_prompt()
    user = _render(user_tmpl, {
        "SOURCE_TEXT": source_text,
        "USER_HINT": user_hint or "（无）",
    })

    try:
        pkg = chat_json(system, user, model=model, temperature=0.2, max_tokens=3000, timeout=120)
    except LLMError as e:
        raise DistillerError(f"llm failed: {e}") from e

    errs = _validate_package(pkg)
    if errs:
        raise DistillerError(f"schema validation failed: {errs[:10]}")

    leaks = _leak_check(pkg, extra_hints=extra_leak_hints)
    if leaks:
        raise DistillerError(f"anti-leak check failed: {leaks}")

    return pkg


def main():
    ap = argparse.ArgumentParser()
    src_grp = ap.add_mutually_exclusive_group(required=True)
    src_grp.add_argument("--in", dest="in_path", help="input text file")
    src_grp.add_argument("--text", help="inline text")
    ap.add_argument("--hint", default="", help="optional user hint")
    ap.add_argument("--out", default="", help="output JSON path (default: <stem>.distilled.json)")
    ap.add_argument("--leak-hint", action="append", default=[],
                    help="extra words that should not appear in output (e.g., source character names)")
    args = ap.parse_args()

    if args.in_path:
        src = Path(args.in_path).read_text("utf-8")
        if not args.out:
            args.out = str(Path(args.in_path).with_suffix(".distilled.json"))
    else:
        src = args.text
        if not args.out:
            args.out = "./distilled.json"

    try:
        pkg = distill(src, args.hint, extra_leak_hints=args.leak_hint)
    except DistillerError as e:
        print(f"DISTILL FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    Path(args.out).write_text(json.dumps(pkg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"  characters:    {len(pkg.get('characters', []))}")
    print(f"  relationships: {len(pkg.get('relationships', []))}")
    print(f"  secrets:       {len(pkg.get('secrets', []))}")
    print(f"  triggers:      {len(pkg.get('triggers', []))}")


if __name__ == "__main__":
    main()
