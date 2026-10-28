"""Manual current-graph evaluation and comparable trend data."""
import asyncio
from typing import Literal

from fastapi import APIRouter,Depends,HTTPException,Query,Request
from pydantic import BaseModel,ConfigDict

from app.evaluation.runner import build_trends
from app.flywheel.locks import LockUnavailable

router=APIRouter(prefix='/api/evaluations',tags=['evaluations'])


def get_runner(request:Request):
    runner=getattr(request.app.state,'evaluation_runner',None)
    if runner is None:raise HTTPException(503,'ch09评估服务未启用。')
    return runner


class RunRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    trigger:Literal['手动']='手动'


@router.post('/run')
async def run(payload:RunRequest | None=None,runner=Depends(get_runner)):
    try:return await runner.run_evaluation(trigger='手动')
    except LockUnavailable:raise HTTPException(409,'已有评估正在运行，请查看运行记录。') from None
    except Exception:raise HTTPException(503,'评估未能完成，请查看记录并稍后重试。') from None


@router.get('/runs')
async def runs(limit:int=Query(100,ge=1,le=1000),runner=Depends(get_runner)):
    return {'items':await asyncio.to_thread(runner.repository.list,limit)}


@router.get('/trends')
async def trends(limit:int=Query(100,ge=1,le=1000),runner=Depends(get_runner)):
    return {'points':build_trends(await asyncio.to_thread(runner.repository.list,limit))}
