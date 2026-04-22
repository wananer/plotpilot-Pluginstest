# PlotPilot Plugin Platform Installer

这个仓库当前已带上 **PlotPilot 最小插件平台宿主接入层**，目标是：

- 后续自定义功能尽量都放进 `plugins/`
- 宿主只保留极少量、可审查、可重复安装的插件平台接入点
- 方便和 upstream 保持同步，降低每次 `git pull` 后的冲突面

## 包含内容

最小宿主接入点包括：

- `plugins/loader.py`：后端插件发现 / manifest / 静态资源挂载 / daemon hook
- `frontend/public/plugin-loader.js`：前端插件运行时与脚本加载器
- `frontend/index.html`：注入 `plugin-loader.js`
- `frontend/vite.config.ts`：开发态代理 `/plugins`
- `interfaces/main.py`：注册插件 manifest API、启动时加载 API 插件
- `scripts/start_daemon.py`：守护进程启动时加载 daemon 插件
- `scripts/install_plugin_platform.py`：一键补齐以上最小接入点

## 一键安装

在一个 **干净的 PlotPilot 上游仓库** 中执行：

```bash
python scripts/install_plugin_platform.py
```

安装器会：

1. 复制插件 loader 与前端 runtime 文件
2. 自动给 `frontend/index.html` 注入 `<script src="/plugin-loader.js"></script>`
3. 自动给 Vite 增加 `/plugins` 代理
4. 自动给 `interfaces/main.py` 注入 manifest router 与 `init_api_plugins(app)`
5. 自动给 `scripts/start_daemon.py` 注入 `init_daemon_plugins()`

## 安装后验证

### 1) 后端

```bash
uvicorn interfaces.main:app --host 127.0.0.1 --port 8005 --reload
```

验证：

```bash
curl http://127.0.0.1:8005/api/v1/plugins/manifest
curl http://127.0.0.1:8005/api/v1/plugins
```

### 2) 前端

```bash
cd frontend
npm run dev
```

浏览器打开前端后，检查：

- 能正常请求 `/plugin-loader.js`
- 能正常请求 `/api/v1/plugins/manifest`
- 若某插件有 `static/inject.js`，会被自动拉起

## 约束原则

这个平台只做 **宿主最小接入**，不承载业务逻辑：

- 业务 API / 业务数据 / 业务 UI 尽量在 `plugins/<name>/` 内实现
- 宿主层只保留“加载、挂载、代理、分发”
- 若某历史自定义能力还在宿主代码里，应优先评估迁入插件

## 建议目录结构

```text
plugins/
  your_plugin/
    __init__.py
    plugin.json
    routes.py
    static/
      inject.js
```

最小示例：

```python
# plugins/your_plugin/__init__.py

def init_api(app):
    pass


def init_daemon():
    pass
```

```json
{
  "name": "your_plugin",
  "display_name": "Your Plugin",
  "version": "0.1.0",
  "enabled": true,
  "frontend": {
    "scripts": ["static/inject.js"]
  }
}
```

## 自测

已提供安装器回归测试：

```bash
pytest tests/unit/scripts/test_install_plugin_platform.py -q
```

目标：确保安装器可重复执行、不会重复注入、关键接入点存在。
