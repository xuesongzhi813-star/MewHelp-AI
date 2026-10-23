"""SiliconFlow BGE-M3 批量嵌入封装。

直接调用官方 ``openai`` SDK 的 ``client.embeddings.create``:
``base_url`` 指向 OpenAI 兼容端点,``input`` 始终传字符串列表。
不使用 LangChain ``OpenAIEmbeddings``——它可能把输入编码成 token 数组,
兼容端点会直接返回 400。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

from openai import OpenAI, APITimeoutError, APIConnectionError

from app.config import get_settings

logger = logging.getLogger(__name__)

# 每批最多尝试 3 次;两次重试之间按 0.5s、1s 指数退避。
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (0.5, 1.0)


class EmbeddingError(RuntimeError):
    """嵌入调用失败(未配置密钥、请求失败或维度不符)。"""

    def __init__(self, message: str, *, kind: str = 'unavailable'):
        self.kind = kind
        super().__init__(message)

    @property
    def public_message(self):
        hints = {'authentication':'鉴权失败，请核对嵌入密钥后重启应用',
                 'payment':'额度或账户余额不足，请核对供应商账户',
                 'rate_limit':'请求限流，请稍后重试',
                 'timeout':'请求超时，请检查网络和供应商状态',
                 'transport':'连接失败，请检查网络、代理和供应商状态',
                 'configuration':'未配置密钥，请检查根目录配置后重启应用',
                 'request':'请求配置不被供应商接受，请核对模型和接口'}
        return '嵌入服务' + hints.get(self.kind, '暂时不可用，请检查网络、密钥、额度与模型配置')


# 模块级懒加载客户端;测试可 monkeypatch 成假对象。
_client: OpenAI | None = None


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    """把一批文本转换为 BGE-M3 向量。

    Args:
        texts: 待向量化文本;空序列直接返回空列表,不发起请求。

    Returns:
        与 ``texts`` 顺序一致的 1024 维向量列表。

    Raises:
        EmbeddingError: 未配置 ``EMBEDDING_API_KEY``、请求连续失败,
            或返回向量维度与 ``settings.embedding_dim`` 不一致。
    """
    if not texts:
        return []

    settings = get_settings()
    if not settings.embedding_api_key:
        raise EmbeddingError("未配置 EMBEDDING_API_KEY,请写入 .env", kind='configuration')

    client = _get_client(settings)
    batch_size = settings.embedding_batch_size
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = list(texts[start : start + batch_size])
        vectors.extend(_embed_batch(client, settings, batch))
    return vectors


def _get_client(settings: Any) -> OpenAI:
    """按配置懒加载 OpenAI 兼容客户端。"""
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            timeout=settings.embedding_timeout,
            max_retries=0,
        )
    return _client


def _embed_batch(client: Any, settings: Any, batch: list[str]) -> list[list[float]]:
    """请求单个批次,失败时按指数退避重试,最多尝试 ``MAX_ATTEMPTS`` 次。"""
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.embeddings.create(
                model=settings.embedding_model,
                input=batch,
            )
        except Exception as exc:  # noqa: BLE001 - 统一包装成 EmbeddingError
            last_error = exc
            status = getattr(exc, 'status_code', None)
            kind = ('timeout' if isinstance(exc, APITimeoutError) else
                    'transport' if isinstance(exc, APIConnectionError) else
                    {401:'authentication',403:'authentication',402:'payment',429:'rate_limit'}.get(status,
                    'request' if isinstance(status, int) and 400 <= status < 500 else 'unavailable'))
            if kind in {'authentication','payment','request'}:
                raise EmbeddingError('嵌入请求失败：' + kind, kind=kind) from None
            if attempt < MAX_ATTEMPTS - 1:
                delay = BACKOFF_SECONDS[attempt]
                logger.warning(
                    "嵌入批次失败(第 %d/%d 次): %s;%.1f 秒后重试",
                    attempt + 1,
                    MAX_ATTEMPTS,
                    kind,
                    delay,
                )
                time.sleep(delay)
            continue
        return _validate_embeddings(response, settings.embedding_dim)

    raise EmbeddingError(
        f"嵌入请求失败(已尝试 {MAX_ATTEMPTS} 次): {kind}", kind=kind
    ) from None


def _validate_embeddings(response: Any, expected_dim: int) -> list[list[float]]:
    """按 ``index`` 排序返回向量,并校验每条维度。"""
    vectors: list[list[float]] = []
    for item in sorted(response.data, key=lambda entry: entry.index):
        vector = [float(value) for value in item.embedding]
        if len(vector) != expected_dim:
            raise EmbeddingError(
                f"嵌入维度不匹配:期望 {expected_dim},实际 {len(vector)}"
            )
        vectors.append(vector)
    return vectors
