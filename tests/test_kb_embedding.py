from types import SimpleNamespace
import pytest
import app.kb.embedding as emb

class FakeEmbeddings:
    def __init__(self, dim=1024, fail_times=0, value_for=None):
        self.calls = []
        self.dim = dim
        self.fail_times = fail_times
        # 默认按批内 index 生成不同向量;需要跨批区分时由测试传入 value_for。
        self.value_for = value_for or (lambda text, index: float(index))
    def create(self, *, model, input):
        self.calls.append({"model": model, "input": input})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("temporary")
        items = [
            SimpleNamespace(
                embedding=[self.value_for(text, index)] * self.dim,
                index=index,
            )
            for index, text in enumerate(input)
        ]
        # 故意打乱响应顺序:只有按 index 排序才能还原输入顺序。
        return SimpleNamespace(data=items[::-1])

def test_empty_input_returns_empty_without_call(monkeypatch):
    fake = FakeEmbeddings()
    monkeypatch.setattr(emb, "_client", SimpleNamespace(embeddings=fake))
    assert emb.embed_texts([]) == []
    assert fake.calls == []

def test_batches_of_32(monkeypatch):
    fake = FakeEmbeddings()
    monkeypatch.setattr(emb, "_client", SimpleNamespace(embeddings=fake))
    vectors = emb.embed_texts([f"t{i}" for i in range(65)])
    assert len(vectors) == 65
    assert [len(c["input"]) for c in fake.calls] == [32, 32, 1]

def test_vectors_follow_input_order_across_batches(monkeypatch):
    # 向量首分量编码输入编号,跨 2 个批次(32 + 2)验证:
    # 既要按 index 还原批内顺序,也不能颠倒批次拼接顺序。
    fake = FakeEmbeddings(
        value_for=lambda text, index: float(text.removeprefix("t"))
    )
    monkeypatch.setattr(emb, "_client", SimpleNamespace(embeddings=fake))
    vectors = emb.embed_texts([f"t{i}" for i in range(34)])
    assert [c["input"] for c in fake.calls] == [
        [f"t{i}" for i in range(32)],
        [f"t{i}" for i in range(32, 34)],
    ]
    assert [vector[0] for vector in vectors] == [float(i) for i in range(34)]


def test_dimension_mismatch_raises(monkeypatch):
    fake = FakeEmbeddings(dim=768)
    monkeypatch.setattr(emb, "_client", SimpleNamespace(embeddings=fake))
    with pytest.raises(emb.EmbeddingError, match="1024"):
        emb.embed_texts(["x"])

def test_retries_three_times_then_raises(monkeypatch):
    fake = FakeEmbeddings(fail_times=3)
    monkeypatch.setattr(emb, "_client", SimpleNamespace(embeddings=fake))
    with pytest.raises(emb.EmbeddingError):
        emb.embed_texts(["x"])
    assert len(fake.calls) == 3

def test_missing_api_key_raises_clear_error(monkeypatch):
    monkeypatch.setattr(emb, "get_settings",
                        lambda: SimpleNamespace(embedding_api_key="",
                                                embedding_base_url="x",
                                                embedding_model="m",
                                                embedding_dim=1024,
                                                embedding_batch_size=32))
    monkeypatch.setattr(emb, "_client", None)
    with pytest.raises(emb.EmbeddingError, match="EMBEDDING_API_KEY"):
        emb.embed_texts(["x"])
