# PlotPilot 原生写作链路精读报告

生成日期：2026-04-29

## 1. 结论摘要

原版 PlotPilot 不是“一章调用一次大模型”的简单生成器，而是一个规划驱动的写作状态机。它先生成宏观结构和幕/章规划，再按 beat 逐段流式写作；章节保存后，会继续调用章后同步、记忆更新和审查链路，把新章节沉淀为摘要、事件、triples、伏笔、时间线、对白样本和事实锁，之后再通过洋葱式上下文预算反馈到下一章。

核心链路可以概括为：

```text
Novel / Bible / Knowledge
  -> macro planning
  -> act and chapter planning
  -> chapter context assembly
  -> beat-level streaming generation
  -> chapter aftermath sync
  -> memory and review
  -> next chapter context
```

对 Evolution 的关键启示是：Evolution 不应该复制 PlotPilot 的主写作流程，也不应该重复注入长上下文。更合理的定位是读取 PlotPilot 原生资料，输出短策略、事实锁、风险证据和跨章反思，帮助原生规划与上下文层更稳地工作。

## 2. 主要代码入口

本报告基于以下原生链路代码阅读整理：

- `application/engine/services/autopilot_daemon.py`
  - `_handle_macro_planning`：第 276 行，宏观规划状态。
  - `_handle_writing`：第 495 行，章节写作状态。
  - `_handle_auditing`：第 796 行，章节审查状态。
  - `_stream_llm_with_stop_watch`：第 1298 行，流式生成和停止监听。
- `application/workflows/auto_novel_generation_workflow.py`
  - `prepare_chapter_generation`：第 159 行，生成前上下文准备。
  - `post_process_generated_chapter`：第 254 行，章节生成后处理。
  - `generate_chapter_stream`：第 456 行，章节流式生成。
  - `suggest_outline`：第 657 行，章节大纲建议。
  - `_build_prompt`：第 804 行，章节生成 prompt 拼装。
- `application/blueprint/services/continuous_planning_service.py`
  - `_get_bible_context`：第 1291 行，规划阶段读取 Bible/设定上下文。
  - `_build_quick_macro_prompt`：第 1593 行，快速宏观规划 prompt。
  - `_build_act_planning_prompt`：第 2218 行，幕/章节规划 prompt。
  - Bible 为空时会出现“暂无详细设定，请基于通用的商业小说套路生成结构”：第 1762 行。
- `application/engine/services/context_budget_allocator.py`
  - 文件头部定义 T0/T1/T2/T3 洋葱预算模型。
  - `allocate`：第 220 行，按优先级分配 token。
  - 插件上下文补丁进入 T0/T1：第 434 行附近。
- `application/engine/services/chapter_aftermath_pipeline.py`
  - `ChapterAftermathPipeline`：第 55 行。
  - `run_after_chapter_saved`：第 82 行，章节保存后的统一处理入口。
- `application/world/services/chapter_narrative_sync.py`
  - `llm_chapter_extract_bundle`：第 150 行，章后叙事同步的 LLM 抽取。
- `application/engine/services/memory_engine.py`
  - 文件头部定义 FACT_LOCK、COMPLETED_BEATS、REVEALED_CLUES。
  - `MemoryEngine`：第 368 行。
  - `update_from_chapter`：第 459 行，章节后更新记忆。
- `application/audit/services/chapter_review_service.py`
  - `review_chapter`：第 143 行。
  - 原生审查包含人物、时间线、故事线和伏笔：第 156-169 行。
- `infrastructure/ai/provider_factory.py`
  - `DynamicLLMService`：第 69 行。
  - `generate`：第 102 行。
  - `stream_generate`：第 111 行。

## 3. 原生状态机如何写作

`AutopilotDaemon` 是全托管写作的核心调度器。它不是直接进入正文生成，而是按小说状态推进：

1. **宏观规划**
   - 进入 `_handle_macro_planning`。
   - 调用连续规划服务生成全书或阶段性的结构设计。
   - 输入主要来自 Bible、故事设定、已有结构和目标章节数。
   - 输出是宏观结构、幕结构、章节节点或可继续扩展的规划资产。

