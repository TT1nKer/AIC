/**
 * test_runner — pose_rules + verbal_redlines 回归测试 runner
 *
 * 直接消费 JSON 测试文件，不依赖 compiler.ts。
 * 用 ts-node / tsx 跑。
 */

import { readFileSync } from "fs";
import { resolve as poseResolve, type PoseRules, type ResolverContext } from "./pose_resolver.js";
import { check as redlineCheck, type VerbalRedlines, type Surface } from "./redline_checker.js";

const ROOT = new URL("../", import.meta.url).pathname;

function loadJson<T>(rel: string): T {
  return JSON.parse(readFileSync(ROOT + rel, "utf-8")) as T;
}

// ── pose rules tests ──

interface PoseCase {
  case_id: string;
  kind: string;
  input: {
    recommended_response_mode: string;
    current_read_digest?: Record<string, unknown>;
    speaker_model_digest?: Record<string, unknown>;
    character_state_digest?: Record<string, unknown>;
    situation_digest?: Record<string, unknown>;
    strategy_preferences?: Record<string, unknown>;
    [k: string]: unknown;
  };
  expect: Record<string, unknown>;
}

interface PoseFile { cases: PoseCase[] }

function buildCtx(input: PoseCase["input"]): ResolverContext {
  return {
    current_read: input.current_read_digest ?? {},
    speaker_model: input.speaker_model_digest ?? {},
    character_state: input.character_state_digest ?? {},
    situation: input.situation_digest ?? {},
  };
}

function assertPose(rules: PoseRules, c: PoseCase): string[] {
  const failures: string[] = [];
  const ctx = buildCtx(c.input);
  const result = poseResolve(rules, c.input.recommended_response_mode, ctx);

  const mode = rules.modes[result.mode];
  if (!mode) { failures.push(`resolved to unknown mode: ${result.mode}`); return failures; }

  const ex = c.expect;

  if (ex.chosen_mode_after_resolution !== undefined) {
    if (result.mode !== ex.chosen_mode_after_resolution) {
      failures.push(`mode: want ${ex.chosen_mode_after_resolution}, got ${result.mode}`);
    }
  }

  if (ex.hard_constraints_first_src !== undefined) {
    const first = rules.global.top_level_constraints?.[0];
    if (!first || (first as { src: string }).src !== ex.hard_constraints_first_src) {
      failures.push(`first constraint src: want ${ex.hard_constraints_first_src}`);
    }
  }

  if (Array.isArray(ex.hard_constraints_must_include_src)) {
    const allSrcs = [
      ...((rules.global as { top_level_constraints: Array<{ src: string }> }).top_level_constraints ?? []).map((c: { src: string }) => c.src),
      ...(mode.decider_constraints as Array<{ src: string }> ?? []).map((c: { src: string }) => c.src),
    ];
    for (const want of ex.hard_constraints_must_include_src as string[]) {
      if (!allSrcs.includes(want)) failures.push(`missing src: ${want}`);
    }
  }

  if (Array.isArray(ex.hard_constraints_must_not_include_src)) {
    const modeSrcs = (mode.decider_constraints as Array<{ src: string }> ?? []).map((c: { src: string }) => c.src);
    for (const bad of ex.hard_constraints_must_not_include_src as string[]) {
      if (modeSrcs.includes(bad)) failures.push(`unexpected src in mode constraints: ${bad}`);
    }
  }

  if (Array.isArray(ex.required_candidate_types_superset_of)) {
    const actual = mode.required_candidate_types as string[] ?? [];
    for (const want of ex.required_candidate_types_superset_of as string[]) {
      if (!actual.includes(want)) failures.push(`missing required_candidate_type: ${want}`);
    }
  }

  if (Array.isArray(ex.forbidden_candidate_types_superset_of)) {
    const actual = mode.forbidden_candidate_types as string[] ?? [];
    for (const want of ex.forbidden_candidate_types_superset_of as string[]) {
      if (!actual.includes(want)) failures.push(`missing forbidden_candidate_type: ${want}`);
    }
  }

  if (ex.fit_score_caps_at_most !== undefined) {
    const caps = mode.fit_score_caps as Record<string, number> ?? {};
    for (const [k, max] of Object.entries(ex.fit_score_caps_at_most as Record<string, number>)) {
      if (caps[k] !== undefined && caps[k] > max) {
        failures.push(`fit_score_caps.${k}: want ≤${max}, got ${caps[k]}`);
      }
    }
  }

  if (ex.tiebreaker_disable_strategy_preferences === true) {
    const tb = mode.tiebreaker_overrides as { disable_strategy_preferences?: boolean } | null;
    if (!tb?.disable_strategy_preferences) {
      failures.push(`tiebreaker_overrides.disable_strategy_preferences should be true`);
    }
  }

  const style = mode.expresser_style as Record<string, unknown> | undefined;
  if (style) {
    if (Array.isArray(ex.expresser_forbid_superset_of)) {
      const actual = style.forbid as string[] ?? [];
      for (const want of ex.expresser_forbid_superset_of as string[]) {
        if (!actual.includes(want)) failures.push(`expresser.forbid missing: ${want}`);
      }
    }
    if (ex.expresser_must_end_with_question !== undefined) {
      const actual = style.utterance_ends_with_question;
      if (actual !== ex.expresser_must_end_with_question) {
        failures.push(`utterance_ends_with_question: want ${ex.expresser_must_end_with_question}, got ${actual}`);
      }
    }
    if (ex.expresser_sentences_max_at_most !== undefined) {
      const actual = style.sentences_max as number | undefined;
      if (actual !== undefined && actual > (ex.expresser_sentences_max_at_most as number)) {
        failures.push(`sentences_max: want ≤${ex.expresser_sentences_max_at_most}, got ${actual}`);
      }
    }
    if (ex.expresser_sentences_max_not_set_or_ge !== undefined) {
      const actual = style.sentences_max as number | undefined;
      if (actual !== undefined && actual < (ex.expresser_sentences_max_not_set_or_ge as number)) {
        failures.push(`sentences_max: want ≥${ex.expresser_sentences_max_not_set_or_ge} or unset, got ${actual}`);
      }
    }
    if (ex.expresser_utterance_max_chars_at_most !== undefined) {
      const actual = style.utterance_max_chars as number | undefined;
      if (actual !== undefined && actual > (ex.expresser_utterance_max_chars_at_most as number)) {
        failures.push(`utterance_max_chars: want ≤${ex.expresser_utterance_max_chars_at_most}, got ${actual}`);
      }
    }
  }

  if (Array.isArray(ex.hard_constraints_must_include_text_fragment)) {
    const texts = (mode.decider_constraints as Array<{ text: string }> ?? []).map((c: { text: string }) => c.text);
    for (const frag of ex.hard_constraints_must_include_text_fragment as string[]) {
      if (!texts.some((t: string) => t.includes(frag))) failures.push(`no constraint text contains: "${frag}"`);
    }
  }
  if (Array.isArray(ex.hard_constraints_must_not_include_text_fragment)) {
    const texts = (mode.decider_constraints as Array<{ text: string }> ?? []).map((c: { text: string }) => c.text);
    for (const frag of ex.hard_constraints_must_not_include_text_fragment as string[]) {
      if (texts.some((t: string) => t.includes(frag))) failures.push(`constraint text should not contain: "${frag}"`);
    }
  }

  return failures;
}

