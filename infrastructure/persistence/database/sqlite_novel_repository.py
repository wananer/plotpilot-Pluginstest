"""SQLite Novel Repository 实现"""
import logging
import json
from typing import Optional, List
from datetime import datetime
from domain.novel.entities.novel import Novel, AutopilotStatus, NovelStage
from domain.novel.value_objects.novel_id import NovelId
from domain.novel.repositories.novel_repository import NovelRepository
from infrastructure.persistence.database.connection import DatabaseConnection

logger = logging.getLogger(__name__)


class SqliteNovelRepository(NovelRepository):
    """SQLite Novel Repository 实现"""

    def __init__(self, db: DatabaseConnection):
        self.db = db

    def save(self, novel: Novel) -> None:
        """保存小说"""
        sql = """
            INSERT INTO novels (
                id, title, slug, author, target_chapters, premise,
                autopilot_status, auto_approve_mode, current_stage, current_act, current_chapter_in_act,
                max_auto_chapters, current_auto_chapters, last_chapter_tension,
                consecutive_error_count, current_beat_index,
                last_audit_chapter_number, last_audit_similarity, last_audit_drift_alert,
                last_audit_narrative_ok, last_audit_at,
                last_audit_vector_stored, last_audit_foreshadow_stored,
                last_audit_triples_extracted, last_audit_quality_scores, last_audit_issues,
                target_words_per_chapter, audit_progress,
                boundary_gate_status, last_boundary_issue, revision_attempts,
                chapter_draft_status, last_chapter_draft_issue,
                route_gate_status, last_route_issue, auto_revision_history,
                constraint_gate_status, last_constraint_issue, constraint_revision_history,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                slug = excluded.slug,
                author = excluded.author,
                target_chapters = excluded.target_chapters,
                premise = excluded.premise,
                autopilot_status = excluded.autopilot_status,
                auto_approve_mode = excluded.auto_approve_mode,
                current_stage = excluded.current_stage,
                current_act = excluded.current_act,
                current_chapter_in_act = excluded.current_chapter_in_act,
                max_auto_chapters = excluded.max_auto_chapters,
                current_auto_chapters = excluded.current_auto_chapters,
                last_chapter_tension = excluded.last_chapter_tension,
                consecutive_error_count = excluded.consecutive_error_count,
                current_beat_index = excluded.current_beat_index,
                last_audit_chapter_number = excluded.last_audit_chapter_number,
                last_audit_similarity = excluded.last_audit_similarity,
                last_audit_drift_alert = excluded.last_audit_drift_alert,
                last_audit_narrative_ok = excluded.last_audit_narrative_ok,
                last_audit_at = excluded.last_audit_at,
                last_audit_vector_stored = excluded.last_audit_vector_stored,
                last_audit_foreshadow_stored = excluded.last_audit_foreshadow_stored,
                last_audit_triples_extracted = excluded.last_audit_triples_extracted,
                last_audit_quality_scores = excluded.last_audit_quality_scores,
                last_audit_issues = excluded.last_audit_issues,
                target_words_per_chapter = excluded.target_words_per_chapter,
                audit_progress = excluded.audit_progress,
                boundary_gate_status = excluded.boundary_gate_status,
                last_boundary_issue = excluded.last_boundary_issue,
                revision_attempts = excluded.revision_attempts,
                chapter_draft_status = excluded.chapter_draft_status,
                last_chapter_draft_issue = excluded.last_chapter_draft_issue,
                route_gate_status = excluded.route_gate_status,
                last_route_issue = excluded.last_route_issue,
                auto_revision_history = excluded.auto_revision_history,
                constraint_gate_status = excluded.constraint_gate_status,
                last_constraint_issue = excluded.last_constraint_issue,
                constraint_revision_history = excluded.constraint_revision_history,
                updated_at = excluded.updated_at
        """
        now = datetime.utcnow().isoformat()
        novel_id = novel.novel_id.value if hasattr(novel, 'novel_id') else novel.id
        slug = novel_id
        premise = getattr(novel, 'premise', '')
        author = getattr(novel, 'author', '未知作者')
        _ap = getattr(novel, 'autopilot_status', 'stopped')
        autopilot_status = _ap.value if isinstance(_ap, AutopilotStatus) else _ap
        auto_approve_mode = 1 if getattr(novel, 'auto_approve_mode', False) else 0
        _cs = getattr(novel, 'current_stage', 'planning')
        current_stage = _cs.value if isinstance(_cs, NovelStage) else _cs
        current_act = getattr(novel, 'current_act', 0)
        current_chapter_in_act = getattr(novel, 'current_chapter_in_act', 0)
        max_auto_chapters = getattr(novel, 'max_auto_chapters', 9999)
        current_auto_chapters = getattr(novel, 'current_auto_chapters', 0)
        last_chapter_tension = getattr(novel, 'last_chapter_tension', 0)
        consecutive_error_count = getattr(novel, 'consecutive_error_count', 0)
        current_beat_index = getattr(novel, 'current_beat_index', 0)
        lacn = getattr(novel, "last_audit_chapter_number", None)
        lasim = getattr(novel, "last_audit_similarity", None)
        ladr = 1 if getattr(novel, "last_audit_drift_alert", False) else 0
        lano = 1 if getattr(novel, "last_audit_narrative_ok", True) else 0
        laat = getattr(novel, "last_audit_at", None)
        # 新增字段
        lavs = 1 if getattr(novel, "last_audit_vector_stored", False) else 0
        lafs = 1 if getattr(novel, "last_audit_foreshadow_stored", False) else 0
        late = 1 if getattr(novel, "last_audit_triples_extracted", False) else 0
        laqs = getattr(novel, "last_audit_quality_scores", {})
        laqs_json = json.dumps(laqs) if laqs else None
        lai = getattr(novel, "last_audit_issues", [])
        lai_json = json.dumps(lai) if lai else None
        twpc = getattr(novel, "target_words_per_chapter", 2500)
        audit_progress = getattr(novel, "audit_progress", None)
        boundary_gate_status = getattr(novel, "boundary_gate_status", None)
        last_boundary_issue = getattr(novel, "last_boundary_issue", {}) or {}
        last_boundary_issue_json = json.dumps(last_boundary_issue) if last_boundary_issue else None
        revision_attempts = int(getattr(novel, "revision_attempts", 0) or 0)
        chapter_draft_status = getattr(novel, "chapter_draft_status", None)
        last_chapter_draft_issue = getattr(novel, "last_chapter_draft_issue", {}) or {}
        last_chapter_draft_issue_json = json.dumps(last_chapter_draft_issue) if last_chapter_draft_issue else None
        route_gate_status = getattr(novel, "route_gate_status", None)
        last_route_issue = getattr(novel, "last_route_issue", {}) or {}
        last_route_issue_json = json.dumps(last_route_issue) if last_route_issue else None
        auto_revision_history = getattr(novel, "auto_revision_history", []) or []
        auto_revision_history_json = json.dumps(auto_revision_history) if auto_revision_history else None
        constraint_gate_status = getattr(novel, "constraint_gate_status", None)
        last_constraint_issue = getattr(novel, "last_constraint_issue", {}) or {}
        last_constraint_issue_json = json.dumps(last_constraint_issue) if last_constraint_issue else None
        constraint_revision_history = getattr(novel, "constraint_revision_history", []) or []
        constraint_revision_history_json = json.dumps(constraint_revision_history) if constraint_revision_history else None

        self.db.execute(sql, (
            novel_id,
            novel.title,
            slug,
            author,
            novel.target_chapters,
            premise,
            autopilot_status,
            auto_approve_mode,
            current_stage,
            current_act,
            current_chapter_in_act,
            max_auto_chapters,
            current_auto_chapters,
            last_chapter_tension,
            consecutive_error_count,
            current_beat_index,
            lacn,
            lasim,
            ladr,
            lano,
            laat,
            lavs,
            lafs,
            late,
            laqs_json,
            lai_json,
            twpc,
            audit_progress,
            boundary_gate_status,
            last_boundary_issue_json,
            revision_attempts,
            chapter_draft_status,
            last_chapter_draft_issue_json,
            route_gate_status,
            last_route_issue_json,
            auto_revision_history_json,
            constraint_gate_status,
            last_constraint_issue_json,
            constraint_revision_history_json,
            now,
            now
        ))
        self.db.get_connection().commit()

    async def async_save(self, novel: Novel) -> None:
        """异步保存小说（守护进程使用）"""
        self.save(novel)

    def get_by_id(self, novel_id: NovelId) -> Optional[Novel]:
        """根据 ID 获取小说"""
        sql = "SELECT * FROM novels WHERE id = ?"
        row = self.db.fetch_one(sql, (novel_id.value,))

        if not row:
            return None

        return self._row_to_novel(novel_id, row)

    def get_by_slug(self, slug: str) -> Optional[Novel]:
        """根据 slug 获取小说"""
        sql = "SELECT * FROM novels WHERE slug = ?"
        row = self.db.fetch_one(sql, (slug,))

        if not row:
            return None

        return self._row_to_novel(NovelId(row['id']), row)

    def list_all(self) -> List[Novel]:
        """列出所有小说"""
        sql = "SELECT * FROM novels ORDER BY created_at DESC"
        rows = self.db.fetch_all(sql)
        return [self._row_to_novel(NovelId(row['id']), row) for row in rows]

    def find_by_autopilot_status(self, status: str) -> List[Novel]:
        """根据自动驾驶状态查找小说列表"""
        sql = "SELECT * FROM novels WHERE autopilot_status = ? ORDER BY updated_at DESC"
        rows = self.db.fetch_all(sql, (status,))
        return [self._row_to_novel(NovelId(row['id']), row) for row in rows]

    def _row_to_novel(self, novel_id: NovelId, row: dict) -> Novel:
        """将数据库行转换为 Novel 实体"""
        raw_status = row.get('autopilot_status', 'stopped')
        try:
            autopilot_status = AutopilotStatus(raw_status)
        except ValueError:
            autopilot_status = AutopilotStatus.STOPPED

        raw_stage = row.get('current_stage', 'planning')
        try:
            current_stage = NovelStage(raw_stage)
        except ValueError:
            current_stage = NovelStage.PLANNING

        _lad = row.get("last_audit_drift_alert")
        _lano = row.get("last_audit_narrative_ok")
        
        # 解析 JSON 字段
        laqs_json = row.get("last_audit_quality_scores")
        laqs = json.loads(laqs_json) if laqs_json else {}
        lai_json = row.get("last_audit_issues")
        lai = json.loads(lai_json) if lai_json else []
        lbi_json = row.get("last_boundary_issue")
        lbi = json.loads(lbi_json) if lbi_json else {}
        lcdi_json = row.get("last_chapter_draft_issue")
        lcdi = json.loads(lcdi_json) if lcdi_json else {}
        lri_json = row.get("last_route_issue")
        lri = json.loads(lri_json) if lri_json else {}
        arh_json = row.get("auto_revision_history")
        arh = json.loads(arh_json) if arh_json else []
        lci_json = row.get("last_constraint_issue")
        lci = json.loads(lci_json) if lci_json else {}
        crh_json = row.get("constraint_revision_history")
        crh = json.loads(crh_json) if crh_json else []
        
        return Novel(
            id=novel_id,
            title=row['title'],
            author=row.get('author', '未知作者'),
            target_chapters=row.get('target_chapters', 0),
            premise=row.get('premise', ''),
            autopilot_status=autopilot_status,
            auto_approve_mode=bool(row.get('auto_approve_mode', 0)),
            current_stage=current_stage,
            current_act=row.get('current_act', 0),
            current_chapter_in_act=row.get('current_chapter_in_act', 0),
            max_auto_chapters=row.get('max_auto_chapters', 9999),
            current_auto_chapters=row.get('current_auto_chapters', 0),
            last_chapter_tension=row.get('last_chapter_tension', 0),
            consecutive_error_count=row.get('consecutive_error_count', 0),
            current_beat_index=row.get('current_beat_index', 0),
            last_audit_chapter_number=row.get("last_audit_chapter_number"),
            last_audit_similarity=row.get("last_audit_similarity"),
            last_audit_drift_alert=bool(_lad) if _lad is not None else False,
            last_audit_narrative_ok=bool(_lano) if _lano is not None else True,
            last_audit_at=row.get("last_audit_at"),
            last_audit_vector_stored=bool(row.get("last_audit_vector_stored", 0)),
            last_audit_foreshadow_stored=bool(row.get("last_audit_foreshadow_stored", 0)),
            last_audit_triples_extracted=bool(row.get("last_audit_triples_extracted", 0)),
            last_audit_quality_scores=laqs,
            last_audit_issues=lai,
            target_words_per_chapter=row.get("target_words_per_chapter", 2500),
            audit_progress=row.get("audit_progress"),
            boundary_gate_status=row.get("boundary_gate_status"),
            last_boundary_issue=lbi,
            revision_attempts=row.get("revision_attempts", 0) or 0,
            chapter_draft_status=row.get("chapter_draft_status"),
            last_chapter_draft_issue=lcdi,
            route_gate_status=row.get("route_gate_status"),
            last_route_issue=lri,
            auto_revision_history=arh,
            constraint_gate_status=row.get("constraint_gate_status"),
            last_constraint_issue=lci,
            constraint_revision_history=crh,
        )

    def delete(self, novel_id: NovelId) -> None:
        """删除小说（级联删除所有关联数据）"""
        sql = "DELETE FROM novels WHERE id = ?"
        self.db.execute(sql, (novel_id.value,))
        self.db.get_connection().commit()
        logger.info(f"Deleted novel: {novel_id.value}")

    def exists(self, novel_id: NovelId) -> bool:
        """检查小说是否存在"""
        sql = "SELECT 1 FROM novels WHERE id = ? LIMIT 1"
        row = self.db.fetch_one(sql, (novel_id.value,))
        return row is not None
