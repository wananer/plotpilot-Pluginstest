# PlotPilot 动态角色卡 / 世界演化 插件实体设计稿

## 设计目标
这份文档是一份**实体设计稿 + 平台适配设计稿**：

它不仅定义插件本体的实体（Entity）模型、关系、生命周期、数据流与接口边界，也补齐了 **PlotPilot 本地插件平台为了适配 `ST-Evolution-World-Assistant` 这类“重运行时 / 重工作流 / 重上下文注入”插件所需要的改进方向**。

它服务的对象不是普通用户，而是后续开发插件本体、插件平台 runtime、宿主桥、hook 层时的统一设计基线。

适用方向：
- 动态角色卡插件
- 世界演化核心插件
- 与仿生记忆协同的章节后处理 sidecar 插件
- 后续同类“持续状态型 / 工作流型 / 上下文注入型” PlotPilot 插件

---

## 一、设计原则

### 1.1 零侵入原则
该插件必须遵守 PlotPilot 当前架构约束：
- 不修改 `domain/`、`application/` 的核心业务语义
- 不接管主库结构
- 不新增与主系统重复的大型业务架构
- 只通过插件接口、宿主桥、章节事件、上下文注入来扩展能力

### 1.2 寄生式整合原则
插件不是新系统，而是寄生在现有写作链路上的 sidecar：
- 复用章节提交后的后处理时机
- 复用 `ContextBuilder` 注入上下文
- 复用 `get_llm_service()`
- 复用后台任务服务
- 复用插件 loader / runtime / event bridge

### 1.3 事实驱动原则
所有实体状态必须由章节事实驱动：
- 不能脱离正文乱演化
- 不能凭空推进角色行动
- 不能重写主线
- 不能与已写章节冲突

一句话：

**插件负责维护“已发生事实的结构化状态”，不是代替作者新写一套世界。**

### 1.4 平台先于业务原则
对这类插件，`plugin.json + inject.js + routes.py` 只是最小壳。
真正决定插件是否可落地的，是宿主是否提供：
- 生命周期接口
- 统一事件协议
- 生成前后 hook
- 状态去重与回滚语义
- 后台任务桥
- 可预测的上下文 API

因此本设计稿默认把“插件本体设计”和“插件平台增强设计”一起看，不再把平台当成单纯的脚本挂载器。

---

## 二、对本地插件平台的现状判断

基于本地仓库实际检查，当前 PlotPilot 已具备**插件平台最小闭环**：

### 2.1 后端已有能力
已确认存在：
- `plugins/loader.py`
- `interfaces/main.py` 中的插件接入
- 插件 manifest / 列表 API
- 插件静态资源挂载
- 插件 `routes.py` 动态接入
- 插件导入能力（本地 / GitHub / zip）

这说明后端已完成：
- 插件发现
- 插件注册
- 插件 API 挂载
- 插件静态资源暴露

### 2.2 前端已有能力
已确认存在：
- `frontend/public/plugin-loader.js`
- `frontend/index.html` 自动注入 plugin runtime
- `frontend/vite.config.ts` 代理 `/plugins`
- `Workbench.vue` / `WorkArea.vue` 的部分宿主事件发射

目前 runtime 已具备基础能力：
- `window.PlotPilotPlugins`
- runtime event bus
- runtime context
- host event emit
- 动态脚本加载
- manifest 拉取

### 2.3 当前平台成熟度判断
当前平台属于：

**“插件可加载 + 前端可注入 + 事件面初步可用”的最小运行时平台**

但还**不是**“可稳定承载 `ST-Evolution-World-Assistant` 这类复杂状态型插件的平台”。

核心原因：
- 当前更偏向“脚本注入平台”
- 缺少标准化的生命周期 contract
- 缺少生成链路 hook 规范
- 缺少能力声明 manifest
- 缺少统一 runtime pipeline 语义
- 缺少失败回滚 / 重放 / 补跑 / 去重的一致抽象

---

## 三、为什么要以 ST-Evolution-World-Assistant 为对照目标

`ST-Evolution-World-Assistant` 不是普通 UI 插件，而是典型的：

