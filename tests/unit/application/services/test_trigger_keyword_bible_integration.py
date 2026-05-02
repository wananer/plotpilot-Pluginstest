"""Scene trigger keywords no longer inject full Bible world-setting slices."""
from unittest.mock import MagicMock

from application.services.context_builder import ContextBuilder


def _make_scene_director(trigger_keywords):
    sd = MagicMock()
    sd.trigger_keywords = trigger_keywords
    sd.characters = []
    sd.locations = []
    return sd


def _make_context_builder():
    storyline_manager = MagicMock()
    storyline_manager.repository.get_by_novel_id.return_value = []

    chapter_repository = MagicMock()
    chapter_repository.list_by_novel.return_value = []

    return ContextBuilder(
        bible_service=MagicMock(),
        storyline_manager=storyline_manager,
        relationship_engine=MagicMock(),
        vector_store=None,
        novel_repository=MagicMock(),
        chapter_repository=chapter_repository,
        plot_arc_repository=None,
        embedding_service=None,
    )


def test_trigger_keywords_do_not_reinflate_bible_slices():
    """Trigger keywords are accepted but do not revive the removed Layer2 Bible slice."""
    cb = _make_context_builder()

    result = cb.build_structured_context(
        novel_id="novel-1",
        chapter_number=5,
        outline="主角与宿敌展开战斗",
        max_tokens=50000,
        scene_director=_make_scene_director(["战斗"]),
    )

    combined = "\n".join(
        [result["layer1_text"], result["layer2_text"], result["layer3_text"]]
    )
    assert "生命周期行为准则" in combined
    assert "Triggered World Settings" not in combined


def test_empty_trigger_keywords_are_safe():
    cb = _make_context_builder()

    result = cb.build_structured_context(
        novel_id="novel-1",
        chapter_number=5,
        outline="主角与宿敌展开战斗",
        max_tokens=50000,
        scene_director=_make_scene_director([]),
    )

    assert result["token_usage"]["total"] > 0


def test_unknown_trigger_keywords_are_safe():
    cb = _make_context_builder()

    result = cb.build_structured_context(
        novel_id="novel-1",
        chapter_number=5,
        outline="签订神秘契约",
        max_tokens=50000,
        scene_director=_make_scene_director(["神秘契约"]),
    )

    assert result["token_usage"]["total"] > 0
