from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, StreamingResponse

from app.chat_service import stream_chat
from app.dependencies import get_chat_model, get_extractor
from app.history import SessionStore
from app.schemas import AfterSalesInfo, ChatRequest, ExtractRequest
from app.session_store import MySQLSessionStore


def create_app() -> FastAPI:
    application = FastAPI()
    # ch02:会话历史落 MySQL。构造期不连库(引擎惰性建连),MySQL 不可达时
    # 首次 store 操作才抛 OperationalError,由 stream_chat 的 error 帧路径兜住。
    # config.max_sessions 仅内存版 store 的 FIFO 淘汰参数,MySQL 版不淘汰,字段保留不删。
    application.state.session_store = MySQLSessionStore()

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @application.post("/chat")
    async def chat(
        request: ChatRequest,
        llm=Depends(get_chat_model),
    ) -> StreamingResponse:
        # ch01 的手拼消息 + astream 已整体下沉到 chat_service.stream_chat,
        # 这里只做接线;帧协议、media_type、响应头全部保持不变。
        # model 仍走每请求构建的 FastAPI 依赖(与 ch01 同款注入模式,测试可 override)。
        store: SessionStore = application.state.session_store
        return StreamingResponse(
            stream_chat(request.session_id, request.message, store=store, model=llm),
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