2. **幕/章节规划**
   - 连续规划服务继续把宏观结构拆为 act、chapter、beat。
   - `_build_act_planning_prompt` 会把 Bible 上下文、上一阶段摘要和章节数量要求交给模型。
   - 这一层决定后续正文生成的“章节目标”和“节拍任务”。

3. **章节写作**
   - 进入 `_handle_writing`。
   - `AutoNovelGenerationWorkflow.prepare_chapter_generation` 准备当前章上下文。
   - `_build_prompt` 拼装最终章节 prompt。
   - `generate_chapter_stream` 通过 `DynamicLLMService.stream_generate` 流式生成正文。
   - 正文通常按 beat 或流式片段产生，因此一章可能触发多次生成相关调用，而不是严格一次调用。

4. **章节保存与章后同步**
   - 章节生成完成后进入 `post_process_generated_chapter`。
   - `ChapterAftermathPipeline.run_after_chapter_saved` 启动统一章后处理。
   - `chapter_narrative_sync.llm_chapter_extract_bundle` 抽取摘要、事件、线索、triples、伏笔、故事线、时间线、对白等结构化资料。
   - `MemoryEngine.update_from_chapter` 更新事实锁、已完成节拍和已揭露线索。

5. **审查与反馈**
   - `_handle_auditing` 进入审查阶段。
   - `ChapterReviewService.review_chapter` 分别检查人物一致性、时间线一致性、故事线推进、伏笔使用。
   - 审查结果会成为后续上下文和修正建议的一部分。

## 4. LLM 调用分类

PlotPilot 的大模型调用可以按阶段分为以下几类。

| 阶段 | 触发时机 | 典型输入 | 典型输出 | 调用频率 |
| --- | --- | --- | --- | --- |
| 宏观规划 | 小说进入规划阶段、结构不足或需要重建结构时 | Bible、题材目标、目标章节数、已有规划 | 全书结构、幕结构、章节骨架 | 通常每本小说或每轮重规划调用 |
| 幕/章节规划 | 需要把宏观结构落到章节节点时 | act 节点、Bible 上下文、上一阶段摘要 | 章节列表、章节目标、节拍 | 每幕或每批章节调用 |
| 章节大纲建议 | 前端或自动流程需要当前章建议时 | 当前章编号、已有章节、上下文 | 当前章 outline | 按需调用 |
| 正文流式生成 | 点击生成或全托管进入 writing 状态 | 章节目标、上下文预算结果、beat、约束、审查反馈 | 章节正文 chunk 和最终正文 | 每章至少一次，可能按 beat 多次 |
| 章后叙事同步 | 章节保存后 | 新章节正文、已有叙事资料 | summary、events、triples、foreshadow、timeline、dialogue | 每章保存后调用 |
| MemoryEngine 更新 | 章节保存后 | 新章节、FACT_LOCK、已完成 beat、已揭露线索 | 新增 beat、clue、fact violation | 每章保存后可能调用 |
| 原生章节审查 | 章节生成/保存后进入 audit | 章节正文、人物设定、时间线、故事线、伏笔 | issues、suggestions | 每章审查阶段多类调用 |

因此，回答“是一章一调用还是一章多次调用”：原生 PlotPilot 更接近“一章多阶段、多调用”。正文生成本身可能是一次流式调用或按 beat 多次；章节结束后还会有章后同步、记忆更新、审查等额外调用。

## 5. 章节生成 Prompt 的构成

`AutoNovelGenerationWorkflow._build_prompt` 是正文生成 prompt 的核心组装点。它会把当前章写作目标与上下文层组合成最终输入。典型构成包括：

- 当前小说和章节信息。
- 当前章目标、章节编号、计划节点或 beat 要求。
- 已有章节摘要和近期上下文。
- Bible 人物、地点、世界观等设定。
- Storyline、Timeline、Foreshadowing 等原生资料。
- MemoryEngine 产生的 FACT_LOCK、COMPLETED_BEATS、REVEALED_CLUES。
- ContextBudgetAllocator 分配后的洋葱上下文。
- 插件提供的 context patch，例如 Evolution 的短策略块。
- 可能存在的审查反馈、继续生成要求或停止条件。

