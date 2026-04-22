# AICharacter

**一个可验证、可扩展、可嵌入的角色连续心智内核。**

## 是什么

在**受约束的互动世界**中，让角色获得：

1. **连续自我感** — 同一角色跨多轮、多天、多次追问后仍像同一个人
2. **过去约束当前** — 回答受真实经历、记忆痕迹、关系张力影响，不是现场乱编
3. **知识与表达边界** — 区分我知道/我不知道/我只知道一部分/我不愿意说
4. **有限但稳定的主观性** — 同一事件不同角色有不同但可追溯的理解
5. **可嵌入性** — 首先在游戏壳中验证，未来可迁移到其它互动容器

**一句话**：不是做"万能 AI 人格"，而是做"在世界约束下仍能像同一个人的角色心智内核"。

## 不是什么

- 不是先做完整游戏
- 不是先做通用完美人格模拟器
- 不是先做 AI 伴侣产品
- 不是无限扩张的认知实验室

## 当前策略（Phase 1）

以游戏壳（Doomsday 风格末日世界）作为第一验证容器。利用规则明确、事件有限的世界降低复杂度，验证角色内核的真实性与一致性。

- **世界壳不是终点**。它只是降低复杂度的工具。
- **游戏完整性是 Phase 3**，不是现在。

## 三层责任边界

每个新模块上线前必须明确它属于哪一层。**层一混，后面一定爆。**

| 层 | 负责 | 对应代码 |
|---|---|---|
| **世界壳** | 发生了什么 / 谁在场 / 谁缺席 / 资源短缺 / 玩家行为 / 真实事实 | Doomsday（外部）+ from_doomsday adapter |
| **心智内核**（主战场）| 角色怎么理解 / 记忆什么 / 承不承认 / 说不说 / 跨轮是否同一个人 | `src/{compiler, speaker_reader, decider, expresser, schema_matcher, ...}.py` + `rules/*.json` |
| **应用层** | CLI / 对话 UI / 演出 / 外部容器 | `src/cli_demo.py` 目前；未来接入游戏 |

## 核心问题（Problem Definition）

> **如何构建一个在有限世界中可验证、但在架构上可向更复杂角色心智持续扩展的角色内核？**

这是双重目标：
- **A / 现在落地**：在有限事件里做出跨轮、跨追问保持连续性的人感角色
- **B / 未来扩展**：不锁死记忆沉淀 / 信念层 / 隐瞒防御 / 社会动力 / 多容器的通路

## 当前必须做好的能力（今天成立）

1. 连续自我感
2. 过去约束当前回答
3. 追问时少乱编
4. 知道 / 不知道 / 不愿说的边界
5. 不同角色表现出稳定的差异

## 只预留接口、不做满的能力（明天再说）

- 长线记忆自动沉淀
- 完整信念层
- 防御机制
- 复杂隐瞒 / 反向表达
- 多 agent 信息传播
- 丰富关系动力学

**顺序原则**：今天先让角色像"有过去的人"，明天再像"有复杂心理的人"。

## 扩展原则（"MVE"：Minimum Viable Engine）

不是"最小可行"，是**最小可用 + 架构上可向上生长**。三条铁律：

1. **能力最小化** — 现在只做验证需要的最小表面
2. **接口前瞻化** — 几乎确定要长出来的维度留口，所有可能性不预做
3. **数据形状守口** — `character_state` / `current_read` / `memories` / `relationships` 不为单一实验写死语义

## 五个总闸门（每次加模块都要过）

1. **它提升的是哪一种核心能力？**（连续性 / 过去约束 / 边界感 / 关系痕迹 / 可控性 / 可嵌入性）
2. **没有它，当前最痛的问题是不是解不了？** 不是 → 先别做
3. **它属于世界壳、心智内核、还是表现层？** 别混层
4. **它有没有明确的 A/B 验证方式？** 没有 → 先别开发
5. **它会不会让接口更模糊？** 会 → 可能在破坏落地性

## 两种现在要防的高级风险

### 风险 A — 为未来设计过度
表现：还没用到的层先全建 / 接口过度通用 / 代码优雅但行为未验证

