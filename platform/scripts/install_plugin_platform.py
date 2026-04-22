"""Install PlotPilot plugin-platform bootstrap into a fresh clone.

This script patches the minimum host touchpoints so plugins can be
distributed as a portable platform bundle instead of requiring manual edits.
"""

from __future__ import annotations

from pathlib import Path
import shutil


_HOST_IMPORT = "from plugins.loader import init_api_plugins, create_plugin_manifest_router\n"
_HOST_INIT = (
    "\n\n"
    "def init_api(app: FastAPI) -> list[str]:\n"
    "    loaded_plugins = init_api_plugins(app)\n"
    "    manifest_route = \"/api/v1/plugins/manifest\"\n"
    "    if not any(getattr(route, \"path\", \"\") == manifest_route for route in app.routes):\n"
    "        app.include_router(create_plugin_manifest_router(), prefix=\"/api/v1\")\n"
    "    return loaded_plugins\n"
)

_DAEMON_IMPORT = "from plugins.loader import init_daemon_plugins\n"
_DAEMON_CALL = "\nloaded_plugins = init_daemon_plugins()\n"
_INDEX_SNIPPET = '<script src="/plugin-loader.js"></script>'
_VITE_PROXY = (
    "      '/plugins': {\n"
    "        target: 'http://127.0.0.1:3000',\n"
    "        changeOrigin: true,\n"
    "      },\n"
)


def _copy_if_missing(src: Path, dst: Path) -> bool:
    if dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _ensure_contains(path: Path, needle: str, insertion: str, *, after: str | None = None) -> bool:
    text = path.read_text(encoding="utf-8")
    if needle in text:
        return False
    if after and after in text:
        text = text.replace(after, after + insertion, 1)
    else:
        text += insertion
    path.write_text(text, encoding="utf-8")
    return True


def _ensure_main_py(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    changed = False
    if _HOST_IMPORT not in text:
        anchor = "from fastapi import FastAPI\n"
        if anchor not in text:
            raise ValueError(f"Cannot patch {path}: missing FastAPI import anchor")
        text = text.replace(anchor, anchor + _HOST_IMPORT, 1)
        changed = True
    if "def init_api(app: FastAPI) -> list[str]:" not in text:
        if "app = FastAPI()" in text:
            text = text.replace("app = FastAPI()\n", _HOST_INIT + "\napp = FastAPI()\n", 1)
        else:
            text += _HOST_INIT
        changed = True
    path.write_text(text, encoding="utf-8")
    return changed


def _ensure_start_daemon(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    changed = False
    if _DAEMON_IMPORT not in text:
        anchor = "import sys\n"
        if anchor not in text:
            raise ValueError(f"Cannot patch {path}: missing import anchor")
        text = text.replace(anchor, anchor + _DAEMON_IMPORT, 1)
        changed = True
    if "loaded_plugins = init_daemon_plugins()" not in text:
        anchor = _DAEMON_IMPORT
        text = text.replace(anchor, anchor + _DAEMON_CALL, 1)
        changed = True
    path.write_text(text, encoding="utf-8")
    return changed


def _ensure_index_html(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if _INDEX_SNIPPET in text:
        return False
    anchor = '<script type="module" src="/src/main.ts"></script>'
    if anchor not in text:
        raise ValueError(f"Cannot patch {path}: missing frontend entry anchor")
    text = text.replace(anchor, anchor + _INDEX_SNIPPET, 1)
    path.write_text(text, encoding="utf-8")
    return True


def _ensure_vite_proxy(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "'/plugins': {" in text:
        return False
    multiline_anchor = "    proxy: {\n"
    if multiline_anchor in text:
        text = text.replace(multiline_anchor, multiline_anchor + _VITE_PROXY, 1)
        path.write_text(text, encoding="utf-8")
        return True

    inline_anchor = "proxy: {"
    if inline_anchor not in text:
        raise ValueError(f"Cannot patch {path}: missing Vite proxy anchor")
    text = text.replace(inline_anchor, "proxy: { '/plugins': { target: 'http://127.0.0.1:3000', changeOrigin: true }, ", 1)
    path.write_text(text, encoding="utf-8")
    return True


def install_plugin_platform(repo_root: str | Path) -> bool:
    repo_root = Path(repo_root)
    source_root = Path(__file__).resolve().parents[1]

    changed = False
    changed |= _ensure_main_py(repo_root / "interfaces" / "main.py")
    changed |= _ensure_start_daemon(repo_root / "scripts" / "start_daemon.py")
    changed |= _ensure_index_html(repo_root / "frontend" / "index.html")
    changed |= _ensure_vite_proxy(repo_root / "frontend" / "vite.config.ts")
    changed |= _copy_if_missing(source_root / "frontend" / "public" / "plugin-loader.js", repo_root / "frontend" / "public" / "plugin-loader.js")
    changed |= _copy_if_missing(source_root / "plugins" / "loader.py", repo_root / "plugins" / "loader.py")
    return changed


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Install PlotPilot plugin platform into a repository")
    parser.add_argument("repo_root", nargs="?", default=".", help="Target PlotPilot repository root")
    args = parser.parse_args()

    changed = install_plugin_platform(Path(args.repo_root).resolve())
    print("patched" if changed else "already-installed")