- **重运行时插件**：有 `initRuntime()` / `disposeRuntime()`
- **重工作流插件**：有 before / after 双阶段处理
- **重事件插件**：依赖宿主事件和状态同步
- **重注入插件**：真正参与生成上下文，而不是只显示数据
- **重状态插件**：有 snapshot、workflow、去重守卫、回滚、手动补跑
- **重 UI 入口插件**：有 FAB / panel / notice，但 UI 只是入口，不是核心逻辑

它的实际架构思路可以抽象成：

```text
host adapter
→ runtime / pipeline
→ workflow dispatch / dedup / replay
→ state snapshot / controller
→ UI panel / FAB / notice
```

这个思路对 PlotPilot 的意义是：

> 如果要做动态角色卡 / 世界演化 / 仿生记忆协同，就不能只做“插件目录 + inject.js”，而要补齐“宿主桥 + runtime pipeline + hook 协议”。

---

## 四、插件定位

建议把当前需求拆成两个插件实体层级：

### 4.1 动态角色卡插件
负责维护：
- 角色当前状态
- 角色近期经历
- 角色称号 / 关系 / 位置变化
- 角色“正在做什么”时间线

它的核心对象是：**角色**。

### 4.2 世界演化核心插件
负责维护：
- 势力状态
- 地区状态
- 世界事件
- 非主角实体的动态推进

它的核心对象是：**世界状态**。

### 4.3 两者关系
建议不是做成两个完全分裂系统，而是：
- `world_evolution_core` 负责世界层事件与状态快照
- `dynamic_rolecard` 负责角色层状态展示与角色线程
- 二者共享章节事实提取结果，但各自维护独立 sidecar 数据表
- 二者都跑在统一插件 runtime 协议上，而不是各自发明一套工作流

如果第一阶段要控制复杂度，可以先做：

**Phase 1：先落动态角色卡实体与 runtime 接口设计**

然后把世界演化作为二期扩展。

---

## 五、平台改进方向（为适配 ST-Evolution-World-Assistant 必须补齐）

这一章是本次优化的重点。

### 5.1 从“插件加载器”升级为“插件运行时平台”
当前平台已具备 loader，但不足以承载复杂插件。

建议明确把平台拆成四层：

#### A. Discovery Layer（发现层）
职责：
- 扫描 `plugins/*`
- 读取 `plugin.json`
- 构建 manifest
- 暴露 `/api/v1/plugins` / `/api/v1/plugins/manifest`
- 管理 enable / disable 状态

当前基本已具备。

#### B. Runtime Layer（运行时层）
职责：
- 负责前端插件 init / dispose
- 保存已注册插件实例
- 管理 script / style 生命周期
- 统一向插件暴露 runtime context
- 负责插件 reload / deactivate / version drift 感知

当前明显不足，是下一步重点。

#### C. Host Bridge Layer（宿主桥接层）
职责：
- 把 PlotPilot 的路由、工作台、章节、生成、重写、保存行为统一抽象成标准宿主事件
- 给插件提供稳定的宿主上下文访问方式
- 隔离插件对页面内部实现的直接依赖

当前已有雏形，但还需标准化。

#### D. Workflow Hook Layer（工作流钩子层）
职责：
- 让插件真正进入写作链路
- 提供 before / after / manual / rebuild 的统一 hook 语义
- 支持注入、后处理、回滚、补跑、重建

当前基本缺失，是适配 ST-EWA 类插件的核心缺口。

### 5.2 plugin.json 必须从“静态声明”升级为“能力声明”
当前样例 `plugin.json` 过轻，只适合简单插件。

为了适配持续状态型插件，建议引入以下结构：

```json
{
  "name": "world_evolution_core",
  "display_name": "World Evolution Core",
  "version": "0.1.0",
  "enabled": true,
  "frontend": {
    "scripts": ["static/inject.js"],
    "styles": ["static/style.css"]
  },
  "backend": {
    "router": true,
    "daemon": true
  },
  "capabilities": {
    "ui_panel": true,
    "floating_fab": true,
    "chapter_hooks": true,
    "generation_hooks": true,
    "rewrite_hooks": true,
    "timeline_rebuild": true,
    "background_jobs": true,
    "context_injection": true,
    "status_api": true,
    "rollback": true
  },
  "runtime": {
    "requires_events": [
      "workbench:opened",
      "novel:selected",
      "chapter:loaded",
      "chapter:saved",
      "chapter:committed",
      "generation:started",
      "generation:completed",
      "rewrite:started",
      "rewrite:completed",
      "manual:rerun_requested",
      "timeline:rebuild_requested"
    ],
    "hook_points": [
      "before_context_build",
      "after_generation",
      "after_commit",
      "manual_rebuild"
    ]
  },
  "constraints": {
    "supported_views": ["workbench"],
    "requires_single_novel_scope": true
  }
}
```

