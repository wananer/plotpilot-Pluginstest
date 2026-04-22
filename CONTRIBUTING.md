# Contributing

欢迎继续把这个仓库维护为 **纯净的 PlotPilot 插件平台骨架**。

## 提交原则

- 平台仓库只放：插件 loader、frontend runtime、bootstrap installer、契约测试、平台文档
- 不要把具体业务插件实现直接塞进这个仓库
- 不要引入依赖本机绝对路径或外部 PlotPilot 主仓库的测试
- 新增测试后，必须保证 fresh clone 后在仓库根目录直接 `pytest` 可通过

## 推荐开发流程

1. 先补最小测试
2. 再做平台层改动
3. 根目录运行：

```bash
pytest
```

4. 确认 `git status` 干净后再提交

## Pull Request 要求

PR 描述建议至少说明：
- 这次变更属于 loader / runtime / installer / docs / tests 的哪一类
- 是否影响宿主接入点
- 是否增加了新的平台契约
- 如何验证
