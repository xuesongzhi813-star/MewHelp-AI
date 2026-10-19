from types import SimpleNamespace

import pytest

import app.kb.milvus as milvus


class FakeSchema:
    """记录 add_field 参数的假 schema。"""

    def __init__(self) -> None:
        self.fields: dict[str, dict] = {}

    def add_field(self, field_name: str, datatype, **kwargs) -> None:
        self.fields[field_name] = {"datatype": datatype, **kwargs}

    def has_field(self, name: str, dim: int | None = None) -> bool:
        if name not in self.fields:
            return False
        return dim is None or self.fields[name].get("dim") == dim


class FakeClient:
    def __init__(
        self,
        has: bool = False,
        search_result: list[list[dict]] | None = None,
        query_result: list[dict] | None = None,
        raise_on: str | None = None,
    ) -> None:
        self._has = has
        self.created: list[dict] = []
        self.loaded: list[str] = []
        self.upserts: list[list[dict]] = []
        self.search_calls: list[dict] = []
        self.query_calls: list[dict] = []
        self.flushed: list[str] = []
        self.dropped: list[str] = []
        self.search_result = search_result if search_result is not None else [[]]
        self.query_result = query_result if query_result is not None else []
        self.raise_on = raise_on

    def has_collection(self, collection_name: str) -> bool:
        return self._has

    def create_schema(self, **kwargs) -> FakeSchema:
        return FakeSchema()

    def create_collection(self, collection_name: str, **kwargs) -> None:
        self.created.append({"collection_name": collection_name, **kwargs})

    def load_collection(self, collection_name: str, **kwargs) -> None:
        self.loaded.append(collection_name)

    def list_collections(self, **kwargs) -> list[str]:
        if self.raise_on == "list_collections":
            raise RuntimeError("connection refused")
        return ["knowledge"]

    def upsert(self, collection_name: str, data: list[dict], **kwargs) -> dict:
        self.upserts.append(data)
        return {"upsert_count": len(data)}

    def search(
        self,
        collection_name: str,
        data=None,
        limit: int = 10,
        output_fields=None,
        search_params=None,
        **kwargs,
    ) -> list[list[dict]]:
        self.search_calls.append(
            {
                "collection_name": collection_name,
                "data": data,
                "limit": limit,
                "output_fields": output_fields,
                "search_params": search_params,
            }
        )
        return self.search_result

    def query(
        self,
        collection_name: str,
        filter: str = "",
        output_fields=None,
        limit: int | None = None,
        **kwargs,
    ) -> list[dict]:
        self.query_calls.append(
            {
                "collection_name": collection_name,
                "filter": filter,
                "output_fields": output_fields,
                "limit": limit,
            }
        )
        return self.query_result

    def flush(self, collection_name: str, **kwargs) -> None:
        self.flushed.append(collection_name)

    def drop_collection(self, collection_name: str, **kwargs) -> None:
        self.dropped.append(collection_name)


def test_ensure_collection_skips_when_exists(monkeypatch):
    fake = FakeClient(has=True)
    monkeypatch.setattr(milvus, "_client", fake)

    milvus.ensure_collection()

    assert fake.created == []
    assert fake.loaded == []


def test_ensure_collection_schema_dim_and_metric(monkeypatch):
    fake = FakeClient(has=False)
    monkeypatch.setattr(milvus, "_client", fake)

    milvus.ensure_collection()

    created = fake.created[0]
    assert created["collection_name"] == "knowledge"
    assert created["schema"].has_field("id")
    assert created["schema"].has_field("vector", dim=1024)
    index = created["index_params"][0].to_dict()
    assert index["field_name"] == "vector"
    assert index["index_type"] == "AUTOINDEX"
    assert index["metric_type"] == "COSINE"
    assert fake.loaded == ["knowledge"]


