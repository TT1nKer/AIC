/**
 * pose_resolver — 姿态冲突裁判器
 *
 * 职责：给定 SpeakerReader 推荐的 mode + 当前 context，
 * 经过 safety_hooks 抬升后，输出最终生效的 mode。
 *
 * 不含 LLM 调用。纯函数、确定性。
 */

// ── types ──

export interface TriggerCmp {
  path: string;
  value: string | number | boolean | null;
}

export type TriggerExpr =
  | { eq: TriggerCmp }
  | { ne: TriggerCmp }
  | { gte: TriggerCmp }
  | { lte: TriggerCmp }
  | { gt: TriggerCmp }
  | { lt: TriggerCmp }
  | { any_of: TriggerExpr[] }
  | { all_of: TriggerExpr[] }
  | { not: TriggerExpr };

export interface SafetyHook {
  name: string;
  trigger: TriggerExpr;
  forced_mode: string;
  src: string;
}

export interface ModeEntry {
  priority: number;
  [k: string]: unknown;
}

export interface PoseRules {
  modes: Record<string, ModeEntry>;
  global: {
    safety_hooks: SafetyHook[];
    [k: string]: unknown;
  };
  [k: string]: unknown;
}

export interface ResolverContext {
  [key: string]: unknown;
}

export interface ResolveResult {
  mode: string;
  escalated_by: string | null;
}

// ── trigger evaluator ──

function getPath(ctx: ResolverContext, path: string): unknown {
  let cur: unknown = ctx;
  for (const seg of path.split(".")) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[seg];
  }
  return cur;
}

function cmp(
  op: "eq" | "ne" | "gte" | "lte" | "gt" | "lt",
  actual: unknown,
  expected: string | number | boolean | null,
): boolean {
  if (actual === undefined) return false;
  switch (op) {
    case "eq":  return actual === expected;
    case "ne":  return actual !== expected;
    case "gte": return typeof actual === "number" && typeof expected === "number" && actual >= expected;
    case "lte": return typeof actual === "number" && typeof expected === "number" && actual <= expected;
    case "gt":  return typeof actual === "number" && typeof expected === "number" && actual > expected;
    case "lt":  return typeof actual === "number" && typeof expected === "number" && actual < expected;
  }
}

function evalTrigger(expr: TriggerExpr, ctx: ResolverContext): boolean {
  if ("eq"  in expr) return cmp("eq",  getPath(ctx, expr.eq.path),  expr.eq.value);
  if ("ne"  in expr) return cmp("ne",  getPath(ctx, expr.ne.path),  expr.ne.value);
  if ("gte" in expr) return cmp("gte", getPath(ctx, expr.gte.path), expr.gte.value);
  if ("lte" in expr) return cmp("lte", getPath(ctx, expr.lte.path), expr.lte.value);
  if ("gt"  in expr) return cmp("gt",  getPath(ctx, expr.gt.path),  expr.gt.value);
  if ("lt"  in expr) return cmp("lt",  getPath(ctx, expr.lt.path),  expr.lt.value);
  if ("any_of" in expr) return expr.any_of.some((e) => evalTrigger(e, ctx));
  if ("all_of" in expr) return expr.all_of.every((e) => evalTrigger(e, ctx));
  if ("not" in expr) return !evalTrigger(expr.not, ctx);
  return false;
}

// ── resolver ──

export function resolve(
  rules: PoseRules,
  recommended: string,
  ctx: ResolverContext,
): ResolveResult {
  if (!(recommended in rules.modes)) {
    throw { code: "UNKNOWN_MODE", mode: recommended };
  }

  let finalMode = recommended;
  let escalatedBy: string | null = null;

  for (const hook of rules.global.safety_hooks) {
    if (!(hook.forced_mode in rules.modes)) {
      throw { code: "UNKNOWN_MODE", mode: hook.forced_mode };
    }
    if (evalTrigger(hook.trigger, ctx)) {
      const forced = rules.modes[hook.forced_mode];
      const current = rules.modes[finalMode];
      if (forced.priority > current.priority) {
        finalMode = hook.forced_mode;
        escalatedBy = hook.name;
      }
    }
  }

  return { mode: finalMode, escalated_by: escalatedBy };
}
