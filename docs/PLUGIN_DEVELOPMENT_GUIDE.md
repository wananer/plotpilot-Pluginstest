# PlotPilot 插件平台开发说明

> 参考 SillyTavern 文档的“语言/扩展开发说明”组织方式编写，但内容严格对应 **PlotPilot 当前插件平台**，不是泛化设计稿。

## 这是什么

PlotPilot 插件平台是一套 **宿主最小接入 + 插件目录约定 + 前后端运行时加载链路**。

它的目标不是把所有功能继续写进 PlotPilot 宿主代码里，而是把后续自定义功能收敛到统一插件接口下：

- 后端通过 `plugins/loader.py` 发现并初始化插件
- 前端通过 `frontend/public/plugin-loader.js` 拉取插件清单并注入脚本
- 宿主只保留少量必要接入点
- 业务插件尽量独立维护，减少与 upstream 冲突

如果你熟悉 SillyTavern，可以把它理解为：

- **我们已经有了基础插件宿主链路**
- **但还不是完整的 SillyTavern 级生态运行时**
- 当前更适合开发 PlotPilot-native 插件，而不是宣称“任意 ST 插件可直接无改运行”

---

## 当前平台提供了什么

当前仓库已经具备这些基础能力：

### 1. 后端插件发现与初始化
文件：`platform/plugins/loader.py`

负责：
- 扫描 `plugins/<name>/`
- 读取插件 manifest（若存在）
- 初始化 API 插件
- 初始化 daemon 插件
- 暴露插件 manifest/list API

### 2. 前端插件运行时加载器
文件：`platform/frontend/public/plugin-loader.js`

负责：
- 请求后端插件 manifest
- 解析插件前端脚本列表
- 按顺序注入 JS
- 维护基础运行时对象
- 分发宿主事件

### 3. 宿主最小接入点
见：`docs/HOST_TOUCHPOINTS.md`

宿主只需要接入：
- `interfaces/main.py`
- `scripts/start_daemon.py`
- `frontend/index.html`
- `frontend/vite.config.ts`
- `frontend/public/plugin-loader.js`
- `plugins/loader.py`

### 4. 自动安装脚本
文件：`platform/scripts/install_plugin_platform.py`

负责把插件平台补丁自动打到一份 PlotPilot 宿主仓库中，避免手工改宿主文件。

---

## 当前不提供什么

这一点要说清楚，避免误判平台成熟度。

当前平台 **不等于** SillyTavern 完整扩展生态。它暂时不默认承诺：

- 任意第三方 ST 插件无改兼容
- 完整 ST 事件总线 API 全量兼容
- 完整 ST 设置系统兼容
- 完整插件市场 / 安装管理 UI
- 完整插件启停管理界面

所以更准确的说法是：

> PlotPilot 当前已经有 **原生插件平台基础闭环**，适合开发和迁移 PlotPilot 自己的 sidecar / memory / prompt / UI 类插件；
> 但还没有达到 SillyTavern 那种成熟的第三方扩展兼容层。

---

## 开发模型总览

一个 PlotPilot 插件通常分成两部分：

1. **后端插件入口**
2. **前端注入脚本**

推荐目录结构：

```text
plugins/<plugin_name>/
├── __init__.py
├── plugin.json            # 可选，但强烈建议
├── routes.py              # 可选：插件自己的 API
├── core.py                # 可选：存储/服务/数据库逻辑
├── service.py             # 可选：后台处理逻辑
└── static/
    ├── inject.js          # 前端入口脚本
    └── style.css          # 可选样式
```

### 设计原则

开发插件时，优先遵守下面四条：

1. **零侵入**
   - 不修改 `domain/`、`application/` 等核心业务层，除非绝对必要
2. **宿主最小接入**
   - 宿主只保留插件加载器和前端运行时入口，不承载具体业务
3. **业务逻辑插件化**
   - 新功能优先写在 `plugins/<name>/` 下
4. **可回退 / 可同步 upstream**
   - 除插件宿主接入点外，其余代码尽量与 upstream 保持一致

---

## 插件清单（manifest）

推荐每个插件提供 `plugin.json`。

一个最小示例：

```json
{
  "name": "example_plugin",
  "display_name": "Example Plugin",
  "version": "0.1.0",
  "enabled": true,
  "frontend": {
    "scripts": [
      "static/inject.js"
    ]
  }
}
```

### 推荐字段

- `name`
  - 插件唯一标识，建议与目录名一致
- `display_name`
  - 人类可读名称
- `version`
  - 插件版本
- `enabled`
  - 是否启用
- `frontend.scripts`
  - 前端脚本列表，按顺序加载

### 路径解析规则

- 相对路径：
  - `static/inject.js`
  - 解析为 `/plugins/<plugin_name>/static/inject.js`
- 绝对路径：
  - `/plugins/shared/runtime.js`
  - 保持原样，不再拼接

### 兼容规则

如果插件没有 `plugin.json`，平台通常应回退到传统约定：

