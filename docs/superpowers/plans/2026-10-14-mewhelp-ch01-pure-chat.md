# MewHelp AI ch01 纯对话 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一个 FastAPI + LangChain 的纯对话客服服务:SSE 流式对话、session_id 多轮上下文(带 token 预算裁剪)、PromptTemplate 管理的客服 System Prompt、独立的 `POST /extract` 结构化提取接口。

**Architecture:** 单服务分层:`config`(读 .env)→ `llm`(ChatOpenAI 工厂,统一 OpenAI 协议)→ `history`(内存会话存储 + 裁剪)→ `api` 路由(/chat SSE、/extract JSON)。供应商差异(DeepSeek 关思考的 extra_body)只存在于 .env。

**Tech Stack:** Python 3.12 + uv,fastapi + uvicorn,langchain / langchain-openai(ChatOpenAI),pydantic + pydantic-settings,pytest + pytest-asyncio + httpx。

**Spec:** `docs/superpowers/specs/2026-10-14-mewhelp-ch01-pure-chat-design.md`

## Global Constraints

- 依赖用 uv 管理,Python 3.12(`uv init --python 3.12` / `uv add`)
- 模型接入只用 OpenAI 协议:`ChatOpenAI(base_url=..., model=..., api_key=...)`,全部来自 .env;换供应商(GPT/Claude 网关/Ollama)只许改 .env,不许改代码
- DeepSeek 硬性要求:构造时透传 `extra_body={"thinking": {"type": "disabled"}}`;结构化输出必须 `method="function_calling"`
- `.env` 不进 git(`.gitignore` 已有),`.env.example` 进 git
- 涉及 FastAPI/LangChain 等库的具体 API 用法拿不准时,先查 Context7 官方文档再写(用户硬性要求,版本对不上的 API 是返工重灾区)
- 技术选型已定死;实现中发现矛盾或走不通,停下问用户,不自行换方案
- **每个任务完成(测试通过、commit 后),在 `dev-notes/ch01.md` 追加一段留痕(该步的关键产出、翻车与返工);不许收尾一次性补**
- 提交信息用 conventional commits,中文描述

## Review Focus(易翻车输入,测试钉死在所属任务里)

1. `.env` 缺失或 `LLM_EXTRA_BODY` 不是合法 JSON → 启动即报清晰错误,而不是首个请求 500 → Task 1 测试
2. 空 `message` / 空 `session_id` → 422 校验拒绝,不进模型调用 → Task 6 测试
3. LLM 流式调用中途异常 → SSE 发 `error` 事件并正常结束流,不悬挂连接 → Task 6 测试
4. 历史超预算裁剪后:最旧消息被丢、最新消息保留、System Prompt 永远在 → Task 4 测试
5. `/extract` 收到非售后文本 → 正常返回 `is_after_sales: false`,不报错 → Task 7 测试

---

### Task 1: 项目脚手架与配置

**Files:**
- Create: `pyproject.toml`(uv init + uv add 生成)
- Create: `app/__init__.py`, `app/config.py`
- Create: `.env`, `.env.example`
- Test: `tests/__init__.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `app.config.Settings` 类,字段:`llm_base_url: str`、`llm_api_key: str`、`llm_model: str`、`llm_extra_body: dict`、`llm_temperature: float = 0.7`、`history_token_budget: int = 4000`、`max_sessions: int = 1000`、`shop_name: str`、`agent_name: str`;模块级单例 `get_settings() -> Settings`(lru_cache)

- [ ] **Step 1: 初始化项目与依赖**

```powershell
uv init --python 3.12 --no-readme
uv add fastapi "uvicorn[standard]" langchain langchain-openai pydantic-settings
uv add --dev pytest pytest-asyncio httpx
```

删掉 uv init 生成的 `hello.py`(如生成)。`.gitignore` 已存在,确认含 `.env`。

- [ ] **Step 2: 写 `.env`(真实,不进 git)与 `.env.example`(占位,进 git)**

`.env` 内容(密钥用用户提供的真实值;`LLM_MODEL` 先写 `deepseek-chat`,Task 9 实测后校正):

```ini
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=sk-xxx
LLM_MODEL=deepseek-chat
LLM_EXTRA_BODY={"thinking": {"type": "disabled"}}
LLM_TEMPERATURE=0.7
HISTORY_TOKEN_BUDGET=4000
MAX_SESSIONS=1000
SHOP_NAME=MewHelp 商城
AGENT_NAME=小喵
```

`.env.example` 同结构,密钥处写 `sk-your-api-key`。

- [ ] **Step 3: 写失败测试 `tests/test_config.py`**

```python
def test_settings_loads_extra_body_as_dict():
    # 用 monkeypatch 设 env 临时目录或直接读真实 .env
    s = get_settings()
    assert s.llm_extra_body == {"thinking": {"type": "disabled"}}
    assert s.history_token_budget == 4000

