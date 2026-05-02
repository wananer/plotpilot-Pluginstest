import sqlite3

from infrastructure.ai.prompt_manager import PromptManager
from plugins.world_evolution_core.prompt_registry import EVOLUTION_PROMPTS


class _MemoryDb:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE prompt_templates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                category TEXT NOT NULL DEFAULT 'user',
                version TEXT NOT NULL DEFAULT '1.0.0',
                author TEXT DEFAULT '',
                icon TEXT DEFAULT '',
                color TEXT DEFAULT '',
                is_builtin INTEGER NOT NULL DEFAULT 0,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE prompt_nodes (
                id TEXT PRIMARY KEY,
                template_id TEXT NOT NULL,
                node_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                category TEXT NOT NULL DEFAULT 'generation',
                source TEXT DEFAULT '',
                output_format TEXT DEFAULT 'text',
                contract_module TEXT,
                contract_model TEXT,
                tags TEXT NOT NULL DEFAULT '[]',
                variables TEXT NOT NULL DEFAULT '[]',
                system_file TEXT,
                owner TEXT NOT NULL DEFAULT 'native',
                runtime_status TEXT NOT NULL DEFAULT 'asset',
                authority_domain TEXT NOT NULL DEFAULT '',
                runtime_reader TEXT NOT NULL DEFAULT 'hardcoded',
                editable INTEGER NOT NULL DEFAULT 1,
                is_builtin INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                active_version_id TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE prompt_versions (
                id TEXT PRIMARY KEY,
                node_id TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                system_prompt TEXT NOT NULL DEFAULT '',
                user_template TEXT NOT NULL DEFAULT '',
                change_summary TEXT DEFAULT '',
                created_by TEXT DEFAULT 'system',
                created_at TEXT,
                UNIQUE(node_id, version_number)
            );
            """
        )

    def get_connection(self):
        return self.conn

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def commit(self):
        self.conn.commit()


def test_seed_plugin_prompts_is_idempotent_and_exposes_runtime_metadata():
    mgr = PromptManager(_MemoryDb())
    mgr._seeded = True

    first = mgr.seed_prompt_entries(
        EVOLUTION_PROMPTS,
        template_name="Evolution World Assistant",
        template_description="Evolution 插件运行时提示词",
        template_category="plugin",
    )
    second = mgr.seed_prompt_entries(
        EVOLUTION_PROMPTS,
        template_name="Evolution World Assistant",
        template_description="Evolution 插件运行时提示词",
        template_category="plugin",
    )

    assert first["created"] == len(EVOLUTION_PROMPTS)
    assert second["created"] == 0
    node = mgr.get_node("plugin.world_evolution_core.structured-extraction", by_key=True)
    assert node is not None
    data = node.to_dict()
    assert data["owner"] == "plugin:world_evolution_core"
    assert data["runtime_status"] == "active"
    assert data["authority_domain"] == "chapter_facts"
    assert data["editable"] is True


def test_seeded_plugin_prompt_edit_renders_active_version():
    mgr = PromptManager(_MemoryDb())
    mgr._seeded = True
    mgr.seed_prompt_entries(
        EVOLUTION_PROMPTS,
        template_name="Evolution World Assistant",
        template_description="Evolution 插件运行时提示词",
        template_category="plugin",
    )
    node = mgr.get_node("plugin.world_evolution_core.connection-test", by_key=True)
    assert node is not None

    mgr.update_node(
        node.id,
        system_prompt="edited system",
        user_template="edited user {token}",
        change_summary="测试编辑生效",
    )

    rendered = mgr.render("plugin.world_evolution_core.connection-test", {"token": "OK"})
    assert rendered == {"system": "edited system", "user": "edited user OK"}