- 如果存在 `static/inject.js`
- 则仍可作为前端脚本入口

这能兼容老插件，也方便逐步迁移。

---

## 后端插件入口

插件目录下的 `__init__.py` 是后端入口。

推荐暴露两个可选函数：

```python
def init_api(app):
    ...


def init_daemon():
    ...
```

### `init_api(app)`

用于：
- 注册插件自己的初始化逻辑
- 可选做轻量 patch / hook
- 可选注册插件 router（如果 loader 约定支持）

注意：
- 这里应尽量只做 **插件自身初始化**
- 不要把宿主公共逻辑再塞进插件里
- 不要让每个插件都各自接管宿主 `/plugins` 挂载

### `init_daemon()`

用于：
- 后台线程/守护进程环境下的插件初始化
- daemon 侧的 patch / service 启动

如果插件根本不需要 daemon 侧能力，可以不实现。

---

## 前端插件入口

插件前端入口一般是：

```text
plugins/<plugin_name>/static/inject.js
```

这个脚本会被 `plugin-loader.js` 动态加载。

### 适合放在 inject.js 里的东西

- 插件 UI 注入
- Shadow DOM 面板
- 前端事件监听
- 与插件 API 通信
- 响应宿主事件（章节加载、章节保存、路由变化）

### 不建议放在 inject.js 里的东西

- 与宿主路由深度耦合的大量硬编码
- 重写整页主逻辑
- 自己再实现一套插件发现机制
- 自己再 monkey patch 全局 history/router（若宿主 runtime 已提供事件）

---

## 宿主运行时对象

前端插件应尽量依赖宿主运行时，而不是各自重复造轮子。

当前建议围绕全局对象使用：

```js
window.PlotPilotPlugins
```

推荐能力包括：

- `events.on(eventName, handler)`
- `events.once(eventName, handler)`
- `events.emit(eventName, payload)`
- `plugins.register(plugin)`
- `plugins.list()`
- `plugins.get(name)`
- `settings.get(pluginName, key, fallback)`
- `settings.set(pluginName, key, value)`
- `context.getRoute()`
- `context.getNovelId()`
- `context.getChapterNumber()`
- `host.emitChapterLoaded(payload)`
- `host.emitChapterSaved(payload)`
- `host.emitRouteChanged(payload)`

如果插件需要检测宿主状态变化，**优先订阅 runtime 事件**，不要优先使用轮询和 DOM 猜测。

---

## 推荐事件模型

当前插件平台最值得统一的是三类宿主事件：

### 1. `route:changed`
用于：
- 页面切换
- 路由参数变化
- 重新判断当前上下文

### 2. `chapter:loaded`
用于：
- 当前章节切换后刷新插件状态
- 重新获取章节关联数据

### 3. `chapter:saved`
用于：
- 章节保存成功后触发 sidecar 更新
- 更新角色卡 / 记忆 / 提取结果 / 面板状态

### 推荐事件载荷

```json
{
  "novelId": "novel-123",
  "chapterId": "chapter-456",
  "chapterNumber": 7,
  "title": "第7章",
  "content": "...",
  "source": "manual-save"
}
```

---

## 插件 API 设计建议

如果插件需要后端 API，推荐放在插件目录内部，例如：

```text
plugins/<plugin_name>/routes.py
```

建议 API 路径命名保持独立：

```text
/api/v1/plugins/<plugin_name>/...
```

例如：

- `GET /api/v1/plugins/example_plugin/status`
- `GET /api/v1/plugins/example_plugin/items`
- `POST /api/v1/plugins/example_plugin/rebuild`

这样可以避免和宿主已有业务 API 混在一起。

---

## 宿主文件最小改动原则

如果你在开发插件平台本身，而不是单个业务插件，请只保留以下宿主改动：

### 必须保留
- `interfaces/main.py`
  - 接入 `init_api_plugins`
  - 接入插件 manifest/list 路由
- `scripts/start_daemon.py`
  - 接入 `init_daemon_plugins`
- `frontend/index.html`
  - 加载 `/plugin-loader.js`
- `frontend/vite.config.ts`
  - 增加 `/plugins` 代理
- `frontend/public/plugin-loader.js`
- `plugins/loader.py`

### 不应继续扩散
- 把具体业务逻辑继续写回宿主 Vue/TS/Python 文件
- 每个插件都自己接管一套宿主挂载逻辑
- 为单一业务插件改坏整个宿主结构

---

## 插件开发最小示例

下面给一个最小插件示意。

### 目录

```text
plugins/example_plugin/
├── __init__.py
├── plugin.json
└── static/
    └── inject.js
```

### `plugins/example_plugin/__init__.py`

```python
def init_api(app):
    print("[example_plugin] init_api")


def init_daemon():
    print("[example_plugin] init_daemon")
```

### `plugins/example_plugin/plugin.json`

```json
{
  "name": "example_plugin",
  "display_name": "Example Plugin",
  "version": "0.1.0",
  "enabled": true,
  "frontend": {
    "scripts": [
      "static/inject.js"
    ]
  }
}
```

