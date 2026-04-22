# PlotPilot Plugins Platform

可独立分发的 PlotPilot 插件平台最小闭环。

包含：
- `platform/scripts/install_plugin_platform.py`：把插件平台最小接入点补丁打到一份新的 PlotPilot 仓库
- `platform/plugins/loader.py`：后端插件发现 / manifest / static mount / API & daemon 初始化
- `platform/frontend/public/plugin-loader.js`：前端 runtime / manifest 拉取 / 插件脚本注入 / host 事件分发
- `tests/`：最小回归测试

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

## 当前定位

这是**插件平台骨架仓库**，不是业务插件全集。
业务插件（如 bionic_memory）后续可作为独立插件仓库继续拆分。
