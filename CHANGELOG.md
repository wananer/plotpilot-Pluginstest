# Changelog

All notable changes to this repository will be documented in this file.

## [Unreleased]

### Added
- `PURITY_REPORT.md`，明确平台仓库纯净度边界与审计结论
- `LICENSE`（MIT）
- `.gitignore`
- `CONTRIBUTING.md`
- GitHub Actions CI：push / pull_request 自动执行 `pytest -q`

### Changed
- `README.md` 重写为更适合公开模板仓库的说明结构
- `pytest.ini` 调整为 `pythonpath = platform`，支持 fresh clone 后在仓库根目录直接测试
- `tests/test_plugin_loader_manifest.py` 去除外部宿主路径和业务插件依赖，改为平台内 runtime + sample plugin contract 测试

## [2026-04-22]

### Changed
- 清理并发布第一版“纯净 PlotPilot 插件平台骨架”
