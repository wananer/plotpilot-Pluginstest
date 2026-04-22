#!/usr/bin/env python3
"""Install PlotPilot plugin platform into a clean upstream checkout.

This script applies the minimum host-side integration points required for the
zero-intrusion plugin platform:
- plugins/loader.py backend loader
- frontend/public/plugin-loader.js runtime
- frontend/index.html injection for plugin-loader.js
- frontend/vite.config.ts proxy for /plugins -> backend
- interfaces/main.py API manifest router + plugin init hooks
- scripts/start_daemon.py daemon plugin init hook

The goal is to keep all business customizations inside plugins/, while the host
keeps only a tiny, reviewable platform surface.
"""
from __future__ import annotations

from pathlib import Path
import sys

SCRIPT_PATH = Path(__file__).resolve()
SOURCE_ROOT = SCRIPT_PATH.parents[1]
TARGET_ROOT = Path.cwd()

LOADER_SRC = SOURCE_ROOT / "plugins" / "loader.py"
RUNTIME_SRC = SOURCE_ROOT / "frontend" / "public" / "plugin-loader.js"
INDEX_HTML = TARGET_ROOT / "frontend" / "index.html"
VITE_CONFIG = TARGET_ROOT / "frontend" / "vite.config.ts"
INTERFACES_MAIN = TARGET_ROOT / "interfaces" / "main.py"
DAEMON_START = TARGET_ROOT / "scripts" / "start_daemon.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def ensure_file_copy(src: Path, dst: Path) -> bool:
    content = _read(src)
    if dst.exists() and _read(dst) == content:
        return False
    _write(dst, content)
    return True


def ensure_contains(path: Path, needle: str, insert_after: str) -> bool:
    content = _read(path)
    if needle in content:
        return False
    if insert_after in content:
        content = content.replace(insert_after, insert_after + needle, 1)
    else:
        if not content.endswith("\n"):
            content += "\n"
        content += needle.lstrip("\n")
        if not content.endswith("\n"):
            content += "\n"
    _write(path, content)
    return True


def patch_frontend_index() -> bool:
    needle = "\n    <script src=\"/plugin-loader.js\"></script>"
    anchor = "\n    <script type=\"module\" src=\"/src/main.ts\"></script>"
    return ensure_contains(INDEX_HTML, needle, anchor)


def patch_vite_proxy() -> bool:
    content = _read(VITE_CONFIG)
    if "'/plugins':" in content or '"/plugins":' in content:
        return False
    anchor = "      '/api': {"
    needle = """      '/plugins': {
        target: 'http://127.0.0.1:8005',
        changeOrigin: true,
        rewrite: (path) => path,
      },
"""
    if anchor not in content:
        raise RuntimeError("vite proxy anchor not found")
    _write(VITE_CONFIG, content.replace(anchor, needle + anchor, 1))
    return True


def patch_interfaces_main() -> bool:
    changed = False
    changed |= ensure_contains(
        INTERFACES_MAIN,
        "\nfrom plugins.loader import create_plugin_manifest_router, init_api_plugins",
        "\nfrom infrastructure.persistence.database.connection import get_database\n",
    )

    main_content = _read(INTERFACES_MAIN)
    router_line = 'app.include_router(create_plugin_manifest_router(), prefix="/api/v1")'
    if router_line not in main_content:
        if '# ── 前端静态文件托管 ──' in main_content:
            main_content = main_content.replace(
                '# ── 前端静态文件托管 ──',
                f'{router_line}\n\n# ── 前端静态文件托管 ──',
                1,
            )
        else:
            if not main_content.endswith("\n"):
                main_content += "\n"
            main_content += f"\n{router_line}\n"
        _write(INTERFACES_MAIN, main_content)
        changed = True

    changed |= ensure_contains(
        INTERFACES_MAIN,
        "\n    init_api_plugins(app)\n",
        "\n    logger.info(f\"📊 Registered {len(app.routes)} routes\")\n",
    )
    return changed


def patch_start_daemon() -> bool:
    changed = False
    changed |= ensure_contains(
        DAEMON_START,
        "\nfrom plugins.loader import init_daemon_plugins",
        "\nfrom interfaces.api.middleware.logging_config import setup_logging\n",
    )
    changed |= ensure_contains(
        DAEMON_START,
        "\n    init_daemon_plugins()\n",
        "\n    daemon = build_daemon()\n",
    )
    return changed


def main() -> int:
    changed_items = []

    if ensure_file_copy(LOADER_SRC, TARGET_ROOT / "plugins" / "loader.py"):
        changed_items.append("plugins/loader.py")
    if ensure_file_copy(RUNTIME_SRC, TARGET_ROOT / "frontend" / "public" / "plugin-loader.js"):
        changed_items.append("frontend/public/plugin-loader.js")
    if patch_frontend_index():
        changed_items.append("frontend/index.html")
    if patch_vite_proxy():
        changed_items.append("frontend/vite.config.ts")
    if patch_interfaces_main():
        changed_items.append("interfaces/main.py")
    if patch_start_daemon():
        changed_items.append("scripts/start_daemon.py")

    if changed_items:
        print("Installed/updated plugin platform:")
        for item in changed_items:
            print(f"- {item}")
    else:
        print("Plugin platform already installed; no changes made.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