def test_invalid_extra_body_json_raises_clear_error():
    # LLM_EXTRA_BODY 设为 "not json" 时,Settings 抛出含字段名的 ValueError
```

- [ ] **Step 4: 运行测试确认失败**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL(模块不存在)

- [ ] **Step 5: 实现 `app/config.py`**

`pydantic-settings` 的 `BaseSettings`,env_file=".env";`llm_extra_body` 声明为 `dict`,用 field_validator 解析 JSON 字符串,非法时 `raise ValueError("LLM_EXTRA_BODY 不是合法 JSON: ...")`。`get_settings()` 用 `functools.lru_cache` 包构造。

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS(2 个)

- [ ] **Step 7: Commit**

```powershell
git add -A
git commit -m "feat: 项目脚手架与 .env 配置读取"
```

### Task 2: 数据模型(Schemas)

**Files:**
- Create: `app/schemas.py`
- Test: `tests/test_schemas.py`

**Interfaces:**
- Produces: `ChatRequest(BaseModel)`:`session_id: str`(min_length=1)、`message: str`(min_length=1);`ExtractRequest(BaseModel)`:`message: str`(min_length=1);`AfterSalesInfo(BaseModel)`:`is_after_sales: bool`、`order_id: str | None = None`、`request_type: str | None = None`、`expected_solution: str | None = None`,各字段带中文 `Field(description=...)`(function calling 的 schema 描述就来自这里)

- [ ] **Step 1: 写失败测试 `tests/test_schemas.py`**

```python
import pytest
from app.schemas import ChatRequest, ExtractRequest, AfterSalesInfo

def test_chat_request_rejects_empty_fields():
    with pytest.raises(ValidationError):
        ChatRequest(session_id="", message="hi")

def test_after_sales_info_defaults():
    info = AfterSalesInfo(is_after_sales=True, order_id="SO123", request_type="仅退款")
    assert info.expected_solution is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: FAIL(模块不存在)

- [ ] **Step 3: 实现 `app/schemas.py`**(纯 Pydantic 模型,按 Interfaces 字段定义)

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: PASS

- [ ] **Step 5: Commit**(`git add -A && git commit -m "feat: 请求与结构化输出数据模型"`)

### Task 3: Prompt 模板

**Files:**
- Create: `app/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Produces: `build_system_prompt(settings: Settings) -> str` —— 内部用 `ChatPromptTemplate.from_messages([("system", SYSTEM_TEMPLATE)])` 渲染,模板变量 `{shop_name}`、`{agent_name}` 来自 Settings

- [ ] **Step 1: 写失败测试 `tests/test_prompts.py`**

```python
def test_system_prompt_renders_shop_and_agent_name():
    prompt = build_system_prompt(settings)   # settings 里 shop_name="测试商城", agent_name="小测"
    assert "测试商城" in prompt and "小测" in prompt
    assert "{" not in prompt    # 无未渲染变量
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 `app/prompts.py`**

`SYSTEM_TEMPLATE` 要点(具体文案在 Task 8 拿真实模型过样例时定稿,以下要点先钉住):角色是 {shop_name} 的智能客服 {agent_name};约束:本章查不到订单/物流数据,不编造、不私自承诺退款赔付,信息不足时引导用户提供,超范围建议转人工;始终简体中文、简洁口语化。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**(`git commit -m "feat: 客服 System Prompt 模板"`)

### Task 4: 会话存储与上下文裁剪

**Files:**
- Create: `app/history.py`
- Test: `tests/test_history.py`

**Interfaces:**
- Produces:
  - `estimate_tokens(text: str) -> int` —— 近似:CJK 字符 1 字 1 token,其余 4 字符 1 token
  - `trim_history(messages: list[BaseMessage], budget: int) -> list[BaseMessage]` —— 从最新往旧累加,超预算即止(返回按时间正序的被保留消息;预算外消息可被全部丢弃,但不报错)
  - `SessionStore`(Protocol):`get_history(session_id: str) -> list[BaseMessage]`、`append_message(session_id: str, message: BaseMessage) -> None`
  - `InMemorySessionStore(session_store): 实现 Protocol`,`max_sessions` 上限,超限淘汰最旧会话(FIFO)

- [ ] **Step 1: 写失败测试 `tests/test_history.py`**

