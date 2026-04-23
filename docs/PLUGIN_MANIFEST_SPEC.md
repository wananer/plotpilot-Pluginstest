# PlotPilot 插件 Manifest 规范

> 本文档定义 PlotPilot 原生插件的 `plugin.json` 约定。目标是让插件发现、前端脚本注入、启用/禁用控制有稳定契约。

## 1. 文件位置

每个插件建议在目录下提供：

```text
plugins/<plugin_name>/plugin.json
```

例如：

```text
plugins/example_plugin/plugin.json
```

---

## 2. 最小示例

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

---

## 3. 字段定义

### 3.1 `name`
- 类型：`string`
- 建议：与插件目录名一致
- 用途：插件唯一标识

推荐：
```json
{ "name": "bionic_memory" }
```

### 3.2 `display_name`
- 类型：`string`
- 用途：人类可读显示名
- 若缺失，宿主会回退到：`manifest.name -> 目录名`

### 3.3 `version`
- 类型：`string`
- 用途：版本展示、后续兼容检查
- 当前不强制 semver，但建议遵守 semver

示例：
```json
{ "version": "1.2.0" }
```

### 3.4 `enabled`
- 类型：`boolean`
- 默认：`true`
- 语义：`false` 时整个插件跳过加载

规则：
- `enabled: false` → 后端 loader 跳过该插件
- 被跳过的插件不出现在启用插件列表中
- 前端脚本也不应继续暴露给 manifest/list API

### 3.5 `frontend`
- 类型：`object`
- 用途：声明插件前端资源

当前已定义子字段：

#### 3.5.1 `frontend.scripts`
- 类型：`string[]`
- 含义：前端脚本加载顺序列表

示例：

```json
{
  "frontend": {
    "scripts": [
      "static/inject.js",
      "/plugins/shared/runtime.js"
    ]
  }
}
```

---

## 4. 路径解析规则

### 4.1 相对路径
如果脚本不是以 `/` 开头，则按插件目录解析：

```json
{
  "frontend": {
    "scripts": ["static/inject.js"]
  }
}
```

解析为：

```text
/plugins/<plugin_name>/static/inject.js
```

### 4.2 绝对路径
如果脚本以 `/` 开头，则保持原样：

```json
{
  "frontend": {
    "scripts": ["/plugins/shared/runtime.js"]
  }
}
```

解析后仍然是：

```text
/plugins/shared/runtime.js
```

---

## 5. 缺省与回退行为

如果插件没有 `plugin.json`，当前平台允许兼容旧约定：

- 若存在 `plugins/<name>/static/inject.js`
- 则仍可把它视为默认前端入口

也就是说：

### 有 manifest 时
优先使用：
- `frontend.scripts`

### 无 manifest 时
回退使用：
- `static/inject.js`

这个回退机制是为了支持老插件平滑迁移，不建议长期依赖。

---

## 6. Loader 当前行为契约

当前 `platform/plugins/loader.py` 的稳定行为可总结为：

1. 扫描 `plugins/*/` 下带 `__init__.py` 的目录
2. 尝试读取 `plugin.json`
3. 若 `enabled` 为 `false`，跳过该插件
4. 构造插件 manifest record
5. 解析 `frontend.scripts`
6. 若未声明 `frontend.scripts`，回退到 `static/inject.js`
7. 将启用插件暴露到：
   - `GET /api/v1/plugins`
   - `GET /api/v1/plugins/manifest`

---

## 7. Manifest API 返回形态

当前后端返回的单个插件记录建议理解为：

```json
{
  "name": "example_plugin",
  "display_name": "Example Plugin",
  "version": "0.1.0",
  "enabled": true,
  "frontend_scripts": [
    "/plugins/example_plugin/static/inject.js"
  ],
  "manifest": {
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
}
```

注意：
- `frontend_scripts` 是 **宿主已解析后的可直接加载路径**
- `manifest.frontend.scripts` 是 **原始声明**

---

## 8. 推荐写法

### 推荐：最小明确声明

```json
{
  "name": "rolecard",
  "display_name": "Dynamic Role Card",
  "version": "0.1.0",
  "enabled": true,
  "frontend": {
    "scripts": [
      "static/inject.js"
    ]
  }
}
```

### 推荐：共享运行时脚本 + 插件脚本

```json
{
  "name": "memory_panel",
  "display_name": "Memory Panel",
  "version": "0.2.0",
  "enabled": true,
  "frontend": {
    "scripts": [
      "/plugins/shared/runtime-helpers.js",
      "static/inject.js"
    ]
  }
}
```

### 推荐：临时禁用插件

```json
{
  "name": "experimental_plugin",
  "enabled": false
}
```

---

## 9. 不推荐写法

### 9.1 `scripts` 塞空值或非字符串

不推荐：

```json
{
  "frontend": {
    "scripts": [null, "", 123]
  }
}
```

虽然 loader 会尽量跳过无效值，但这不是可靠契约。

### 9.2 `name` 与目录名长期不一致

技术上可能还能工作，但会带来：
- 文档歧义
- API 列表展示混乱
- 插件管理时难排查

### 9.3 把宿主业务配置塞进 manifest

`plugin.json` 当前应该只承担：
- 插件元信息
- 启用/禁用
- 前端资源声明

不要把大量宿主业务逻辑配置、数据库配置、敏感信息直接混进来。

---

## 10. 版本兼容建议

当前平台还没有完整的 manifest 版本协商字段，但建议从现在开始遵守：

- 插件自己的 `version` 使用 semver
- 不要假设宿主 runtime API 永远不变
- 插件应在 `inject.js` 内对关键 API 做存在性判断

例如：

```javascript
const runtime = window.PlotPilotPlugins;
if (!runtime || !runtime.events || !runtime.plugins) {
  console.warn('[my_plugin] unsupported runtime');
  return;
}
```

---

## 11. 推荐未来扩展字段

下面这些字段现在可以先不实现，但未来很值得标准化：

- `description`
- `author`
- `homepage`
- `requires`
- `optional`
- `loading_order`
- `api`
- `styles`
- `compatibility`

如果后续要向 SillyTavern 式插件生态靠近，这些字段会很重要。

---

## 12. 实践结论

对当前 PlotPilot 平台来说，`plugin.json` 的核心价值不是“做得很复杂”，而是先把三件事稳定下来：

1. 插件能否被启用/禁用
2. 前端脚本如何声明与解析
3. 插件列表 API 能否稳定输出

只要这三点稳定，后续再加依赖、顺序、兼容字段才有意义。
