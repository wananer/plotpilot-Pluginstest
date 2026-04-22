# PlotPilot Plugin Platform Purity Report

## 结论

当前仓库可认定为一个**可独立分发、测试自洽、无外部宿主/业务插件硬依赖**的 PlotPilot 插件平台骨架仓库。

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

- 任何外部宿主绝对路径依赖，如 `/Users/.../PlotPilot/...`
- 对外部业务插件源码的测试硬依赖
- `bionic_memory` / `rolecard` / `autopilot` / `rewrite` / `novel` 等业务功能实现直接进入平台主体
- 需要依赖用户本机已有 PlotPilot 主仓库或业务插件仓库，仓库自身无法自测的测试设计

## 本次审计确认结果

### 1. 代码主体
- 未发现业务功能实现混入 `platform/` 主体
- 平台核心职责清晰：loader / runtime / installer

### 2. 测试层
- 已移除对外部宿主仓库文件的绝对路径依赖
- 已移除对真实 `bionic_memory` 插件文件的硬依赖
- 样例注入器已去业务化，改为通用 sample plugin 语义

### 3. 工程自洽性
- `pytest.ini` 已调整为：`pythonpath = platform`
- 仓库根目录可直接运行：

```bash
pytest
```

### 4. 验证结果
- 全量当前测试：`13 passed`
- 外部硬编码残留搜索：未发现 `/Users/.../PlotPilot/...`、`bootstrapBionicMemoryPlugin`、`__BMHost`、`__bmRefresh` 等残留

## 当前允许存在的文档级示例

README 中提及：

- `bionic_memory` 作为“业务插件示例”

这是**文档级举例**，不构成平台污染；只要代码与测试不再依赖该业务插件仓库，就可接受。

## 后续维护规则

建议后续继续遵守以下规则：

1. 平台仓库测试只验证“插件协议 / runtime / 宿主接入合约”，不要依赖真实业务插件实现。
2. 新增示例时优先使用通用 sample plugin 命名，不带业务品牌色彩。
3. 若未来增加更多测试，必须保证 fresh clone 后在仓库根目录可直接运行。
4. 若要扩展能力，优先放在：
   - loader 能力
   - frontend runtime 能力
   - bootstrap installer 能力
   - 平台文档与契约测试

而不是把某个具体业务插件直接塞进平台仓库。
