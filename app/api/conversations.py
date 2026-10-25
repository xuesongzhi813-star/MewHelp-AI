"""Read-only anonymous conversation grouping endpoints."""
import asyncio
from fastapi import APIRouter, HTTPException, Request, Query, Path
from app.context.store import ConversationStore

router=APIRouter(prefix='/api/conversations',tags=['conversations'])


def context_store(request):
    runtime=getattr(request.app.state,'workflow_runtime',None)
    return runtime.context_store if runtime else ConversationStore()


@router.get('')
async def conversations(request:Request,user_id:str=Query(pattern=r'^[0-9a-f]{32}$')):
    try:
        return {'items':await asyncio.to_thread(context_store(request).list_conversations,user_id)}
    except Exception:
        raise HTTPException(503,'会话列表暂时不可用。') from None


@router.get('/{conversation_id}/messages')
async def messages(request:Request,conversation_id:int=Path(gt=0),user_id:str=Query(pattern=r'^[0-9a-f]{32}$')):
    store=context_store(request)
    try:
        identity=await asyncio.to_thread(store.get_owned,conversation_id,user_id)
        rows=await asyncio.to_thread(store.read_messages,conversation_id)
        return dict(conversation_id=conversation_id,session_id=identity.session_id,messages=[
            dict(id=r.id,role=r.role,content=r.content,created_at=r.created_at) for r in rows
            if r.role in {'user','assistant'} and not r.tool_calls and (r.role=='user' or r.content)])
    except LookupError:
        raise HTTPException(404,'会话不存在。') from None
    except Exception:
        raise HTTPException(503,'会话历史暂时不可用。') from None
