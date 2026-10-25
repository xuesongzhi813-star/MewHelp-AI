import json
import logging
from langchain_core.messages import SystemMessage, HumanMessage
from app.context.contracts import HistoryView, SummaryRow
from app.context.logging import configure_context_logging, log_history_context, log_model_context
from app.config import get_settings
from app.context.budget import derive_budget


def test_exact_context_and_single_utf8_handler(tmp_path):
    path=tmp_path/'app.log'
    configure_context_logging(path); configure_context_logging(path)
    messages=[SystemMessage('固定前缀'),HumanMessage('原话'),HumanMessage('梗概全文ORD-1001')]
    budget=derive_budget(get_settings(),system_tokens=1,summary_tokens=1)
    log_model_context('s',1,'r',messages=messages,schemas=[],budget=budget,phase='answer')
    ctx=HistoryView(1,(SummaryRow(1,1,1,1,2,'订单ORD-1001没收到'),),messages[1:],'订单ORD-1001没收到\n原话',20)
    log_history_context('s',1,'r',context=ctx,phase='turn')
    text=path.read_text(encoding='utf-8')
    model=json.loads(next(line.split('model_ctx ',1)[1] for line in text.splitlines() if 'model_ctx ' in line))
    assert model['messages']==[dict(role='system',content='固定前缀'),dict(role='user',content='原话'),dict(role='user',content='梗概全文ORD-1001')]
    assert 'summary:1' in text and 'history_ctx' in text
    assert len([h for h in logging.getLogger('app').handlers if getattr(h,'baseFilename',None)==str(path.resolve())])==1


async def test_nonagent_turn_still_logs_history(tmp_path):
    from app.workflow.runtime import WorkflowRuntime,open_checkpointer
    from app.history import InMemorySessionStore
    from tests.test_workflow_graph import Gateway
    path=tmp_path/'nonagent.log'; configure_context_logging(path)
    async with open_checkpointer(str(tmp_path/'state.sqlite')) as saver:
        runtime=WorkflowRuntime(saver,InMemorySessionStore(10),gateway_factory=lambda m:m)
        events=[e async for e in runtime.stream_turn('s','你好',model=Gateway('闲聊'))]
    assert events[-1]['type']=='done'
    text=path.read_text(encoding='utf-8')
    assert 'history_ctx' in text and 'model_ctx' not in text


async def test_gateway_logs_exact_received_payload_on_each_call(tmp_path):
    from tests.test_workflow_models import Provider
    from app.workflow.models import ModelGateway
    from app.workflow.agent import new_budget
    from app.context.tokens import model_payload
    from langchain_core.messages import AIMessage
    class Capture(Provider):
        def __init__(self):
            super().__init__(); self.received=[]
        async def ainvoke(self,messages,**kwargs):
            self.received.append(model_payload(messages))
            return AIMessage('',tool_calls=[dict(name='respond_to_user',args={},id='x')])
        async def astream(self,messages,**kwargs):
            self.received.append(model_payload(messages))
            async for chunk in super().astream(messages,**kwargs):
                yield chunk
    path=tmp_path/'calls.log';configure_context_logging(path)
    provider=Capture();gateway=ModelGateway(provider);budget=new_budget(contextual=True)
    messages=[SystemMessage('固定'),HumanMessage('当轮'),HumanMessage('summary:1 早期订单ORD-1001')]
    await gateway.decide(messages,budget)
    _=[c async for c in gateway.stream_answer(messages,budget)]
    rows=[json.loads(line.split('model_ctx ',1)[1]) for line in path.read_text(encoding='utf-8').splitlines() if 'model_ctx ' in line]
    assert [r['messages'] for r in rows]==provider.received
    assert [r['phase'] for r in rows]==['decide','answer']
