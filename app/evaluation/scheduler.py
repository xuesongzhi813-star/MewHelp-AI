"""Application-local schedule; no OS or Codex automation is created."""
import asyncio
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import logging


def scheduled_slot(now,*,hour=2,minute=0):
    if now.tzinfo is None or now.utcoffset() is None:raise ValueError('timezone-aware clock required')
    local=now.astimezone(ZoneInfo('Asia/Shanghai'))
    slot=local.replace(hour=hour,minute=minute,second=0,microsecond=0)
    if slot>local:slot-=timedelta(days=1)
    return slot.astimezone(timezone.utc).isoformat()


class EvalScheduler:
    def __init__(self,runner,*,hour=2,minute=0):
        self.runner,self.hour,self.minute=runner,hour,minute
        self._slot=None;self._task=None
    async def tick(self,now):
        slot=scheduled_slot(now,hour=self.hour,minute=self.minute)
        if slot==self._slot:return
        await self.runner.run_evaluation(trigger='定时',slot=slot)
        self._slot=slot
    async def _run(self):
        while True:
            try:await self.tick(datetime.now(timezone.utc))
            except asyncio.CancelledError:raise
            except Exception as exc:logging.getLogger(__name__).warning('evaluation schedule failed exception=%s',type(exc).__name__)
            await asyncio.sleep(60)
    async def start(self):
        if self._task is None:self._task=asyncio.create_task(self._run())
    async def aclose(self):
        if self._task:
            self._task.cancel()
            try:await self._task
            except asyncio.CancelledError:pass
            self._task=None
