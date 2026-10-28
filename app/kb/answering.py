"""先完成证据判断及引用/承诺校验，再由编排层发布正文；本模块无业务写入。"""
from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Sequence

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from app.config import get_settings
from app.kb.contracts import AnswerDecision, Evidence, Hit, QueryPlan, RetrievalResult
from app.prompts import EVIDENCE_ANSWER_PROMPT
from app.observability.spans import model_config_kwargs

REFUSAL_TEXT = "抱歉，现有知识不足以可靠回答这个问题，我无法给出确定答案。建议联系人工客服核实。"


class AnswerServiceError(RuntimeError):
    """模型/网络失败，不能计入知识缺口。"""


def refuse(reason: str) -> AnswerDecision:
    return AnswerDecision(can_answer=False, reason=reason, answer="", used_citation_numbers=[])


def make_evidence(hits: Sequence[Hit]) -> list[Evidence]:
    return [Evidence(number, hit) for number, hit in enumerate(hits[:10], 1)]


def reorder_evidence(evidence: Sequence[Evidence]) -> list[Evidence]:
    return list(evidence[::2]) + list(evidence[1::2][::-1])


_CITATION = re.compile(r"\[(\d+)\]")
_NEGATED = re.compile(r"不能|不保证|无法保证|不能保证|不承诺|不支持|不提供|不赔|不补|不会|并非|不是|不得|不允许|勿|未承诺|无法承诺|不能确定|不确定")
_PROMISE = re.compile(
    r"(?:保证|承诺|一定|必定|肯定|确保|必然|绝对|百分百).{0,16}(?:到账|入账|送达|必达|收到|签收|真伪|正品|全新|合身|库存|有货|赔付|补偿|维修)"
    r"|(?:\d+(?:[-—至]\d+)?\s*(?:个工作日|工作日|天|日|小时)|明天|今天|后天|下周|周[一二三四五六日天]|\d+月\d+日).{0,8}(?:到账|入账)"
    r"|(?:免费维修|赔付\s*\d+|补偿\s*\d+)"
    r"|(?:会|将|给予|提供|直接|安排).{0,10}(?:赔付|补偿)"
    r"|必达"
)


def has_forbidden_promise(text: str) -> bool:
    # 分开转折和独立分句，防“不能保证X，但保证Y”用前半句的否定掩护后半句。
    for clause in re.split(r"[。！？!?；;，,\n]|但是|不过|然而|但", text):
        for match in _PROMISE.finditer(clause):
            context = clause[max(0, match.start() - 12):match.end()]
            if not _NEGATED.search(context):
                # 后置否定只允许紧接的明确否认，不用整段任意一个“不”掩盖承诺。
                suffix = clause[match.end():match.end() + 20]
                if not re.match(r"(?:并非|不是|不属于|并不是).*(?:权益|承诺|服务|保证)", suffix):
                    return True
    return False


def validate_answer(decision: AnswerDecision, evidence: Sequence[Evidence]) -> AnswerDecision:
    if not decision.can_answer:
        return refuse(decision.reason or "证据不足")
    text = decision.answer.strip()
    if has_forbidden_promise(text):
        return refuse("回答包含禁止承诺")
    cited = {int(n) for n in _CITATION.findall(text)}
    allowed = {e.number for e in evidence}
    used = decision.used_citation_numbers
    if not text or not cited or cited != set(used) or not cited <= allowed or len(used) != len(set(used)):
        return refuse("引用缺失、未知或与实际使用编号不一致")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n|\n(?=\s*(?:[-*•]|\d+[.、]))", text) if part.strip()]
    if any(not _CITATION.search(part) for part in paragraphs):
        return refuse("事实段落缺少引用")
    return decision.model_copy(update={"answer": text})


def _parsed_decision(response) -> AnswerDecision:
    if isinstance(response, dict) and "parsed" in response:
        if response.get("parsing_error") is not None or response["parsed"] is None:
            return refuse("生成自评结构非法")
        response = response["parsed"]
    try:
        return AnswerDecision.model_validate(response)
    except (ValidationError, TypeError):
        return refuse("生成自评结构非法")


def _fact_text(text: str) -> str:
    # 只允许调整引用/Markdown/空白；金额、SKU、否定、日期、措辞均保持。
    text = re.sub(r"\[(?:\d+|N)\]", "", text)
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    return re.sub(r"\s+", "", text.replace("**", "").replace("__", ""))


def retrieval_rejection_reason(result: RetrievalResult, *, apply_score_gate: bool = True) -> str:
    if not result.hits:
        return "没有召回可用证据"
    if result.strategy == "hybrid_rerank" and apply_score_gate:
        score = result.hits[0].rerank_score
        if score is None or not math.isfinite(score) or score < get_settings().rerank_min_score:
            return "重排最高分低于部署阈值或无有效重排分数"
    return ""


async def generate_answer(plan: QueryPlan, result: RetrievalResult, *, model,
                          apply_score_gate: bool = True) -> AnswerDecision:
    rejection = retrieval_rejection_reason(result, apply_score_gate=apply_score_gate)
    if rejection:
        return refuse(rejection)
    evidence = make_evidence(result.hits)
    data = {"raw_question": plan.raw_query, "normalized_question": plan.normalized_query,
            "evidence": [{"citation": f"[{e.number}]", "chunk_id": e.hit.chunk_id,
                          "section_path": e.hit.section_path, "questions": e.hit.questions,
                          "text": e.hit.answer} for e in reorder_evidence(evidence)]}
    try:
        # 初次生成及最多一次格式修复共享原有总预算，不增加业务工具轮次。
        async with asyncio.timeout(get_settings().answer_timeout):
            original = _parsed_decision(await model.ainvoke([
                SystemMessage(content=EVIDENCE_ANSWER_PROMPT),
                HumanMessage(content=json.dumps(data, ensure_ascii=False)),
            ], **model_config_kwargs()))
            validated = validate_answer(original, evidence)
            format_error = validated.reason in {"引用缺失、未知或与实际使用编号不一致", "事实段落缺少引用"}
            allowed = {e.number for e in evidence}
            known_numbers = {int(n) for n in _CITATION.findall(original.answer)} | set(original.used_citation_numbers)
            if validated.can_answer or not original.can_answer or not format_error or not known_numbers <= allowed:
                return validated
            repair_data = {**data, "draft": original.model_dump(), "format_error": validated.reason}
            repaired = _parsed_decision(await model.ainvoke([
                SystemMessage(content=EVIDENCE_ANSWER_PROMPT + "\n本次仅修复draft的引用格式：正文全部事实和措辞必须原样保留，"
                              "只可调整引用编号位置、Markdown和空白；连引导语也需引用，不输出字面[N]。"
                              "used_citation_numbers必须与正文数字引用一致。不得新增/删减事实、修改数字/SKU/否定，"
                              "不得为了通过校验给无证据的事实乱贴引用；无法修复则can_answer=false。"),
                HumanMessage(content=json.dumps(repair_data, ensure_ascii=False)),
            ], **model_config_kwargs()))
            if repaired.can_answer and _fact_text(repaired.answer) != _fact_text(original.answer):
                return refuse("引用格式修复改变了事实文字")
            return validate_answer(repaired, evidence)
    except asyncio.CancelledError:
        raise
    except Exception:
        raise AnswerServiceError("回答生成服务暂时不可用") from None
