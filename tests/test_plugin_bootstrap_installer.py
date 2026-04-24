"""插件平台即插即用安装器测试。"""

from pathlib import Path

from scripts import install_plugin_platform


def test_install_plugin_platform_patches_fresh_clone_files(tmp_path):
    repo = tmp_path / "PlotPilot"
    (repo / "interfaces").mkdir(parents=True)
    (repo / "scripts").mkdir(parents=True)
    (repo / "frontend" / "public").mkdir(parents=True)
    (repo / "plugins").mkdir(parents=True)

    (repo / "interfaces" / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n",
        encoding="utf-8",
    )
    (repo / "scripts" / "start_daemon.py").write_text(
        "import sys\n"
        "from application.paths import AITEXT_ROOT\n",
        encoding="utf-8",
    )
    (repo / "frontend" / "index.html").write_text(
        "<html><body><div id=\"app\"></div><script type=\"module\" src=\"/src/main.ts\"></script></body></html>\n",
        encoding="utf-8",
    )
    (repo / "frontend" / "vite.config.ts").write_text(
        "import { defineConfig } from 'vite'\n"
        "export default defineConfig({\n"
        "  server: {\n"
        "    proxy: {\n"
        "      '/api': { target: 'http://127.0.0.1:8005', changeOrigin: true },\n"
        "    },\n"
        "  },\n"
        "})\n",
        encoding="utf-8",
    )

    changed = install_plugin_platform.install_plugin_platform(repo)

    assert changed is True
    assert "from plugins.loader import init_api_plugins, create_plugin_manifest_router" in (
        repo / "interfaces" / "main.py"
    ).read_text(encoding="utf-8")
    assert "init_daemon_plugins" in (repo / "scripts" / "start_daemon.py").read_text(encoding="utf-8")
    assert '<script src="/plugin-loader.js"></script>' in (repo / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "'/plugins': {" in (repo / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert (repo / "frontend" / "public" / "plugin-loader.js").exists()
    assert (repo / "plugins" / "loader.py").exists()
    assert (repo / "plugins" / "platform" / "hook_dispatcher.py").exists()
    assert (repo / "plugins" / "platform" / "host_facade.py").exists()


def test_install_plugin_platform_is_idempotent(tmp_path):
    repo = tmp_path / "PlotPilot"
    (repo / "interfaces").mkdir(parents=True)
    (repo / "scripts").mkdir(parents=True)
    (repo / "frontend" / "public").mkdir(parents=True)
    (repo / "plugins").mkdir(parents=True)

    (repo / "interfaces" / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n",
        encoding="utf-8",
    )
    (repo / "scripts" / "start_daemon.py").write_text(
        "import sys\nfrom application.paths import AITEXT_ROOT\n",
        encoding="utf-8",
    )
    (repo / "frontend" / "index.html").write_text(
        "<html><body><div id=\"app\"></div><script type=\"module\" src=\"/src/main.ts\"></script></body></html>\n",
        encoding="utf-8",
    )
    (repo / "frontend" / "vite.config.ts").write_text(
        "import { defineConfig } from 'vite'\n"
        "export default defineConfig({ server: { proxy: { '/api': { target: 'http://127.0.0.1:8005', changeOrigin: true } } } })\n",
        encoding="utf-8",
    )

    first = install_plugin_platform.install_plugin_platform(repo)
    second = install_plugin_platform.install_plugin_platform(repo)

    assert first is True
    assert second is False
    assert (repo / "interfaces" / "main.py").read_text(encoding="utf-8").count(
        "from plugins.loader import init_api_plugins, create_plugin_manifest_router"
    ) == 1