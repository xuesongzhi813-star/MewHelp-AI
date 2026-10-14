# MewHelp AI ch01:纯对话跑通 — 设计文档

- 日期:2026-10-14
- 状态:待用户评审
- 章节:电商智能客服系统 第一章(共多章,后续:工具调用/Agent、存储建库等)

## 意图与背景

MewHelp AI 是一个分章推进的电商智能客服系统学习/实战项目。第一章的目标是**跑通纯对话**:一个能流式回复、记得住上下文、能从用户话里提取售后结构化信息的服务。后续章节会在本章的骨架上叠加工具调用、Agent 循环、数据库存储等能力,所以本章的接口层要留好抽象,但不提前实现。

## 目标(验收标准)

1. `curl` 调对话接口能看到 SSE 流式回复(逐 token 推送)
2. 连续问两轮,第二轮能续上第一轮的上下文(按 session_id)
3. 发一段售后描述,能在同一条 SSE 流里拿到结构化 JSON(订单号、诉求类型、期望方案)

## 非目标 / 反目标

- **不做**工具调用、Agent 循环 —— 后续章节
- **不做**数据库存储 —— 本章会话历史存内存字典,接口抽象成 Protocol,后续章节换 SQLite 实现
- **不做**聊天页面 —— 将来单独做,用 Vibe Coding 方式
- **反目标**:为通用性过度抽象;把后续章节的能力提前塞进来;换供应商时需要改代码(一切供应商差异只允许存在于 `.env`)

## 约束(用户拍板,不可自行变更)

- 技术栈:Python + FastAPI + LangChain,依赖用 uv 管理
- 模型接入统一说 OpenAI 协议:地址、模型名、密钥全在 `.env`,GPT、Claude(网关模式)、DeepSeek、Ollama 靠换环境变量切换
- 验收用 DeepSeek 直连:`https://api.deepseek.com` + 用户提供的密钥(密钥只进 `.env`,不进 git)
- DeepSeek 两条硬性要求(用户实测得出):
  1. **必须关闭思考模式**(新版 API `thinking` 参数默认 `enabled`,不关会导致输出前长时间无响应/混入思考内容)
  2. **结构化输出必须用 `method="function_calling"`**(DeepSeek 的 `response_format` 只支持 `text`/`json_object`,不支持 OpenAI 的 json_schema)
- 涉及具体库的 API 用法一律先查 Context7 官方文档再动手

## 架构与关键决策

### 对话接口

`POST /chat`,请求体 JSON:`{"session_id": "...", "message": "..."}`
响应:`text/event-stream`,SSE 事件协议(**我的设计**,字段名实现阶段可微调):

| 事件 type | data 内容 | 说明 |
|---|---|---|
| `token` | `{"content": "..."}` | 逐 token 的回复文本 |
| `structured` | `{"order_id": ..., "request_type": ..., "expected_solution": ...}` | 仅当判定用户在描述售后问题时发送 |
| `done` | `{}` | 流结束 |
| `error` | `{"message": "..."}` | 出错时发送并以流终止 |

另设 `GET /health` 便于存活检查。

### 模型接入(统一 OpenAI 协议)

用 `langchain-openai` 的 `ChatOpenAI`,构造参数全部来自 `.env`:

```ini
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=sk-xxx
LLM_MODEL=<实现开始时用 GET /models 实测确认,写入 .env.example 默认值>
LLM_EXTRA_BODY={"thinking": {"type": "disabled"}}   # DeepSeek 关思考;换其他供应商时改成 {}
LLM_TEMPERATURE=0.7
```

`LLM_EXTRA_BODY` 是 JSON 字符串,透传给 `ChatOpenAI` 的 `extra_body`。这样"关思考"这类供应商差异只存在于 `.env`,代码不动 —— 满足"换着接"的需求。(**我的设计**)

### Prompt 管理

System Prompt 用 `ChatPromptTemplate`/`PromptTemplate` 模板化,要点(具体文案实现阶段写,评审样例时定稿):

- 角色:某电商平台智能客服,名字、语气设定
- 行为约束:不编造订单/物流信息(本章无工具,拿不到数据,要引导用户提供)、不私自承诺退款赔付、超出范围转人工、回复简体中文、简洁
- 模板变量:店铺名、客服名等,从 `.env` 注入,便于后续改

