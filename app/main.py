import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.config import get_settings
from app.dependencies import get_chat_model, get_extractor
from app.history import InMemorySessionStore, SessionStore, trim_history
from app.prompts import build_system_prompt
from app.schemas import AfterSalesInfo, ChatRequest, ExtractRequest


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def create_app() -> FastAPI:
    application = FastAPI()
    settings = get_settings()
    application.state.settings = settings
    application.state.session_store = InMemorySessionStore(max_sessions=settings.max_sessions)
    application.state.system_prompt = build_system_prompt(settings)

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
        store: SessionStore = application.state.session_store
        history = store.get_history(request.session_id)
        store.append_message(request.session_id, HumanMessage(content=request.message))

        messages: list[BaseMessage] = [
            SystemMessage(content=application.state.system_prompt),
            *trim_history(history, application.state.settings.history_token_budget),
            HumanMessage(content=request.message),
        ]

        async def event_stream() -> AsyncIterator[str]:
            collected = ""
            try:
                async for chunk in llm.astream(messages):
                    content = chunk.content
                    if content:
                        collected += content
                        yield _sse({"type": "token", "content": content})
            except Exception as exc:  # noqa: BLE001 - 任何模型异常都转成 error 帧
                yield _sse({"type": "error", "message": str(exc)})
                return
            store.append_message(request.session_id, AIMessage(content=collected))
            yield _sse({"type": "done"})

        return StreamingResponse(
            event_stream(),
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
