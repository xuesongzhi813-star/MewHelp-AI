"""Bounded background generation and independent verification; never edits checkpoint."""
import asyncio
import json
import logging
import re
import time
from dataclasses import replace
from pydantic import BaseModel, ConfigDict, Field
from langchain_core.messages import HumanMessage, SystemMessage
from app.context.budget import ensure_fits, ContextBudgetError
from app.context.tokens import model_payload, count_model_tokens
from app.context.prompts import SUMMARY_PROMPT, COVERAGE_PROMPT
from app.context.layers import summary_text
from app.observability.spans import traced_operation, model_config_kwargs

logger = logging.getLogger(__name__)


class SummaryCandidate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str = Field(max_length=200)


class CoverageCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    grounded: bool = Field(strict=True)
    complete: bool = Field(strict=True)
    missing: list[str]


def entities(text):
    from app.workflow.understanding import order_ids
    return order_ids(text) | set(re.findall(r'(?i)MH-[A-Z]{2}\d+|(?<!\d)1\d{10}(?!\d)', text))


def entities_valid(batch, candidate):
    source = '\n'.join(str(m.content) for m in batch.messages)
    return entities(source) == entities(candidate)


class Summarizer:
    def __init__(self, model, *, settings, observability=None):
        self.model, self.settings = model, settings
        self.observability = observability
        self.calls = []

    async def _call(self, batch, schema, prompt, candidate=None):
        body = {'background':summary_text(batch.old_summaries), 'new_messages':model_payload(batch.messages)}
        if candidate is not None:
            body['candidate'] = candidate.content
        messages = [SystemMessage(prompt),HumanMessage(json.dumps(body,ensure_ascii=False))]
        output_cap = 512
        ensure_fits(messages,schemas=[schema.model_json_schema()],output_cap=output_cap,settings=self.settings)
        started=time.monotonic()
        async with asyncio.timeout(self.settings.answer_timeout):
            response = await self.model.with_structured_output(schema,method='json_mode',include_raw=True,
                max_tokens=output_cap).ainvoke(messages,max_tokens=output_cap, **model_config_kwargs())
        raw = response.get('raw')
        usage = getattr(raw,'usage_metadata',None)
        self.calls.append(dict(schema=schema.__name__,estimate=count_model_tokens(messages),usage=usage,
                               elapsed=time.monotonic()-started))
        logger.info('summary usage conversation=%s schema=%s estimate=%s usage=%s',batch.conversation_id,
                    schema.__name__,self.calls[-1]['estimate'],usage)
        if response.get('parsing_error') is not None or raw is None or not isinstance(raw.content,str):
            raise ValueError('invalid summary JSON')
        return schema.model_validate_json(raw.content)

    async def summarize(self,batch):
        return await self._call(batch,SummaryCandidate,SUMMARY_PROMPT)

    async def verify(self,batch,candidate):
        return await self._call(batch,CoverageCheck,COVERAGE_PROMPT,candidate)


def split_batch(batch):
    starts = [i for i,m in enumerate(batch.messages) if isinstance(m,HumanMessage)]
    if len(starts)<2:
        return None
    pivot = starts[len(starts)//2]
    def db_id(m):
        if batch.refs is not None:
            return batch.refs[m.id]['db_id']
        return int(m.id[3:]) if m.id and m.id.startswith('db:') else None
    left, right = batch.messages[:pivot],batch.messages[pivot:]
    left_ids=[i for m in left if (i:=db_id(m)) is not None]
    right_ids=[i for m in right if (i:=db_id(m)) is not None]
    if not left_ids or not right_ids:
        return None
    return (replace(batch,messages=left,upto_msg_id=max(left_ids)),
            replace(batch,messages=right,from_msg_id=min(right_ids),expected_upto=max(left_ids)))


class SummaryManager:
    def __init__(self,store,summarizer,*,settings):
        self.store,self.summarizer,self.settings=store,summarizer,settings
        self.tasks={}
        self.closed=False

    def _log(self,event,batch,started,**fields):
        logger.info('summary %s conversation=%s from=%s upto=%s expected=%s layer1=%s request=%s tokens=%s budget=%s elapsed_ms=%s %s',
            event,batch.conversation_id,batch.from_msg_id,batch.upto_msg_id,batch.expected_upto,
            batch.captured_layer1_from,batch.request_id,batch.layer2_tokens,batch.layer2_budget,
            round((time.monotonic()-started)*1000),fields)

    def maybe_schedule(self,batch,*,layer2_tokens,layer2_budget):
        if batch is None:
            return False
        batch=replace(batch,layer2_tokens=layer2_tokens,layer2_budget=layer2_budget)
        started=time.monotonic()
        if self.closed or layer2_tokens<=layer2_budget or batch.conversation_id in self.tasks:
            self._log('skip',batch,started,reason='closed/below_budget/singleflight')
            return False
        self._log('trigger',batch,started,reason=f'层2 约 {layer2_tokens} token > 预算 {layer2_budget}')
        task=asyncio.create_task(self._run(batch),name=f'summary-{batch.conversation_id}')
        self.tasks[batch.conversation_id]=task
        task.add_done_callback(lambda t:self.tasks.pop(batch.conversation_id,None))
        return True

    async def _process(self,batch,started):
        reason='fact_capacity'
        for attempt in range(2):
            try:
                candidate=await self.summarizer.summarize(batch)
                if candidate.content and entities_valid(batch,candidate.content):
                    check=await self.summarizer.verify(batch,candidate)
                    if check.grounded and check.complete:
                        row=await asyncio.to_thread(self.store.append_summary,batch.conversation_id,
                            expected_upto=batch.expected_upto,from_msg_id=batch.from_msg_id,
                            upto_msg_id=batch.upto_msg_id,content=candidate.content)
                        self._log('done' if row else 'skip',batch,started,
                                  segment=f'第{row.seq}段' if row else None,reason='committed' if row else 'CAS')
                        return row is not None
            except ContextBudgetError:
                reason='input_capacity'
                break
        children=split_batch(batch)
        if children is None:
            self._log('fail',batch,started,reason=reason)
            return False
        if not await self._process(children[0],started):
            return False
        return await self._process(children[1],started)

    @traced_operation('summary', purpose='summary')
    async def _run(self,batch):
        started=time.monotonic()
        self._log('start',batch,started)
        try:
            await self._process(batch,started)
        except asyncio.CancelledError:
            self._log('skip',batch,started,reason='cancel')
            raise
        except Exception as exc:
            self._log('fail',batch,started,reason=type(exc).__name__)

    async def aclose(self):
        self.closed=True
        tasks=list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
