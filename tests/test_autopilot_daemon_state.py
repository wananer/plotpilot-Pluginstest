from domain.novel.entities.novel import AutopilotStatus, Novel, NovelStage
from domain.novel.value_objects.novel_id import NovelId

from application.engine.services.autopilot_daemon import AutopilotDaemon


class RecordingRepository:
    def __init__(self):
        self.saved = None

    def save(self, novel):
        self.saved = novel


def make_daemon(db_status):
    daemon = object.__new__(AutopilotDaemon)
    daemon.novel_repository = RecordingRepository()
    daemon._read_autopilot_status_ephemeral = lambda novel_id: db_status
    return daemon


def make_novel(status=AutopilotStatus.RUNNING):
    return Novel(
        id=NovelId("novel-state"),
        title="测试小说",
        author="tester",
        target_chapters=10,
        autopilot_status=status,
        current_stage=NovelStage.WRITING,
    )


def test_flush_preserves_internal_stopped_when_db_still_running():
    daemon = make_daemon(AutopilotStatus.RUNNING)
    novel = make_novel(AutopilotStatus.STOPPED)
    novel.current_stage = NovelStage.COMPLETED

    daemon._flush_novel(novel)

    assert daemon.novel_repository.saved is novel
    assert novel.autopilot_status == AutopilotStatus.STOPPED
    assert novel.current_stage == NovelStage.COMPLETED


def test_flush_honors_external_stop_when_memory_still_running():
    daemon = make_daemon(AutopilotStatus.STOPPED)
    novel = make_novel(AutopilotStatus.RUNNING)

    daemon._flush_novel(novel)

    assert daemon.novel_repository.saved is novel
    assert novel.autopilot_status == AutopilotStatus.STOPPED
