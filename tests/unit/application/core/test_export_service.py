import io
import zipfile

from application.core.services.export_service import ExportService
from domain.novel.entities.chapter import Chapter
from domain.novel.entities.novel import Novel
from domain.novel.value_objects.novel_id import NovelId


class _UnusedRepository:
    pass


def test_epub_export_writes_non_empty_chapter_xhtml():
    novel_id = NovelId("novel-export")
    novel = Novel(
        id=novel_id,
        title="导出测试",
        author="PlotPilot",
        target_chapters=2,
        premise="测试简介",
    )
    chapters = [
        Chapter(
            id="chapter-1",
            novel_id=novel_id,
            number=1,
            title="第一章",
            content="第一章正文\n第二段",
        ),
        Chapter(
            id="chapter-2",
            novel_id=novel_id,
            number=2,
            title="第二章",
            content="第二章正文",
        ),
    ]
    service = ExportService(_UnusedRepository(), _UnusedRepository())

    data, media_type, filename = service._export_to_epub(novel, chapters)

    assert media_type == "application/epub+zip"
    assert filename == "导出测试.epub"
    with zipfile.ZipFile(io.BytesIO(data)) as epub:
        chapter_names = [
            name
            for name in epub.namelist()
            if name.startswith("OEBPS/chap") and name.endswith(".xhtml")
        ]
        assert len(chapter_names) == 2
        for chapter_name in chapter_names:
            assert epub.getinfo(chapter_name).file_size > 0
        assert "第一章正文" in epub.read("OEBPS/chap001.xhtml").decode("utf-8")
