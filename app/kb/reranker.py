"""SiliconFlow bge-reranker-v2-m3；单次HTTP，服务错误不伪装成无召回。"""
from __future__ import annotations

import math
from collections.abc import Sequence

import httpx

from app.config import get_settings
from app.observability.spans import observed_call

_client: httpx.Client | None = None


class RerankError(RuntimeError):
    def __init__(self, kind: str, status_code: int | None = None):
        self.kind, self.status_code = kind, status_code
        super().__init__(f"重排服务失败:{kind}" + (f" (HTTP {status_code})" if status_code else ""))


@observed_call('rerank', input_fields=('query','documents','top_n'),
               output_selector=lambda result: {'ranking': result})
def rerank(query: str, documents: Sequence[str], *, top_n: int = 10) -> list[tuple[int, float]]:
    if not documents:
        return []
    if type(top_n) is not int or not 1 <= top_n <= 10:
        raise ValueError("top_n 必须为1..10的整数")
    settings = get_settings()
    key = settings.rerank_api_key or settings.embedding_api_key
    if not key:
        raise RerankError("configuration")
    global _client
    if _client is None:
        # HTTPX默认不重试。执行器统一决定是否再次调用；不叠加provider重试。
        _client = httpx.Client(timeout=settings.rerank_timeout)
    try:
        response = _client.post(
            settings.rerank_base_url.rstrip("/") + "/rerank",
            headers={"Authorization": f"Bearer {key}"}, timeout=settings.rerank_timeout,
            json={"model": settings.rerank_model, "query": query, "documents": list(documents),
                  "top_n": top_n, "return_documents": False},
        )
    except httpx.TimeoutException:
        raise RerankError("timeout") from None
    except httpx.RequestError:
        raise RerankError("transport") from None
    if not 200 <= response.status_code < 300:
        status = response.status_code
        kind = {401: "authentication", 403: "authentication", 402: "payment",
                429: "rate_limit"}.get(status, "provider" if status >= 500 else "request")
        raise RerankError(kind, status)
    try:
        results = response.json()["results"]
        if not isinstance(results, list) or len(results) != min(top_n, len(documents)):
            raise ValueError("count")
        pairs = []
        seen = set()
        for item in results:
            index = item["index"]
            score = item["relevance_score"]
            if type(index) is not int or index < 0 or index >= len(documents) or index in seen:
                raise ValueError("index")
            if isinstance(score, bool) or not math.isfinite(float(score)):
                raise ValueError("score")
            seen.add(index)
            pairs.append((index, float(score)))
        return sorted(pairs, key=lambda p: -p[1])
    except (ValueError, TypeError, KeyError):
        raise RerankError("invalid_response") from None
