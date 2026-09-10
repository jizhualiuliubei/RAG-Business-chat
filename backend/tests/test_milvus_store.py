from app.core import milvus_store


class _FakeZillizClient:
    def __init__(self):
        self.has_collection_calls = []

    def list_databases(self):
        raise AssertionError("Zilliz token mode should not call list_databases")

    def use_database(self, db_name):
        raise AssertionError("Zilliz token mode should not call use_database")

    def has_collection(self, collection_name):
        self.has_collection_calls.append(collection_name)
        return True

    def describe_collection(self, collection_name):
        return {
            "fields": [
                {"name": "id"},
                {"name": "vector"},
                {"name": "text"},
                {"name": "source"},
                {"name": "doc_id"},
                {"name": "kb_id"},
                {"name": "chunk_id"},
            ]
        }


class _FakeDeleteClient:
    def __init__(self):
        self.deleted = []
        self.flushed = []

    def delete(self, collection_name, filter):
        self.deleted.append((collection_name, filter))

    def flush(self, collection_name):
        self.flushed.append(collection_name)


class _FakeWriteClient:
    def __init__(self, fields):
        self.fields = fields
        self.upserted = []
        self.flushed = []

    def describe_collection(self, collection_name):
        return {"fields": [{"name": name} for name in self.fields]}

    def upsert(self, collection_name, data):
        self.upserted.append((collection_name, data))

    def flush(self, collection_name):
        self.flushed.append(collection_name)


class _FakeSearchClient:
    def __init__(self):
        self.search_calls = []

    def describe_collection(self, collection_name):
        return {
            "fields": [
                {"name": "id"},
                {"name": "vector"},
                {"name": "text"},
                {"name": "source"},
                {"name": "doc_id"},
                {"name": "kb_id"},
                {"name": "enterprise_id"},
                {"name": "chunk_id"},
            ]
        }

    def search(self, collection_name, data, limit, output_fields, filter):
        self.search_calls.append(
            {
                "collection_name": collection_name,
                "data": data,
                "limit": limit,
                "output_fields": output_fields,
                "filter": filter,
            }
        )
        return [[]]


class _Chunk:
    def __init__(self, text):
        self.page_content = text


def test_zilliz_token_mode_skips_database_management(monkeypatch):
    fake_client = _FakeZillizClient()
    monkeypatch.setattr(milvus_store, "_client", fake_client)
    monkeypatch.setattr(milvus_store, "_initialized", False)
    monkeypatch.setattr(milvus_store, "_collection_fields", None)
    monkeypatch.setattr(milvus_store, "MILVUS_TOKEN", "token")
    monkeypatch.setattr(milvus_store, "MILVUS_COLLECTION", "knowledge_chunks")

    milvus_store.ensure_collection()

    assert fake_client.has_collection_calls == ["knowledge_chunks"]


def test_delete_by_doc_id_flushes_deleted_vectors(monkeypatch):
    fake_client = _FakeDeleteClient()
    monkeypatch.setattr(milvus_store, "_client", fake_client)
    monkeypatch.setattr(milvus_store, "_initialized", True)
    monkeypatch.setattr(milvus_store, "_collection_fields", None)
    monkeypatch.setattr(milvus_store, "MILVUS_COLLECTION", "knowledge_chunks")

    milvus_store.delete_by_doc_id(990001)

    assert fake_client.deleted == [("knowledge_chunks", 'doc_id in ["990001"]')]
    assert fake_client.flushed == ["knowledge_chunks"]


def test_delete_by_doc_ids_flushes_once_for_batch(monkeypatch):
    fake_client = _FakeDeleteClient()
    monkeypatch.setattr(milvus_store, "_client", fake_client)
    monkeypatch.setattr(milvus_store, "_initialized", True)
    monkeypatch.setattr(milvus_store, "_collection_fields", None)
    monkeypatch.setattr(milvus_store, "MILVUS_COLLECTION", "knowledge_chunks")

    milvus_store.delete_by_doc_ids([1, 2, 3])

    assert fake_client.deleted == [("knowledge_chunks", 'doc_id in ["1", "2", "3"]')]
    assert fake_client.flushed == ["knowledge_chunks"]


def test_search_can_filter_to_active_document_ids(monkeypatch):
    fake_client = _FakeSearchClient()
    monkeypatch.setattr(milvus_store, "_client", fake_client)
    monkeypatch.setattr(milvus_store, "_initialized", True)
    monkeypatch.setattr(milvus_store, "_collection_fields", None)
    monkeypatch.setattr(milvus_store, "MILVUS_COLLECTION", "knowledge_chunks")

    milvus_store.search([0.1, 0.2], top_k=5, kb_id=7, enterprise_id=3, doc_ids=[11, 12])

    assert fake_client.search_calls[0]["filter"] == (
        'enterprise_id == "3" and kb_id == "7" and doc_id in ["11", "12"]'
    )


def test_search_returns_empty_when_document_scope_is_empty(monkeypatch):
    fake_client = _FakeSearchClient()
    monkeypatch.setattr(milvus_store, "_client", fake_client)
    monkeypatch.setattr(milvus_store, "_initialized", True)
    monkeypatch.setattr(milvus_store, "_collection_fields", None)
    monkeypatch.setattr(milvus_store, "MILVUS_COLLECTION", "knowledge_chunks")

    assert milvus_store.search([0.1, 0.2], top_k=5, kb_id=7, enterprise_id=3, doc_ids=[]) == []
    assert fake_client.search_calls == []


def test_add_chunks_omits_enterprise_id_when_legacy_collection_lacks_field(monkeypatch):
    fake_client = _FakeWriteClient(["id", "vector", "text", "source", "doc_id", "kb_id", "chunk_id"])
    monkeypatch.setattr(milvus_store, "_client", fake_client)
    monkeypatch.setattr(milvus_store, "_initialized", True)
    monkeypatch.setattr(milvus_store, "_collection_fields", None)
    monkeypatch.setattr(milvus_store, "MILVUS_COLLECTION", "knowledge_chunks")

    milvus_store.add_chunks([_Chunk("hello")], [[0.1, 0.2]], doc_id=7, kb_id=3, source="a.txt", enterprise_id=9)

    inserted = fake_client.upserted[0][1][0]
    assert "enterprise_id" not in inserted
    assert inserted["kb_id"] == "3"
    assert inserted["doc_id"] == "7"


def test_add_chunks_keeps_enterprise_id_when_collection_has_field(monkeypatch):
    fake_client = _FakeWriteClient(["id", "vector", "text", "source", "doc_id", "kb_id", "enterprise_id", "chunk_id"])
    monkeypatch.setattr(milvus_store, "_client", fake_client)
    monkeypatch.setattr(milvus_store, "_initialized", True)
    monkeypatch.setattr(milvus_store, "_collection_fields", None)
    monkeypatch.setattr(milvus_store, "MILVUS_COLLECTION", "knowledge_chunks")

    milvus_store.add_chunks([_Chunk("hello")], [[0.1, 0.2]], doc_id=7, kb_id=3, source="a.txt", enterprise_id=9)

    inserted = fake_client.upserted[0][1][0]
    assert inserted["enterprise_id"] == "9"