#### 设计价值
让宿主可以在插件尚未真正运行前，就知道：
- 该插件需不需要 hook 写作链路
- 是否依赖后台任务
- 是否需要 context injection
- 是否提供状态接口
- 是否有回滚 / 重放 / 补跑需求

### 5.3 前端插件需要标准生命周期接口
对照 ST-EWA，复杂插件一定需要：
- 初始化
- 卸载
- 清理监听器
- 重新挂载
- 热重载兼容

建议设计统一前端 contract：

```ts
interface PlotPilotFrontendPlugin {
  name: string
  version: string
  init(ctx: PlotPilotPluginContext): Promise<void> | void
  dispose?(): Promise<void> | void
  onHostEvent?(event: PlotPilotHostEvent): void
}
```

并约定 runtime 注册协议：

```js
window.PlotPilotPlugins.plugins.register({
  name: 'world_evolution_core',
  version: '0.1.0',
  init(ctx) {},
  dispose() {},
})
```

#### 当前平台缺口
当前 `plugin-loader.js` 的重点还在：
- 拉 manifest
- 动态注入脚本

但复杂插件真正需要的是：
- runtime 主动调用 `init()`
- runtime 保存 `dispose()` handle
- 停用 / reload 时能清理资源
- 版本切换时能避免重复 mount

### 5.4 必须新增统一 Hook 语义，而不是只靠散事件
ST-EWA 最重要的不是按钮或面板，而是：
- `before_reply`
- `after_reply`
- fallback 去重
- 手动重跑
- 状态回放

映射到 PlotPilot，建议明确四类 hook：

#### A. before_context_build
时机：生成上下文前
用途：
- 注入角色当前态
- 注入世界摘要
- 注入约束块

#### B. after_generation
时机：章节生成完成后
用途：
- 提取新事实
- 记录 generation output snapshot
- 生成临时 sidecar 分析结果

#### C. after_commit
时机：章节确认保存为剧情事实后
用途：
- 更新正式角色状态
- 更新世界状态
- 写 `PluginJobRecord`
- 更新摘要快照

#### D. manual_rebuild
时机：用户手动点击补跑 / 重建
用途：
- 重建某章事实
- 重建最近 N 章状态
- 重算某角色 / 势力 / 地区线程

### 5.5 自动执行 / 手动补跑 / 重建必须统一成同一条 workflow family
这是从 ST-EWA 可直接复用的经验。

不要设计成：
- 自动执行一套逻辑
- 手动重跑另一套逻辑
- 时间线重建再写第三套逻辑

正确方式：
- 同一条 pipeline
- 只是 `trigger_type` 不同

推荐统一任务语义：
- `trigger_type = auto_after_commit`
- `trigger_type = auto_after_generation`
- `trigger_type = manual_rerun`
- `trigger_type = timeline_rebuild`

好处：
- 去重策略统一
- 错误处理统一
- 回滚策略统一
- 前端状态展示统一
- 日后扩展仿生记忆协同时不用重造第三套流程

### 5.6 必须把“去重 / 防重复推进 / 防重复注入”上升为平台级能力
ST-EWA 的 `intercept-guard` 给出的经验非常重要：

对于持续状态型插件，最危险的问题不是“不执行”，而是“重复执行”。

在 PlotPilot 里至少要防三类重复：

#### A. 同一章节重复推进状态
例如：
- 用户保存两次
- 自动保存与手动保存都发了事件
- 章节重写后又触发一轮提交

需要依据：
- `chapter_id`
- `chapter_number`
- `content_hash`
- `trigger_type`
- `plugin_name`

做幂等键。

#### B. 同一轮生成重复注入上下文
例如：
- before_context_build 被多次触发
- 重试生成导致相同摘要被重复拼入 prompt

需要依据：
- `request_id`
- `chapter_id`
- `summary_hash`
- `plugin_name`

做注入守卫。

