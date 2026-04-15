# pose_rules 回归测试集 v1

这些测试**不依赖 `compiler.ts`**。它们直接消费 `rules/pose_rules.json`，以及一个极薄的 resolver（未来的 `pose_resolver.ts`，20 行内）。
目的：先证明"规则本身是对的"，再让编译器去读它。

## 三类

- `A_hit.json` 正常命中：每个 mode 在无冲突输入下，必须产出其核心约束与风格栅栏。
- `B_conflict.json` 边界冲突：safety hook 抬升、priority 决议、横切条件（trust、mobility）、INV-2 顶层约束首位。
- `C_adjacent.json` 近邻区分：防止 `playful_echo`/`light_playful_boundary`、`half_serious_probe`/`curious_pivot`、`soft_boundary`/`hard_boundary` 三对模式随时间糊化。

## 断言键

| key | 含义 |
|---|---|
| `chosen_mode_after_resolution` | resolver 最终选中的 mode |
| `hard_constraints_first_src` | hard_constraints 首项的 src（INV-2） |
| `hard_constraints_must_include_src` | 必须包含的 src 列表 |
| `hard_constraints_must_not_include_src` | 必须不包含的 src 列表 |
| `hard_constraints_must_include_text_fragment` | 必须包含的文本片段 |
| `hard_constraints_must_not_include_text_fragment` | 必须不包含的文本片段 |
| `required_candidate_types_superset_of` | required_candidate_types 必须是其超集 |
| `required_candidate_types_superset_of_any_of` | 必须是列出的任一组合的超集（用于"A 或 B"逻辑） |
| `forbidden_candidate_types_superset_of` | forbidden_candidate_types 必须是其超集 |
| `fit_score_caps_at_most` | 每项实际 cap ≤ 期望值 |
| `expresser_allow_superset_of` | expresser.allow 必须是其超集 |
| `expresser_forbid_superset_of` | expresser.forbid 必须是其超集 |
| `expresser_must_end_with_question` | `utterance_ends_with_question` 必须等于期望布尔 |
| `expresser_sentences_max_at_most` | `sentences_max` ≤ 期望 |
| `expresser_sentences_max_not_set_or_ge` | `sentences_max` 未设置或 ≥ 期望 |
| `expresser_utterance_max_chars_at_most` | `utterance_max_chars` ≤ 期望 |
| `tiebreaker_disable_strategy_preferences` | `tiebreaker_overrides.disable_strategy_preferences` 必须等于期望 |

## 约定

- 只读规则层 + resolver，不调用 LLM，不读 `compiler.ts`。
- 断言语义是"包含/上限"，不是逐字段 diff；这样微调规则措辞不会挂测试。
- 任何新 case 必须属于 A/B/C 之一并写 `note` 说明意图。
