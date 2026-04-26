# PlotPilot 插件宿主审计报告

> 目标：在保持 PlotPilot 统一插件平台可用的前提下，将非插件代码尽量收敛到 upstream 风格，并明确区分“必须保留的插件宿主接入点”与“可继续清理的历史偏离”。

## 1. 审计结论（当前可直接采用）

当前仓库已经具备 **PlotPilot 原生插件平台最小闭环**，并且宿主层差异已基本收敛到可解释范围。

### 1.1 当前应保留的最小插件宿主接入点
以下文件属于插件平台的 **最小宿主接入面**，应视为“必留”：

- `plugins/loader.py`
- `plugins/**`
- `interfaces/main.py`
- `scripts/start_daemon.py`
- `frontend/index.html`
- `frontend/public/plugin-loader.js`
- `frontend/vite.config.ts`

这些文件共同承担以下职责：
- 后端 API 进程预加载插件
- Daemon 进程预加载插件
- 暴露插件清单/清单清单接口
- 前端统一加载 `plugin-loader.js`
- 代理 `/plugins` 静态资源

### 1.2 当前不应再视为“历史脏改”的项
`frontend/vite.config.ts` 目前已经从“硬编码本地环境偏离”收敛为“**可配置的环境适配层**”：

- 默认值回到 upstream 风格：
  - 前端端口默认 `3000`
  - `/api` 默认代理到 `http://127.0.0.1:8005`
- 插件平台必需能力保留：
  - `/plugins` 代理保留
- 本地开发环境通过环境变量覆盖：
  - `PLOTPILOT_FRONTEND_PORT`
  - `PLOTPILOT_API_TARGET`
  - `PLOTPILOT_PLUGIN_TARGET`

因此，该文件当前更准确的分类是：

- **插件平台必需接入**：`/plugins` 代理
- **环境适配漂移**：端口和 `/api` 目标通过环境变量覆盖

而不是“不可控的历史宿主污染”。

## 2. 本次实查到的宿主链状态

### 2.1 后端 API 宿主接入
在 `interfaces/main.py` 中已确认：

- 引入 `init_api_plugins`
- 引入 `create_plugin_manifest_router`
- 存在 `init_api(app)` 统一入口
- 通过 `app.include_router(create_plugin_manifest_router(), prefix="/api/v1")` 注册插件清单接口

这说明：
- API 进程的插件预加载链已存在
- 插件清单接口已归口到统一 loader，而不是散落在单个业务插件中

### 2.2 Daemon 宿主接入
在 `scripts/start_daemon.py` 中已确认：

- 引入 `init_daemon_plugins`
- 在 daemon 业务导入前调用插件初始化

这说明：
- 多进程场景下 daemon 侧插件注入链已存在
- 插件不会只在主 API 进程生效而在 daemon 进程失效

### 2.3 前端统一加载入口
在 `frontend/index.html` 中已确认：

- 存在 `/plugin-loader.js` 注入：
  - `<script src="/plugin-loader.js"></script>`

这说明：
- 前端已从“每个插件手工写死 inject.js”收敛到“统一前端宿主加载器”模式

### 2.4 前端插件运行时
在 `frontend/public/plugin-loader.js` 中已确认：

- 存在全局运行时 `window.PlotPilotPlugins`
- 存在清单接口：`/api/v1/plugins/manifest`
- 存在插件列表接口：`/api/v1/plugins`
- 存在 `manifest` / `pluginsPayload` 运行时状态
- 存在 `plugins:loaded` / `manifest:loaded` 相关事件

这说明：
- 仓库已不是“只有一个业务插件脚本硬插入”的状态
- 已有一个基础可扩展的前端插件宿主运行时

### 2.5 Vite 宿主适配
在 `frontend/vite.config.ts` 中已确认：

- `/plugins` 代理保留
- `/api` 代理保留
- 默认值回归 upstream 风格
- 本地端口/后端地址支持环境变量覆盖
- 已修复 ESM 场景下 `__dirname` 兼容问题：
  - 使用 `fileURLToPath(import.meta.url)` + `dirname(...)`

## 3. 已补上的回归保护

为避免 `vite.config.ts` 以后再次被改回硬编码本地值，已新增测试：

- `frontend/tests/vite.config.test.mjs`

当前测试覆盖：

### 3.1 默认值保护
断言默认加载时：
- `port === 3000`
- `/api -> http://127.0.0.1:8005`
- `/plugins -> http://127.0.0.1:8005`

### 3.2 本地环境覆盖保护
断言设置以下环境变量后：
- `PLOTPILOT_FRONTEND_PORT=3001`
- `PLOTPILOT_API_TARGET=http://127.0.0.1:3000`
- `PLOTPILOT_PLUGIN_TARGET=http://127.0.0.1:3000`

配置会正确切换到本地开发模式。

### 3.3 已实际踩出的兼容问题
在做 TDD 时，测试先红出一个真实问题：

- `vite.config.ts` 在 ESM 下直接用 `__dirname`
- `node --test` 加载时报错：`__dirname is not defined in ES module scope`

此问题已修复，因此本次测试不仅是“补文档式测试”，还顺手解决了一个真实配置兼容问题。

## 4. 当前推荐的差异分类

为了后续继续清理与对齐 upstream，建议把当前仓库差异分成三类看：

### A. 最小插件宿主接入点（必须保留）
- `plugins/**`
- `plugins/loader.py`
- `interfaces/main.py`
- `scripts/start_daemon.py`
- `frontend/index.html`
- `frontend/public/plugin-loader.js`
- `frontend/vite.config.ts` 中与 `/plugins` 代理相关的部分

### B. 环境适配差异（可保留，但应单独标注）
- `frontend/vite.config.ts` 中：
  - `PLOTPILOT_FRONTEND_PORT`
  - `PLOTPILOT_API_TARGET`
  - `PLOTPILOT_PLUGIN_TARGET`

这类差异不是插件平台架构污染，而是为了兼容本地开发端口分配的环境适配层。

### C. 仍需持续审计的历史业务残留（本报告未宣称已全部清零）
本次审计重点确认的是 **插件宿主主链**，不是全仓库所有业务逻辑都已完全回退到 upstream。

因此，以下结论当前 **不能过度宣称**：

- 不能仅因插件宿主链已绿，就认定整个仓库已经完全纯净
- 不能仅因 `plugin-loader.js` 与 loader 已就位，就认定全部历史业务自定义都已迁完

更准确的说法应是：

> 当前“插件平台最小闭环”已成立；但全仓库范围内是否仍存在历史业务残留，仍需继续按宿主文件与业务文件分层审计。

## 5. 后续建议的清理顺序

建议后续继续按下面顺序推进，而不是无差别大改：

1. **先守住当前最小插件宿主链**
   - 不要再把插件接入逻辑散回业务文件
2. **继续做宿主差异审计**
   - 优先检查是否还有超出最小接入面的宿主改动
3. **再审历史业务自定义残留**
   - 按“能迁入 plugins/ 就迁入 plugins/”的原则逐项处理
4. **最后再做 upstream 对齐说明**
   - 明确哪些差异是必留
   - 哪些差异是环境适配
   - 哪些差异是应迁移/应回退

## 6. 当前一句话状态（可用于汇报）

当前 PlotPilot 仓库已经具备统一插件平台的最小宿主闭环；宿主层差异已基本收敛到插件接口接入点与少量可配置环境适配，其中 `vite.config.ts` 已被回归测试锁住，不再属于不可控的历史脏改。