这意味着 Evolution 最适合参与的位置不是“再塞一份世界观全文”，而是在插件上下文补丁中给出短小、可执行、可追溯的写作约束，例如：

- 上一章终点锁。
- 当前角色事实边界。
- 本章不得重复完成的 beat。
- 待推进或待回收的伏笔。
- 当前 milestone 的推进要求。
- 重复进入、章节回滚、人物污染等风险提醒。

## 6. 洋葱上下文预算模型

`ContextBudgetAllocator` 使用 T0/T1/T2/T3 分层模型：

- **T0：绝对不删减**
  - 系统 prompt、强制伏笔、角色锚点、FACT_LOCK、COMPLETED_BEATS、REVEALED_CLUES、硬约束类插件 context patch。
- **T1：可压缩**
  - 图谱子网、近期幕摘要、软参考类插件 context。
- **T2：动态水位线**
  - 最近章节内容。
- **T3：可牺牲**
  - 向量召回片段等辅助参考。

预算紧张时，从 T3 到 T2 再到 T1 逐层压缩，T0 优先保护。这个设计说明 PlotPilot 已有比较完整的上下文裁剪机制，Evolution 不需要另起一套长上下文管理系统；应该尽量把“必须遵守”的事实锁和短策略放进 T0，把“建议参考”的策略放进 T1。

## 7. 章后同步如何形成原生资料

章节保存后，`ChapterAftermathPipeline` 负责把正文转化为后续可用的结构化资料。关键步骤包括：

- 调用 `chapter_narrative_sync.llm_chapter_extract_bundle`。
- 从章节正文抽取 summary、key events、open threads。
- 更新或生成 triples，用于知识图谱和事实关系。
- 同步 foreshadowing、storyline、timeline。
- 提取 dialogue samples，服务后续人物声线和对白一致性。
- 将这些资料交给后续上下文构建、审查和 MemoryEngine 使用。

这条链路对 Evolution 很重要：Evolution 的 evidence 应优先引用这些原生产物的摘要或记录 id，而不是复制整段正文。这样既能降低 token，也能避免插件和主流程各自维护一套互相冲突的事实源。

## 8. MemoryEngine 的反馈机制

`MemoryEngine` 主要维护三类跨章约束：

- **FACT_LOCK**
  - 从 Bible 和知识图谱构建不可篡改事实，例如角色身份、死亡名单、关系、时间线。
- **COMPLETED_BEATS**
  - 记录已经完成的关键剧情 beat，防止后续章节重复进入、重复发现、重复执行。
- **REVEALED_CLUES**
  - 记录已经揭露的线索，避免同一线索被写成第一次发现，或与已知事实冲突。

这些内容会通过 `ContextBudgetAllocator` 进入 T0 层。也就是说，PlotPilot 原生已经有“把章后事实变成下一章硬约束”的闭环。Evolution 更应该补强这个闭环中当前粒度不足的地方，例如路线回滚、重复进入、地点转场缺失、人物调色盘漂移，而不是替代 MemoryEngine。

## 9. 原生审查链路

`ChapterReviewService.review_chapter` 原生会执行四类审查：

- 人物一致性：对照 Bible 角色设定检查章节表现。
- 时间线一致性：对照 timeline registry 检查事件顺序。
- 故事线一致性：对照 active storylines 检查主线推进。
- 伏笔使用：对照 unrevealed foreshadowings 检查是否使用、遗漏或冲突。

Evolution 的审查定位应该是补位型：

- 检查章节首尾状态回滚。
- 检查重复进入地点或重复完成同一 beat。
- 检查缺少移动桥段。
- 检查人物卡污染和无效实体。
- 检查性格调色盘缺失或漂移。
- 检查高频模板句和重复表达。
- 检查角色认知边界和能力边界越界。

所有 Evolution issue 应带 `source_plugin`、`issue_family`、`host_source_refs`、`evidence` 和 `suggestion`，方便进入 Capsule、Reflection、GeneCandidate 链路。

## 10. 当前 A/B 实验暴露的问题

