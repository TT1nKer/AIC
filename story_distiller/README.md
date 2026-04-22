# story_distiller — 故事蒸馏器 v0.1

## 目标

把一段成熟叙事（小说章节 / 电影剧情摘要 / 剧本片段）**蒸**成可被 AICharacter 系统加载的**人物厚度结构**。

**不是**：拷贝情节、搬运台词、注入原设定
**而是**：抽出关系张力、秘密、防御方式、触发模式、可沉淀的过去痕迹

## 原则

1. **蒸结构，不蒸内容。** 不保留原台词 / 原事件顺序 / 原角色名 / 原世界观 / 高辨识度桥段
2. **只保留可运行的密度。** 输出的每条必须能直接影响 AICharacter 中 Decider 选动作或 Expresser 表达
3. **不做剧情再生成。** 蒸馏器不替别人写故事，只抽结构
4. **版权安全**：推荐用公版作品、自己写的摘要、或结构性改编作原料
5. **不替代 AICharacter 的其它层。** 蒸馏器只是角色**加厚器**，不是"灵魂生成器"。像人来自持续互动、红线、discourse_state，蒸馏只补"过去厚度"这一层

## 作用范围

**v0.1**：只做 **角色关系蒸馏器**
- 输入：一段文本摘要（≤2000 字）
- 输出：`distilled_package.v0.1` JSON
- 不自动合入 AICharacter 的 CharacterState（人工对接，v0.2 再做）

**超出 v0.1 的**：
- 自动合入 AICharacter persona（v0.2）
- 多章节长篇蒸馏 / 跨角色一致性（v0.3）
- 自动生成 trace cards 供 schema_matcher（v0.4）
- 从已蒸馏角色反向生成事件剧本（v1.0）

## 和 AICharacter 的关系

```
story_text
    ↓  (distiller LLM call)
distilled_package.json
    ↓  (人工挑选 + 映射，v0.1 阶段)
AICharacter persona JSON (character_state + memories + relationships)
    ↓
cli_demo / dialogue_runner / live_runner
```

Distiller 作用在最前段。它不是 AICharacter 的一部分，但它的产物是 AICharacter 的输入。

## 目录结构

```
story_distiller/
  schemas/           v0.1 output JSON Schemas
  prompts/           distiller LLM prompt
  src/               distiller.py: call LLM, validate, save
  examples/          sample inputs + expected outputs
  tests/             schema validity + distillation quality cases
```

## 为什么这个项目存在

AICharacter v2 已经解决：
- 角色会看人、会选姿态、会守红线
- 过去（from_doomsday）能让角色有真实 memories 可引用

但剩下一个瓶颈：
- from_doomsday 生成的过去**机械且薄** —— 事件流只有 facility_failure / contact_absent 等几类
- 想让 NPC 像"有复杂人际史、多重秘密、内心矛盾的人"，手工写 persona 不可持续

故事蒸馏器填这一层：**把成熟叙事里的人物厚度机制，压成 AICharacter 能吃的数据**。

## License / 版权

仅用于研究与自用。蒸馏器产物不得包含原作品的可识别台词、具体设定、或高辨识度桥段。建议：
- 原料用公版（PG: 汉语经典、二十世纪初之前作品）
- 或用自己撰写的摘要
- 或把原作先抽象化再喂给蒸馏器