#### C. 手动补跑与自动执行撞车
例如：
- 自动任务尚未完成
- 用户又手动点击重建

需要平台明确定义：
- 是排队
- 是拒绝
- 还是以 manual 优先并取消旧任务

建议默认：
- 同一 scope 仅允许一个 in-flight job
- manual_rebuild 优先级高于 auto

### 5.7 必须提供“回滚 / 重放 / 重建”能力，不然状态型插件不可维护
ST-EWA 的 snapshot / replay 思路表明：

只要插件会更新持续状态，就必须能：
- 重放
- 回滚
- 局部重建
- 失败恢复

PlotPilot 侧建议明确：

#### 最小回滚语义
- 角色状态更新失败，不写正式 current snapshot
- 世界演化失败，不覆盖上一版 world snapshot
- 允许保留失败 job 记录，但不污染正式 state

#### 最小重放语义
- 支持按 `chapter_id` 重新提取事实
- 支持按 `chapter_range` 重建状态
- 支持按 `entity_id` 重算角色 / 势力 / 地区线程

#### 最小状态版本化语义
建议 `ChapterFactSnapshot` / `WorldStateSnapshot` / `CharacterStateSnapshot` 至少能保留：
- `source_job_id`
- `content_hash`
- `version`
- `supersedes_snapshot_id`

这样后续才能查明：
- 为什么这个状态变成这样
- 是哪一次重写导致的
- 是否需要回滚到上一版

### 5.8 宿主服务桥必须标准化，避免插件直接 import 宿主内部
建议新增统一 host facade，而不是让插件直接 import 散落依赖：

```python
class PlotPilotPluginHost:
    def get_llm_service(self): ...
    def get_context_builder(self): ...
    def get_background_task_service(self): ...
    def get_novel_reader(self): ...
    def get_chapter_reader(self): ...
    def emit_event(self, name, payload): ...
    def get_plugin_runtime_state(self): ...
```

#### 原则
- 插件依赖 `host facade`
- 不依赖宿主内部文件组织
- 避免日后 upstream 变动导致插件 import 图谱整体崩裂

### 5.9 UI/FAB 只作为入口层，不承载核心业务逻辑
ST-EWA 的一个重要经验是：

> FAB / 面板 / notice 很重要，但它们只是“入口层”，不应承载核心状态更新逻辑。

因此 PlotPilot 这边也应明确：
- `inject.js` 负责挂 UI
- runtime / scheduler / service 才负责业务
- 面板点击“重建”只是触发 job，不直接写状态

这样后续就能：
- 替换 UI 不伤业务
- 增加命令式入口 / API 入口 / 定时入口
- 在无前端交互时也能后台跑任务

---

## 六、核心实体设计

以下是建议的核心实体（Entity）集合。

### 6.1 NovelScope（小说作用域）
表示插件数据归属的小说空间。

#### 字段
- `novel_id`
- `novel_title`
- `plugin_name`
- `schema_version`
- `runtime_capability_version`
- `last_synced_chapter`
- `last_snapshot_at`

#### 作用
- 作为所有 sidecar 数据的顶层作用域
- 隔离不同小说的数据
- 记录插件同步进度
- 标识当前数据使用的是哪一版 runtime / schema 协议

---

### 6.2 ChapterFactSnapshot（章节事实快照）
表示从某一章提取出的结构化事实快照。

#### 字段
- `id`
- `novel_id`
- `chapter_id`
- `chapter_number`
- `title`
- `content_hash`
- `source_type`（generated / manual_save / rewrite / rebuild）
- `trigger_type`（after_generation / after_commit / manual_rebuild）
- `source_job_id`
- `version`
- `supersedes_snapshot_id`
- `extracted_at`
- `facts_json`
- `summary`

#### facts_json 建议内容
- 出场人物
- 人物动作
- 场景地点
- 势力变化
- 事件结果
- 显式关系变化
- 暗线 / 线索变化（可选）
- 可确认事实 / 推测事实分层

#### 作用
- 作为一切角色状态和世界状态更新的输入源
- 保证插件不是直接反复扫原始正文，而是基于“章节事实快照”推进
- 为回滚 / 重放 / 重建提供版本基础

---

### 6.3 CharacterCard（角色主卡）
表示角色的稳定身份卡。

