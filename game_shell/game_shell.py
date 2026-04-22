"""
game_shell — Phase 3 最小可玩验证壳。

不是完整游戏，是验证三件事的最小 REPL 容器：
  1. 玩家能和同一个角色连续聊几轮（recent_turns 不重置）
  2. 世界事件会改变角色回答（/event 注入 → 后续对话体现）
  3. 玩家会开始在意某个人或某个地方（emergent；取决于剧本+NPC 记忆）

架构分层（严格遵守 PROJECT.md 三层边界）：
  - 世界壳：这里（game_shell） — 发生了什么、什么到场、事件注入
  - 心智内核：src/* 原封不动复用；import run_turn，不碰推理层
  - 应用层：这个 REPL 本身

用法：
  python3 game_shell.py                              # 默认 shelter_morning
  python3 game_shell.py --scenario shelter_morning   # 指定剧本
  python3 game_shell.py --debug                      # 打开 src.cli_demo 的 trace

REPL commands:
  /look                 打印当前 scene
  /events               列出所有可用事件
  /event <event_id>     注入一个事件 (修改 character_state + scene)
  /state                简要角色状态
  /history              打印 recent_turns
  :reset                清空 recent_turns（通常不需要）
  :q / :quit / :exit    退出
"""

from __future__ import annotations
import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

