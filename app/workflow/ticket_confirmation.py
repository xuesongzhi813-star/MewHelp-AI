"""Confirmation protocol validates the persisted interrupt twice under session lock."""
import asyncio
import time
import uuid
from contextlib import aclosing
from langgraph.types import Command
from app.workflow.actions import ActionError
from app.workflow.agent import new_budget
from app.observability.spans import traced_stream, current_request_id, bind_turn
from app.flywheel.contracts import TurnIdentity
from app.flywheel.publishing import knowledge_turn_stream


class TicketConfirmationCoordinator:
    def __init__(self,runtime):
        self.runtime=runtime

    def validate(self,snapshot,request):
        state=snapshot.values
        confirmation=state.get('ticket_confirmation')
        if not state:
            raise ActionError('会话不存在。',404)
        if (not confirmation or confirmation['status']!='available' or not snapshot.interrupts
                or confirmation['preview_id']!=request.preview_id or confirmation['session_id']!=request.session_id
                or snapshot.interrupts[0].value.get('type')!='tool_confirmation'
                or snapshot.interrupts[0].value.get('preview_id')!=request.preview_id
                or time.time()>confirmation['expires_at']):
            raise ActionError('工单预览已处理或失效，请查看最新消息。',409)
        return state

    async def preflight(self,request):
        from app.context.identity import parse_canonical
        if parse_canonical(request.session_id):
            await self.runtime.resolve_identity(request.session_id)
        async with self.runtime.locks.hold(request.session_id):
            self.validate(await self.runtime.graph().aget_state(self.runtime.config(request.session_id)),request)

    @traced_stream('ticket-confirmation')
    @knowledge_turn_stream
    async def stream(self,request,*,model):
        runtime=self.runtime
        async with runtime.locks.hold(request.session_id):
            graph=runtime.graph(model)
            request_id=current_request_id() or uuid.uuid4().hex
            started=completed=False
            try:
                state=self.validate(await graph.aget_state(runtime.config(request.session_id)),request)
                turn = runtime.turn_identity(state, request_id=request_id)
                bind_turn(TurnIdentity(**turn))
                yield {'type':'turn', **turn}
                budget=new_budget(contextual=True)
                ctx=await runtime._context(state['conversation_id'],state['messages'],state['message_refs'],
                    state['current_turn_from_msg_id'],request_id,state['tool_snapshot'])
                data=Command(resume={'preview_id':request.preview_id,'decision':request.decision},
                    update={**ctx,'budget':budget.model_dump(),'request_id':request_id,'status':'running',
                        'continuation_of':state['request_id'], 'turn_identity':turn,'usage_segments':[*state.get('usage_segments',[]),
                            {'request_id':state['request_id'],'budget':state['budget']}]})
                started=True
                async with asyncio.timeout(max(.001,budget.deadline-time.time())):
                    async with aclosing(graph.astream(data,runtime.config(request.session_id),
                            stream_mode=['custom','updates'],durability='sync')) as stream:
                        async for kind,event in stream:
                            if kind=='custom':
                                yield event
                final=(await graph.aget_state(runtime.config(request.session_id))).values
                archived = await runtime._archive(final,legacy=True)
                if archived:
                    yield {'type':'turn', **archived}
                confirmation=final['ticket_confirmation']
                yield {'type':'tool_confirmation_result','preview_id':request.preview_id,
                    'status':confirmation['status'],'result':confirmation.get('result','工单已取消，未提交。')}
                completed=True
                yield {'type':'done'}
            except asyncio.CancelledError:
                raise
            except ActionError as exc:
                yield {'type':'error','message':str(exc)}
            except Exception:
                yield {'type':'error','message':'工单处理未完成，提交结果请先核实，请勿重复提交。'}
            finally:
                if started and not completed:
                    await asyncio.shield(runtime._interrupt(request.session_id,request_id))