#### 字段
- `id`
- `novel_id`
- `character_id`
- `name`
- `aliases_json`
- `gender`
- `age_hint`
- `identity_summary`
- `personality_summary`
- `title_summary`
- `default_affiliation`
- `is_major`
- `first_seen_chapter`
- `last_seen_chapter`
- `status`
- `updated_at`

#### 作用
- 维护角色相对稳定的信息
- 作为时间线和状态线程的归属锚点

---

### 6.4 CharacterStateSnapshot（角色状态快照）
表示角色在某章时点的状态。

#### 字段
- `id`
- `novel_id`
- `character_card_id`
- `chapter_id`
- `chapter_number`
- `source_job_id`
- `content_hash`
- `version`
- `supersedes_snapshot_id`
- `location`
- `physical_state`
- `mental_state`
- `goal`
- `current_action`
- `relationship_summary`
- `power_status`
- `equipment_summary`
- `status_summary`
- `confidence`
- `created_at`

#### 作用
- 回答“这个角色在第 N 章时是什么状态”
- 支撑按章节查看角色卡
- 支撑写下一章时角色状态注入
- 支撑版本回溯和重放

---

### 6.5 CharacterActivityThread（角色活动线程）
表示角色最近连续在做什么。

#### 字段
- `id`
- `novel_id`
- `character_card_id`
- `thread_status`（active / paused / resolved / unknown）
- `current_objective`
- `current_location`
- `current_activity`
- `latest_progress`
- `last_seen_chapter`
- `related_chapter_id`
- `confidence`
- `updated_at`

#### 作用
- 回答“这个角色现在正在做什么”
- 比离散快照更适合前端高频展示
- 可作为生成下一章时的轻量上下文

---

### 6.6 CharacterRelationshipEdge（角色关系边）
表示两个角色之间的动态关系。

#### 字段
- `id`
- `novel_id`
- `source_character_id`
- `target_character_id`
- `relation_type`
- `relation_summary`
- `intensity`
- `trust_level`
- `hostility_level`
- `last_changed_chapter`
- `evidence`
- `updated_at`

#### 作用
- 支撑角色关系变化
- 给角色卡展示“当前与谁关系如何”
- 支撑后续关系网可视化

---

### 6.7 FactionState（势力状态）
表示某个势力在当前时间点的摘要状态。

#### 字段
- `id`
- `novel_id`
- `name`
- `type`
- `leader`
- `base_region`
- `status_summary`
- `objective`
- `resource_summary`
- `threat_level`
- `last_changed_chapter`
- `updated_at`

#### 作用
- 作为世界演化核心插件的基础实体之一

---

### 6.8 RegionState（地区状态）
表示某地区在当前时间点的状态。

#### 字段
- `id`
- `novel_id`
- `name`
- `region_type`
- `controller`
- `risk_level`
- `activity_level`
- `status_summary`
- `last_changed_chapter`
- `updated_at`

#### 作用
- 描述地区热度、控制权、危险度等

---

### 6.9 WorldEvent（世界事件）
表示章节驱动产生的世界变化事件。

#### 字段
- `id`
- `novel_id`
- `chapter_id`
- `chapter_number`
- `event_type`
- `subject_type`
- `subject_id`
- `region_id`
- `faction_id`
- `importance`
- `visibility`
- `summary`
- `evidence`
- `source_job_id`
- `created_at`

#### 作用
- 记录这一章后“世界发生了什么”
- 给世界动态时间线与状态快照提供依据

---

### 6.10 WorldStateSnapshot（世界状态快照）
表示某章结束后的世界摘要状态。

#### 字段
- `id`
- `novel_id`
- `chapter_id`
- `chapter_number`
- `snapshot_type`（global / faction / region / timeline）
- `scope_key`
- `source_job_id`
- `content_hash`
- `version`
- `supersedes_snapshot_id`
- `summary`
- `state_json`
- `created_at`

#### 作用
- 回答“当前世界到这一章时是什么样”
- 给下一章上下文注入提供结构化摘要
- 支撑世界状态的回滚、对比、重建

---

### 6.11 PluginJobRecord（插件任务记录）
表示插件异步处理任务。

#### 字段
- `id`
- `plugin_name`
- `novel_id`
- `chapter_id`
- `chapter_number`
- `job_type`
- `trigger_type`
- `timing`（before_context_build / after_generation / after_commit / manual_rebuild）
- `dedup_key`
- `status`
- `payload_json`
- `result_summary`
- `error_message`
- `started_at`
- `finished_at`

