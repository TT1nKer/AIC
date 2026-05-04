# Evidence Layer — AIC v2 架构方向

> 写于 2026-05-04。把"反编造"问题从 prompt/规则层提升到 **证据链**层的设计提案。
> 直接挑明 v1(`schema_matcher` + `verbal_redlines` + 硬规则)失败的根因,并给出
> 不依赖"让 LLM 决定真伪"的替代方案。
> 来源:做 ai-town fork 时跟 collaborator 讨论"5 角色分离 + provenance"的派生。
> 这套体系对 ai-town(5 NPC 闲聊)是 over-engineering;对 AIC(单角色心智内核
> 研究)恰好对路。

---

## 1. v1 失败的真正根因(回顾)

`schema_matcher` 的设计是:让 LLM **判定**当前状态属于哪个 enum:

```
LLM 看 → 输出: knows_level: "partial" | "unaware" | "suspects_but_avoids_checking"
                attitude:    "will_admit_if_pressed" | "will_kill_topic"
```

**这本身就是让 LLM 做"真伪 / 知道不知道"的判定**。然后 `decider` 用硬规则约束输出。

塌方在两个地方:

1. **LLM 给"真伪"判定时跟 prompt 工程在"打架"** — 它看到角色卡说不知道,但训练数据里大概率回答"我大概知道",最后给个收敛到"谨慎"的中间值。所有 persona 收敛到同一个安全 enum。
2. **离散 enum 把连续的"不确定"压扁** — partial / unaware / suspects 三档之间没有梯度,LLM 拿到 partial 就一律输出"我有点印象但不太清楚"模板。

**根因(一句话)**:让 LLM 决定"什么是事实 / 我知道什么",这件事本身就不可靠。LLM 不是 truth oracle,它是续写概率最大的 token 流。

## 2. 关键 insight(从 ai-town 讨论里来的)

> **AI 不应该判定"它是不是真的事实"。AI 最多只能说"根据当前证据,这个 claim 可以/不可以被当作某种层级的事实使用"。**

把 5 个角色严格分开:

```
Event Log     = 客观事实(由 simulation engine / from_doomsday adapter 产生)
Memory        = 角色信念(每条带 provenance,可错、可扭曲)
Utterance     = 角色说出口的话(对应 expresser 输出)
LLM           = 解释器(把结构化输入翻译成 utterance)
Harness       = 审计器(查 evidence,判 fact_status,改写违规)
```

**只要这 5 个角色不混,系统就反编造**。

## 3. 这套架构对 AIC 已有模块的映射

| AIC v1 模块 | v2 角色 | 改动 |
|---|---|---|
| `from_doomsday` adapter | **Event Log 来源** | 不变 — 它本来就是产生 ground truth 的东西 |
| `compiler` | **Memory 装配器** | 现在不是"装 character_state",是"按 evidence chain 装 belief 列表" |
| `speaker_reader` | **Memory 读取器** | 给 utterance 之前,读相关 memory 并附 provenance |
| `schema_matcher` | ❌ 删掉 / 重构成 **Harness 的 claim 抽取器** | 不再让 LLM 输出 enum;让它从 utterance 里抽 claim,Harness 查 evidence 判 fact_status |
| `decider` | **Harness 的规则审计器** | 拿 LLM 想说的 claim,查 evidence chain,决定 allowed_usage |
| `expresser` | **LLM (Interpreter)** | 输入是已 audit 过的"可说的 claims + belief 状态",LLM 只渲染 |
| `pose_resolver` | **Harness 的语气仲裁器** | 不变,但接收的是 evidence-status 而不是 LLM 自报的 enum |
| `verbal_redlines` | **Harness 的 utterance 校验** | 保留 — 它是最后一道 utterance 防线 |

## 4. 核心数据结构

### 4.1 Event(世界事实,只有 simulation 能写)

```yaml
event_id: E392
type: theft
actor: A02
object: medicine
location: ration_station
day: 14
generated_by: simulation_engine    # 必填,只有这个值才能 confirm world fact
```

由 from_doomsday adapter 生成。LLM 永远不能写。

### 4.2 Memory(角色信念,每条都带 provenance)

```yaml
memory_id: M5821
agent: A03                          # 谁的记忆
content_token: "A02_stole_medicine" # 结构化 token,不是自由文本
subject: A02                        # 关于谁
day: 14
emotional_weight: -0.6
emotion: suspicion

# provenance 链 — 这是 anti-fabrication 的根
source_type: heard_from_other       # observed / heard_from_other / inferred / world_event
source_event_id: null               # 如果 source=world_event 才填
source_agent: A06                   # 如果 source=heard_from_other,谁告诉的
parent_memory_id: M5103             # 这个信念衍生自哪条 memory(谣言传播链)
confidence: 0.4                     # 0..1

# fact_status — 不是 true/false,而是"可作什么使用"
allowed_usage:
  as_world_fact: false              # 永远 false,除非 source=world_event 直接验证
  as_my_belief: true                # 我可以"我相信"
  as_certain_assertion: false       # 但我不能"我确定"
  as_attribution: true              # 我可以"我听 A06 说过"
  as_rumor_relay: true              # 我可以转述,但 confidence 会再降
```

### 4.3 Utterance(角色说出口的话)