近期前端 A/B 实验中，对照组和实验组虽然使用了相同 premise，但输出题材出现明显分叉：对照组偏向玄幻退婚流，实验组更接近科幻/悬疑方向。结合代码阅读，最可疑的原因是：

1. 宏观规划阶段主要依赖 `_get_bible_context` 返回的 Bible/设定资料。
2. 当 Bible 或插件 story context 为空时，宏观规划 prompt 会回退到“暂无详细设定，请基于通用的商业小说套路生成结构”。
3. 如果 `Novel.premise` 没有被稳定注入宏观规划 prompt，模型会用通用网文套路补齐结构。
4. 一旦宏观结构漂移，后续章节规划、beat 和正文生成都会沿着漂移后的结构继续写。

这解释了为什么即使两组小说创建时 premise 相同，后续正式输出仍可能走向不同题材。问题不一定发生在正文生成 prompt，而可能更早发生在 macro planning。

## 11. Evolution 适配建议

Evolution 下一阶段应围绕 PlotPilot 原生链路做“短策略协作”，而不是扩大为第二套写作引擎。

建议方向：

1. **规划前补强题材和 premise**
   - 将 `Novel.premise`、核心题材、主线承诺作为宏观规划硬输入。
   - 当 Bible 为空时，也不得回退为纯通用商业套路。
   - Evolution 可提供 `plugin_story_context`，只包含题材锁、主线锁、世界观边界和禁止漂移项。

2. **写作前只注入短策略**
   - 对齐洋葱上下文模型。
   - T0 放硬事实和不可违背约束。
   - T1 放策略建议。
   - 不重复注入 PlotPilot 已经拥有的长摘要、长正文、完整知识库。

3. **写作后优先读取原生产物**
   - after_commit 后先读 chapter narrative sync、timeline、storyline、foreshadowing、triples、dialogue。
   - 原生同步缺失时才启用 Evolution fallback，并标记 degraded。
   - Evidence 引用原生记录或摘要，避免复制长正文。

4. **审查后形成可学习闭环**
   - Evolution issue 进入 Capsule。
   - Reflection 输出下一章可执行约束。
   - GeneCandidate 记录策略是否有效，但不自动升级为正式 Gene。

5. **实验中记录宏观规划输入**
   - A/B manifest 必须保存 macro planning 实际收到的 premise、Bible 命中、plugin story context 摘要。
   - 如果两组宏观规划输入不一致，实验应标记为无效或结论受限。

## 12. 后续改造建议

优先改造顺序如下：

1. **把 `Novel.premise` 作为宏观规划硬输入**
   - 在 `_get_bible_context` 或 `_build_quick_macro_prompt` 中明确加入 premise。
   - 即使 Bible 为空，也必须让 prompt 包含题材、主角、核心冲突、世界观边界。

2. **修改 macro planning fallback 文案**
   - 当前“暂无详细设定，请基于通用的商业小说套路生成结构”容易诱导题材漂移。
   - 应改为“暂无详细 Bible，但必须严格遵守小说 premise 和用户输入的题材/主线”。

3. **增加宏观规划输入审计**
   - 在实验 manifest 或 LLM audit 中标记 macro planning 是否收到 premise。
   - 保存 premise hash、Bible 命中数、plugin context 命中数，不保存敏感配置。

4. **Evolution 增加题材锁和主线锁**
   - 在 `before_context_build` 或规划前插件上下文中输出短约束。
   - 约束应包括“不得改写题材类型”“不得替换主线冲突”“不得把未设定的通用套路当成事实”。

5. **重新设计 A/B 有效性门槛**
   - 两组必须证明宏观规划输入一致。
   - 两组必须记录正文生成、章后同步、审查、Evolution Agent API 的调用拆分。
   - 若原生资料命中为 0，应单独标注“原生适配未充分参与”。

## 13. 一句话判断

PlotPilot 原生写作链路已经具备完整的规划、生成、同步、记忆和审查闭环；当前最需要修的是“上游规划事实输入的稳定性”和“实验可复盘性”。Evolution 的最佳价值不是替代这个系统，而是把 PlotPilot 已有资料读准、压短、变成可执行策略，并在章后审查中把问题沉淀为下一章能真正使用的约束。
