"""
from_doomsday — 把 Doomsday 的 NPC 状态转成 AICharacter 的 persona JSON。

策略（零 API 成本）：
  - 导入 Doomsday 的 AGENT_CARD_BASE + SCENARIO + tension_engine
  - 本地确定性回放 10 天（不调 LLM）
  - 把最终 tensions + 事件流 + memory_traces + anchors 映射成
    AICharacter 的 CharacterState（含 memories / goals / relationships / beliefs / skills）

用法：
  python3 from_doomsday.py                     # 转 T-014，写到 fixtures 旁边
  python3 from_doomsday.py --out <path>.json   # 指定输出
"""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

DOOMSDAY = Path("/home/hostsjim/Projects/Doomsday")
sys.path.insert(0, str(DOOMSDAY))

from ten_tick import AGENT_CARD_BASE, SCENARIO, LOCATION  # noqa: E402
from tension_engine import compute_deltas, apply_deltas   # noqa: E402


# ── trait mapping (Doomsday skeleton 0..1 → AICharacter traits 0..100) ──

def _map_traits(skel: dict) -> dict:
    return {
        "control_need":       int(skel.get("order_bias", 0.5) * 100),
        "self_preservation":  int(skel.get("self_protection_bias", 0.5) * 100),
        "empathy":            int(skel.get("trust_bias", 0.5) * 100),
        "risk_tolerance":     int(skel.get("persistence_bias", 0.5) * 100),
        "impulsivity":        100 - int(skel.get("persistence_bias", 0.5) * 100),
        "shame_sensitivity":  50,
    }


# ── tension → goals ──

GOAL_FROM_TENSION = {
    "duty_pull":       "履行手上的职责",
    "attachment_pull": "照顾/不失去在意的人",
    "survival_pull":   "活下去",
    "comfort_pull":    "维持日常与稳定",
    "meaning_pull":    "找出继续做事的理由",
}


def _map_goals(tensions: dict) -> list[dict]:
    goals = []
    for key, text in GOAL_FROM_TENSION.items():
        v = tensions.get(key, 0.5)
        goals.append({"text": text, "priority": int(v * 100)})
    goals.sort(key=lambda g: -g["priority"])
    return goals[:6]


# ── final tensions → emotion ──

def _derive_emotion(final_tensions: dict, events_log: list[list[dict]]) -> dict:
    """简单规则：duty 高+ B-3 出过故障 → 一定 fear/anger；attachment 高 + A07 缺席多次 → sadness。"""
    fear = 20
    anger = 10
    sadness = 10
    hope = 40
    numbness = 25

    failures = sum(1 for day in events_log for e in day if e.get("type") == "facility_failure")
    absences = sum(1 for day in events_log for e in day if e.get("type") == "contact_absent")
    deaths   = sum(1 for day in events_log for e in day if e.get("type") == "death_reported")

    fear     = min(100, 20 + failures * 8 + absences * 4 + deaths * 10)
    sadness  = min(100, 10 + absences * 5 + deaths * 15)
    numbness = min(100, 25 + absences * 3)
    hope     = max(0, 40 - failures * 5 - deaths * 8)

    if final_tensions.get("duty_pull", 0) > 0.75 and failures >= 2:
        anger = min(100, anger + 20)

    return {"fear": fear, "anger": anger, "sadness": sadness, "hope": hope, "numbness": numbness}


# ── anchors + events → relationships ──

def _map_relationships(anchors: dict, events_log: list[list[dict]]) -> list[dict]:
    rels = []
    attach = anchors.get("attachment", [])
    for a in attach:
        target_id = a.split(":", 1)[-1] if ":" in a else a
        absences = sum(1 for day in events_log for e in day
                       if e.get("type") == "contact_absent" and _same_target(e.get("target"), target_id))
        presences = sum(1 for day in events_log for e in day
                        if e.get("type") == "contact_present" and _same_target(e.get("target"), target_id))
        deaths = sum(1 for day in events_log for e in day
                     if e.get("type") == "death_reported" and _same_target(e.get("target"), target_id))
        # heuristic: more presence → higher trust; more absence/death → more worry (fear)
        trust = max(-100, min(100, 30 + presences * 5 - absences * 3 - deaths * 50))
        affection = max(-100, min(100, 40 + presences * 3 - deaths * 60))
        fear_of = min(100, absences * 6 + deaths * 40)
        rels.append({
            "target_id": target_id,
            "label": "同事/挂记的人",
            "trust": trust,
            "affection": affection,
            "fear": fear_of,
        })
    return rels


def _same_target(a, b) -> bool:
    def canon(x):
        if not isinstance(x, str):
            return x
        return x.split(":", 1)[-1] if ":" in x else x
    return canon(a) == canon(b)


# ── memory_traces + events → beliefs + memories ──

def _map_beliefs(traces: list[str]) -> list[dict]:
    return [
        {"text": t, "confidence": 75, "is_true_unknown_to_character": False}
        for t in traces
    ]


