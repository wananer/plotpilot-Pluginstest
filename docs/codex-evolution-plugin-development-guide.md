# PlotPilot Evolution Codex 提示词与 Skill 说明

本说明是人读版索引；执行时的项目专属规则以 skill 为准：

- Skill: `.agents/skills/plotpilot-evolution-dev/SKILL.md`
- 详细闭环: `.agents/skills/plotpilot-evolution-dev/references/development-loop.md`

以后凡是开发 PlotPilot 的 Evolution 相关能力，包括 `world_evolution_core`、插件平台 hook、Prompt Plaza 里的 Evolution prompt、上下文注入、章节审查、压力测试、Codex 提示词/skill 编排，都应先使用 `plotpilot-evolution-dev`。

## 适用范围

使用该 skill 的场景：

- 修改 `plugins/world_evolution_core/**`
- 修改 `plugins/platform/**` 或 `plugins/loader.py`
- 修改 Evolution prompt、Prompt Plaza 注册或 prompt resolver 相关逻辑
- 修改 Evolution 前端注入脚本、状态面板、诊断面板
- 修改 Evolution smoke/pressure/evaluation 脚本
- 设计 Codex Evolution 插件、Codex skill、Codex 角色提示词编排
- 审查、验证、发布 Evolution 相关改动

不适用：

- 与 Evolution 无关的普通功能开发
- 纯业务文本内容编辑
- 无需理解本项目 Evolution 链路的小型样式或拼写修复

## Codex Prompt 路由

| 阶段 | 主提示词 | 辅助提示词 | 产物 |
| --- | --- | --- | --- |
| 需求澄清 | `analyst` | `planner` | 目标、边界、验收标准 |
| 规划 | `planner` | `architect`, `critic` | 计划、ADR、测试策略 |
| 实现 | `executor` | `debugger`, `build-fixer` | 最小可验证 diff |
| 测试 | `test-engineer` | `verifier` | 测试规格和结果 |
| 审查 | `code-reviewer` | `security-reviewer`, `code-simplifier` | findings 和修复建议 |
| 完成证明 | `verifier` | `writer` | PASS/FAIL/PARTIAL 与证据 |
| 发布 | `git-master` | `writer`, `github:yeet` | commit/PR/release notes |

## 开发闭环

Evolution 开发必须走闭环：

```text
需求进入
-> 上下文摄取
-> Codex prompt 选型
-> 共识规划
-> 测试规格
-> 实现
-> 本地验证
-> 审查
-> 完成验证
-> 记忆沉淀
-> 提交/PR/发布
-> 反馈回流
```

关键门禁：

- 没有计划，不做多文件 Evolution 改动。
- 没有测试规格，不改行为。
- 没有新鲜验证输出，不声明完成。
- 触及权限、token、路径、SQL、外部命令、MCP 或 hook 边界时，必须加安全审查。
- 非显而易见的决策要沉淀到文档、`.omx/notepad.md` 或 Lore commit trailer。

## 推荐验证命令

按改动范围选择最小证明集：

```bash
.venv/bin/python -m pytest tests/test_plugin_platform_runtime.py tests/test_evolution_world_service.py -q
.venv/bin/python -m pytest tests/unit/infrastructure/ai/test_prompt_manager_registry.py -q
.venv/bin/python scripts/evaluation/evolution_smoke_test.py
cd frontend && npm run build
```

触及共享平台、Prompt Plaza、生成链路或前端 runtime 时，扩大到相关集成测试或完整测试。

## 维护规则

- `SKILL.md` 保持短而可执行。
- 详细流程放在 `references/development-loop.md`。
- 说明书只做索引，不复制完整 skill 内容，避免漂移。
- 更新 Evolution 开发流程时，先改 skill，再同步本说明。
