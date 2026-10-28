"""Backend-only Cloud reads; frontend receives aggregates, never credentials."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter,Depends,HTTPException,Query,Request

from app.config import get_settings
from app.observability.runtime import JAPAN_URL

router=APIRouter(prefix='/api/observability',tags=['observability'])


def get_cost_service(request:Request):
    service=getattr(request.app.state,'cost_service',None)
    if service is None:raise HTTPException(503,'观测统计服务尚未初始化。')
    return service


@router.get('/status')
async def status(request:Request):
    observed=getattr(request.app.state,'observability',None)
    settings=get_settings()
    return {'status':getattr(observed,'status','disabled'),'enabled':settings.langfuse_enabled,
        'keys_configured':bool(settings.langfuse_public_key and settings.langfuse_secret_key),
        'region':'jp','dashboard_url':JAPAN_URL,'shutdown_confirmed':getattr(observed,'shutdown_confirmed',False)}


@router.get('/costs')
async def costs(start:datetime | None=Query(None,alias='from'),end:datetime | None=Query(None,alias='to'),
        purpose:Literal['chat','summary','flywheel','evaluation','publication','calibration','label_impact','all']='chat',
        service=Depends(get_cost_service)):
    default_start,default_end=service.default_window()
    try:return await service.collect_costs(start or default_start,end or default_end,purpose=purpose)
    except ValueError:raise HTTPException(422,'仅支持最近30天内的有效带时区窗口。') from None