def _map_memories(events_log: list[list[dict]]) -> list[dict]:
    """把逐日事件压成 AICharacter 的 memories[]（type/text/salience）。"""
    mems = []
    for day_idx, day in enumerate(events_log, start=1):
        for e in day:
            t = e.get("type")
            tg = e.get("target", "")
            if t == "facility_failure":
                mems.append({"type": "event", "text": f"第 {day_idx} 天，{tg} 出了故障", "salience": 75})
            elif t == "contact_absent":
                mems.append({"type": "relation", "text": f"第 {day_idx} 天，{tg} 没来", "salience": 55})
            elif t == "contact_present" and e.get("demeanor") == "off":
                mems.append({"type": "relation", "text": f"第 {day_idx} 天，{tg} 回来了，但状态不太对", "salience": 65})
            elif t == "death_reported":
                mems.append({"type": "emotion", "text": f"第 {day_idx} 天听说 {tg} 死了", "salience": 90})
            elif t == "resource_shortage":
                mems.append({"type": "event", "text": f"第 {day_idx} 天，{tg} 开始告急", "salience": 70})
    # 合并相邻同类为一条更概括的记忆，减少碎片化
    merged = _coalesce(mems)
    # 按 salience 降序保留 12 条
    merged.sort(key=lambda m: -m["salience"])
    return merged[:12]


def _coalesce(mems: list[dict]) -> list[dict]:
    """相邻天数+同类合并。"""
    if not mems:
        return mems
    out = [dict(mems[0])]
    for m in mems[1:]:
        last = out[-1]
        # crude: same type + similar target mention → extend
        if m["type"] == last["type"] and _last_token(m["text"]) == _last_token(last["text"]):
            last["text"] = f"{last['text']}；{m['text']}"
            last["salience"] = min(100, last["salience"] + 5)
        else:
            out.append(dict(m))
    return out


def _last_token(s: str) -> str:
    # get the target-like token (after first comma)
    if "，" in s:
        return s.split("，", 1)[-1][:4]
    return s[:4]


# ── role → skills + background ──

ROLE_TO_SKILLS = {
    "technician": [
        {"name": "设备维修",   "level": 75},
        {"name": "故障诊断",   "level": 65},
        {"name": "临时拼件",   "level": 55},
    ],
    "nurse":     [{"name": "基础护理", "level": 70}, {"name": "简单包扎", "level": 65}],
    "courier":   [{"name": "路线记忆", "level": 65}, {"name": "体力", "level": 60}],
    "shopkeeper":[{"name": "记账", "level": 60}, {"name": "察言观色", "level": 55}],
    "drifter":   [{"name": "躲避", "level": 60}, {"name": "拾荒", "level": 55}],
    "teacher":   [{"name": "组织孩子", "level": 60}, {"name": "讲解", "level": 55}],
    "clerk":     [{"name": "表格", "level": 50}],
    "scavenger": [{"name": "翻找", "level": 65}, {"name": "识别物资", "level": 60}],
}


ROLE_TO_BACKGROUND = {
    "technician": "技术员，平时修设备",
    "nurse":      "护士，以前在诊所",
    "courier":    "跑腿送货的",
    "shopkeeper": "以前看店",
    "drifter":    "到处漂",
    "teacher":    "教过书",
    "clerk":      "坐办公室的",
    "scavenger":  "翻找废墟的",
}


# ── main conversion ──

def convert(agent_card: dict, scenario: list[list[dict]]) -> dict:
    """Replay tensions deterministically and build CharacterState."""
    tensions = dict(agent_card["tensions"])
    anchors = agent_card["anchors"]
    event_history = []

    for events in scenario:
        deltas = compute_deltas(events, anchors, event_history)
        tensions = apply_deltas(tensions, deltas)
        event_history.append(events)

    traits = _map_traits(agent_card["skeleton"])
    goals = _map_goals(tensions)
    emotion = _derive_emotion(tensions, event_history)
    relationships = _map_relationships(anchors, event_history)
    beliefs = _map_beliefs(agent_card.get("memory_traces", []))
    memories = _map_memories(event_history)
    role = agent_card.get("role", "drifter")
    skills = list(ROLE_TO_SKILLS.get(role, [{"name": "杂活", "level": 40}]))
    background = ROLE_TO_BACKGROUND.get(role, "做过点杂事")

    character_state = {
        "schema_version": "1.0.0",
        "identity": {
            "id": agent_card["id"],
            "age": 34,
            "background": background,
        },
        "traits": traits,
        "physiology": {"hunger": 45, "fatigue": 55, "pain": 0, "injury": 0},
        "emotion": emotion,
        "goals": goals,
        "beliefs": beliefs,
        "memories": memories,
        "relationships": relationships,
        "skills": skills,
        "constraints": {"mobility": 75, "resources": ["工具包", "旧扳手"]},
    }

    speaker_model = {
        "baseline_style": {
            "humor": 35, "irony": 30, "seriousness": 70,
            "provocation": 25, "emotional_openness": 30, "abstraction": 45,
        },
        "trust_and_familiarity": {"trust": 50, "familiarity": 40, "caution": 45},
        "strategy_preferences": {},
    }

    return {
        "scenario_id": f"from_doomsday/{agent_card['id']}",
        "context": {
            "now_iso": "2026-04-18T22:00:00+08:00",
            "character_state": character_state,
            "speaker_model": speaker_model,
            "situation": {"user_message": ""},
            "recent_turns": [],
            "lessons": [],
        },
        "_source": {
            "from": "Doomsday",
            "agent_id": agent_card["id"],
            "role": role,
            "scenario_days": len(scenario),
            "final_tensions": tensions,
            "location": LOCATION,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tests/e2e/persona_doomsday_t014.json")
    args = ap.parse_args()

    result = convert(AGENT_CARD_BASE, SCENARIO)
    out_path = Path(__file__).resolve().parent.parent / args.out
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"  agent: {result['_source']['agent_id']}  role: {result['_source']['role']}")
    print(f"  memories: {len(result['context']['character_state']['memories'])}")
    print(f"  goals:    {[g['text'] for g in result['context']['character_state']['goals']]}")
    print(f"  tensions: {result['_source']['final_tensions']}")


if __name__ == "__main__":
    main()
