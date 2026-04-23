# PlotPilot 前端插件 Runtime API 参考

> 本文档描述当前 `frontend/public/plugin-loader.js` 暴露的前端插件运行时对象：`window.PlotPilotPlugins`。

## 1. 全局入口

插件前端脚本加载后，应优先通过：

```javascript
const runtime = window.PlotPilotPlugins;
```

访问宿主提供的运行时能力。

如果对象不存在，说明：
- 宿主未正确加载 `/plugin-loader.js`
- 插件脚本早于 runtime 初始化执行
- 当前页面不是完整宿主环境

推荐保护写法：

```javascript
const runtime = window.PlotPilotPlugins;
if (!runtime) {
  console.warn('[my_plugin] PlotPilot runtime missing');
  return;
}
```

---

## 2. 顶层结构

当前 runtime 主要包含：

```javascript
window.PlotPilotPlugins = {
  version,
  endpoints,
  events,
  settings,
  plugins,
  scripts,
  state,
  hooks,
  host,
  context,
  fetchJson,
}
```

---

## 3. `version`

```javascript
runtime.version
```

- 类型：`string`
- 当前示例值：`0.2.0`
- 用途：运行时版本标识

插件不要对某个具体版本号做强耦合硬判断，除非你真的在处理兼容分支。

---

## 4. `endpoints`

```javascript
runtime.endpoints
```

当前字段：

```javascript
{
  manifest: '/api/v1/plugins/manifest',
  plugins: '/api/v1/plugins'
}
```

用途：
- 给插件提供宿主已知的插件 API 入口
- 避免在每个插件里硬编码相同路径

---

## 5. `events`

运行时事件总线。

### 5.1 `events.on(eventName, handler)`

注册事件监听器。

```javascript
const off = runtime.events.on('chapter:loaded', (payload) => {
  console.log(payload);
});
```

返回值：
- 一个取消订阅函数

### 5.2 `events.once(eventName, handler)`

只监听一次。

```javascript
runtime.events.once('plugins:loaded', (payload) => {
  console.log('plugins loaded', payload);
});
```

### 5.3 `events.emit(eventName, payload)`

主动触发事件。

```javascript
runtime.events.emit('my-plugin:ready', { ok: true });
```

### 5.4 当前常见事件

宿主/runtime 当前会触发的常见事件包括：

- `runtime:ready`
- `manifest:loaded`
- `manifest:error`
- `plugins:loaded`
- `plugins:error`
- `script:loaded`
- `script:error`
- `plugin:registered`
- `plugin:updated`
- `settings:changed`
- `route:changed`
- `chapter:loaded`
- `chapter:saved`

说明：
- `chapter:*` / `route:*` 是否真正触发，取决于宿主桥接是否已接好
- 不能只看 runtime 文档，还要看宿主是否真的调用了 `host.emit...`

---

## 6. `settings`

运行时内存级插件设置存储。

### 6.1 `settings.get(pluginName, key, fallback)`

```javascript
const value = runtime.settings.get('my_plugin', 'mode', 'default');
```

### 6.2 `settings.set(pluginName, key, value)`

```javascript
runtime.settings.set('my_plugin', 'mode', 'compact');
```

设置后会触发：

- `settings:changed`

### 6.3 `settings.all(pluginName)`

```javascript
const all = runtime.settings.all('my_plugin');
```

### 当前限制

当前 `settings` 是运行时内存存储，不等于持久化配置系统。

也就是说：
- 刷新页面后未必保留
- 不能当成正式插件配置数据库

如果插件需要真正持久化，建议自己走插件 API 或本地存储方案。

---

## 7. `plugins`

插件注册表。

### 7.1 `plugins.register(plugin)`

```javascript
runtime.plugins.register({
  name: 'my_plugin',
  displayName: 'My Plugin',
  version: '0.1.0'
});
```

行为：
- 首次注册 → 触发 `plugin:registered`
- 再次注册同名插件 → 合并字段并触发 `plugin:updated`

### 7.2 `plugins.list()`

```javascript
const allPlugins = runtime.plugins.list();
```

### 7.3 `plugins.get(name)`

```javascript
const plugin = runtime.plugins.get('my_plugin');
```

用途：
- 查询已注册插件元信息
- 辅助调试或插件间轻度协作

---

## 8. `scripts`

用于跟踪前端脚本加载状态。

### 8.1 `scripts.has(src)`

```javascript
runtime.scripts.has('/plugins/my_plugin/static/inject.js');
```

### 8.2 `scripts.mark(src)`

```javascript
runtime.scripts.mark('/plugins/my_plugin/static/inject.js');
```

### 8.3 `scripts.list()`

```javascript
const loaded = runtime.scripts.list();
```

用途：
- 去重
- 调试当前已加载脚本

---

## 9. `state`

运行时状态快照。

### 当前字段

```javascript
runtime.state = {
  manifest,
  pluginsPayload,
  startedAt,
  currentRoute,
}
```

### 9.1 `state.manifest`
- 最近一次 `/api/v1/plugins/manifest` 返回值

### 9.2 `state.pluginsPayload`
- 最近一次 `/api/v1/plugins` 返回值

### 9.3 `state.startedAt`
- runtime 初始化时间

### 9.4 `state.currentRoute`

```javascript
{
  path,
  query,
  hash
}
```

由 `host.emitRouteChanged(...)` 维护。

---

## 10. `hooks`

`hooks` 是对 `events` 的轻量命名空间封装。

### 10.1 `hooks.emit(name, payload)`

```javascript
runtime.hooks.emit('chapter:saved', payload);
```

实际会转成：

```text
hook:chapter:saved
```

### 10.2 `hooks.on(name, handler)`

```javascript
runtime.hooks.on('chapter:saved', (payload) => {
  console.log(payload);
});
```

