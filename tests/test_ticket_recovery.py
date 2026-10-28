from app.workflow.tool_calls import prepare_tool_call,prepare_ticket_write,execute_ticket_write
from tests.test_ticket_interrupt import setup_runtime
from app.workflow.runtime import open_checkpointer


async def test_lost_process_capability_never_reissues_or_replays_write(tmp_path):
    async with open_checkpointer(str(tmp_path/'recovery.sqlite')) as saver:
        runtime,service,audit,calls,gateway=await setup_runtime(tmp_path,saver)
        try:
            _=[e async for e in runtime.stream_turn('s','包裹未收到，帮我建工单',model=gateway)]
            state=await runtime.get_state('s')
            state['ticket_confirmation']['status']='confirmed'
            state.update(prepare_ticket_write(state,service))
            assert state['ticket_confirmation']['status']=='creating'
            service.capabilities.clear()
            result=await execute_ticket_write(state,service)
            assert not calls and result['ticket_confirmation']['status']=='uncertain'
        finally:
            await service.aclose()


async def test_same_creating_state_cannot_write_twice(tmp_path):
    async with open_checkpointer(str(tmp_path/'repeat.sqlite')) as saver:
        runtime,service,audit,calls,gateway=await setup_runtime(tmp_path,saver)
        try:
            _=[e async for e in runtime.stream_turn('s','包裹未收到，帮我建工单',model=gateway)]
            state=await runtime.get_state('s')
            state['ticket_confirmation']['status']='confirmed'
            state.update(prepare_ticket_write(state,service))
            first=await execute_ticket_write(state,service)
            second=await execute_ticket_write(state,service)
            assert len(calls)==1
            assert first['ticket_confirmation']['status']=='completed'
            assert second['ticket_confirmation']['status']=='uncertain'
        finally:
            await service.aclose()
