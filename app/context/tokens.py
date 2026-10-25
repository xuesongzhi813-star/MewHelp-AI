"""One calibrated estimator for model-visible text, calls and tool schemas."""
import json
import math
from collections.abc import Sequence

from langchain_core.messages import BaseMessage, convert_to_openai_messages


def estimate_text(text: str, *, cjk_ratio: float = 1.0) -> int:
    if not math.isfinite(cjk_ratio) or cjk_ratio <= 0:
        raise ValueError('中文token系数必须为有限正数')
    cjk = sum('\u4e00' <= ch <= '\u9fff' for ch in text)
    return math.ceil(cjk * cjk_ratio) + math.ceil((len(text) - cjk) / 4)


def model_payload(messages: Sequence[BaseMessage]) -> list[dict]:
    # Public LangChain conversion excludes artifacts, archive IDs and internal flags.
    return convert_to_openai_messages(list(messages), include_id=False)


def count_model_tokens(messages: Sequence[BaseMessage], *, schemas: Sequence[dict] = (),
                       cjk_ratio: float = 1.0) -> int:
    payload = json.dumps(model_payload(messages), ensure_ascii=False, separators=(',', ':'))
    definitions = json.dumps(list(schemas), ensure_ascii=False, separators=(',', ':')) if schemas else ''
    return estimate_text(payload + definitions, cjk_ratio=cjk_ratio) + 4 * len(messages)