#### 作用
- 防止重复处理
- 方便失败重跑
- 给前端显示任务状态
- 承担平台级幂等、重放、回滚追溯锚点

#### 设计说明
这个实体非常重要，尤其是后面要做：
- 自动处理
- 手动补跑
- 时间线重建
- 状态回滚
- 插件健康诊断

---

## 七、实体关系设计

### 7.1 主关系链路
建议关系如下：

```text
NovelScope
 ├── ChapterFactSnapshot
 ├── CharacterCard
 │    ├── CharacterStateSnapshot
 │    ├── CharacterActivityThread
 │    └── CharacterRelationshipEdge
 ├── FactionState
 ├── RegionState
 ├── WorldEvent
 ├── WorldStateSnapshot
 └── PluginJobRecord
```

### 7.2 核心数据流关系

```text
章节正文
→ ChapterFactSnapshot
→ CharacterStateSnapshot / CharacterActivityThread / CharacterRelationshipEdge
→ WorldEvent / FactionState / RegionState
→ WorldStateSnapshot
→ ContextBuilder 注入摘要
```

这条链一定要明确：

**正文不是直接改角色卡，而是先经过事实快照层。**

### 7.3 平台与插件的关系链

```text
PlotPilot Host
→ Plugin Runtime
→ Hook Dispatcher
→ Plugin Service / Scheduler
→ Sidecar Repository
→ Snapshot / State / Job Records
→ UI / API / Context Injection
```

这条链也必须明确：

**UI 和 API 都只是消费层，核心状态变化必须先经过 runtime 与 hook dispatcher。**

---

## 八、生命周期设计

### 8.1 触发时机
插件应支持四类触发：

#### A. before_context_build
生成下一章前：
- 读取角色当前态
- 读取世界状态快照
- 读取最近相关章节事实
- 组装轻量注入上下文
- 做注入去重守卫

#### B. after_generation
章节生成完成后：
- 读取生成结果
- 生成临时 `ChapterFactSnapshot`
- 记录生成轮次分析结果
- 为后续 commit 决策提供候选状态

#### C. after_commit
章节确认保存为剧情事实后：
- 提取正式章节事实
- 更新角色状态
- 更新世界状态
- 写入 `PluginJobRecord`
- 刷新 sidecar current snapshot

#### D. manual_rebuild
用户主动触发：
- 重建某章角色状态
- 重建最近 N 章时间线
- 刷新某角色卡
- 重算世界状态
- 局部回滚并重放

### 8.2 生命周期步骤

#### 角色卡插件链路
1. 接收 `chapter:committed`
2. 读取章节正文 / 已有事实快照
3. 生成或更新 `ChapterFactSnapshot`
4. 识别角色实体
5. 更新 `CharacterCard`
6. 写入 `CharacterStateSnapshot`
7. 更新 `CharacterActivityThread`
8. 如有关系变化则更新 `CharacterRelationshipEdge`
9. 记录 `PluginJobRecord`

#### 世界演化插件链路
1. 接收 `chapter:committed`
2. 读取章节事实快照
3. 识别世界事件
4. 写入 `WorldEvent`
5. 更新 `FactionState`
6. 更新 `RegionState`
7. 写入 `WorldStateSnapshot`
8. 记录 `PluginJobRecord`

#### 生成前注入链路
1. 接收 `before_context_build`
2. 拉取相关 `CharacterActivityThread`
3. 拉取最近 `WorldStateSnapshot`
4. 生成结构化摘要块
5. 通过注入守卫判断是否已注入过
6. 将摘要交给 `ContextBuilder` 拼装

### 8.3 生命周期清理与卸载要求
适配复杂 runtime 时，前端插件必须支持：
- 页面切换时释放监听器
- 插件 reload 时先 dispose 再 init
- 停用插件时撤销 FAB / 面板 / 订阅器
- 避免多次 mount 导致重复监听与重复任务

这部分应由 runtime 统一托管，而不是让每个插件自行赌运气处理。

---

## 九、约束系统设计

### 9.1 角色约束
角色状态更新必须满足：
- 不新增正文未出现的重要行动
- 不篡改已写事实
- 不把推测写成确定结论
- 若证据不足，允许 `confidence` 降低

