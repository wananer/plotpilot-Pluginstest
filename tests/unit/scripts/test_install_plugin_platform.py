from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "install_plugin_platform.py"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_installer(workdir: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _seed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _write(repo / "plugins" / "loader.py", "placeholder loader\n")
    _write(repo / "frontend" / "public" / "plugin-loader.js", "placeholder runtime\n")
    _write(
        repo / "frontend" / "index.html",
        """<!doctype html>
<html>
  <body>
    <div id=\"app\"></div>
    <script type=\"module\" src=\"/src/main.ts\"></script>
  </body>
</html>
""",
    )
    _write(
        repo / "frontend" / "vite.config.ts",
        """import { defineConfig } from 'vite'
export default defineConfig({
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8005',
        changeOrigin: true,
        ws: true,
        timeout: 0,
        rewrite: (path) => path,
      },
    },
  },
})
""",
    )
    _write(
        repo / "interfaces" / "main.py",
        """from infrastructure.persistence.database.connection import get_database

# ── imports above ──
app = FastAPI(
    title=\"PlotPilot API\",
    version=\"1.0.2\",
    description=\"PlotPilot（墨枢）AI 小说创作平台 API\",
    redirect_slashes=True,
)

# ── 前端静态文件托管 ──
@app.on_event(\"startup\")
async def startup_event():
    logger.info(f\"📊 Registered {len(app.routes)} routes\")
""",
    )
    _write(
        repo / "scripts" / "start_daemon.py",
        """from interfaces.api.middleware.logging_config import setup_logging

logger = None

if __name__ == \"__main__\":
    daemon = build_daemon()
""",
    )
    return repo


def test_installer_injects_minimal_plugin_platform(tmp_path: Path):
    repo = _seed_repo(tmp_path)

    stdout = _run_installer(repo)

    assert "Installed/updated plugin platform:" in stdout
    assert (repo / "plugins" / "loader.py").read_text(encoding="utf-8").startswith('"""Generic PlotPilot plugin loader.')
    assert "<script src=\"/plugin-loader.js\"></script>" in (repo / "frontend" / "index.html").read_text(encoding="utf-8")
    vite_content = (repo / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert "'/plugins': {" in vite_content
    main_content = (repo / "interfaces" / "main.py").read_text(encoding="utf-8")
    assert "from plugins.loader import create_plugin_manifest_router, init_api_plugins" in main_content
    assert 'app.include_router(create_plugin_manifest_router(), prefix="/api/v1")' in main_content
    assert "init_api_plugins(app)" in main_content
    daemon_content = (repo / "scripts" / "start_daemon.py").read_text(encoding="utf-8")
    assert "from plugins.loader import init_daemon_plugins" in daemon_content
    assert "init_daemon_plugins()" in daemon_content


def test_installer_is_idempotent(tmp_path: Path):
    repo = _seed_repo(tmp_path)

    _run_installer(repo)
    stdout = _run_installer(repo)

    assert "Plugin platform already installed; no changes made." in stdout
    index_content = (repo / "frontend" / "index.html").read_text(encoding="utf-8")
    assert index_content.count("<script src=\"/plugin-loader.js\"></script>") == 1
    vite_content = (repo / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert vite_content.count("'/plugins': {") == 1
    main_content = (repo / "interfaces" / "main.py").read_text(encoding="utf-8")
    assert main_content.count("init_api_plugins(app)") == 1
    daemon_content = (repo / "scripts" / "start_daemon.py").read_text(encoding="utf-8")
    assert daemon_content.count("init_daemon_plugins()") == 1
