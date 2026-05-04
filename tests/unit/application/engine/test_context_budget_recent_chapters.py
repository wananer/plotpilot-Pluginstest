"""ContextBudgetAllocator：紧邻上一章章末承接摘录。"""

from application.engine.services.context_budget_allocator import ContextBudgetAllocator


class PriorChapterRepository:
    def list_by_novel(self, novel_id):
        return [
            type(
                "Chapter",
                (),
                {
                    "number": 1,
                    "title": "坠入石室",
                    "content": "三个人抓住彼此，石室坠入地下，红光吞没一切。",
                },
            )(),
        ]


def test_excerpt_empty():
    alloc = ContextBudgetAllocator()
    assert alloc._excerpt_immediate_previous_chapter("") == ""
    assert alloc._excerpt_immediate_previous_chapter("   ") == ""


def test_excerpt_short_full_in_tail_block():
    alloc = ContextBudgetAllocator()
    text = "x" * 800
    out = alloc._excerpt_immediate_previous_chapter(text)
    assert "章末节选，供本章开头承接" in out
    assert "章首略览" not in out
    assert text in out


def test_excerpt_long_head_and_tail():
    alloc = ContextBudgetAllocator()
    text = "A" * 350 + "M" * 500 + "Z" * 2200
    out = alloc._excerpt_immediate_previous_chapter(text)
    assert "章首略览" in out
    assert "章末节选，供本章开头承接" in out
    assert out.endswith("Z" * 2000)
    assert "A" * 300 in out


def test_chapter_boundary_bridge_is_t0_hard_constraint():
    alloc = ContextBudgetAllocator(chapter_repository=PriorChapterRepository())

    allocation = alloc.allocate("novel-1", 2, "第二章开头")
    bridge = allocation.slots["chapter_boundary_bridge"]

    assert bridge.is_mandatory
    assert "章节边界承接硬约束" == bridge.name
    assert "下一章开头必须先兑现上一章章末" in bridge.content
    assert "石室坠入地下" in bridge.content