### 9.2 世界约束
世界演化必须满足：
- 只根据章节结果推进
- 不抢主线
- 不做无依据的大跳变
- 不让未登场势力突然剧烈变化而无事件证据

### 9.3 插件运行约束
- 同一章节不可重复注入同一批摘要
- 同一章节重复提交不可重复推进实体状态
- 重写章节时若剧情未改，只允许做轻量刷新或跳过
- 同一 scope 同时仅允许一个 in-flight 状态推进任务
- manual_rebuild 默认优先级高于 auto 任务

这里建议继续沿用和仿生记忆相似的 **去重拦截 / 哈希守卫** 思路。

---

## 十、前端展示实体映射

### 10.1 动态角色卡 UI 对应实体

#### 角色列表页
使用：
- `CharacterCard`
- `CharacterActivityThread`

#### 单角色详情页
使用：
- `CharacterCard`
- `CharacterStateSnapshot`
- `CharacterRelationshipEdge`

#### 按章节查看角色状态
使用：
- `CharacterStateSnapshot`

#### “角色正在做什么”面板
使用：
- `CharacterActivityThread`

### 10.2 世界演化 UI 对应实体

#### 世界总览页
使用：
- `WorldStateSnapshot(snapshot_type=global)`
- `WorldEvent`

#### 势力页
使用：
- `FactionState`
- `WorldEvent`

#### 地区页
使用：
- `RegionState`
- `WorldEvent`

#### 最近变化时间线
使用：
- `WorldEvent`
- `WorldStateSnapshot`

### 10.3 FAB / 面板 / 通知层的边界
用于：
- 打开插件面板
- 查看任务状态
- 手动触发补跑 / 重建
- 呈现 warning / error / success notice

不用于：
- 直接改写核心 sidecar 状态
- 绕过 runtime 直接做业务更新

---

## 十一、接口设计建议

### 11.1 动态角色卡插件接口
建议最小接口：
- `GET /api/v1/plugins/rolecard/novels/{novel_id}/characters`
- `GET /api/v1/plugins/rolecard/novels/{novel_id}/characters/{character_id}`
- `GET /api/v1/plugins/rolecard/novels/{novel_id}/characters/{character_id}/timeline`
- `POST /api/v1/plugins/rolecard/novels/{novel_id}/rebuild`

### 11.2 世界演化插件接口
建议最小接口：
- `GET /api/v1/plugins/world-evolution/novels/{novel_id}/overview`
- `GET /api/v1/plugins/world-evolution/novels/{novel_id}/events`
- `GET /api/v1/plugins/world-evolution/novels/{novel_id}/factions`
- `GET /api/v1/plugins/world-evolution/novels/{novel_id}/regions`
- `POST /api/v1/plugins/world-evolution/novels/{novel_id}/rebuild`

### 11.3 插件状态接口
建议统一补齐：
- `GET /api/v1/plugins/{plugin_name}/status`
- `GET /api/v1/plugins/{plugin_name}/jobs`
- `POST /api/v1/plugins/{plugin_name}/jobs/{job_id}/rerun`
- `POST /api/v1/plugins/{plugin_name}/rollback`

用于展示：
- sidecar DB 状态
- 最近任务
- 最近错误
- 最近同步章节
- 前端面板是否可加载
- 当前是否存在 in-flight job

### 11.4 宿主服务桥接口建议
建议平台补统一 host facade：
- `get_llm_service()`
- `get_context_builder()`
- `get_background_task_service()`
- `get_novel_reader()`
- `get_chapter_reader()`
- `emit_event(name, payload)`
- `get_runtime_state()`

---

## 十二、前端宿主桥与事件协议建议

### 12.1 当前已确认的宿主事件面
本地平台已具备或已补齐的关键事件：
- `route:changed`
- `workbench:opened`
- `novel:selected`
- `chapter:loaded`
- `chapter:saved`
- `chapter:committed`
- `generation:completed`
- `rewrite:completed`
- `manual:rerun_requested`
- `timeline:rebuild_requested`

### 12.2 建议继续标准化补齐的事件
为了完整适配状态型插件，建议继续补：
- `generation:started`
- `rewrite:started`
- `context:before_build`
- `context:after_build`
- `plugin:job_started`
- `plugin:job_completed`
- `plugin:job_failed`
- `plugin:status_changed`

