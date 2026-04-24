"""Install PlotPilot plugin-platform bootstrap into a fresh clone.

This script patches the minimum host touchpoints so plugins can be
distributed as a portable platform bundle instead of requiring manual edits.
"""

from __future__ import annotations

from pathlib import Path


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
_VITE_PLUGINS_PROXY = (
    "      '/plugins': {\n"
    "        target: 'http://127.0.0.1:3000',\n"
    "        changeOrigin: true,\n"
    "        rewrite: (path) => path,\n"
    "      },\n"
)
_VITE_SERVER_BLOCK = (
    "  server: {\n"
    "    port: 3001,\n"
    "    host: '127.0.0.1',\n"
    "    proxy: {\n"
    "      '/plugins': {\n"
    "        target: 'http://127.0.0.1:3000',\n"
    "        changeOrigin: true,\n"
    "        rewrite: (path) => path,\n"
    "      },\n"
    "      '/api': {\n"
    "        target: 'http://127.0.0.1:3000',\n"
    "        changeOrigin: true,\n"
    "        ws: true,\n"
    "        timeout: 0,\n"
    "        rewrite: (path) => path,\n"
    "      },\n"
    "    },\n"
    "  },\n"
)


def _write_if_different(src: Path, dst: Path) -> bool:
    src_text = src.read_text(encoding="utf-8")
    if dst.exists() and dst.read_text(encoding="utf-8") == src_text:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src_text, encoding="utf-8")
    return True


def _copytree_if_different(src: Path, dst: Path) -> bool:
    changed = False
    for source_file in src.rglob("*"):
        if any(part in {"__pycache__", ".pytest_cache"} for part in source_file.parts):
            continue
        if source_file.suffix in {".pyc", ".pyo"}:
            continue
        if not source_file.is_file():
            continue
        relative_path = source_file.relative_to(src)
        changed |= _write_if_different(source_file, dst / relative_path)
    return changed


def _ensure_main_py(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    changed = False
    if _HOST_IMPORT not in text:
        import_anchors = [
            "from fastapi import FastAPI\n",
            "from fastapi import FastAPI, HTTPException\n",
        ]
        anchor = next((item for item in import_anchors if item in text), None)
        if anchor is None:
            raise ValueError(f"Cannot patch {path}: missing FastAPI import anchor")
        text = text.replace(anchor, anchor + _HOST_IMPORT, 1)
        changed = True
    if "def init_api(app: FastAPI) -> list[str]:" not in text:
        if "# 创建 FastAPI 应用\n" in text:
            text = text.replace("# 创建 FastAPI 应用\n", _HOST_INIT + "\n# 创建 FastAPI 应用\n", 1)
        elif "app = FastAPI()\n" in text:
            text = text.replace("app = FastAPI()\n", _HOST_INIT + "\napp = FastAPI()\n", 1)
        elif "app = FastAPI(\n" in text:
            text = text.replace("app = FastAPI(\n", _HOST_INIT + "\napp = FastAPI(\n", 1)
        else:
            text += _HOST_INIT
        changed = True
    if changed:
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
    if changed:
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
    original = text

    if "port: 3001" not in text:
        text = text.replace("port: 3000", "port: 3001")
    if "host: '127.0.0.1'" not in text and "host: '0.0.0.0'" in text:
        text = text.replace("host: '0.0.0.0'", "host: '127.0.0.1'")
    if "target: 'http://127.0.0.1:3000'" not in text and "target: 'http://127.0.0.1:8005'" in text:
        text = text.replace("target: 'http://127.0.0.1:8005'", "target: 'http://127.0.0.1:3000'")

    if "'/plugins': {" not in text:
        multiline_anchor = "    proxy: {\n"
        if multiline_anchor in text:
            text = text.replace(multiline_anchor, multiline_anchor + _VITE_PLUGINS_PROXY, 1)
        else:
            inline_anchor = "proxy: {"
            if inline_anchor not in text:
                raise ValueError(f"Cannot patch {path}: missing Vite proxy anchor")
            text = text.replace(
                inline_anchor,
                "proxy: { '/plugins': { target: 'http://127.0.0.1:3000', changeOrigin: true, rewrite: (path) => path }, ",
                1,
            )

    needs_server_defaults = "port: 3001" not in text or "host: '127.0.0.1'" not in text
    if needs_server_defaults:
        multiline_server_anchor = "  server: {\n"
        if multiline_server_anchor in text:
            server_block = text.split(multiline_server_anchor, 1)[1]
            if "port: 3001" not in server_block:
                text = text.replace(multiline_server_anchor, multiline_server_anchor + "    port: 3001,\n", 1)
            if "host: '127.0.0.1'" not in server_block:
                text = text.replace(multiline_server_anchor, multiline_server_anchor + "    host: '127.0.0.1',\n", 1)
        elif "server: {" in text:
            text = text.replace("server: {", "server: { port: 3001, host: '127.0.0.1', ", 1)
        else:
            define_anchor = "export default defineConfig({\n"
            if define_anchor in text:
                text = text.replace(define_anchor, define_anchor + _VITE_SERVER_BLOCK, 1)
            elif "export default defineConfig({" in text:
                text = text.replace("export default defineConfig({", "export default defineConfig({\n" + _VITE_SERVER_BLOCK, 1)
            else:
                raise ValueError(f"Cannot patch {path}: missing defineConfig anchor")

    if text == original:
        return False
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
    changed |= _write_if_different(source_root / "frontend" / "public" / "plugin-loader.js", repo_root / "frontend" / "public" / "plugin-loader.js")
    changed |= _write_if_different(source_root / "plugins" / "loader.py", repo_root / "plugins" / "loader.py")
    changed |= _copytree_if_different(source_root / "plugins" / "platform", repo_root / "plugins" / "platform")
    return changed


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Install PlotPilot plugin platform into a repository")
    parser.add_argument("repo_root", nargs="?", default=".", help="Target PlotPilot repository root")
    args = parser.parse_args()

    changed = install_plugin_platform(Path(args.repo_root).resolve())
    print("patched" if changed else "already-installed")
