"""Run the deployed graph with isolated archives and hard-disabled write tools."""
from pathlib import Path
import uuid
import time
import json

from app.tooling.service import ToolService
from app.tooling.scope import current_tools,snapshot_object
from app.evaluation.runner import case_metrics


def judge_json_instructions():
    from app.kb.evaluation_judge import JudgeFactsOutput
    return '只返回JSON对象，严格遵守下列输出结构，不附加Markdown或其他字段：\n'+json.dumps(
        JudgeFactsOutput.model_json_schema(),ensure_ascii=False,sort_keys=True)


def judge_prompt_identity():
    from app.kb.evaluation_judge import FAITHFULNESS_PROMPT
    from app.evaluation.artifacts import digest
    return digest({'prompt':FAITHFULNESS_PROMPT,'json_instructions':judge_json_instructions()})


class SideEffectAttempt(BaseException):
    """Deliberately escapes business Exception recovery; caught only by evaluation."""


class MemoryAudit:
    async def write(self,*args,**kwargs):pass


class ReadOnlyToolService(ToolService):
    def __init__(self,policy_loader):super().__init__(policy_loader,audit_sink=MemoryAudit())
    async def execute(self,name,args,**kwargs):
        snapshot=snapshot_object(kwargs.get('snapshot')) or current_tools()[1]
        definitions=snapshot.definitions if snapshot is not None else self.registry.snapshot(self.policy_loader.load()).definitions
        if name=='create_ticket' or any(d.get('name')==name and d.get('effect')=='write' for d in definitions):
            raise SideEffectAttempt('write tool attempted during read-only evaluation')
        return await super().execute(name,args,**kwargs)


class CaseExecutor:
    def __init__(self,coordinator,*,work_root=Path('log/ch09/evaluation'),observability=None,
                 model_factory=None,gateway_factory=None,service_factory=None,retrieve=None,judge=None):
        from app.config import get_settings
        from app.llm import build_workflow_model
        from app.workflow.models import ModelGateway
        from app.tooling.policy import PolicyLoader
        from app.kb.retriever import retrieve_result
        self.coordinator,self.work_root,self.observability=coordinator,Path(work_root),observability
        self.model_factory=model_factory or (lambda:build_workflow_model(get_settings()))
        self.gateway_factory=gateway_factory or ModelGateway
        self.service_factory=service_factory or (lambda:ReadOnlyToolService(PolicyLoader(get_settings().tool_policy_path)))
        self.retrieve=retrieve or retrieve_result;self.judge=judge
        self.service=None;self.model=None;self.started=False

    async def start(self):
        if self.started:return
        self.model=self.model_factory();self.service=self.service_factory()
        if self.service is not None:await self.service.start()
        self.started=True

    async def aclose(self):
        if self.service is not None:await self.service.aclose()
        self.service=None;self.started=False

    async def _judge(self,question,answer,evidence):
        if self.judge is not None:return await self.judge(question,answer,evidence)
        from app.config import get_settings
        from app.llm import build_workflow_model
        from app.workflow.models import ModelGateway
        from app.workflow.contracts import BudgetState
        from app.kb.evaluation_judge import judge_faithfulness,JudgeFactsOutput
        gateway=ModelGateway(build_workflow_model(get_settings().model_copy(update={'llm_temperature':0})))
        class Adapter:
            async def ainvoke(self,messages):
                from langchain_core.messages import SystemMessage
                return await gateway._json(JudgeFactsOutput,[SystemMessage(content=judge_json_instructions()),*messages],
                    BudgetState(deadline=time.time()+get_settings().answer_timeout),output_cap=2048)
        return (await judge_faithfulness(question,answer,evidence,model=Adapter())).model_dump()

    async def __call__(self,bound,identity,run_id):
        if not self.started:await self.start()
        from app.history import InMemorySessionStore
        from app.context.store import InMemoryConversationStore
        from app.workflow.runtime import WorkflowRuntime,open_checkpointer
        from app.workflow.knowledge import load_retrieval
        from app.kb.contracts import Evidence,Hit
        from app.kb.answering import REFUSAL_TEXT
        from app.workflow.agent import BUDGET_TEXT
        from app.observability.spans import execution_scope
        case=bound.case
        key=uuid.uuid5(uuid.NAMESPACE_URL,run_id+':'+case.id).hex
        checkpoint=self.work_root/run_id/(key+'.sqlite')
        def forbidden(*args,**kwargs):raise SideEffectAttempt('real write dependency attempted')
        def retrieve(question,**kwargs):
            plan=kwargs.get('plan')
            if plan is not None:
                updates=case.filters.model_dump(exclude_unset=True)
                kwargs['plan']=plan.model_copy(update={'filters':plan.filters.model_copy(update=updates)})
            return self.retrieve(question,**kwargs)
        frames=[];state={};error=None
        async with open_checkpointer(str(checkpoint)) as saver:
            runtime=WorkflowRuntime(saver,InMemorySessionStore(10),context_store=InMemoryConversationStore(),
                gateway_factory=self.gateway_factory,tool_service=self.service,observability=self.observability,
                retrieve=retrieve,execute=forbidden,record=forbidden,release_coordinator=self.coordinator,
                self_check_enabled=True,read_only=True,trace_purpose='evaluation')
            session_id='eval-'+key
            try:
                frames=[frame async for frame in runtime.stream_turn(session_id,case.raw_query,model=self.model)]
                state=await runtime.get_state(session_id)
                if any(f['type']=='error' for f in frames):error='graph_execution_error'
            except SideEffectAttempt:error='read_only_violation'
        retrieval=load_retrieval(state['retrieval']) if state.get('retrieval') else None
        answer=state.get('final_text','')
        can_answer=bool(state.get('status')=='completed' and (state.get('sufficiency') or {}).get('can_answer')
            and retrieval and answer not in {'',REFUSAL_TEXT,BUDGET_TEXT} and not state.get('gate_reason'))
        evidence=[]
        for item in next((f['items'] for f in reversed(frames) if f['type']=='citations'),[]):
            evidence.append(Evidence(item['number'],Hit(item['chunk_id'],0.,'', '',item['text'],'',item['section_path'])))
        judgment=None
        if can_answer and not error:
            with execution_scope('evaluation.judge',self.observability,purpose='evaluation',
                    metadata={'case_id':case.id,'source_trace_id':(state.get('turn_identity') or {}).get('trace_id')}):
                judgment=await self._judge(case.raw_query,answer,evidence)
        row=case_metrics(bound,retrieval,can_answer=can_answer,faithfulness=judgment,error=error)
        row.update(node_trace=state.get('node_trace',[]),trace_id=(state.get('turn_identity') or {}).get('trace_id'))
        return row