### 风险 B — 为当前锁死未来
表现：世界壳和心智层耦死 / 某世界专用语义写进内核 / 新能力只能硬 patch

**正确路线**：能力最小化，接口前瞻化。

## 当前成功标准（v1，已达成）

- [x] 跨轮仍像同一个人
- [x] 回答受真实 past 约束（from_doomsday 验证）
- [x] 追问时明显少乱编（Step 0.5 验证）
- [x] 更自然地区分知道 / 不知道 / 不愿说（discourse_state + 直接答 mode）
- [ ] 在一个小型互动容器里，被外部观察者感知为"比普通 NPC 更像活人"（Phase 3）

v1 的 7 条具体检查项见 [SCOPE.md](SCOPE.md)（已全部 pass）。

## Phase 2：关系痕迹可积累 — ✅ done (2026-04-22)

**封板依据**（三层 + 两尺度 + 一硬化）：

- **P1 过去厚度**（shipped commit `70cc08a` + distilled 验证 `75eadea` / `0ff16cb`）：空白角色不再空心；domain-mismatch sediment 通过 damping + gate 控住。
- **P2 知识边界**（`0e6b786`）：partial / suspects_but_avoids_checking / admit_if_pressed / kill_topic 能被单轮精准转化；anti-fabrication 守则去掉了 full_truth/will_deflect 诱发的编造；5/8 固定题明显 B>A。
- **P3 关系偏置**（`4aeab36`）：对特定对象展现 protects_from_truth / blames / owes_something；未匹配 target 不激活（双向证明 "没匹配就不起作用"）；4/6 固定题明显 B>A。
- **纵向稳定**（`fe254f0`）：T-014 + C01 各 14 轮连续对话，recent_turns 不重置。3/4 门槛通过，意外涌现 **RB 跨对象对比**（单轮未达成，长对话达成）。
- **空话硬化**（`4620d61`）：万能空话从 prompt 软劝升级为 `verbal_redlines.json` v1.0.2 的 block-level regex；spot re-verify 0/5 platitude occurrence。

**已知边界**（写清不遮盖）：

1. **RB v0.1 做方向性偏置，不做强对比引擎**。单轮 "换成别人" 题四个 persona 趋同；长对话里才涌现对象间对比。想做稳定对象对比需加 intensity / counter-bias（v2.1 后）。
2. **跨 session 记忆尚未开启**。同一 session recent_turns 累积有效；session 结束后 SpeakerModel 不持久化学习。v2.8 再碰。
3. **横向泛化验证尚未完成**。P1+P2+P3 在 T-014 (technician) + C01 (blank) 两种 persona 形状上成立；nurse / courier / drifter 等其它 role 还没测 → V2-5 正在验。
4. **三个 schema_matcher 类型收窄**（Step 2 controlled 裁剪）：knows_level 只保留 `partial / unaware / suspects_but_avoids_checking`；attitude 只保留 `will_admit_if_pressed / will_kill_topic`。`full_truth / will_deflect / wrong_version / will_volunteer / will_deny` 已从 v0.2 移出——需要时再引入新字段代替而非回炉旧枚举。
5. **compliance error 率 ~5%**（Decider / Expresser schema miss 偶发 LLM variance）。retry-once 兜底够用；想到 ~0% 要专门工程化。
6. **语言范围只中文**。未测其它语种。

## 下一阶段目标（Phase 2.5 / V2-5 横向验证）— 进行中

目标：**P1+P2+P3 在不同 role 上是否仍保持净收益，而不塌成统一腔调**。不扩复杂度，只横向扩 persona。

- nurse / courier / drifter 三种 role 新 personas（from_doomsday 产出）
- 每 persona 跑一套覆盖 P1/P2/P3 触发的题
- 4 条门槛：每 persona 至少 1 次 P1 命中 / 1 次 P2 命中 / 1 次 P3 命中 / 不变成统一谨慎腔

## Phase 3 目标（尚未开工）

- 嵌入一个小型游戏壳
- 外部玩家（不看架构）能感知"这个 NPC 不太一样"

## 一句话收尾

> **不是模拟完整人类，而是做出"在受约束世界里持续像同一个人的角色内核"。**
> **不是最小可行 demo，而是最小可用 + 可向上生长的内核。**
