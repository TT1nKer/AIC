/**
 * redline_checker — verbal_redlines 执行器
 *
 * 检查一段文字（utterance / thought / lesson_text）是否违反 S/L/V 红线。
 * 纯函数、确定性、不含 LLM 调用。
 */

// ── types ──

export interface RegexBlock {
  name: string;
  pattern: string;
  applies_to: Array<"utterance" | "thought" | "lesson_text">;
  note?: string;
}

export interface VerbalRedlines {
  global_blacklist_terms: string[];
  global_regex_blocks: RegexBlock[];
  [k: string]: unknown;
}

export type Surface = "utterance" | "thought" | "lesson_text";

export interface CheckResult {
  verdict: "pass" | "block";
  hit_rule: string | null;
}

// ── checker ──

export function check(
  redlines: VerbalRedlines,
  surface: Surface,
  text: string,
): CheckResult {
  if (!text || text.trim().length === 0) {
    return { verdict: "pass", hit_rule: null };
  }

  const lower = text.toLowerCase();

  for (const term of redlines.global_blacklist_terms) {
    if (lower.includes(term.toLowerCase())) {
      return { verdict: "block", hit_rule: `blacklist:${term}` };
    }
  }

  for (const block of redlines.global_regex_blocks) {
    if (!block.applies_to.includes(surface)) continue;
    const re = new RegExp(block.pattern, "i");
    if (re.test(text)) {
      return { verdict: "block", hit_rule: `regex:${block.name}` };
    }
  }

  return { verdict: "pass", hit_rule: null };
}