def test_upsert_stringifies_ids_and_batches(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(milvus, "_client", fake)

    milvus.upsert([1, 2], [[0.1] * 1024, [0.2] * 1024])

    assert fake.upserts[0] == [
        {"id": "1", "vector": [0.1] * 1024},
        {"id": "2", "vector": [0.2] * 1024},
    ]


def test_upsert_rejects_length_mismatch(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(milvus, "_client", fake)

    with pytest.raises(ValueError, match="长度"):
        milvus.upsert(["1", "2"], [[0.1] * 1024])

    assert fake.upserts == []


def test_search_returns_id_score_pairs_in_order(monkeypatch):
    fake = FakeClient(
        search_result=[
            [
                {"id": "7", "distance": 0.81},
                {"id": "3", "distance": 0.62},
            ]
        ]
    )
    monkeypatch.setattr(milvus, "_client", fake)

    assert milvus.search([0.0] * 1024, 3) == [("7", 0.81), ("3", 0.62)]
    assert fake.search_calls[0] == {
        "collection_name": "knowledge",
        "data": [[0.0] * 1024],
        "limit": 3,
        "output_fields": ["id"],
        "search_params": {"metric_type": "COSINE"},
    }


def test_search_empty_result_returns_empty_list(monkeypatch):
    fake = FakeClient(search_result=[[]])
    monkeypatch.setattr(milvus, "_client", fake)

    assert milvus.search([0.0] * 1024, 3) == []


def test_ping_returns_false_on_error(monkeypatch):
    fake = FakeClient(raise_on="list_collections")
    monkeypatch.setattr(milvus, "_client", fake)

    assert milvus.ping() is False


def test_ping_returns_true_when_reachable(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(milvus, "_client", fake)

    assert milvus.ping() is True


def test_list_ids_uses_query(monkeypatch):
    fake = FakeClient(query_result=[{"id": "1"}, {"id": "2"}])
    monkeypatch.setattr(milvus, "_client", fake)

    assert milvus.list_ids() == ["1", "2"]
    assert fake.query_calls[0] == {
        "collection_name": "knowledge",
        "filter": 'id != ""',
        "output_fields": ["id"],
        "limit": 5000,
    }


def test_count_uses_query_count_star(monkeypatch):
    fake = FakeClient(query_result=[{"count(*)": 2}])
    monkeypatch.setattr(milvus, "_client", fake)

    assert milvus.count() == 2
    assert fake.query_calls[0]["output_fields"] == ["count(*)"]
    assert fake.query_calls[0]["collection_name"] == "knowledge"


def test_flush_and_drop_delegate(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(milvus, "_client", fake)

    milvus.flush()
    milvus.drop()

    assert fake.flushed == ["knowledge"]
    assert fake.dropped == ["knowledge"]


def test_get_client_uses_settings_and_caches(monkeypatch):
    created: list[dict] = []

    class FakeMilvusClient:
        def __init__(self, **kwargs) -> None:
            created.append(kwargs)

    monkeypatch.setattr(milvus, "MilvusClient", FakeMilvusClient)
    monkeypatch.setattr(milvus, "_client", None)
    monkeypatch.setattr(
        milvus,
        "get_settings",
        lambda: SimpleNamespace(
            milvus_uri="http://milvus.test:19530",
            milvus_token="",
        ),
    )

    first = milvus.get_client()
    second = milvus.get_client()

    assert first is second
    assert created == [
        {"uri": "http://milvus.test:19530", "token": None},
    ]


def test_get_client_wraps_connection_errors(monkeypatch):
    class BrokenMilvusClient:
        def __init__(self, **kwargs) -> None:
            raise ConnectionError("refused")

    monkeypatch.setattr(milvus, "MilvusClient", BrokenMilvusClient)
    monkeypatch.setattr(milvus, "_client", None)
    monkeypatch.setattr(
        milvus,
        "get_settings",
        lambda: SimpleNamespace(milvus_uri="http://milvus.test:19530", milvus_token=""),
    )

    with pytest.raises(milvus.MilvusUnavailable):
        milvus.get_client()