```yaml
utterance_id: U7299
speaker: A03
text: "我听 A06 说,A02 偷了药"
turn: 47

# claim 抽取(由 LLM 帮忙做,但只是"翻译",不是"判定")
extracted_claims:
  - claim: "A02 stole medicine"
    certainty_in_speech: medium     # LLM 标:说话语气有多确定
    backed_by_memory: M5821         # 关联回我的 memory
    audit_status: allowed_as_attribution  # Harness 算出来的:"我听 X 说"形式可,直接断言不可
```

## 5. 关键防线(rule,不是 prompt)

**硬规则**(Harness 强制,LLM 无法绕过):

```python
# rule_1: memory 不能自动升级为 world fact
def can_assert_as_world_fact(memory):
    return memory.source_type == "world_event"

# rule_2: confidence < 0.6 不能用"我确定/绝对/肯定"形式表达
def can_express_certain(memory):
    return memory.confidence >= 0.6

# rule_3: source=heard 必须带 attribution(不能"省略"听来的来源说成自己知道)
def must_attribute(memory):
    return memory.source_type == "heard_from_other"

# rule_4: 没 memory 支撑的 claim 不能进 utterance
def claim_has_evidence(claim, memory_set):
    return any(m.content_token == claim_token_match(claim) for m in memory_set)

# rule_5: rumor 经多人传播后 confidence 不上升只下降
def rumor_decay(memory):
    if memory.source_type == "heard_from_other":
        return memory.confidence * 0.85  # 每次转手降 15%
```

**这些规则是 Python**,不是 prompt 里的指令。LLM 看不到也绕不过。

## 6. LLM 在这架构里干什么

只做 4 件事,**全是"翻译"性质**:

1. **Claim 抽取**:从 utterance 文本抽出 claim 三元组(subject, predicate, object)+ certainty。**这是语言层翻译,不判断真伪**。
2. **Memory → utterance 渲染**:给定 audit 过的 belief + allowed_usage,渲染成自然中文。
3. **建议改写**:Harness 判定违规时(比如 claim 没 evidence),让 LLM 给个合规的替换说法。
4. **Inference 提议**(可选):LLM 看 memory 集合,提议一个 inferred memory(标 source=inferred),Harness 决定要不要采纳。

**LLM 永远不做**:
- 决定一件事是真是假
- 决定一个角色"知道"还是"不知道"什么
- 直接写 memory(每条 memory 必须有 provenance,LLM 写不出真 provenance)
- 判定 utterance 是否合规(Harness 干这个)

## 7. 这套架构能解决 v1 哪些塌方

| v1 失败模式 | v2 解法 |
|---|---|
| 所有 persona 收敛到"谨慎" | 不再有 enum 收敛点 — 每个角色的可说空间由各自的 memory 集合 + provenance 算出来,天然不同 |
| schema_matcher 输出不稳 / compliance error 5% | 删掉 schema_matcher 的"判定"职责,只剩"翻译"。判定交给 Harness 规则,确定性 100% |
| LLM 跟规则打架 | LLM 不知道有规则;它只看到"已经审计过、可以说"的 belief,渲染就行 |
| RB v0.1 单轮换对象不显著 | 现在每条 memory 都有 subject 字段,对不同对象的可说内容由 memory 集合差异自然驱动 |
| 万能空话 | utterance 必须 backed_by_memory,空话填不出 backing,被改写或拒发 |

## 8. 这套架构带来的新能力

是 v1 没设计、但是免费送的:

- **冤案 / 误会**:多个角色的 memory 都指向某个 false claim(都是 heard 来源),但 world_event 没有,所以社会层面有"共识谣言",世界真相不变。
- **谣言衰减/扭曲**:每次转述 confidence 降,parent_memory_id 链可追溯,适当节点可触发"扭曲"。
- **认知盲点**:某 agent 的 memory 集合里**根本没有**某 entity → 它就是真不知道,不需要 prompt 写"假装不知道"。
- **跨角色对比**:同一 fact 在不同 agent 的 memory 里 confidence 不同 → 同一句话在不同 NPC 嘴里说出来口气不同,**自然涌现**而非编程出来的差异。

## 9. v1 → v2 的切割路径(如果你有空回 AIC)

不是推倒重来,是**拆解 + 重组**:

| v1 模块 | 命运 |
|---|---|
| `from_doomsday.py` | **保留**,产出 Event Log |
| `compiler.py` | **重构**:从"装 character_state"改成"装 audited_belief_set" |
| `speaker_reader.py` | **保留** + 加 provenance 读取 |
| `schema_matcher.py` | **拆**:语言抽取部分变 claim_extractor;判定部分删掉 |
| `decider.py` | **重构**为 Harness 规则引擎(这次是 Python 规则,不是 LLM 调用) |
| `expresser.py` | **保留**,但只做渲染(纯 LLM 调用,不再有规则约束) |
| `pose_resolver.py` | **保留**,接收 audit 过的状态 |
| `verbal_redlines.json` | **保留**,作为 utterance 最后防线 |
| `memory_schemas.v1.json` | **替换**为 v2 schema(本文档第 4 节) |

## 10. 一句话

> **v1 把反编造做成了"让 LLM 内省后报告"——结果 LLM 演员化、规则压不住。
> v2 把反编造做成了"基础设施层强制证据链"——LLM 在审计后的输入空间里自由演,反而更自然,也更可信。**

不是把 LLM 当工具用得更狠,而是**把 LLM 该负责的东西收窄到它真正擅长的(语言翻译)**,把它不擅长的(判定真伪)交给规则。

这是把 ai-town fork 验证过的 "LLM-as-Interpreter" 原则推到记忆和事实层 — 但 AIC 的研究规模才配得上做完整版。
