from application.engine.services.context_budget_allocator import ContextBudgetAllocator
import application.engine.services.context_budget_allocator as allocator_module


class EmptyTripleRepository:
    def __init__(self):
        self.calls = 0

    def get_by_novel_sync(self, novel_id):
        self.calls += 1
        return []


class ExistingTripleRepository:
    def __init__(self):
        self.triple = type("Triple", (), {"id": "triple-1"})()
        self.calls = 0

    def get_by_novel_sync(self, novel_id):
        self.calls += 1
        return [self.triple]


class FailingVectorStore:
    async def list_collections(self):
        raise AssertionError("empty triple sets must not reach vector search")


class FailingEmbeddingService:
    def get_dimension(self):
        return 512

    async def embed(self, text):
        raise AssertionError("empty triple sets must not request embeddings")


class EmptyChapterRepository:
    def list_by_novel(self, novel_id):
        return []


class PriorChapterRepository:
    def list_by_novel(self, novel_id):
        return [
            type("Chapter", (), {"number": 1, "content": "上一章已经进入 C307。"})(),
        ]


class EmptyVectorStore:
    collections = {}


class NonEmptyIndex:
    ntotal = 1


class SearchableVectorStore:
    def __init__(self):
        self.collections = {
            "novel_novel-has-history_chunks": {
                "index": NonEmptyIndex(),
                "metadata": {"chunk-1": {"chapter_number": 1}},
            }
        }
        self.search_calls = 0

    async def search(self, collection, query_vector, limit):
        self.search_calls += 1
        return [
            {
                "payload": {
                    "chapter_number": 1,
                    "text": "上一章末尾：沈砚已经站在 C307 门内。",
                }
            }
        ]


class SearchableTripleVectorStore:
    def __init__(self):
        self.search_calls = 0

    async def list_collections(self):
        return ["novel_novel-has-triples_triples"]

    async def search(self, collection, query_vector, limit):
        self.search_calls += 1
        return [
            {
                "score": 0.91,
                "payload": {"triple_id": "triple-1"},
            }
        ]


class RecordingEmbeddingService:
    def __init__(self):
        self.calls = 0

    def get_dimension(self):
        return 512

    async def embed(self, text):
        self.calls += 1
        return [0.0] * 512


def test_semantic_triples_skip_vector_search_when_no_triples():
    triple_repo = EmptyTripleRepository()
    allocator = ContextBudgetAllocator(
        triple_repository=triple_repo,
        vector_store=FailingVectorStore(),
        embedding_service=FailingEmbeddingService(),
    )

    assert allocator._get_semantic_triples("novel-empty", "第一章大纲") == []
    assert triple_repo.calls == 1


def test_semantic_triples_still_search_when_triples_exist():
    triple_repo = ExistingTripleRepository()
    vector_store = SearchableTripleVectorStore()
    embedding_service = RecordingEmbeddingService()
    allocator = ContextBudgetAllocator(
        triple_repository=triple_repo,
        vector_store=vector_store,
        embedding_service=embedding_service,
    )

    result = allocator._get_semantic_triples("novel-has-triples", "寻找三元组")

    assert result == [triple_repo.triple]
    assert triple_repo.calls == 1
    assert embedding_service.calls == 1
    assert vector_store.search_calls == 1


def test_semantic_triples_skip_embedding_when_triple_collection_empty():
    triple_repo = ExistingTripleRepository()
    embedding_service = RecordingEmbeddingService()
    allocator = ContextBudgetAllocator(
        triple_repository=triple_repo,
        vector_store=EmptyVectorStore(),
        embedding_service=embedding_service,
    )

    assert allocator._get_semantic_triples("novel-empty-triple-index", "第二章大纲") == []
    assert triple_repo.calls == 1
    assert embedding_service.calls == 0


def test_vector_recall_skips_embedding_when_collection_missing():
    allocator = ContextBudgetAllocator(
        chapter_repository=EmptyChapterRepository(),
        vector_store=EmptyVectorStore(),
        embedding_service=FailingEmbeddingService(),
    )

    assert allocator._get_vector_recall("novel-empty", 1, "第一章大纲") == ""


def test_vector_recall_still_searches_when_history_exists():
    vector_store = SearchableVectorStore()
    embedding_service = RecordingEmbeddingService()
    allocator = ContextBudgetAllocator(
        chapter_repository=PriorChapterRepository(),
        vector_store=vector_store,
        embedding_service=embedding_service,
    )

    result = allocator._get_vector_recall("novel-has-history", 2, "第二章大纲")

    assert "【相关上下文（向量召回）】" in result
    assert "沈砚已经站在 C307 门内" in result
    assert embedding_service.calls == 1
    assert vector_store.search_calls == 1


def test_plugin_context_patches_split_hard_constraints_from_soft_references(monkeypatch):
    def fake_collect_blocks(novel_id, chapter_number, outline, *, source):
        return [
            {
                "title": "章节承接状态",
                "kind": "chapter_state_bridge",
                "priority": 82,
                "content": "上一章末尾沈砚已经在C307内部。",
            },
            {
                "title": "本章焦点角色",
                "kind": "focus_character_state",
                "priority": 76,
                "content": "沈砚：谨慎、疲惫。",
            },
        ]

    monkeypatch.setattr(allocator_module, "collect_generation_context_blocks", fake_collect_blocks)
    allocator = ContextBudgetAllocator()

    critical, support = allocator._get_plugin_context_patches("novel-1", 2, "沈砚调查C307")

    assert "章节承接状态" in critical
    assert "已经在C307内部" in critical
    assert "本章焦点角色" not in critical
    assert "本章焦点角色" in support