os.environ.setdefault("AICHAR_V2", "1")
os.environ.setdefault("AICHAR_P2", "1")
os.environ.setdefault("AICHAR_P3", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cli_demo import run_turn, _short, _debug  # noqa: E402

SHELL_DIR = Path(__file__).resolve().parent
SCENARIOS_DIR = SHELL_DIR / "scenarios"
EVENTS_PATH = SHELL_DIR / "events.json"

RECENT_TURNS_CAP = 20  # higher than cli_demo's 6 — game shell values continuity


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def load_json(p: Path) -> dict:
    return json.loads(p.read_text("utf-8"))


def load_scenario(name: str) -> dict:
    """
    Load a scenario JSON; resolve its persona_path (relative to repo root) and
    merge persona.context with scenario.scene. Returns a ready-to-use ctx dict.
    """
    sc_path = SCENARIOS_DIR / f"{name}.json"
    if not sc_path.exists():
        raise FileNotFoundError(f"scenario not found: {sc_path}")
    sc = load_json(sc_path)
    persona_path = ROOT / sc["persona_path"]
    if not persona_path.exists():
        raise FileNotFoundError(f"persona_path missing: {persona_path}")
    persona = load_json(persona_path)
    ctx = copy.deepcopy(persona.get("context", persona))
    ctx["recent_turns"] = []
    ctx["situation"] = {"user_message": "", "scene": sc.get("scene", {})}
    return {
        "scenario_id": sc["scenario_id"],
        "opening": sc.get("opening_narration", ""),
        "scene": sc.get("scene", {}),
        "ctx": ctx,
    }


def apply_event(ctx: dict, scene: dict, event: dict, applied_ids: set):
    """
    Mutate ctx + scene in place according to event.effects.

    Effects supported:
      - memory_append: list of memory items → appended to character_state.memories
      - pressure_delta: dict of field → int → added to internal_pressures (clamped [0,100])
      - scene_update: dict merged into scene (list replace, scalar overwrite)
    """
    effects = event.get("effects", {})
    cs = ctx["character_state"]

    mems = cs.setdefault("memories", [])
    for m in effects.get("memory_append", []) or []:
        mems.append(copy.deepcopy(m))

    ip = cs.setdefault("internal_pressures", {})
    for k, delta in (effects.get("pressure_delta") or {}).items():
        cur = int(ip.get(k, 0))
        ip[k] = max(0, min(100, cur + int(delta)))

    for k, v in (effects.get("scene_update") or {}).items():
        scene[k] = v

    ctx["situation"]["scene"] = scene
    applied_ids.add(event["event_id"])


def fmt_scene(scene: dict) -> str:
    lines = []
    if scene.get("location"):
        lines.append(f"[地点] {scene['location']}")
    if scene.get("time_of_day"):
        lines.append(f"[时间] {scene['time_of_day']}")
    if scene.get("npcs_present"):
        lines.append(f"[在场] {', '.join(scene['npcs_present'])}")
    if scene.get("npcs_absent"):
        lines.append(f"[缺席] {', '.join(scene['npcs_absent'])}")
    if scene.get("resources_state"):
        lines.append(f"[资源] {scene['resources_state']}")
    return "\n".join(lines) if lines else "(场景为空)"


def fmt_events(events: list, applied_ids: set) -> str:
    lines = []
    for e in events:
        marker = "✓" if e["event_id"] in applied_ids else " "
        req = e.get("requires")
        blocked = " [需先触发 " + req + "]" if req and req not in applied_ids else ""
        lines.append(f"  {marker} {e['event_id']:26s} — {e['title']}{blocked}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="shelter_morning")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    rules = load_json(ROOT / "rules" / "pose_rules.json")
    redlines = load_json(ROOT / "rules" / "verbal_redlines.json")
    events_catalog = load_json(EVENTS_PATH)["events"]
    events_by_id = {e["event_id"]: e for e in events_catalog}
    applied_ids: set = set()

    sc = load_scenario(args.scenario)
    ctx = sc["ctx"]
    scene = sc["scene"]
    ident = ctx["character_state"]["identity"]
    char_name = ident.get("name") or ident["id"]

    print(f"[scenario] {sc['scenario_id']}  character: {char_name}")
    print(f"AICHAR_V2={os.environ['AICHAR_V2']} P2={os.environ['AICHAR_P2']} P3={os.environ['AICHAR_P3']}")
    print("commands: /look  /events  /event <id>  /state  /history  :reset  :q\n")
    if sc["opening"]:
        print(f"(旁白) {sc['opening']}\n")

    debug = args.debug

    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue

        if line in (":q", ":quit", ":exit"):
            break
        if line == ":reset":
            ctx["recent_turns"] = []
            print("[session reset]\n")
            continue
        if line == "/look":
            print(fmt_scene(scene) + "\n")
            continue
        if line == "/events":
            print("events:")
            print(fmt_events(events_catalog, applied_ids) + "\n")
            continue
        if line.startswith("/event "):
            eid = line[len("/event "):].strip()
            ev = events_by_id.get(eid)
            if not ev:
                print(f"[unknown event] {eid}. /events 看列表\n")
                continue
            req = ev.get("requires")
            if req and req not in applied_ids:
                print(f"[blocked] 需要先触发 {req}\n")
                continue
            if eid in applied_ids:
                print(f"[already applied] {eid}\n")
                continue
            apply_event(ctx, scene, ev, applied_ids)
            print(f"(世界) {ev['title']}")
            print(f"       {ev['description']}\n")
            continue
        if line == "/state":
            ip = ctx["character_state"].get("internal_pressures", {})
            mems = ctx["character_state"].get("memories", [])
            print(f"[state] character={ident.get('id')} bg={ident.get('background')}")
            print(f"[state] memories={len(mems)}  pressures={ip}")
            print(f"[state] recent_turns={len(ctx.get('recent_turns', []))}\n")
            continue
        if line == "/history":
            for t in ctx.get("recent_turns", []):
                role = "you" if t["role"] == "user" else char_name
                print(f"  {role}: {t['text']}")
            print()
            continue

        summary, exp_out, errs = run_turn(ctx, rules, redlines, line)
        if errs:
            print("[error]")
            for e in errs:
                print(f"  {e}")
            print()
            continue

        if debug:
            print(_debug(summary, exp_out, char_name))
        else:
            utt = exp_out.get("utterance", "") or "(沉默)"
            print(_short(summary, char_name, utt))
        print()

        now = _now_iso()
        ctx.setdefault("recent_turns", []).append(
            {"role": "user", "text": line, "timestamp": now}
        )
        if exp_out.get("utterance"):
            ctx["recent_turns"].append(
                {"role": "character", "text": exp_out["utterance"], "timestamp": now}
            )
        ctx["recent_turns"] = ctx["recent_turns"][-RECENT_TURNS_CAP:]


if __name__ == "__main__":
    main()
