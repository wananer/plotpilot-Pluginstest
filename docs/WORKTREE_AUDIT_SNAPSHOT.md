# Worktree Audit Snapshot

> 审计对象：`/tmp/plotpilot-Pluginstest-sync`
> 分支：`main`
> 远端：`origin = https://github.com/wananer/plotpilot-Pluginstest.git`

## 1. 本次实查结论

当前这个 **plugin platform 独立仓库** 已基本处于“平台主链干净、仅剩少量文档/示例补充未提交”的状态。

### 1.1 已确认的最小平台主链
以下平台主链文件已落地：

- `plugins/loader.py`
- `frontend/public/plugin-loader.js`
- `interfaces/main.py`
- `scripts/start_daemon.py`
- `frontend/index.html`
- `frontend/vite.config.ts`
- `platform/scripts/install_plugin_platform.py`
- `tests/test_plugin_loader_manifest.py`
- `tests/test_plugin_bootstrap_installer.py`

### 1.2 当前 worktree 与 `origin/main` 的关系
实查结果：

- `git diff --name-only origin/main...HEAD` → **空**
- 说明：**本地 HEAD 与远端 main 没有已提交差异**

也就是说：

> 目前 GitHub 上的 `plotpilot-Pluginstest` 主线已经同步到当前最后一次提交状态，
> 不存在“本地有一批已提交但未推送的平台代码”的情况。

## 2. 当前未提交内容分类

### 2.1 已跟踪文件修改
当前 `git diff --name-only` 仅有：

- `README.md`
- `tests/test_plugin_loader_manifest.py`

这类属于：
- 平台文档增强
- 平台测试增强

不属于宿主污染，也不属于业务逻辑回灌。

### 2.2 未跟踪文件
当前 `git ls-files --others --exclude-standard` 为：

- `docs/PLUGIN_DEVELOPMENT_GUIDE.md`
- `docs/PLUGIN_DOCS_INDEX.md`
- `docs/PLUGIN_MANIFEST_SPEC.md`
- `docs/PLUGIN_RUNTIME_API.md`
- `plugins/example_plugin/__init__.py`
- `plugins/example_plugin/plugin.json`
- `plugins/example_plugin/static/inject.js`

这批文件的性质非常明确：

#### A. 文档层新增
- `PLUGIN_DEVELOPMENT_GUIDE.md`
- `PLUGIN_DOCS_INDEX.md`
- `PLUGIN_MANIFEST_SPEC.md`
- `PLUGIN_RUNTIME_API.md`

属于平台说明文档，不是业务代码。

#### B. 示例插件新增
- `plugins/example_plugin/**`

属于平台配套示例，不是 `bionic_memory` / `rolecard` / `rewrite` 之类业务插件残留。

## 3. 关键词残留审计结果

### 3.1 宿主链关键词
以下关键词实查命中在合理平台位置：

- `init_api_plugins`
- `init_daemon_plugins`
- `window.PlotPilotPlugins`

主要落在：
- `interfaces/main.py`
- `scripts/start_daemon.py`
- `plugins/loader.py`
- `frontend/public/plugin-loader.js`
- 平台测试
- 平台文档

这符合“统一插件平台仓库”的预期。

### 3.2 业务关键词残留
关键词：
- `bionic_memory`
- `rolecard`

本次实查主要出现在：
- `README.md`
- `PURITY_REPORT.md`
- 文档中的示例说明

**没有看到业务插件源码被重新混回平台仓库主体。**

换句话说：

> 当前出现的 `bionic_memory` / `rolecard` 更多是“文档举例语义”，不是“架构耦合残留”。

## 4. 宿主最小接入点 vs 非宿主内容

### 4.1 必留的平台/宿主接入点
这些属于平台成立所必需，应保留：

- `plugins/loader.py`
- `frontend/public/plugin-loader.js`
- `interfaces/main.py`
- `scripts/start_daemon.py`
- `frontend/index.html`
- `frontend/vite.config.ts`
- `platform/scripts/install_plugin_platform.py`
- 对应测试文件

### 4.2 当前新增但不属于“宿主脏改”的内容
这些是平台配套资产，可以保留，也可以视发布策略决定是否提交：

- 平台文档：`docs/*.md`
- 示例插件：`plugins/example_plugin/**`
- `README.md` 补充说明

### 4.3 当前没有看到的坏味道
本次没有看到以下问题重新出现：

- 对外部 PlotPilot 主仓绝对路径的测试硬依赖
- 对真实业务插件源码的直接测试依赖
- 将 `bionic_memory` / `rolecard` 业务实现直接塞回平台主体
- 宿主平台代码与业务插件代码重新混堆

## 5. 当前最准确的一句话状态

当前 `plotpilot-Pluginstest` 仓库的 **已提交主线已经同步到 GitHub**；本地剩余未提交内容主要是 **平台文档增强 + example_plugin 示例插件 + 少量 README/测试补充**，不属于历史业务污染回流。

## 6. 建议的下一步

推荐直接做这一步，而不是再反复口头审计：

1. 整理并提交当前未提交内容
   - `docs/*.md`
   - `plugins/example_plugin/**`
   - `README.md`
   - `tests/test_plugin_loader_manifest.py`
2. 跑一次测试确认示例插件与文档配套没有引入回归
3. 再推送到 GitHub

如果继续精简，可把后续工作分成两类：
- **发布增强**：补文档、示例插件、README
- **平台核心**：保持不动，避免无必要重写