测试覆盖:①`estimate_tokens`("你好abc" → CJK 2 + 3/4 向上取整 1 = 3);②裁剪:构造 6 条历史、小预算,断言保留的是最新的、顺序为正序;③System Prompt 不参与裁剪(裁剪函数只吃历史,调用方负责拼 system —— 测试断言 trim 结果不含 system 也行,本质是接口约定);④新 session_id 返回空列表;⑤`append_message` 后 `get_history` 可取回;⑥超过 `max_sessions` 后最旧会话被淘汰。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_history.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 `app/history.py`**(按 Interfaces;淘汰最旧会话用 `dict` 保序性,弹出首个 key)

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_history.py -v`
Expected: PASS

- [ ] **Step 5: Commit**(`git commit -m "feat: 内存会话存储与 token 预算裁剪"`)

### Task 5: LLM 工厂

**Files:**
- Create: `app/llm.py`
- Create: `app/dependencies.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `Settings`(Task 1)、`AfterSalesInfo`(Task 2)
- Produces:
  - `app/dependencies.py`:`get_chat_model() -> ChatOpenAI`、`get_extractor() -> Runnable`(FastAPI 依赖,测试用 `dependency_overrides` 替换)
  - `get_chat_model` 实现:`ChatOpenAI(model=s.llm_model, api_key=s.llm_api_key, base_url=s.llm_base_url, temperature=s.llm_temperature, extra_body=s.llm_extra_body)`
  - `get_extractor` 实现:`get_chat_model().with_structured_output(AfterSalesInfo, method="function_calling")`

- [ ] **Step 1: 写失败测试 `tests/test_llm.py`**

```python
def test_chat_model_carries_extra_body():
    llm = get_chat_model()
    assert llm.extra_body == {"thinking": {"type": "disabled"}}
    assert llm.model_name == settings.llm_model
```

(不发起网络调用,只断言构造参数。若该版本 ChatOpenAI 的 extra_body 断言路径不同,以 Context7 查到的当前版本属性为准调整断言,不改设计。)

- [ ] **Step 2: 运行测试确认失败** → FAIL

- [ ] **Step 3: 实现 `app/llm.py` 与 `app/dependencies.py`**(若 ChatOpenAI 构造器不支持 extra_body 形参,查 Context7 确认当前版本正确传法,仍走"构造期注入"语义,不改 .env 结构)

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_llm.py -v`
Expected: PASS

- [ ] **Step 5: Commit**(`git commit -m "feat: ChatOpenAI 工厂与结构化提取器"`)

### Task 6: POST /chat —— SSE 流式对话

**Files:**
- Create: `app/main.py`(`create_app()` 工厂 + 路由)
- Test: `tests/test_chat_api.py`

**Interfaces:**
- Consumes: 全部前序接口;`langchain_core.messages` 的 `HumanMessage`/`AIMessage`
- Produces: `POST /chat`(body=`ChatRequest`)→ 200 `text/event-stream`,SSE 帧格式 `data: {"type": "token"|"done"|"error", ...}\n\n`;`GET /health` → `{"status": "ok"}`

- [ ] **Step 1: 写失败测试 `tests/test_chat_api.py`**

用 `httpx.AsyncClient(transport=ASGITransport(app=app))` + `dependency_overrides` 注入 FakeChatModel(自定义类,`astream` 逐段 yield `AIMessageChunk(content=...)`)。断言:

```python
async def test_chat_streams_tokens_then_done():
    # 请求 {"session_id": "s1", "message": "你好"}
    # 解析 SSE 帧:先若干 {"type":"token"} 后恰好一个 {"type":"done"},token 拼接等于 FakeChatModel 的完整回复
async def test_second_turn_sees_first_turn_history():
    # 第一轮后 FakeChatModel 记录收到的 messages;第二轮断言其收到 [system, human(第一问), ai(第一答), human(第二问)]
async def test_llm_error_yields_error_event():
    # FakeChatModel.astream 抛异常 → 流以 {"type":"error"} 帧结束,连接正常关闭
async def test_empty_message_rejected():
    # message="" → 422
```

- [ ] **Step 2: 运行测试确认失败** → FAIL

- [ ] **Step 3: 实现 `app/main.py`**

要点:`create_app()` 内挂路由与依赖;`/chat` 处理流程——取 `session_id` 历史 → 用户消息先 `append_message`(human)→ 拼 `[("system", system_prompt)] + trim_history(history, budget) + [HumanMessage(message)]` → `StreamingResponse` 异步生成器:`async for chunk in llm.astream(msgs)`,`chunk.content` 非空则 yield token 帧;流结束把拼接的完整回复 `append_message`(ai)再 yield done;生成器内 try/except,异常 yield error 帧。System Prompt 用 Task 3 的 `build_system_prompt`。

- [ ] **Step 4: 运行全部测试确认通过**

Run: `uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**(`git commit -m "feat: /chat SSE 流式对话端点"`)

