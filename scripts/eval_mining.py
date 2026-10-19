"""ch03 对话挖掘评估:用真实 DeepSeek 跑样例并对比预期。

只读取 ``docs/samples/ch03-mining-samples.md`` 与调用 LLM;
绝不调用 embedding / Milvus,也不写 MySQL。

用法:
    uv run python scripts/eval_mining.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES_PATH = ROOT / "docs" / "samples" / "ch03-mining-samples.md"

SAMPLE_PATTERN = re.compile(r"^### ([EN]\d+)\.\s*(.+)$", re.MULTILINE)
CONVERSATION_PATTERN = re.compile(r"```conversation\s*\n(.*?)\n```", re.DOTALL)
TURN_PATTERN = re.compile(r"^(user|assistant|tool)[:：]\s*(.*)$", re.MULTILINE)
EXPECT_QUESTION_PATTERN = re.compile(r"^\s*-\s*问[:：]\s*(.+)$", re.MULTILINE)
EXPECT_ANSWER_PATTERN = re.compile(r"^\s*-\s*答[:：]\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Sample:
    sample_id: str
    title: str
    should_extract: bool
    turns: tuple[tuple[str, str], ...]
    expected_questions: tuple[str, ...]
    expected_answers: tuple[str, ...]


def parse_samples(path: Path) -> list[Sample]:
    """把样例 Markdown 解析成结构化 Sample 列表。"""
    text = path.read_text(encoding="utf-8")
    matches = list(SAMPLE_PATTERN.finditer(text))
    samples: list[Sample] = []
    for index, match in enumerate(matches):
        segment = text[
            match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(text)
        ]
        conversation = CONVERSATION_PATTERN.search(segment)
        if conversation is None:
            raise ValueError(f"样例 {match.group(1)} 缺少 conversation 代码块")
        turns = tuple(
            (role, content.strip())
            for role, content in TURN_PATTERN.findall(conversation.group(1))
        )
        if not turns:
            raise ValueError(f"样例 {match.group(1)} 的 conversation 代码块为空")
        samples.append(
            Sample(
                sample_id=match.group(1),
                title=match.group(2).strip(),
                should_extract=match.group(1).startswith("E"),
                turns=turns,
                expected_questions=tuple(
                    value.strip()
                    for value in EXPECT_QUESTION_PATTERN.findall(segment)
                ),
                expected_answers=tuple(
                    value.strip() for value in EXPECT_ANSWER_PATTERN.findall(segment)
                ),
            )
        )
    return samples


def build_conversations(samples: list[Sample]):
    """为每个样例构造一个伪会话;id 从 9001 起,message_id 稳定递增。"""
    from app.kb.store import ConversationForMining

    conversations = []
    next_message_id = 1
    for index, sample in enumerate(samples, start=9001):
        turns = []
        for role, content in sample.turns:
            turns.append((next_message_id, role, content))
            next_message_id += 1
        conversations.append(
            ConversationForMining(
                id=index,
                user_id=f"eval-{sample.sample_id.lower()}",
                max_message_id=turns[-1][0],
                turns=tuple(turns),
            )
        )
    return conversations


def evaluate(extractor, sample: Sample, conversation, build_prompt) -> dict:
    """跑单个样例,返回评测结果 dict(不抛异常时 verdict 为符合/漏抽/误抽)。"""
    prompt = build_prompt([conversation])
    try:
        result = extractor.invoke(prompt)
        pairs = list(getattr(result, "pairs", None) or [])
    except Exception as exc:  # noqa: BLE001 - 单个样例失败也要继续跑完
        print(f"  调用失败:{exc}")
        return {"sample": sample, "pairs": [], "error": str(exc), "verdict": "调用失败"}

    # 只统计本样例会话 id 的问答;其他 id 视为模型越界。
    valid = [pair for pair in pairs if pair.conversation_id == conversation.id]
    unknown = [pair for pair in pairs if pair.conversation_id != conversation.id]

    print(
        f"  预期:{'应抽取' if sample.should_extract else '不应抽取'}"
        f" {len(sample.expected_questions)} 对"
    )
    for number, (question, answer) in enumerate(
        zip(sample.expected_questions, sample.expected_answers), start=1
    ):
        print(f"    期望 Q{number}: {question}")
        print(f"    期望 A{number}: {answer[:100]}")
    if valid:
        for number, pair in enumerate(valid, start=1):
            print(f"    实际 Q{number}: {pair.question}")
            print(f"    实际 A{number}: {pair.answer[:100]}")
    else:
        print("    实际:(空)")

    if unknown:
        print(
            "    越界 conversation_id:"
            + "、".join(str(pair.conversation_id) for pair in unknown)
        )

    if sample.should_extract:
        verdict = "符合" if valid and not unknown else ("漏抽" if not valid else "越界")
    else:
        verdict = "符合" if not valid and not unknown else "误抽"
    print(f"  判定:{verdict}")
    return {"sample": sample, "pairs": valid, "unknown": unknown, "verdict": verdict}


def main() -> int:
    from app.config import get_settings
    from app.kb.mining import build_mining_model, build_mining_prompt

    samples = parse_samples(SAMPLES_PATH)
    conversations = build_conversations(samples)
    settings = get_settings()
    print(
        f"对话挖掘样例评估:共 {len(samples)} 组,"
        f"模型 {settings.llm_model},只调用 LLM(不调用 embedding/Milvus)。"
    )
    extractor = build_mining_model(settings)

    results = []
    for sample, conversation in zip(samples, conversations):
        print(f"\n===== {sample.sample_id}. {sample.title}")
        results.append(
            evaluate(extractor, sample, conversation, build_mining_prompt)
        )

    print("\n===== 汇总")
    for item in results:
        sample = item["sample"]
        print(
            f"  {sample.sample_id} {sample.title}: {item['verdict']}"
            f"(实际 {len(item['pairs'])} 对)"
        )
    passed = sum(1 for item in results if item["verdict"] == "符合")
    print(f"  符合 {passed}/{len(results)} 组;其余为人工判定项,不自动断言。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
