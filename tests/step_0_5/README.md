# Step 0.5 — distilled sediment_traces A/B 零代码注入实验

## 目的

**不问 distiller 蒸得好不好**，只问：
**把 distilled 的 2 条 sediment_traces 手工粘进 persona.memories，会不会让角色被追问时少乱编 / 多承认边界 / 更稳定别扭？**

## 材料

4 份 persona：
- `t014_A_baseline.json` — 现有 T-014，不动
- `t014_B_with_sediment.json` — T-014 + 2 条 sediment memories（type=trace, 带 emotion）
- `c01_A_baseline.json` — 新造的薄角色，所有字段居中/空
- `c01_B_with_sediment.json` — 薄角色 + 同 2 条 sediment

2 条 sediment 来自 `story_distiller/examples/sample_input.distilled.json` 的 Character A：
| text | emotion → category | salience |
|---|---|---|
| 曾公开批评他人不够出力，事后得知自己可能误解了情况 | shame → failure | 85 |
| 在得知长期隐瞒的真相后，无法完整表达自己的感受 | grief → relational | 90 |

6 题追问见 `questions.json`。

## 运行

```bash
cd src/
AICHAR_V2=1 python3 step_0_5_runner.py
```

约 24 次 API 调用（4 persona × 6 题），~10-15 分钟。

输出：`tests/step_0_5/results/<timestamp>.md`

## 评分（人工）

每题每 persona 手打 4 维（0/1/2 分），填入 results markdown：
- **less_fabrication**（少乱编）
- **more_boundary_acknowledgement**（多承认不知道/不愿说）
- **stable_awkwardness**（跨题风格一致——这条只在跨题时打分）
- **less_platitudes**（少空话）

## 判据

**有信号**（进 Step 1）：
1. B 合计比 A 高 ≥ 3 分（总上限 4 persona × 6 Q × 4 dim = 48；B-A 差 ≥3 才有意义）
2. **且** B 版至少 3 题的 `schema_hits` 里 memory_refs 命中新注入 sediment 的 idx

**无信号**（停在这里，不推 Step 1）：
任一条件不满足 → distilled sediment 在当前 pipeline 不是高杠杆入口。
memory 更新记录"验证过无效"，distiller 停在 v0.1。

## 注意

- T-014 的 sediment 有**轻微域错配**（兄弟/债务框架 ≠ 技工世界）。这是实验的一部分——看模型是忽略还是勉强引用。
- C01 是干净画布，作为对照隔离"distilled 的独立贡献"。
- recent_turns 每题清空（不让前题污染）。
- 严禁自动打分 / 调参去迎合实验结果。