### 12.3 建议统一事件 payload 最小字段

```ts
{
  pluginName?,
  novelId,
  chapterId?,
  chapterNumber?,
  requestId?,
  triggerType?,
  source,
  view,
  contentHash?,
  at
}
```

统一字段的好处：
- 方便做 job 去重
- 方便做运行时日志
- 方便前端状态面板
- 方便跨插件协同

---

## 十三、推荐目录结构

### 13.1 动态角色卡插件

```text
plugins/dynamic_rolecard/
├── __init__.py
├── plugin.json
├── core.py
├── models.py
├── repositories.py
├── extractor.py
├── service.py
├── injector.py
├── routes.py
├── scheduler.py
├── runtime_contract.py
├── static/
│   └── inject.js
└── DYNAMIC_ROLECARD_ENTITY_DESIGN.md
```

### 13.2 世界演化核心插件

```text
plugins/world_evolution_core/
├── __init__.py
├── plugin.json
├── core.py
├── models.py
├── repositories.py
├── extractor.py
├── evolution_engine.py
├── rules_engine.py
├── service.py
├── injector.py
├── routes.py
├── scheduler.py
├── runtime_contract.py
├── static/
│   └── inject.js
└── WORLD_EVOLUTION_ENTITY_DESIGN.md
```

### 13.3 建议新增的平台公共层

```text
plugins/platform/
├── host_facade.py
├── runtime_types.py
├── hook_dispatcher.py
├── job_dedup.py
├── job_replay.py
└── plugin_status.py
```

说明：
- 这不是把业务逻辑上收到宿主核心层
- 而是把“插件平台公共能力”集中到插件平台自身
- 这样能避免每个复杂插件都复制一套 runtime / job / dedup / replay 基础设施

---

## 十四、分阶段落地建议

### Phase 1：角色实体 + runtime contract 最小闭环
只做：
- `ChapterFactSnapshot`
- `CharacterCard`
- `CharacterStateSnapshot`
- `CharacterActivityThread`
- `PluginJobRecord`
- 前端插件 lifecycle contract
- 最小 hook contract
- 最小查询接口
- 最小角色面板

这是最稳的一期。

### Phase 2：世界层与补跑能力
增加：
- `CharacterRelationshipEdge`
- `WorldEvent`
- `FactionState`
- `RegionState`
- `WorldStateSnapshot`
- 手动补跑
- 时间线重建
- 状态去重与回滚

### Phase 3：平台公共层与跨插件协同
增加：
- 插件状态中心
- 插件任务队列管理
- 插件 runtime 健康检查
- 插件间事件协同
- 与仿生记忆摘要协同注入

---

## 十五、最终结论

这份优化后的“实体设计稿”已经不再只是“表怎么建”的文档，而是把下面三件事统一了：

### 15.1 插件本体实体怎么设计
- `CharacterCard`
- `CharacterStateSnapshot`
- `CharacterActivityThread`
- `CharacterRelationshipEdge`
- `WorldEvent`
- `FactionState`
- `RegionState`
- `WorldStateSnapshot`
- `ChapterFactSnapshot`
- `PluginJobRecord`
- `NovelScope`

### 15.2 PlotPilot 本地插件平台为了适配 ST-Evolution-World-Assistant 要补什么
- plugin manifest 能力声明
- 前端插件 lifecycle contract
- 统一 hook 层
- workflow family 抽象
- dedup / replay / rollback 语义
- host facade
- 插件状态接口与任务接口

### 15.3 为什么这些改动是必要的
因为对动态角色卡 / 世界演化 / 仿生记忆协同这类插件来说：

**决定成败的不是“能不能把脚本注进去”，而是宿主有没有能力稳定承载“持续状态 + 生成前后 hook + 去重 + 回滚 + 补跑”。**

如果只做到 loader，不补 runtime / hook / dedup / replay，后面一定会遇到：
- 重复推进
- 重复注入
- 面板能看但状态不可信
- 手动补跑和自动流程互相打架
- 上游一改页面结构插件就碎

所以推荐的正确顺序是：

1. **先把插件平台升级成真正的运行时平台**
2. **再落动态角色卡最小闭环**
3. **最后扩到世界演化与跨插件协同**
