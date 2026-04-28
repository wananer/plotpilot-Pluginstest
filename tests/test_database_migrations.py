import sqlite3
from pathlib import Path

from infrastructure.persistence.database import connection


def test_macro_diagnosis_context_patch_runs_after_base_table(tmp_path, monkeypatch):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "add_macro_diagnosis_context_patch.sql").write_text(
        "\n".join(
            [
                "ALTER TABLE macro_diagnosis_results ADD COLUMN context_patch TEXT;",
                "ALTER TABLE macro_diagnosis_results ADD COLUMN total_words_at_run INTEGER DEFAULT 0;",
            ]
        ),
        encoding="utf-8",
    )
    (migrations_dir / "add_macro_diagnosis_results.sql").write_text(
        """
        CREATE TABLE IF NOT EXISTS macro_diagnosis_results (
            id TEXT PRIMARY KEY,
            novel_id TEXT NOT NULL,
            trigger_reason TEXT NOT NULL,
            trait TEXT NOT NULL,
            breakpoints TEXT NOT NULL DEFAULT '[]'
        );
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(connection, "_database_asset_dir", lambda: tmp_path)

    conn = sqlite3.connect(":memory:")
    connection._apply_migration_files(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(macro_diagnosis_results)").fetchall()}
    assert {"context_patch", "total_words_at_run"}.issubset(columns)


def test_macro_diagnosis_migration_sort_key_places_base_before_patch():
    ordered = sorted(
        [
            "add_macro_diagnosis_context_patch.sql",
            "add_macro_diagnosis_results.sql",
            "add_auto_approve_mode.sql",
        ],
        key=lambda name: connection._migration_sort_key(Path(name)),
    )

    assert ordered.index("add_macro_diagnosis_results.sql") < ordered.index("add_macro_diagnosis_context_patch.sql")
