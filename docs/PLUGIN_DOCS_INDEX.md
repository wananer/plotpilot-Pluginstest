# PlotPilot 插件平台文档索引

当前仓库已补齐的核心开发文档：

## 1. 开发说明总览
- `docs/PLUGIN_DEVELOPMENT_GUIDE.md`
- 用途：给插件开发者快速理解平台边界、目录结构、开发模型、测试方式

## 2. Manifest 规范
- `docs/PLUGIN_MANIFEST_SPEC.md`
- 用途：定义 `plugin.json` 的字段、默认行为、脚本路径解析规则、回退规则

## 3. 前端 Runtime API 参考
- `docs/PLUGIN_RUNTIME_API.md`
- 用途：定义 `window.PlotPilotPlugins` 的当前契约、事件模型、host/context/settings/plugins API

## 4. 宿主最小接入点
- `docs/HOST_TOUCHPOINTS.md`
- 用途：说明宿主仓只应保留哪些最小改动

---

## 推荐阅读顺序

如果你是第一次接入：

1. 先看 `docs/PLUGIN_DEVELOPMENT_GUIDE.md`
2. 再看 `docs/HOST_TOUCHPOINTS.md`
3. 再看 `docs/PLUGIN_MANIFEST_SPEC.md`
4. 最后看 `docs/PLUGIN_RUNTIME_API.md`

如果你是在写前端插件：

1. `docs/PLUGIN_DEVELOPMENT_GUIDE.md`
2. `docs/PLUGIN_RUNTIME_API.md`
3. `docs/PLUGIN_MANIFEST_SPEC.md`

如果你是在写 loader / installer / 宿主接入：

1. `docs/HOST_TOUCHPOINTS.md`
2. `docs/PLUGIN_MANIFEST_SPEC.md`
3. `docs/PLUGIN_DEVELOPMENT_GUIDE.md`

---

## 当前文档边界

这套文档描述的是：

- **PlotPilot 当前真实存在的原生插件平台能力**

不是：

- 完整 SillyTavern 扩展生态兼容文档
- 全功能第三方插件市场规范
- 最终稳定版插件 SDK 说明

也就是说，它适合当前这阶段：

- 给我们自己的插件平台定契约
- 支持后续业务插件迁移到 `plugins/`
- 为后续继续扩展 runtime / manifest / hooks 打基础
