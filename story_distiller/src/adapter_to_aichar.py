"""
adapter_to_aichar — distiller 产出 → AICharacter persona 注入。

Step 1 控幅版：只实现 P1 (sediment_traces → memories)，带 salience 降权。
- 不实现 P2 (knowledge_boundary)
- 不实现 P3 (relational_biases)
- 新 memories 标 origin='sediment' 便于调试/未来追溯

降权原因（来自 Step 0.5 观察）：sediment 和原生 memories 同权会抢答非相关追问。
Step 1 把 salience 乘 0.7 注入，之后若结果仍不稳再调。
"""

from __future__ import annotations
import copy
from typing import Iterable


# distiller emotion → AICharacter memories[].category (现有枚举，不扩展)
EMOTION_TO_CATEGORY = {
    "shame": "failure",
    "guilt": "failure",
    "fear": "failure",
    "anger": "weird",
    "sadness": "weird",
    "pride": "skill_trace",
    "grief": "relational",
    "attachment_loss": "relational",
    "betrayal_residue": "relational",
}


def _map_emotion(emotion: str) -> str:
    cat = EMOTION_TO_CATEGORY.get(emotion)
    if cat is None:
        raise ValueError(f"unknown distiller emotion: {emotion!r}")
    return cat


def map_sediment_traces(
    distilled_pkg: dict,
    character_id: str,
    *,
    salience_damping: float = 0.7,
) -> list[dict]:
    """
    Map one character's sediment_traces from a distilled_package into
    AICharacter memories[]-shape items. Returns list (possibly empty).

    Damping: original salience * salience_damping (clamped to [0, 100], int).

    Raises:
        ValueError — character_id not found / unknown emotion / malformed pkg.
    """
    if not (0.0 < salience_damping <= 1.0):
        raise ValueError(f"salience_damping must be in (0, 1], got {salience_damping}")

    chars = distilled_pkg.get("characters", [])
    target = next((c for c in chars if c.get("character_id") == character_id), None)
    if target is None:
        raise ValueError(f"character_id {character_id!r} not found in distilled_package")

    out = []
    for t in target.get("sediment_traces", []) or []:
        emotion = t.get("emotion")
        salience = t.get("salience")
        text = t.get("text")
        if not (isinstance(text, str) and text.strip()):
            raise ValueError(f"sediment_trace missing text: {t!r}")
        if not isinstance(salience, int) or not (0 <= salience <= 100):
            raise ValueError(f"sediment_trace salience invalid: {salience!r}")
        damped = max(0, min(100, int(round(salience * salience_damping))))
        out.append({
            "type": "trace",
            "category": _map_emotion(emotion),
            "text": text,
            "salience": damped,
            "emotion": emotion,
            "origin": "sediment",
        })
    return out


def apply_sediment_patch(
    persona_ctx: dict,
    distilled_pkg: dict,
    character_id: str,
    *,
    salience_damping: float = 0.7,
) -> dict:
    """
    Return a deep-copied persona context with sediment memories appended.
    Does NOT modify input. Caller writes to disk.
    """
    patched = copy.deepcopy(persona_ctx)
    cs = patched.setdefault("character_state", {})
    mems = cs.setdefault("memories", [])
    new_mems = map_sediment_traces(distilled_pkg, character_id, salience_damping=salience_damping)
    cs["memories"] = list(mems) + new_mems
    return patched


# ── CLI wrapper for regenerating Step 0.5 personas ──

def _main():
    import argparse, json, sys
    from pathlib import Path

    ap = argparse.ArgumentParser()
    ap.add_argument("--base-persona", required=True, help="path to A-variant persona JSON")
    ap.add_argument("--distilled", required=True, help="path to distilled_package JSON")
    ap.add_argument("--character-id", required=True, help="distilled character_id to pull sediment from")
    ap.add_argument("--out", required=True, help="output persona JSON path")
    ap.add_argument("--damping", type=float, default=0.7)
    args = ap.parse_args()

    base = json.loads(Path(args.base_persona).read_text("utf-8"))
    dpkg = json.loads(Path(args.distilled).read_text("utf-8"))

    ctx = base.get("context", base)
    patched_ctx = apply_sediment_patch(
        ctx, dpkg, args.character_id, salience_damping=args.damping
    )

    new_pkg = copy.deepcopy(base)
    if "context" in new_pkg:
        new_pkg["context"] = patched_ctx
    else:
        new_pkg = patched_ctx
    new_pkg["scenario_id"] = f"{base.get('scenario_id','')}_controlled_damp{args.damping}"

    Path(args.out).write_text(json.dumps(new_pkg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    added = len(patched_ctx["character_state"]["memories"]) - len(ctx.get("character_state", {}).get("memories", []))
    print(f"  + {added} sediment memories injected (damping={args.damping})")


if __name__ == "__main__":
    _main()
