from domain.novel.entities.novel import Novel
from domain.novel.value_objects.novel_id import NovelId
from infrastructure.persistence.database.sqlite_novel_repository import (
    SqliteNovelRepository,
)


def test_sqlite_novel_repository_round_trips_route_gate_state(isolated_db):
    repo = SqliteNovelRepository(isolated_db)
    novel_id = NovelId("novel-route-state")
    novel = Novel(
        id=novel_id,
        title="Route State",
        author="Tester",
        target_chapters=10,
        route_gate_status="auto_revised",
        last_route_issue={
            "issue_type": "evolution_route_missing_transition",
            "severity": "blocking",
            "message": "Chapter opened at a new location without route evidence.",
        },
        auto_revision_history=[
            {
                "chapter": 8,
                "auto_revised_reason": "evolution_route_missing_transition",
                "before_opening": "They were suddenly at the archive door.",
                "after_opening_digest": "Added evacuation path and travel time.",
                "remaining_risk": False,
            }
        ],
        constraint_gate_status="needs_review",
        last_constraint_issue={"constraint_type": "time_pressure", "issue_type": "evolution_time_pressure_drift"},
        constraint_revision_history=[{"chapter": 8, "constraint_type": "time_pressure"}],
    )

    repo.save(novel)

    restored = repo.get_by_id(novel_id)

    assert restored is not None
    assert restored.route_gate_status == "auto_revised"
    assert (
        restored.last_route_issue["issue_type"]
        == "evolution_route_missing_transition"
    )
    assert restored.auto_revision_history == novel.auto_revision_history
    assert restored.constraint_gate_status == "needs_review"
    assert restored.last_constraint_issue["constraint_type"] == "time_pressure"
    assert restored.constraint_revision_history == novel.constraint_revision_history