### Task 7: POST /extract —— 结构化提取

**Files:**
- Modify: `app/main.py`(加路由)
- Test: `tests/test_extract_api.py`

**Interfaces:**
- Consumes: `get_extractor()`(Task 5)、`ExtractRequest`/`AfterSalesInfo`(Task 2)
- Produces: `POST /extract`(body=`ExtractRequest`)→ 200,响应体即 `AfterSalesInfo` 序列化 JSON

- [ ] **Step 1: 写失败测试 `tests/test_extract_api.py`**

```python
async def test_extract_returns_after_sales_info():
    # dependency_overrides.get_extractor -> 返回固定 AfterSalesInfo(is_after_sales=True, order_id="SO202610", request_type="退货退款", expected_solution="退货退款")
    # POST {"message": "订单SO202610买的鞋开胶了,我要退货退款"}
    # 断言 200 且四个字段正确
async def test_extract_non_after_sales_text():
    # fake extractor 返回 is_after_sales=False、其余 None → 200,正常返回
async def test_extract_empty_message_rejected():
    # message="" → 422
```

- [ ] **Step 2: 运行测试确认失败** → FAIL

- [ ] **Step 3: 实现:** `/extract` 路由,`await get_extractor()(request.message)`,返回结果。fake 用注入的异步可调用对象。

- [ ] **Step 4: 运行全部测试确认通过**

Run: `uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**(`git commit -m "feat: /extract 售后信息结构化提取端点"`)

### Task 8: 标注样例集与 Prompt 定稿(样例评估,替代 TDD)

**Files:**
- Create: `docs/samples/ch01-samples.md`
- Modify: `app/prompts.py`(若样例跑出问题,迭代 System Prompt 文案)

**Interfaces:**
- Consumes: 全部已完成端点
- Produces: 样例集(5~8 组:≥4 组售后,含订单号/诉求类型/期望方案的期望标注;≥2 组非售后——闲聊、售前咨询;1 组边界——信息不全的售后描述),System Prompt 定稿文案

- [ ] **Step 1: 起草样例集** `docs/samples/ch01-samples.md`,每组含:输入文本、期望的 AfterSalesInfo 标注(或"不触发")、对对话回复的要点期望(如"不编造物流信息")

- [ ] **Step 2: 启动服务真实跑样例**

```powershell
uv run uvicorn app.main:app --port 8000
```

对每组样例发真实请求(对话走 `/chat`,提取走 `/extract`),记录实际输出与标注的偏差。同时观察流式确实逐 token 到达。

- [ ] **Step 3: 迭代 System Prompt 至样例全部符合要点**(逐次修改 `app/prompts.py` 文案,重跑;样例结论写进 `docs/samples/ch01-samples.md` 的"实测记录"小节)

- [ ] **Step 4: Commit**(`git commit -m "docs: ch01 标注样例集与 Prompt 定稿"`)

### Task 9: 真实集成验收(DeepSeek)

**Files:**
- Modify: `.env.example`(若 Task 9 实测出模型名与默认值不同则校正)、`dev-notes/ch01.md`(验收记录)

- [ ] **Step 1: `GET /models` 确认模型名**(用 .env 密钥,`Invoke-RestMethod`,或 httpx 脚本)——确认 `LLM_MODEL` 取值,必要时改 `.env` / `.env.example`

- [ ] **Step 2: 三条验收标准逐条实跑**

验收 1(流式):
```powershell
curl.exe -N -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" -d '{\"session_id\":\"demo1\",\"message\":\"你们店退货流程是什么\"}'
```
Expected: 多帧 `data: {"type":"token",...}` 逐帧到达,末尾 `{"type":"done"}`

验收 2(上下文):
```powershell
# 同 session_id 连发两轮:第一轮"我叫小王,昨天买的鼠标",第二轮"我刚才说我买的是什么?"
# 断言回复含"鼠标"
```

验收 3(结构化):
```powershell
curl.exe -X POST http://127.0.0.1:8000/extract -H "Content-Type: application/json" -d '{\"message\":\"订单SO20261001买的运动鞋开胶了,穿了不到一周,要求退货退款\"}'
```
Expected: `{"is_after_sales": true, "order_id": "SO20261001", "request_type": "退货退款", "expected_solution": "退货退款"}` 字段值正确

- [ ] **Step 3: 记录验收输出到 `dev-notes/ch01.md`**(命令 + 实际响应摘录)

- [ ] **Step 4: Commit**(`git commit -m "test: DeepSeek 真实集成验收通过"`)

- [ ] **Step 5: 提醒用户轮换密钥**(密钥曾出现在聊天记录中)