### 结构化输出(嵌在对话流里)

Pydantic schema(**我的设计**):

```python
class AfterSalesInfo(BaseModel):
    is_after_sales: bool        # 是否售后诉求
    order_id: str | None        # 订单号
    request_type: str | None    # 退货退款/仅退款/换货/维修/投诉/其他
    expected_solution: str | None  # 期望方案(用户原话归纳)
```

流程:每收到用户消息,在流式生成回复的**同时**,对(本轮用户消息 + 最近少量上下文)发起一次 `with_structured_output(AfterSalesInfo, method="function_calling")` 调用;回复 token 流结束后,若 `is_after_sales=True` 且有有效字段,追加 `structured` 事件。每轮共两次模型调用。

(实现提示:两次调用用 `asyncio` 并发;若并发实现复杂,退化成流结束后串行调用,功能等价只是慢一点 —— 计划阶段定。)

### 会话与上下文裁剪

- 存储:`SessionStore`,内存 `dict[session_id] -> list[BaseMessage]`,暴露 Protocol 接口(`get_history` / `append`),后续章节换 SQLite 实现
- 裁剪:每次构造请求消息时,从**最新往旧**累加历史消息,超过 token 预算(默认 4000,`.env` 可配 `HISTORY_TOKEN_BUDGET`)即止;System Prompt 恒定保留在预算之外
- token 估算:近似算法(中文 1 字 ≈ 1 token,英文 4 字符 ≈ 1 token)。不引 tiktoken —— DeepSeek 的 tokenizer 不公开,精确计数做不到,最简版近似够用(**我的决策**)
- 内存防护:会话数上限(如 1000,超限淘汰最旧的)+ 单会话历史条数随裁剪自然受限(**我的决策**)

### 项目结构(大致,计划阶段细化)

```
app/
  main.py        # FastAPI 入口 + 路由
  config.py      # .env 读取(pydantic-settings)
  schemas.py     # 请求/响应模型 + AfterSalesInfo
  prompts.py     # PromptTemplate 定义
  llm.py         # ChatOpenAI 工厂(读 .env)
  history.py     # SessionStore Protocol + InMemorySessionStore + 裁剪
tests/           # pytest
dev-notes/ch01.md # 开发过程留痕
.env / .env.example
pyproject.toml
```

### 测试策略

- 单测(mock 模型):裁剪逻辑(超预算丢旧留新)、SSE 事件序列、schema 解析、PromptTemplate 渲染
- 真实集成(验收):实现者持有 DeepSeek 密钥,在交付前真实跑通三条验收标准(启动服务、curl 实测、`GET /models` 确认模型名并写入 `.env.example` 默认值),交付后由用户复验
- 标注样例集:实现者起草 5~8 组典型对话样例(售后场景含期望提取字段的标注值、非售后场景标注为不触发结构化),System Prompt 定稿与结构化提取效果均以该样例集人工评审,用户在评审时纠偏

## 留给实现的决策

- System Prompt 具体文案(评审样例时定稿)
- SSE 具体字段名、是否用 `sse-starlette` 还是原生 `StreamingResponse` 手写 SSE 格式
- token 估算函数的具体实现
- 文件内函数划分

## 已定决策清单(来源:用户)

| 决策 | 内容 |
|---|---|
| 会话存储 | session_id 内存字典,后续章节换建库建表 |
| 结构化输出位置 | 嵌在对话流里,非独立接口 |
| 模型供应商 | DeepSeek 直连,关思考模式,function_calling 结构化 |
| 流程要求 | Superpowers 全流程,dev-notes 留痕,Prompt 类产出用样例评估替代 TDD |
| 验收执行 | 实现者持密钥真实跑通三条验收标准,交付后用户复验 |
| 样例集 | 实现者起草 5~8 组标注样例,用户评审纠偏 |

> 用户批准本文档即视为设计冻结;文档中标注"我的设计"的项(SSE 事件协议、LLM_EXTRA_BODY 方案、近似 token 估算、内存防护上限)随之定稿。