### 10.3 `hooks.once(name, handler)`

```javascript
runtime.hooks.once('route:changed', (payload) => {
  console.log(payload);
});
```

### 什么时候用 `hooks`，什么时候用 `events`

建议：
- 想订阅宿主/业务钩子语义时，用 `hooks`
- 想订阅 runtime 自己的运行事件时，用 `events`

例如：
- `hooks.on('chapter:saved', ...)`
- `events.on('plugins:loaded', ...)`

---

## 11. `host`

宿主主动推送业务生命周期事件的桥接接口。

### 11.1 `host.emitChapterSaved(payload)`

```javascript
runtime.host.emitChapterSaved({
  novelId: 'novel-1',
  chapterId: 'chapter-2',
  chapterNumber: 5,
  title: '第5章',
  content: '...',
  source: 'manual-save'
});
```

会触发：
- `hook:chapter:saved`
- `chapter:saved`

### 11.2 `host.emitChapterLoaded(payload)`

```javascript
runtime.host.emitChapterLoaded({
  novelId: 'novel-1',
  chapterId: 'chapter-2',
  chapterNumber: 5,
  title: '第5章',
  content: '...',
  source: 'chapter-load'
});
```

会触发：
- `hook:chapter:loaded`
- `chapter:loaded`

### 11.3 `host.emitRouteChanged(payload)`

```javascript
runtime.host.emitRouteChanged({
  path: location.pathname,
  query: location.search,
  hash: location.hash,
  source: 'pushState'
});
```

会更新：
- `runtime.state.currentRoute`

并触发：
- `hook:route:changed`
- `route:changed`

### 推荐规则

插件不要自己重复 patch `history.pushState` / `replaceState` / `popstate` 来猜宿主变化。

只要宿主已经接入 runtime 事件桥接，插件就应该优先监听：
- `route:changed`
- `chapter:loaded`
- `chapter:saved`

---

## 12. `context`

用于快速读取宿主上下文。

### 12.1 `context.getRoute()`

```javascript
const route = runtime.context.getRoute();
```

返回：

```javascript
{
  path,
  query,
  hash
}
```

### 12.2 `context.getNovelId()`

```javascript
const novelId = runtime.context.getNovelId();
```

当前逻辑：
- 优先尝试从 `/book/<id>` 路径中提取
- 否则从 query 参数 `?novel=...` 提取

### 12.3 `context.getChapterNumber()`

```javascript
const chapterNumber = runtime.context.getChapterNumber();
```

当前逻辑：
- 从 query 参数 `?chapter=...` 中提取正整数

### 限制说明

这些 helper 是轻量上下文工具，不是强一致业务状态源。

如果插件需要绝对准确的章节数据，应结合：
- 宿主事件载荷
- 插件自己的 API 查询

---

## 13. `fetchJson(url)`

宿主提供的同源 JSON 请求辅助方法。

```javascript
const data = await runtime.fetchJson('/api/v1/plugins');
```

当前行为：
- 使用 `fetch`
- 带 `credentials: 'same-origin'`
- 默认 `Accept: application/json`
- 非 2xx 会抛异常

适合：
- 调插件 API
- 拉宿主提供的插件信息

不适合：
- 上传大文件
- 流式请求
- 特殊认证流程

---

## 14. 插件推荐接入模板

一个较稳的前端插件写法如下：

```javascript
(() => {
  const runtime = window.PlotPilotPlugins;
  if (!runtime) {
    console.warn('[my_plugin] runtime missing');
    return;
  }

  runtime.plugins.register({
    name: 'my_plugin',
    displayName: 'My Plugin',
    version: '0.1.0',
  });

  async function refresh(payload = {}) {
    const novelId = payload.novelId || runtime.context.getNovelId();
    if (!novelId) return;
    console.log('[my_plugin] refresh', novelId, payload);
  }

  runtime.events.on('chapter:loaded', refresh);
  runtime.events.on('chapter:saved', refresh);
  runtime.events.on('route:changed', refresh);

  refresh({ source: 'startup' });
})();
```

这个模式的优点：
- 不依赖插件自己轮询路由
- 不重复 patch history
- 将刷新逻辑集中到一个入口

---

## 15. 当前已知边界

为了避免误用，这里明确当前 runtime 还不是完整插件框架：

- `settings` 不是正式持久化存储
- `context` 只是轻量 helper
- `host` 事件依赖宿主是否真的接线
- 还没有严格版本协商机制
- 还没有官方插件间依赖解析系统
- 还没有插件权限模型

所以插件开发时要默认：
- 做存在性判断
- 做 graceful fallback
- 不要假设宿主永远提供所有能力

---

## 16. 调试建议

如果插件前端没反应，优先查这几项：

1. 页面是否加载了 `/plugin-loader.js`
2. `window.PlotPilotPlugins` 是否存在
3. `/api/v1/plugins/manifest` 是否返回了你的插件
4. `frontend_scripts` 里是否包含你的脚本
5. 浏览器里是否有 `script:error` / `manifest:error`
6. 插件脚本是否真正执行了 `runtime.plugins.register(...)`

在控制台可快速检查：

```javascript
window.PlotPilotPlugins
window.PlotPilotPlugins?.plugins.list()
window.PlotPilotPlugins?.scripts.list()
window.PlotPilotPlugins?.state.manifest
```

---

## 17. 一句话结论

`window.PlotPilotPlugins` 当前是 PlotPilot 插件平台的 **最小前端宿主契约**。

它已经足够支撑：
- 插件注册
- 脚本去重加载
- 基础事件总线
- 宿主生命周期桥接
- 轻量上下文读取

但还不应被误认为已经等同于 SillyTavern 的完整扩展运行时。
