from types import SimpleNamespace
import pytest
import app.kb.embedding as emb

class FakeEmbeddings:
    def __init__(self, dim=1024, fail_times=0):
        self.calls = []
        self.dim = dim
        self.fail_times = fail_times
    def create(self, *, model, input):
        self.calls.append({"model": model, "input": input})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("temporary")
        return SimpleNamespace(data=[
            SimpleNamespace(embedding=[0.1] * self.dim, index=i)
            for i in range(len(input))
        ])

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
