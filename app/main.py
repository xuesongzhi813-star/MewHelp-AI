from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.api.jobs import router as jobs_router
from app.api.kb import router as kb_router
from app.api.chat_actions import router as chat_actions_router
from app.api.conversations import router as conversations_router
from app.api.feedback import router as feedback_router
from app.api.review import router as review_router
from app.api.evaluations import router as evaluations_router
from app.api.observability import router as observability_router
from app.chat_service import stream_chat
from app.dependencies import get_chat_model, get_extractor, get_answer_model
from app.history import SessionStore
from app.schemas import AfterSalesInfo, ChatRequest, ExtractRequest
from app.session_store import MySQLSessionStore
from app.config import get_settings


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        import asyncio
        from app.observability.runtime import ObservabilityRuntime
        observed = ObservabilityRuntime(get_settings())
        application.state.observability = observed
        await asyncio.to_thread(observed.start)
        from app.observability.costs import CostService,set_cost_service
        application.state.cost_service=CostService(observed.read_api,root=Path(get_settings().ch09_data_root))
        set_cost_service(application.state.cost_service)
        try:
            async with release_lifespan(application):
                async with flywheel_lifespan(application):
                    async with application_lifespan(application):
                        yield
        finally:
            await asyncio.to_thread(observed.close)
            application.state.observability = None
            application.state.cost_service=None
            set_cost_service(None)

    @asynccontextmanager
    async def release_lifespan(application):
        application.state.publisher=None
        application.state.release_coordinator=None
        if not get_settings().ch09_enabled:
            yield;return
        import asyncio
        import logging
        from app.flywheel.publishing import (application_owner,version_store,load_active,Publisher,
            ReleaseCoordinator,ReviewRepository)
        from app.evaluation.release import KnowledgeWriter,CalibrationService,validate_release,load_policy
        from app.evaluation.impact import ImpactService
        from app.workflow.models import ModelGateway
        from app.llm import build_workflow_model
        async with application_owner() as owner:
            versions=version_store()
            def validate_owned(identity):
                owner.assert_owned()
                validate_release(versions,identity)
            coordinator=ReleaseCoordinator(active=lambda:load_active(versions),
                validator=validate_owned,
                policy_loader=lambda identity:load_policy(versions,identity))
            repository=ReviewRepository()
            impact=ImpactService(versions,active=coordinator.active,
                reviews=lambda ids:[repository.get_review(i) for i in ids],
                gateway=ModelGateway(build_workflow_model(get_settings())))
            publisher=Publisher(versions,coordinator,impact,knowledge=KnowledgeWriter(),calibrator=CalibrationService(),
                observability=application.state.observability)
            pending=await asyncio.to_thread(publisher.pending)
            if any(item.write_started for item in pending):coordinator.mark_writing()
            else:
                try:await asyncio.to_thread(validate_release,versions,load_active(versions))
                except Exception as exc:
                    coordinator.mark_writing()
                    logging.getLogger(__name__).warning('ch09 knowledge degraded exception=%s',type(exc).__name__)
            application.state.publisher=publisher
            application.state.release_coordinator=coordinator
            try:yield
            finally:
                application.state.publisher=None;application.state.release_coordinator=None

    @asynccontextmanager
    async def flywheel_lifespan(application):
        worker=None
        if get_settings().flywheel_enabled:
            from app.flywheel.worker import FlywheelWorker
            from app.flywheel.normalization import ModelNormalizer
            from app.workflow.models import ModelGateway
            from app.llm import build_workflow_model
            worker=FlywheelWorker(ModelNormalizer(ModelGateway(build_workflow_model(get_settings()))),
                observability=application.state.observability)
        application.state.flywheel_worker=worker
        if worker: await worker.start()
        try: yield
        finally:
            if worker: await worker.aclose()
            application.state.flywheel_worker=None

    @asynccontextmanager
    async def application_lifespan(application):
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
                async with evaluation_lifespan(application):
                    yield
        finally:
            await service.aclose()
            application.state.tool_service=None

    @asynccontextmanager
    async def evaluation_lifespan(application):
        application.state.evaluation_runner=None
        scheduler=None
        coordinator=application.state.release_coordinator
        if coordinator is not None:
            from app.flywheel.publishing import version_store
            from app.evaluation.runner import EvaluationRunner
            from app.evaluation.executor import CaseExecutor
            from app.evaluation.scheduler import EvalScheduler
            observed=application.state.observability
            runner=EvaluationRunner(version_store(),coordinator,CaseExecutor(coordinator,observability=observed),observability=observed)
            application.state.evaluation_runner=runner
            if get_settings().ch09_eval_enabled:
                scheduler=EvalScheduler(runner,hour=get_settings().ch09_eval_hour,minute=get_settings().ch09_eval_minute)
                await scheduler.start()
        try:yield
        finally:
            if scheduler:await scheduler.aclose()
            application.state.evaluation_runner=None

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
                application.state.workflow_runtime = WorkflowRuntime(saver, application.state.session_store,tool_service=service,
                    observability=application.state.observability,release_coordinator=application.state.release_coordinator,
                    self_check_enabled=get_settings().ch09_enabled)
                manager=SummaryManager(application.state.workflow_runtime.context_store,
                    Summarizer(build_workflow_model(settings),settings=settings,
                        observability=application.state.observability),settings=settings)
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
    application.include_router(feedback_router)
    application.include_router(review_router)
    application.include_router(evaluations_router)
    application.include_router(observability_router)
    # 共享后台脚本与页面资源;index.html 仍由下方 `/` 直接返回,不做改动。
    application.mount("/static", StaticFiles(directory=static_dir), name="static")
    # ch02:会话历史落 MySQL。构造期不连库(引擎惰性建连),MySQL 不可达时
    # 首次 store 操作才抛 OperationalError,由 stream_chat 的 error 帧路径兜住。
    # config.max_sessions 仅内存版 store 的 FIFO 淘汰参数,MySQL 版不淘汰,字段保留不删。
    application.state.session_store = MySQLSessionStore()
    application.state.workflow_runtime = None
    application.state.tool_service = None
    application.state.observability = None

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

    @application.get('/review',include_in_schema=False)
    async def review_page() -> FileResponse:
        return FileResponse(static_dir/'review.html')

    @application.get('/quality',include_in_schema=False)
    async def quality_page() -> FileResponse:
        return FileResponse(static_dir/'quality.html')

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
                        conversation_id=request.conversation_id,tool_service=application.state.tool_service,
                        observability=application.state.observability),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @application.post("/extract")
    async def extract(
        request: ExtractRequest,
        extractor=Depends(get_extractor),
    ) -> AfterSalesInfo:
        from app.observability.spans import execution_scope, model_config_kwargs
        with execution_scope('extract', application.state.observability, purpose='extract') as scope:
            scope.update(input=request.message)
            result = await extractor.ainvoke(request.message, **model_config_kwargs())
            scope.update(output=result)
            return result

    return application


app = create_app()
