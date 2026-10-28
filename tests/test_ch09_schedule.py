from datetime import datetime,timezone
import pytest


async def test_latest_2am_shanghai_slot_only_and_same_day_dedup():
    from app.evaluation.scheduler import EvalScheduler
    class Runner:
        calls=[]
        async def run_evaluation(self,**kwargs):self.calls.append(kwargs);return object()
    runner=Runner();scheduler=EvalScheduler(runner)
    await scheduler.tick(datetime(2026,10,27,19,tzinfo=timezone.utc))
    await scheduler.tick(datetime(2026,10,27,20,tzinfo=timezone.utc))
    assert runner.calls==[{'trigger':'定时','slot':'2026-10-27T18:00:00+00:00'}]
    await scheduler.tick(datetime(2026,10,30,19,tzinfo=timezone.utc))
    assert len(runner.calls)==2 and runner.calls[-1]['slot']=='2026-10-30T18:00:00+00:00'


def test_naive_time_is_rejected():
    from app.evaluation.scheduler import scheduled_slot
    with pytest.raises(ValueError):scheduled_slot(datetime(2026,10,27))
