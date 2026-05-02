"""File-backed Storyline repository for legacy integration tests."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from domain.novel.entities.storyline import Storyline
from domain.novel.repositories.storyline_repository import StorylineRepository
from domain.novel.value_objects.novel_id import NovelId
from domain.novel.value_objects.storyline_milestone import StorylineMilestone
from domain.novel.value_objects.storyline_status import StorylineStatus
from domain.novel.value_objects.storyline_type import StorylineType
from infrastructure.persistence.storage.file_storage import FileStorage


class FileStorylineRepository(StorylineRepository):
    """Small compatibility repository backed by FileStorage."""

    def __init__(self, storage: FileStorage):
        self.storage = storage

    def save(self, storyline: Storyline) -> None:
        self.storage.write_json(
            self._path(storyline.novel_id.value, storyline.id),
            self._to_dict(storyline),
        )

    def get_by_id(self, storyline_id: str) -> Optional[Storyline]:
        for path in self.storage.list_files("novels/*/storylines/*.json"):
            data = self.storage.read_json(path)
            if str(data.get("id") or "") == storyline_id:
                return self._from_dict(data)
        return None

    def get_by_novel_id(self, novel_id: NovelId) -> List[Storyline]:
        storylines = []
        for path in self.storage.list_files(f"novels/{novel_id.value}/storylines/*.json"):
            storylines.append(self._from_dict(self.storage.read_json(path)))
        return sorted(storylines, key=lambda item: item.id)

    def delete(self, storyline_id: str) -> None:
        for path in self.storage.list_files("novels/*/storylines/*.json"):
            data = self.storage.read_json(path)
            if str(data.get("id") or "") == storyline_id:
                self.storage.delete(path)
                return

    @staticmethod
    def _path(novel_id: str, storyline_id: str) -> str:
        return f"novels/{novel_id}/storylines/{storyline_id}.json"

    @staticmethod
    def _to_dict(storyline: Storyline) -> Dict[str, Any]:
        return {
            "id": storyline.id,
            "novel_id": storyline.novel_id.value,
            "storyline_type": storyline.storyline_type.value,
            "status": storyline.status.value,
            "estimated_chapter_start": storyline.estimated_chapter_start,
            "estimated_chapter_end": storyline.estimated_chapter_end,
            "current_milestone_index": storyline.current_milestone_index,
            "name": storyline.name,
            "description": storyline.description,
            "last_active_chapter": storyline.last_active_chapter,
            "progress_summary": storyline.progress_summary,
            "milestones": [
                {
                    "order": item.order,
                    "title": item.title,
                    "description": item.description,
                    "target_chapter_start": item.target_chapter_start,
                    "target_chapter_end": item.target_chapter_end,
                    "prerequisites": list(item.prerequisites),
                    "triggers": list(item.triggers),
                }
                for item in storyline.milestones
            ],
        }

    @staticmethod
    def _from_dict(data: Dict[str, Any]) -> Storyline:
        milestones = [
            StorylineMilestone(
                order=int(item.get("order") or 0),
                title=str(item.get("title") or ""),
                description=str(item.get("description") or ""),
                target_chapter_start=int(item.get("target_chapter_start") or 1),
                target_chapter_end=int(item.get("target_chapter_end") or 1),
                prerequisites=[str(value) for value in (item.get("prerequisites") or [])],
                triggers=[str(value) for value in (item.get("triggers") or [])],
            )
            for item in (data.get("milestones") or [])
            if isinstance(item, dict)
        ]
        return Storyline(
            id=str(data["id"]),
            novel_id=NovelId(str(data["novel_id"])),
            storyline_type=StorylineType(str(data.get("storyline_type") or "main_plot")),
            status=StorylineStatus(str(data.get("status") or "active")),
            estimated_chapter_start=int(data.get("estimated_chapter_start") or 1),
            estimated_chapter_end=int(data.get("estimated_chapter_end") or 1),
            milestones=milestones,
            current_milestone_index=int(data.get("current_milestone_index") or 0),
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            last_active_chapter=int(data.get("last_active_chapter") or 0),
            progress_summary=str(data.get("progress_summary") or ""),
        )
