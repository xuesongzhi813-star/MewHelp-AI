from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.api.jobs import router as jobs_router
from app.api.kb import router as kb_router
from app.api.chat_actions import router as chat_actions_router
from app.api.conversations import router as conversations_router
from app.chat_service import stream_chat
from app.dependencies import get_chat_model, get_extractor, get_answer_model
from app.history import SessionStore
from app.schemas import AfterSalesInfo, ChatRequest, ExtractRequest
from app.session_store import MySQLSessionStore
from app.config import get_settings


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        from app.context.logging import configure_context_logging
        configure_context_logging(Path('log/app.log'))
        from app.tooling.service import ToolService
        from app.tooling.policy import PolicyLoader
        from app.tooling.scope import model_settings
        service=ToolService(PolicyLoader(get_settings().tool_policy_path))
        await service.start()
        application.state.tool_service=service
        try:
            async with workflow_lifespan(application,service):
                yield
        finally:
            await service.aclose()
            application.state.tool_service=None

    @asynccontextmanager
    async def workflow_lifespan(application,service):
        if get_settings().chat_workflow_enabled:
            from app.workflow.runtime import open_checkpointer, WorkflowRuntime
            from app.context.budget import validate_startup
            from app.context.summary import SummaryManager, Summarizer
            from app.llm import build_workflow_model
            from app.context.assembly import fixed_agent_schemas
            snapshot=service.registry.snapshot(service.policy_loader.load())
            from app.tooling.scope import model_settings
            settings=model_settings(get_settings(),snapshot)
            budget=validate_startup(settings,schemas=fixed_agent_schemas(snapshot))
            import logging
            from app.context.budget import cumulative_turn_limit
            logging.getLogger(__name__).info('context startup budget=%s cumulative_turn_limit=%s',budget.to_dict(),cumulative_turn_limit(settings))
            async with open_checkpointer(get_settings().workflow_checkpoint_path) as saver:
                application.state.workflow_runtime = WorkflowRuntime(saver, application.state.session_store,tool_service=service)
                manager=SummaryManager(application.state.workflow_runtime.context_store,
                    Summarizer(build_workflow_model(settings),settings=settings),settings=settings)
                application.state.workflow_runtime.summary_manager=manager
                try:
                    yield
                finally:
                    await manager.aclose()
                    application.state.workflow_runtime = None
        else:
            yield

    application = FastAPI(lifespan=lifespan)
    static_dir = Path(__file__).parent / "static"
    # ch03:知识库与白名单作业 API。
    application.include_router(kb_router)
    application.include_router(jobs_router)
    application.include_router(chat_actions_router)
    application.include_router(conversations_router)
    # 共享后台脚本与页面资源;index.html 仍由下方 `/` 直接返回,不做改动。
    application.mount("/static", StaticFiles(directory=static_dir), name="static")
    # ch02:会话历史落 MySQL。构造期不连库(引擎惰性建连),MySQL 不可达时
    # 首次 store 操作才抛 OperationalError,由 stream_chat 的 error 帧路径兜住。
    # config.max_sessions 仅内存版 store 的 FIFO 淘汰参数,MySQL 版不淘汰,字段保留不删。
    application.state.session_store = MySQLSessionStore()
    application.state.workflow_runtime = None
    application.state.tool_service = None

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @application.get("/admin", include_in_schema=False)
    async def admin_page() -> FileResponse:
        return FileResponse(static_dir / "admin.html")

    @application.get("/kb", include_in_schema=False)
    async def kb_page() -> FileResponse:
        return FileResponse(static_dir / "kb.html")

    @application.post("/chat")
    async def chat(
        request: ChatRequest,
        llm=Depends(get_chat_model),
        answer_llm=Depends(get_answer_model),
    ) -> StreamingResponse:
        # ch01 的手拼消息 + astream 已整体下沉到 chat_service.stream_chat,
        # 这里只做接线;帧协议、media_type、响应头全部保持不变。
        # model 仍走每请求构建的 FastAPI 依赖(与 ch01 同款注入模式,测试可 override)。
        store: SessionStore = application.state.session_store
        from app.context.identity import parse_canonical
        try:
            identity=parse_canonical(request.session_id)
            if identity and ((request.user_id is not None and request.user_id!=identity.user_id)
                    or (request.conversation_id is not None and request.conversation_id!=identity.conversation_id)):
                raise ValueError('identity mismatch')
            if request.conversation_id is not None and identity is None:
                raise ValueError('canonical session_id required')
        except ValueError:
            raise HTTPException(422,'会话标识与请求不一致。') from None
        return StreamingResponse(
            stream_chat(request.session_id, request.message, store=store, model=llm, answer_model=answer_llm,
                        workflow_runtime=application.state.workflow_runtime,user_id=request.user_id,
                        conversation_id=request.conversation_id,tool_service=application.state.tool_service),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @application.post("/extract")
    async def extract(
        request: ExtractRequest,
        extractor=Depends(get_extractor),
    ) -> AfterSalesInfo:
        return await extractor.ainvoke(request.message)

    return application


app = create_app()
