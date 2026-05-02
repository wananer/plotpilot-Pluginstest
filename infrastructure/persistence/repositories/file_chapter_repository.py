"""File-backed Chapter repository for legacy integration tests."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from domain.novel.entities.chapter import Chapter, ChapterStatus
from domain.novel.repositories.chapter_repository import ChapterRepository
from domain.novel.value_objects.chapter_id import ChapterId
from domain.novel.value_objects.novel_id import NovelId
from infrastructure.persistence.storage.file_storage import FileStorage


class FileChapterRepository(ChapterRepository):
    """Small compatibility repository backed by FileStorage."""

    def __init__(self, storage: FileStorage):
        self.storage = storage

    def save(self, chapter: Chapter) -> None:
        self.storage.write_json(self._path(chapter.novel_id.value, str(chapter.id)), self._to_dict(chapter))

    def get_by_id(self, chapter_id: ChapterId) -> Optional[Chapter]:
        for path in self.storage.list_files("novels/*/chapters/*.json"):
            data = self.storage.read_json(path)
            if str(data.get("id") or "") == chapter_id.value:
                return self._from_dict(data)
        return None

    def list_by_novel(self, novel_id: NovelId) -> List[Chapter]:
        chapters = []
        for path in self.storage.list_files(f"novels/{novel_id.value}/chapters/*.json"):
            chapters.append(self._from_dict(self.storage.read_json(path)))
        return sorted(chapters, key=lambda item: item.number)

    def exists(self, chapter_id: ChapterId) -> bool:
        return self.get_by_id(chapter_id) is not None

    def delete(self, chapter_id: ChapterId) -> None:
        for path in self.storage.list_files("novels/*/chapters/*.json"):
            data = self.storage.read_json(path)
            if str(data.get("id") or "") == chapter_id.value:
                self.storage.delete(path)
                return

    @staticmethod
    def _path(novel_id: str, chapter_id: str) -> str:
        return f"novels/{novel_id}/chapters/{chapter_id}.json"

    @staticmethod
    def _to_dict(chapter: Chapter) -> Dict[str, Any]:
        return {
            "id": str(chapter.id),
            "novel_id": chapter.novel_id.value,
            "number": chapter.number,
            "title": chapter.title,
            "content": chapter.content,
            "outline": chapter.outline,
            "status": chapter.status.value if hasattr(chapter.status, "value") else str(chapter.status),
            "tension_score": chapter.tension_score,
            "plot_tension": chapter.plot_tension,
            "emotional_tension": chapter.emotional_tension,
            "pacing_tension": chapter.pacing_tension,
        }

    @staticmethod
    def _from_dict(data: Dict[str, Any]) -> Chapter:
        try:
            status = ChapterStatus(str(data.get("status") or "draft"))
        except ValueError:
            status = ChapterStatus.DRAFT
        return Chapter(
            id=str(data["id"]),
            novel_id=NovelId(str(data["novel_id"])),
            number=int(data.get("number") or 0),
            title=str(data.get("title") or ""),
            content=str(data.get("content") or ""),
            outline=str(data.get("outline") or ""),
            status=status,
            tension_score=float(data.get("tension_score") or 50.0),
            plot_tension=float(data.get("plot_tension") or 50.0),
            emotional_tension=float(data.get("emotional_tension") or 50.0),
            pacing_tension=float(data.get("pacing_tension") or 50.0),
        )
