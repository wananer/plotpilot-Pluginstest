"""Shared pytest setup for repository-wide tests.

The app has several module-level ``TestClient(app)`` tests, so core test
environment must be configured at import time before those modules import the
FastAPI app.
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from urllib.request import urlopen

import pytest

# Add paths immediately at module import time
_root = Path(__file__).resolve().parent.parent
_parent = _root.parent
_session_data_dir = Path(tempfile.mkdtemp(prefix="plotpilot-pytest-data-"))

os.environ.setdefault("DISABLE_AUTO_DAEMON", "1")
os.environ.setdefault("VECTOR_STORE_ENABLED", "false")
os.environ.setdefault("AITEXT_PROD_DATA_DIR", str(_session_data_dir))

# Add project root FIRST (for infrastructure, domain, etc.) - this is most important
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# Add parent for aitext package - but AFTER project root
if str(_parent) not in sys.path:
    sys.path.append(str(_parent))  # Use append instead of insert to keep it lower priority


def pytest_configure(config):
    """Configure pytest - ensure paths are set before test collection"""
    # Ensure project root is first in sys.path
    root_str = str(_root)
    if root_str in sys.path:
        sys.path.remove(root_str)
    sys.path.insert(0, root_str)


def pytest_unconfigure(config):
    shutil.rmtree(_session_data_dir, ignore_errors=True)


def _reset_runtime_singletons():
    """Reset process-global runtime state that otherwise leaks between tests."""
    try:
        from infrastructure.persistence.database import connection

        if connection._db_instance is not None:
            connection._db_instance.close()
        connection._db_instance = None
    except Exception:
        pass

    try:
        import interfaces.api.dependencies as dependencies

        dependencies._storage = None
        dependencies._vector_store_singleton = None
        dependencies._vector_store_init_failed = False
        for fn_name in (
            "get_llm_control_service",
            "get_llm_provider_factory",
        ):
            fn = getattr(dependencies, fn_name, None)
            if hasattr(fn, "cache_clear"):
                fn.cache_clear()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def reset_runtime_state():
    _reset_runtime_singletons()
    yield
    try:
        from interfaces.main import app

        app.dependency_overrides.clear()
    except Exception:
        pass
    _reset_runtime_singletons()


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("AITEXT_PROD_DATA_DIR", str(data_dir))

    import application.paths as paths
    import interfaces.api.dependencies as dependencies

    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(dependencies, "DATA_DIR", data_dir)
    _reset_runtime_singletons()
    return data_dir


@pytest.fixture
def isolated_db(isolated_data_dir):
    from infrastructure.persistence.database.connection import DatabaseConnection

    db = DatabaseConnection(str(isolated_data_dir / "aitext.db"))
    yield db
    db.close()


@pytest.fixture
def api_client(isolated_db, monkeypatch):
    from fastapi.testclient import TestClient
    from infrastructure.persistence.database import connection
    import interfaces.api.dependencies as dependencies
    from interfaces.main import app

    def get_test_database():
        return isolated_db

    monkeypatch.setattr(connection, "get_database", get_test_database)
    monkeypatch.setattr(dependencies, "get_database", get_test_database)
    connection._db_instance = isolated_db
    with TestClient(app) as client:
        yield client


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def backend_base_url():
    data_dir = Path(tempfile.mkdtemp(prefix="plotpilot-e2e-data-"))
    port = _free_port()
    env = os.environ.copy()
    env.update(
        {
            "AITEXT_PROD_DATA_DIR": str(data_dir),
            "DISABLE_AUTO_DAEMON": "1",
            "VECTOR_STORE_ENABLED": "false",
            "PYTHONPATH": str(_root),
        }
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "interfaces.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                output = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"Backend exited during startup:\n{output}")
            try:
                with urlopen(f"{base_url}/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.25)
        else:
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"Backend did not become healthy:\n{output}")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        shutil.rmtree(data_dir, ignore_errors=True)
