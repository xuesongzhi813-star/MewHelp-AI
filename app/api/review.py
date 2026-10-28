"""Human review and version-aware publication, within the existing demo boundary."""
import asyncio
from typing import Literal

from fastapi import APIRouter,Depends,HTTPException,Request,Query
from pydantic import BaseModel,ConfigDict,Field

from app.flywheel.publishing import ReviewRepository,ReviewConflict,KnowledgeMaintenance
from app.evaluation.impact import ImpactConflict
from app.flywheel.locks import LockUnavailable

router=APIRouter(prefix='/api/review',tags=['review'])


def get_publisher(request: Request):
    value=getattr(request.app.state,'publisher',None)
    if value is None:raise HTTPException(503,'ch09审核发布服务未启用。')
    return value


def get_repository():return ReviewRepository()


class ApproveRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    approved_answer: str=Field(min_length=1,max_length=32000)


class ImpactRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    review_ids:list[int]=Field(min_length=1,max_length=20)


class ConfirmRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    digest:str=Field(pattern=r'^[a-f0-9]{64}$')
    edits:list[dict]=Field(max_length=96)
    unchanged_case_ids:list[str]=Field(max_length=96)
    confirmed_by:str=Field(min_length=1,max_length=100)


async def _safe(coroutine):
    try:return await coroutine
    except (ReviewConflict,ImpactConflict):raise HTTPException(409,'审核决定或版本已变化，请刷新后重新确认。') from None
    except LookupError:raise HTTPException(404,'审核记录不存在。') from None
    except (KnowledgeMaintenance,LockUnavailable):raise HTTPException(409,'已有发布或版本维护任务，请稍后重试。') from None
    except (ValueError,FileNotFoundError):raise HTTPException(422,'确认范围、标注来源或发布记录无效，请核对。') from None
    except Exception:raise HTTPException(503,'审核发布服务暂时不可用，请稍后重试。') from None


@router.get('')
async def list_reviews(after:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),
        status:Literal['待审','通过','驳回'] | None=None,publisher=Depends(get_publisher),repository=Depends(get_repository)):
    rows=await _safe(asyncio.to_thread(repository.list,after=after,limit=limit,status=status))
    return {'items':rows,'next_after':rows[-1]['id'] if len(rows)==limit else None}


@router.post('/impact')
async def preview(payload:ImpactRequest,publisher=Depends(get_publisher)):
    draft=await _safe(publisher.preview(payload.review_ids))
    if isinstance(draft,dict):return draft
    version=await _safe(asyncio.to_thread(publisher.versions.load,draft.base['dataset_version']))
    return {**draft.model_dump(mode='json'),'cases':[case.model_dump(mode='json') for case in version.cases]}


@router.post('/recalibrate')
async def recalibrate(publisher=Depends(get_publisher)):
    return await _safe(publisher.recalibrate())


@router.post('/impact/confirm')
async def confirm(payload:ConfirmRequest,publisher=Depends(get_publisher)):
    confirmed=await _safe(asyncio.to_thread(publisher.impact.confirm_impact,payload.digest,edits=payload.edits,
        unchanged_case_ids=payload.unchanged_case_ids,confirmed_by=payload.confirmed_by))
    return await _safe(publisher.publish(confirmed))


@router.get('/releases')
async def releases(limit:int=Query(20,ge=1,le=100),publisher=Depends(get_publisher)):
    return {'items':await _safe(asyncio.to_thread(publisher.list_releases,limit))}


@router.get('/releases/{release_id}')
async def release_status(release_id:str,publisher=Depends(get_publisher)):
    return await _safe(asyncio.to_thread(publisher.get,release_id))


@router.post('/releases/{release_id}/retry')
async def retry_release(release_id:str,publisher=Depends(get_publisher)):
    return await _safe(publisher.retry(release_id))


@router.get('/{review_id}')
async def detail(review_id:int,publisher=Depends(get_publisher),repository=Depends(get_repository)):
    return await _safe(asyncio.to_thread(repository.detail,review_id))


@router.post('/{review_id}/approve')
async def approve(review_id:int,payload:ApproveRequest,publisher=Depends(get_publisher),repository=Depends(get_repository)):
    row=await _safe(asyncio.to_thread(repository.decide,review_id,'通过',approved_answer=payload.approved_answer))
    return {**row,'publication_status':'awaiting_impact_confirmation'}


@router.post('/{review_id}/reject')
async def reject(review_id:int,publisher=Depends(get_publisher),repository=Depends(get_repository)):
    return await _safe(asyncio.to_thread(repository.decide,review_id,'驳回'))