### `plugins/example_plugin/static/inject.js`

```javascript
(() => {
  const runtime = window.PlotPilotPlugins;
  if (!runtime) {
    console.warn('[example_plugin] runtime missing');
    return;
  }

  runtime.plugins.register({
    name: 'example_plugin',
    displayName: 'Example Plugin',
    version: '0.1.0',
  });

  runtime.events.on('chapter:loaded', (payload) => {
    console.log('[example_plugin] chapter loaded', payload);
  });
})();
```

这个示例的目标不是功能完整，而是说明最小接入形态。

---

## 推荐开发流程

### 开发单个插件时

1. 在 `plugins/<name>/` 下建立目录
2. 先写最小 `plugin.json`
3. 先写最小 `__init__.py`
4. 再写 `static/inject.js`
5. 如果需要 API，再补 `routes.py`
6. 如果需要 sidecar 数据层，再补 `core.py` / `service.py`
7. 最后再做宿主联调

### 开发插件平台本身时

1. 先改 `platform/plugins/loader.py`
2. 再改 `platform/frontend/public/plugin-loader.js`
3. 再补 focused tests
4. 再验证 installer 是否仍然可安装、可幂等
5. 最后才同步到宿主仓或目标 GitHub 仓

---

## 测试建议

平台开发不要一上来就只跑整站联调，优先分层验证。

### 1. Loader / manifest 层测试
优先验证：
- 能发现插件
- 能跳过 disabled 插件
- 能解析 `frontend.scripts`
- 没有 manifest 时能回退到 `static/inject.js`

对应仓内已有测试方向：
- `tests/test_plugin_loader_manifest.py`

### 2. Installer 层测试
优先验证：
- fresh clone 宿主可被自动打补丁
- 第二次执行是 no-op / already-installed
- 不重复插入 import / script / proxy

对应仓内已有测试方向：
- `tests/test_plugin_bootstrap_installer.py`

### 3. 运行时文本级回归
对于 `plugin-loader.js`，可以加轻量断言：
- 是否存在 `window.PlotPilotPlugins`
- 是否有 `plugins:loaded`
- 是否有 `emitChapterLoaded`
- 是否有脚本去重逻辑

### 4. 最后再做浏览器/整站联调
这一步才验证：
- 插件脚本实际被加载
- 前端面板是否显示
- 事件是否真的打通
- API 是否与宿主路由共存正常

---

## 与 SillyTavern 的关系：应该怎么理解

参考 SillyTavern 官方文档时，最容易误解的一点是：

> “有插件目录 + 能加载 inject.js” ≠ “已经拥有 ST 级扩展生态兼容性”

目前更准确的对标方式是：

### 我们已经具备
- 插件发现
- manifest 思路
- 前端动态脚本注入
- 宿主事件桥接思路
- daemon / API 双侧初始化

### 我们还需要继续补的，才会更像 ST
- 更稳定的插件 runtime API
- 更完整的 settings 持久化能力
- 更完整的 hooks 体系
- 更规范的 manifest 字段和版本契约
- 插件管理 UI
- 更明确的第三方兼容层 / shim

所以这份文档可以类比 SillyTavern 的“扩展开发说明”，但它描述的是：

> **PlotPilot 当前真实可用的插件开发模型**

而不是假装自己已经实现了完整 ST extension platform。

---

## 实战建议

### 适合优先插件化的功能
- 仿生记忆
- 动态角色卡
- prompt 注入
- 章节级 sidecar 分析
- 独立面板型前端工具

### 暂时不建议优先做成插件的东西
- 深度改写宿主主工作流核心状态机
- 强耦合宿主底层领域模型的重构
- 需要大面积侵入 `domain/` / `application/` 的改造

如果一个功能必须深改宿主大量核心文件，那它大概率还不是一个“成熟的插件点”。

---

## 安装与落地

把插件平台安装到 PlotPilot 宿主：

```bash
python3 platform/scripts/install_plugin_platform.py /path/to/PlotPilot
```

安装后建议至少验证：

```bash
pytest
```

如果是在真实宿主里继续验证，还应检查：

- `interfaces/main.py` 已接入插件 loader
- `scripts/start_daemon.py` 已接入 daemon loader
- `frontend/index.html` 已加载 `/plugin-loader.js`
- `frontend/vite.config.ts` 已配置 `/plugins` 代理
- `frontend/public/plugin-loader.js` 已落地
- `plugins/loader.py` 已落地

---

## 后续建议

如果你准备把这套平台继续做成熟，建议下一批文档继续补三份：

1. **插件 manifest 规范**
   - 定义字段、默认值、兼容策略、版本规则
2. **前端 runtime API 参考**
   - 把 `window.PlotPilotPlugins` 的 API 固化成契约文档
3. **插件模板 / Hello World 教程**
   - 提供可复制的最小插件骨架

这样开发体验会更接近 SillyTavern 文档体系，但仍保持 PlotPilot 自己的真实架构边界。
