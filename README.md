# PlotPilot Plugins Platform

可独立分发的 PlotPilot 插件平台最小闭环，用于把 **插件发现、前端 runtime、宿主接入补丁、契约测试** 从具体业务插件中分离出来，形成一个可复用的平台骨架。

## 仓库包含内容

- `platform/scripts/install_plugin_platform.py`
  - 把插件平台最小接入点补丁打到一份新的 PlotPilot 宿主仓库
- `platform/plugins/loader.py`
  - 后端插件发现 / manifest 解析 / API & daemon 初始化 / manifest list 路由
- `platform/frontend/public/plugin-loader.js`
  - 前端 runtime / manifest 拉取 / 插件脚本注入 / host 事件分发
- `plugins/example_plugin/`
  - 一个最小可运行示例插件，演示 `plugin.json` + `__init__.py` + `static/inject.js`
- `tests/`
  - 最小回归测试（仅依赖当前仓库内容，可直接在仓库根目录执行 `pytest`）
- `docs/HOST_TOUCHPOINTS.md`
  - 宿主最小接入点说明
- `docs/PLUGIN_DOCS_INDEX.md`
  - 插件平台文档总入口
- `PURITY_REPORT.md`
  - 当前仓库纯净度审计结论

## 适用场景

适合你要做这些事时使用：

- 给 PlotPilot 建立统一插件接口/加载器
- 把宿主里的自定义能力逐步迁入 `plugins/`
- 让后续自定义开发走统一插件入口，而不是继续散落在宿主代码里
- 为外部业务插件仓库提供稳定的宿主接入协议

## 安装到 PlotPilot 宿主仓库

```bash
python3 platform/scripts/install_plugin_platform.py /path/to/PlotPilot
```

补丁会自动处理：
- `interfaces/main.py`：接入 `init_api_plugins` + manifest 路由
- `scripts/start_daemon.py`：接入 `init_daemon_plugins`
- `frontend/index.html`：注入 `/plugin-loader.js`
- `frontend/vite.config.ts`：补 `/plugins` 代理
- 复制 `plugin-loader.js` 与 `plugins/loader.py`

## 快速验证

```bash
pytest
```

当前仓库验证目标：
- fresh clone 后无需依赖外部 PlotPilot 主仓库文件
- 仓库根目录可直接运行测试
- 平台 loader、manifest、静态资源、hook 与前端 runtime 能通过 `example_plugin` 验证

## 示例插件

仓库自带一个最小示例插件：`plugins/example_plugin/`

包含：
- `__init__.py`：演示 `init_api(app)` / `init_daemon()`
- `plugin.json`：演示最小 manifest 写法
- `static/inject.js`：演示如何接入 `window.PlotPilotPlugins` runtime、注册插件、监听宿主事件

如果你要新写插件，最简单的起点就是直接复制这个目录，再改成自己的名字。

## 外部业务插件

复杂业务插件应放在独立插件仓库中维护。Evolution World Assistant 对应仓库：

- [wananer/pp-Evolution-World-Assistant](https://github.com/wananer/pp-Evolution-World-Assistant)

平台仓库只定义宿主协议、loader、runtime、installer 与平台测试。业务插件通过平台协议安装到宿主，不作为平台仓库内置代码提交。

## 仓库边界

这是**插件平台骨架仓库**，不是业务插件全集。

- 允许：平台 loader / runtime / installer / 平台测试 / 平台文档
- 不允许混入：`world_evolution_core`、`bionic_memory`、`rolecard`、`autopilot`、`rewrite`、`novel` 等具体业务实现

业务插件应继续作为**独立插件仓库**演进，而不是回灌到平台仓库主体。

## 数据边界

- PlotPilot 原有数据库只通过 `ReadOnlyHostDatabase` 暴露给插件平台，连接使用 SQLite read-only mode，并且只接受 `SELECT` / `WITH` 查询。
- 插件平台自己的读写区域是宿主数据目录下的 `plugin_platform/`，插件状态、任务日志、派生数据都应写入这里。
- 插件不要直接 import 宿主数据库连接或仓储实现；需要宿主数据时通过只读 facade 或明确 hook payload 获取。

## 相关文档

- `docs/PLUGIN_DOCS_INDEX.md`
- `docs/PLUGIN_DEVELOPMENT_GUIDE.md`
- `docs/PLUGIN_MANIFEST_SPEC.md`
- `docs/PLUGIN_RUNTIME_API.md`
- `docs/HOST_TOUCHPOINTS.md`
- `PURITY_REPORT.md`
- `CONTRIBUTING.md`

## License

MIT
