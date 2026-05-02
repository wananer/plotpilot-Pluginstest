"""测试依赖注入配置"""
import os
import pytest
from unittest.mock import patch, MagicMock
import interfaces.api.dependencies as dependencies


class TestGetVectorStore:
    """测试 get_vector_store 依赖注入函数"""

    def setup_method(self):
        dependencies._vector_store_singleton = None
        dependencies._vector_store_init_failed = False

    def test_get_vector_store_returns_chromadb_when_no_env(self):
        """未设置环境变量时默认返回 ChromaDB 实例。"""
        with patch.dict(os.environ, {}, clear=True):
            with patch("infrastructure.ai.chromadb_vector_store.ChromaDBVectorStore") as mock_chromadb:
                mock_instance = MagicMock()
                mock_chromadb.return_value = mock_instance

                result = dependencies.get_vector_store()

                assert result is mock_instance
                mock_chromadb.assert_called_once_with(persist_directory="./data/chromadb")

    def test_get_vector_store_returns_none_when_disabled(self):
        """VECTOR_STORE_ENABLED 为 false 时返回 None。"""
        with patch.dict(os.environ, {"VECTOR_STORE_ENABLED": "false"}, clear=True):
            result = dependencies.get_vector_store()
            assert result is None

    def test_get_vector_store_returns_chromadb_by_default(self):
        """未指定类型时默认使用 ChromaDB。"""
        with patch.dict(os.environ, {}, clear=True):
            with patch("infrastructure.ai.chromadb_vector_store.ChromaDBVectorStore") as mock_chromadb:
                mock_instance = MagicMock()
                mock_chromadb.return_value = mock_instance

                result = dependencies.get_vector_store()

                assert result is mock_instance
                mock_chromadb.assert_called_once_with(persist_directory="./data/chromadb")

    def test_get_vector_store_with_custom_chromadb_path(self):
        """VECTOR_STORE_PATH controls the local ChromaDB persistence directory."""
        with patch.dict(os.environ, {"VECTOR_STORE_PATH": "/tmp/plotpilot-chroma"}, clear=True):
            with patch("infrastructure.ai.chromadb_vector_store.ChromaDBVectorStore") as mock_chromadb:
                mock_instance = MagicMock()
                mock_chromadb.return_value = mock_instance

                result = dependencies.get_vector_store()

                assert result is mock_instance
                mock_chromadb.assert_called_once_with(persist_directory="/tmp/plotpilot-chroma")
