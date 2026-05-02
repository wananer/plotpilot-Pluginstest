"""File-backed Novel repository for legacy integration tests."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from domain.novel.entities.novel import AutopilotStatus, Novel, NovelStage
from domain.novel.repositories.novel_repository import NovelRepository
from domain.novel.value_objects.novel_id import NovelId
from infrastructure.persistence.storage.file_storage import FileStorage


class FileNovelRepository(NovelRepository):
    """Small compatibility repository backed by FileStorage."""

    def __init__(self, storage: FileStorage):
        self.storage = storage

    def save(self, novel: Novel) -> None:
        self.storage.write_json(self._path(novel.novel_id.value), self._to_dict(novel))

    async def async_save(self, novel: Novel) -> None:
        self.save(novel)

    def get_by_id(self, novel_id: NovelId) -> Optional[Novel]:
        path = self._path(novel_id.value)
        if not self.storage.exists(path):
            return None
        return self._from_dict(self.storage.read_json(path))

    def list_all(self) -> List[Novel]:
        novels = []
        for path in self.storage.list_files("novels/*/novel.json"):
            novels.append(self._from_dict(self.storage.read_json(path)))
        return sorted(novels, key=lambda item: item.novel_id.value)

    def find_by_autopilot_status(self, status: AutopilotStatus) -> List[Novel]:
        status_value = status.value if hasattr(status, "value") else str(status)
        return [
            novel for novel in self.list_all()
            if (novel.autopilot_status.value if hasattr(novel.autopilot_status, "value") else str(novel.autopilot_status)) == status_value
        ]

    def delete(self, novel_id: NovelId) -> None:
        self.storage.delete(self._path(novel_id.value))

    def exists(self, novel_id: NovelId) -> bool:
        return self.storage.exists(self._path(novel_id.value))

    @staticmethod
    def _path(novel_id: str) -> str:
        return f"novels/{novel_id}/novel.json"

    @staticmethod
    def _to_dict(novel: Novel) -> Dict[str, Any]:
        return {
            "id": novel.novel_id.value,
            "title": novel.title,
            "author": novel.author,
            "target_chapters": novel.target_chapters,
            "premise": novel.premise,
            "stage": novel.stage.value if hasattr(novel.stage, "value") else str(novel.stage),
            "autopilot_status": novel.autopilot_status.value if hasattr(novel.autopilot_status, "value") else str(novel.autopilot_status),
            "auto_approve_mode": bool(novel.auto_approve_mode),
            "current_stage": novel.current_stage.value if hasattr(novel.current_stage, "value") else str(novel.current_stage),
            "current_act": novel.current_act,
            "current_chapter_in_act": novel.current_chapter_in_act,
            "max_auto_chapters": novel.max_auto_chapters,
            "current_auto_chapters": novel.current_auto_chapters,
            "last_chapter_tension": novel.last_chapter_tension,
            "consecutive_error_count": novel.consecutive_error_count,
            "current_beat_index": novel.current_beat_index,
            "target_words_per_chapter": novel.target_words_per_chapter,
        }

    @staticmethod
    def _from_dict(data: Dict[str, Any]) -> Novel:
        def stage(value: str) -> NovelStage:
            try:
                return NovelStage(value)
            except ValueError:
                return NovelStage.PLANNING

        def autopilot_status(value: str) -> AutopilotStatus:
            try:
                return AutopilotStatus(value)
            except ValueError:
                return AutopilotStatus.STOPPED

        return Novel(
            id=NovelId(str(data["id"])),
            title=str(data.get("title") or ""),
            author=str(data.get("author") or ""),
            target_chapters=int(data.get("target_chapters") or 0),
            premise=str(data.get("premise") or ""),
            stage=stage(str(data.get("stage") or "planning")),
            autopilot_status=autopilot_status(str(data.get("autopilot_status") or "stopped")),
            auto_approve_mode=bool(data.get("auto_approve_mode", False)),
            current_stage=stage(str(data.get("current_stage") or data.get("stage") or "planning")),
            current_act=int(data.get("current_act") or 0),
            current_chapter_in_act=int(data.get("current_chapter_in_act") or 0),
            max_auto_chapters=int(data.get("max_auto_chapters") or 9999),
            current_auto_chapters=int(data.get("current_auto_chapters") or 0),
            last_chapter_tension=int(data.get("last_chapter_tension") or 0),
            consecutive_error_count=int(data.get("consecutive_error_count") or 0),
            current_beat_index=int(data.get("current_beat_index") or 0),
            target_words_per_chapter=int(data.get("target_words_per_chapter") or 2500),
        )