// ── redline tests ──

interface RedlineCase {
  case_id: string;
  surface: Surface;
  text: string;
  note?: string;
  expect: { verdict: "pass" | "block"; hit_rule?: string };
}

interface RedlineFile { cases: RedlineCase[] }

function assertRedline(redlines: VerbalRedlines, c: RedlineCase): string[] {
  const failures: string[] = [];
  const result = redlineCheck(redlines, c.surface, c.text);

  if (result.verdict !== c.expect.verdict) {
    failures.push(`verdict: want ${c.expect.verdict}, got ${result.verdict} (hit: ${result.hit_rule})`);
  }

  return failures;
}

// ── main ──

const rules = loadJson<PoseRules>("rules/pose_rules.json");
const redlines = loadJson<VerbalRedlines>("rules/verbal_redlines.json");

let total = 0;
let passed = 0;
let failed = 0;
const failureLog: string[] = [];

function run<T>(label: string, file: string, cases: T[], fn: (c: T) => string[]) {
  console.log(`\n── ${label} (${file}) ──`);
  for (const c of cases) {
    total++;
    const id = (c as { case_id: string }).case_id;
    const errs = fn(c);
    if (errs.length === 0) {
      passed++;
      console.log(`  PASS  ${id}`);
    } else {
      failed++;
      console.log(`  FAIL  ${id}`);
      for (const e of errs) {
        console.log(`        ${e}`);
        failureLog.push(`${id}: ${e}`);
      }
    }
  }
}

for (const f of ["tests/pose_rules/A_hit.json", "tests/pose_rules/B_conflict.json", "tests/pose_rules/C_adjacent.json"]) {
  const data = loadJson<PoseFile>(f);
  run(data.cases[0]?.kind ?? f, f, data.cases, (c) => assertPose(rules, c));
}

{
  const data = loadJson<RedlineFile>("tests/verbal_redlines/redline_cases.json");
  run("verbal_redlines", "tests/verbal_redlines/redline_cases.json", data.cases, (c) => assertRedline(redlines, c));
}

console.log(`\n════════════════════════════`);
console.log(`Total: ${total}  Pass: ${passed}  Fail: ${failed}`);
if (failureLog.length > 0) {
  console.log(`\nFailures:`);
  for (const line of failureLog) console.log(`  ${line}`);
  process.exit(1);
}
