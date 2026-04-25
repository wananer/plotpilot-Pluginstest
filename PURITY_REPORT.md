# PlotPilot Plugin Platform Purity Report

## 结论

当前仓库可认定为一个**可独立分发、测试自洽、无外部宿主硬依赖**的 PlotPilot 插件平台骨架仓库。

当前仓库按集成验证需求保留 `plugins/world_evolution_core/` 作为真实承载插件；它是明确列出的集成样本，不代表平台主体可以继续混入任意业务实现。

## 平台必留文件

这些文件属于插件平台最小闭环的一部分，应保留：

- `platform/plugins/loader.py`
  - 后端插件发现、manifest 解析、API/daemon 初始化、manifest/list 路由
- `platform/frontend/public/plugin-loader.js`
  - 前端插件 runtime、脚本注入、host 事件桥接入口
- `platform/scripts/install_plugin_platform.py`
  - fresh clone 场景下将最小插件宿主接入点打补丁到 PlotPilot 宿主仓库
- `tests/test_plugin_loader_manifest.py`
  - 验证 loader/manifest/runtime 合约
- `tests/test_plugin_bootstrap_installer.py`
  - 验证 bootstrap installer 的补丁行为与幂等性
- `docs/HOST_TOUCHPOINTS.md`
  - 说明宿主需要开放的最小接入点
- `pytest.ini`
  - 保证仓库根目录直接运行 `pytest` 即可验证

## 不应混入平台主体的内容

以下内容如果再次进入这个仓库，应视为“平台纯净度退化”：

- 任何外部宿主绝对路径依赖，例如用户主目录中的 PlotPilot 工作区路径
- 对外部业务插件源码或用户本机路径的测试硬依赖
- `bionic_memory` / `rolecard` / `autopilot` / `rewrite` / `novel` 等业务功能实现直接进入平台主体
- 需要依赖用户本机已有 PlotPilot 主仓库或业务插件仓库，仓库自身无法自测的测试设计

## 本次审计确认结果

### 1. 代码主体
- 未发现业务功能实现混入 `platform/` 主体
- 平台核心职责清晰：loader / runtime / installer
- `interfaces/main.py` 与 `scripts/start_daemon.py` 已收缩为最小宿主示例，不再携带完整 PlotPilot API、autopilot 守护进程或数据库依赖
- `plugins/world_evolution_core/` 作为本轮指定的真实集成插件存在；其代码不进入 `platform/` 主体
- 宿主原数据库只通过 read-only facade 暴露；插件平台可写数据限定在 `plugin_platform/` 专属区域

### 2. 测试层
- 已移除对外部宿主仓库文件的绝对路径依赖
- 已移除对真实 `bionic_memory` 等外部插件文件的硬依赖
- 样例注入器已去业务化，改为通用 sample plugin 语义
- `world_evolution_core` 相关测试只依赖仓库内已搭载插件，不依赖外部插件仓库路径

### 3. 工程自洽性
- `pytest.ini` 已调整为：`pythonpath = platform`
- 仓库根目录可直接运行：

```bash
pytest
```

### 4. 验证结果
- Python 全量测试：`28 passed`
- 前端 runtime/config 测试：`4 passed`
- 外部硬编码残留搜索：未发现用户主目录工作区路径、临时同步目录、本机项目目录标记、运行数据库、旧业务插件 host 标记等残留
- 跟踪文件清单：未发现 `__pycache__`、`.pytest_cache`、`node_modules`、数据库、dist 构建产物或本地日志被 git 跟踪

## 当前允许存在的文档级示例

README / 文档中提及：

- `bionic_memory` 作为“业务插件示例”
- `rolecard` / `rewrite` 等作为“不应混入平台主体”的反例
- `SillyTavern` / `ST-Evolution-World-Assistant` 作为兼容性对照说明

这些是**文档级举例或反例**，不构成平台污染；只要代码与测试不再依赖这些业务插件仓库，就可接受。

## 当前允许存在的集成插件

- `plugins/world_evolution_core/`

保留理由：用户要求平台仓库验证能够搭载 Evolution World Assistant。该目录必须继续满足：

1. 可通过 `plugin.json` 被通用 loader 发现。
2. 前端资源只通过 `/plugins/world_evolution_core/static/...` 暴露。
3. 后端 hook 只通过 `plugins.platform` 的通用 dispatcher 注册。
4. 不向 `platform/` 主体回写业务专用逻辑。
5. 如插件迁出平台仓库，测试需改为 zip/GitHub 导入契约测试，而不是恢复本机路径依赖。

## 后续维护规则

建议后续继续遵守以下规则：

1. 平台仓库测试优先验证“插件协议 / runtime / 宿主接入合约”；若保留真实集成插件，必须显式列入允许清单。
2. 新增示例时优先使用通用 sample plugin 命名，不带业务品牌色彩。
3. 若未来增加更多测试，必须保证 fresh clone 后在仓库根目录可直接运行。
4. 若要扩展能力，优先放在：
   - loader 能力
   - frontend runtime 能力
   - bootstrap installer 能力
   - 平台文档与契约测试

而不是把某个具体业务插件直接塞进平台仓库。
