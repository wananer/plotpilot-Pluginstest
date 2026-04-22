# Host Touchpoints

插件平台要求宿主只保留以下最小接入点：

1. `interfaces/main.py`
   - `from plugins.loader import init_api_plugins, create_plugin_manifest_router`
   - `init_api(app)`
   - `/api/v1/plugins/manifest` 路由
2. `scripts/start_daemon.py`
   - `from plugins.loader import init_daemon_plugins`
   - 启动早期调用 `loaded_plugins = init_daemon_plugins()`
3. `frontend/index.html`
   - `<script src="/plugin-loader.js"></script>`
4. `frontend/vite.config.ts`
   - `/plugins` 代理
5. `frontend/public/plugin-loader.js`
6. `plugins/loader.py`

除此之外，业务功能应尽量迁入独立插件目录，而不是继续散落在宿主。
