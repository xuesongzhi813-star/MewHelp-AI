# ch05 Workflow 与核心 Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 先以裸循环展示工具型 Agent，再以 LangGraph 实现固定分流、前置证据闸、多步 Agent、官方 SQLite 持久化及两个独立用户操作。

**Architecture:** 确定性节点控制七类意图到四出口，Agent 以决策/工具/回答节点循环。SQLite 保存运行状态，MySQL 继续归档对话与既有业务记录；工单只允许经用户确认入口创建。保留 SSE，最终回答真实流式发布。

**Tech Stack:** Python 3.12、现有 FastAPI/LangChain/SQLAlchemy/MySQL/Milvus、锁定 LangGraph 1.2.15 / checkpoint 4.3.0、兼容的官方 langgraph-checkpoint-sqlite、原生 HTML/CSS/JavaScript。

**Spec:** [已批准设计](../specs/2026-10-21-mewhelp-ch05-workflow-agent-design.md)。用户书面审批原话「批准」。用户已回复「计划通过，Native」，Tasks1–8已完成；Task9最终验证及交付记录见reports/ch05/summary.md和专属账本。

## Global Constraints

- 采用官方 `AsyncSqliteSaver`，本机单进程，默认 `log/ch05/checkpoints.sqlite`；不新增 Postgres，不选社区 MySQL saver，不改既有 MySQL 表。
- Agent 前置证据闸后实时流式；不再承诺 ch04 全文校验后发布，保留证据及禁止承诺 Prompt。
- 预算默认 6 次业务工具、累计 12,000 tokens、整轮 90 秒，包含分类/必要检索模型/决策/回答。无 usage 时标记估算，不声称精确账单。
- 闲聊统一分类一次，固定回复阶段零模型调用；指代节点原样透传。
- 不新增业务工具；转人工仅前端模拟；建工单必须独立点击并确认；普通聊天绝不写 tickets。
- ch04 v2 语料/集合/清单/校准不变，不升级现有依赖、不重建知识库、不读取或修改 `.env` 真实值。
- 保留原有未提交文件；禁止自动 commit/push/merge/deploy。各 Task 的“完成”是验证及留痕，不执行技能模板中的自动提交。
- 可单测代码 TDD；Prompt/数据以固定标注样例评估；每个任务完成及时写 `dev-notes/ch05.md`，无事写无。
- 额度剩余 10% 且不足以完成下一小阶段时，先写 `docs/CH05_CURRENT_PROGRESS.md` 交接，不冒称 finish。

## Review Focus

- 【Task 3/5】包含订单号的政策问题：不得因订单号路由业务而绕过强制检索；旧 QueryPlan 不得覆盖本章路由。
- 【Task 2/5/6】提供商忽视工具约束或一次返回多个调用：不得执行写工具、漏配 ToolMessage、漏记预算或把控制 JSON 发给用户。
- 【Task 4/6】重启、断线或下一轮：不得复用上轮证据/预算、重复导入 MySQL 历史、自动重放操作或虚报完成。
- 【Task 7】MySQL 已创建工单而 SQLite 保存失败：必须保留可核实的不确定状态，不能重复写票或提示成功去重已保证。
- 【Task 8】用户两个按钮都不点或取消确认后继续聊天：零工单写入、无模拟接待，旧按钮状态与新消息互不串扰。

## 文件与接口边界

- `app/workflow/contracts.py`：Intent/Route、结构化控制、ActionSuggestion、BudgetState、WorkflowState 等序列化契约。
- `app/workflow/budget.py`：预算预留、usage结算、截止时间；`models.py`：统一计量模型适配器及控制 schema。
- `app/workflow/agent.py`：模型决策、配对工具结果、最终正文生成的可复用步骤；`app/agent_demo.py`：手写 while 教学入口。
- `app/workflow/routing.py`：分类、固定映射/话术；`knowledge.py`：旧检索适配与前置闸；`nodes.py`：图节点；`graph.py`：图与依赖装配。
- `app/workflow/runtime.py`：SQLite生命周期、会话锁、旧消息导入/归档和图状态读写；`actions.py`：确认工单状态机。
- `app/api/chat_actions.py`：确认API；`app/static/chat-actions.js`：按钮和确认；`app/chat_service.py`/`main.py`：SSE与生命周期接线。
- `scripts/eval_ch05.py`/`docs/samples/ch05-*.jsonl`：Prompt及Agent评估；`reports/ch05/`：真实结果、demo、review；运行缓存进 `log/ch05/`。

类型约定：`WorkflowEvent = dict[str, Any]` 仅表示已验证的服务端事件；`Emit = Callable[[WorkflowEvent], None]`；`ModelGateway` 为本计划定义的计量适配器，不指未经核对的第三方类型。`WorkflowDeps` 持有gateway、store、检索/执行/问题池可注入函数，模型/连接均不进入State。序列化结构用内建dict/list/字符串及LangChain消息，Pydantic负责边界校验。

## 实施前检查（执行方式获批后，Task 1 开始前）

- [x] 读取批准的spec/plan、最新ch05留痕与Git状态；使用 using-git-worktrees 技能安排隔离。优先复用适合的已附工作树，否则从实际 ch04 HEAD 建立托管工作树，不带入无关未跟踪文件；将已批准的两份文档和ch05留痕同步到执行工作树，明确唯一编辑路径。
- [x] 核对现有 `.venv`/uv及服务；不复制 `.env` 到新目录，不运行会打印秘密的命令。需凭据时使用已有安全配置来源；路径/权限不允许时按权限机制处理。
- [x] 运行基线 `uv run --no-sync pytest -q`，记录当前真实结果与服务依赖；测试夹具使用既有事务隔离，不以旧559通过代替。Windows临时文件故障先保留证据再判断，不盲目重跑整套。
- [x] 记录执行方式、工作树路径、基线及未跟踪项。以后各任务分别读取所需技能（TDD、debugging、verification等）；未经批准不先装依赖。

### Task 1：预算及模型控制契约

**Files:** Create `app/workflow/__init__.py`, `contracts.py`, `budget.py`, `models.py`, `tests/test_workflow_budget.py`, `tests/test_workflow_models.py`; Modify `app/config.py`, `.env.example`, `app/llm.py`, `app/dependencies.py`, `tests/test_config.py`。

**Interfaces:**
- `Intent` 七值中文Literal；`Route` = knowledge/business/complaint/chat；`ControlDecision` 含 response_mode(answer/clarify)、actions(受限kind列表)、可选description/ticket_type。
- `BudgetState` 保存token_limit/charged_tokens/tool_calls/decision_calls/deadline及actual/estimated标记；`CallReservation` 保存本次输入估算及输出上限。
- `reserve_call(budget: BudgetState, messages: list[BaseMessage], *, schema_text: str, output_cap: int) -> CallReservation`；`settle_call(budget, reservation, *, usage: dict | None, output_text: str) -> None`。参数为进程内预算快照，更新后写回State；估算包括正文、schema、协议开销，不漏工具参数。
- `ModelGateway` 提供 `async classify(messages, budget) -> Intent`、`async decide(messages, budget) -> AIMessage`、`async stream_answer(messages, budget) -> AsyncIterator[AIMessageChunk]`、`query_adapter(budget)`（供旧build_query_plan同步invoke）。每次预留/结算都走同一预算口径，线程adapter使用锁保护预留、禁止迟到响应覆盖新轮State。
- 新配置 `CHAT_WORKFLOW_ENABLED=false`（历史链回退），`AGENT_MAX_TOOL_CALLS=6`、`AGENT_TOKEN_BUDGET=12000`、`AGENT_TURN_TIMEOUT=90`、`AGENT_MAX_DECISIONS=10`、`WORKFLOW_CHECKPOINT_PATH=log/ch05/checkpoints.sqlite`，正值校验。

- [x] RED：`test_defaults_are_6_12000_90`、`test_reservation_counts_schema_and_bounds_output`、`test_classification_and_query_usage_are_charged`、`test_usage_missing_is_estimated`、`test_exhausted_budget_never_calls_provider`、`test_stream_usage_charged_once`；断言输入占满剩余额度则不调用provider，chunk累加不重复结算usage。
- [x] 运行 `uv run pytest tests/test_workflow_budget.py tests/test_workflow_models.py tests/test_config.py -q`，确认预期缺失/行为失败，保留RED原因。
- [x] Context7核对锁定LangChain消息/ChatOpenAI bind_tools、tool_choice、usage、输出限额。用原始函数schema `respond_to_user` 表示编排控制，参数为ControlDecision；它不加入TOOL_REGISTRY、不执行任何业务。决策绑定现有只读工具和控制schema，最终正文阶段工具禁用；既有create_ticket调用视为被拦截建议。
- [x] 实现上述契约与gateway；新链模型显式 `max_retries=0`，不让SDK隐藏重试绕过预算。结构化分类/查询结果同时保留原始usage；查询adapter可返回旧QueryRewrite而不改变其公开契约。若提供商不支持所需接口，停下来说明，不自行换模型或方案。
- [x] GREEN：运行上面命令及 `tests/test_llm.py`/`tests/test_extract_api.py`，确认旧提取器仍兼容；补本Task留痕（不提交）。

### Task 2：先完成可运行的裸 Agent 循环

**Files:** Create `app/workflow/agent.py`, `app/agent_demo.py`, `tests/test_agent_loop.py`, `docs/samples/ch05-agent.jsonl`; Modify `app/tools.py` 的旧转人工/工单误导docstring（不改业务实现）。

**Interfaces:**
- `async decide_step(messages, *, gateway: ModelGateway, budget: BudgetState) -> AIMessage`。
- `async execute_step(decision: AIMessage, *, budget, context: dict, execute, emit: Emit) -> tuple[list[ToolMessage], list[dict]]`；返回配对结果与尚未执行的建议。
- `async answer_step(messages, *, gateway, budget, emit: Emit) -> AIMessage`，只发正文；`async run_agent_loop(question: str, *, gateway, execute, context, history: list[BaseMessage] | None = None) -> dict`，教学入口自建预算、显式while，返回最终文本/工具名序列/usage/stop_reason。

- [x] RED：`test_order_then_logistics_receives_first_result`断言两次实际工具执行、第二次模型输入含第一条ToolMessage；`test_one_lookup_then_answer`断言只查一次；`test_missing_order_id_clarifies_without_tool`断言零工具；`test_create_ticket_is_suggestion_only`断言写工具spy零调用。
- [x] RED补边界：`test_multiple_tool_calls_get_paired_results`、`test_unknown_tool_is_never_executed`、`test_six_calls_or_ten_decisions_stop_without_extra_model`；最多执行第一个允许工具，其余调用逐个回灌“未执行，请单步调用”，所有tool_call_id都有结果。
- [x] 运行 `uv run pytest tests/test_agent_loop.py -q` 确认RED；实现共用步骤与真正手写while，禁止导入LangGraph编排器。`python -m app.agent_demo --message ...` 使用已有安全配置，不写业务会话/工单；stdout显示脱敏工具名/步数/终态，不打印思维链。
- [x] GREEN运行同命令及 `tests/test_tools.py tests/test_executor.py`；以固定样例真实运行一次简单/复杂/缺参/建工单诱导，记录实际模型表现和失败，不将fake代替真实。教学阶段未证明多步前不勾选完成，不跳到图重构。
- [x] 立即补留痕：裸循环演示命令、真实工具序列、测试结果和模型失败；示例入口后续复用共用步骤，避免第二套业务实现。

### Task 3：七类分类、固定路由与检索前置闸

**Files:** Create `app/workflow/routing.py`, `knowledge.py`, `tests/test_workflow_routing.py`, `tests/test_workflow_knowledge.py`, `docs/samples/ch05-intents.jsonl`, `scripts/eval_ch05.py`; Modify `app/prompts.py`（仅新增ch05 Prompt）。

**Interfaces:**
- `route_intent(intent: Intent) -> Route`；`async classify_intent(question: str, *, gateway, budget) -> Intent`，严格JSON `{"intent":...}`；`resolve_reference(question: str) -> str` 原样返回。
- `async retrieve_knowledge(question: str, *, gateway, budget, retrieve) -> RetrievalResult`：显式构造 `build_query_plan(..., model=gateway.query_adapter(budget))` 再传 `plan=` 给既有 `retrieve_result`，确保没有漏计的隐藏改写调用；旧plan.intent不控制图路由。
- `evidence_gate(result: RetrievalResult) -> str`，空串通过，非空为既有rejection_reason；序列化结果及证据采用内建结构供后续State使用。
- CLI `python scripts/eval_ch05.py --suite intents|agent --output <path>`；intents只分类，agent复用Task2只读入口，报告包含逐题预期/实际/usage/服务失败，不写生产数据。

- [x] RED：`test_all_seven_intents_have_fixed_exit`、`test_reference_is_byte_for_byte_passthrough`、`test_invalid_intent_is_error_not_chat`、`test_plan_business_does_not_override_policy_exit`；分别断言全部固定映射和非法结构异常。
- [x] RED：`test_gate_empty_and_low_score`、`test_gate_nan_rejected`、`test_query_model_usage_charged`、`test_retrieval_service_failure_is_not_knowledge_gap`；分数用测试配置阈值，不改真实profile。
- [x] 运行 `uv run pytest tests/test_workflow_routing.py tests/test_workflow_knowledge.py -q` 见RED后实现；复用既有检索/证据编号/首尾排列，不修改KB语料或阈值。
- [x] Prompt验证替代TDD：先固定28条分类样例（每类4条，含订单政策/退款状态/投诉夹业务/闲聊客套等），保存唯一id、原话、预期类别/出口；测试成功后再跑真实分类，不按成绩改标签。另用8条Agent样例（简单2/复杂2/缺参2/操作建议2）。路由安全相关分类失败必须分析和修正Prompt，服务失败单列，仍失败则明确阻塞正式验收。
- [x] GREEN运行上述测试及 `tests/test_kb_query.py tests/test_kb_retriever.py tests/test_kb_answering.py`；真实评估报告写 `reports/ch05/intents.json`、`agent-loop.json`；记录Prompt迭代及留痕，不以单测覆盖率冒充分类准确率。

### Task 4：官方 SQLite 生命周期与序列化状态

**Files:** Create `app/workflow/runtime.py`, `tests/test_workflow_runtime.py`; Expand `contracts.py`; Modify `pyproject.toml`, `uv.lock`。

**Interfaces:**
- `WorkflowState`：messages、session_id、conversation_id、request_id、raw_question/resolved_question、intent/route、retrieval、gate_reason、budget、status、pending_decision、actions（按action_id持久保存的映射）、turn_action_ids、node_trace；各字段都是可序列化值。
- `@asynccontextmanager open_checkpointer(path: str) -> AsyncIterator[AsyncSqliteSaver]`；`SessionLocks.hold(session_id: str)` 为异步context manager，有引用计数清理且不移除正在被等待的锁。
- `WorkflowRuntime` 后续接收compiled graph/store；本Task实现连接与锁，图状态读写由Task6装配。外部模型不放入State。

- [x] RED：`test_sqlite_reopens_saved_messages_and_actions`（最小无模型图+临时文件，两次独立连接）、`test_state_roundtrip_preserves_tool_message_pair`、`test_same_session_serializes_different_sessions_overlap`、`test_waiting_lock_is_not_evicted`、`test_connection_closed_on_exit`；运行 `uv run pytest tests/test_workflow_runtime.py -q` 见RED。
- [x] Context7核对官方 AsyncSqliteSaver/from_conn_string/setup、`aget_state/aupdate_state`及锁定LangGraph接口；本地metadata/源码核对4.3.0兼容范围。声明直接依赖LangGraph1.2.15并只添加兼容SQLite包；uv解析前后对比，任何既有依赖升级先停下来，不自行接受。
- [x] 实现官方连接生命周期；允许SQLite文件内部表初始化，禁止MySQL DDL。不直接复制在线文档的v3流式接口；使用本地版本已验证的Graph API/custom stream。
- [x] GREEN跑上述临时文件测试及 `uv run python -c "from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver; print('sqlite-checkpointer-import-ok')"`；记录新增版本与锁文件差异，再补留痕。

### Task 5：确定性骨架与 Agent 显式节点循环

**Files:** Create `app/workflow/nodes.py`, `graph.py`, `tests/test_workflow_graph.py`; Expand `contracts.py`, `agent.py`（复用步骤，不复制循环）。

**Interfaces:**
- `WorkflowDeps` 包含gateway、store、retrieve、execute、record_low_confidence；`build_workflow(*, checkpointer, deps: WorkflowDeps)` 返回已编译图。
- 节点名固定 `resolve_reference/classify_intent/retrieve_knowledge/evidence_gate/agent_decide/agent_tools/agent_answer/complaint_reply/chat_reply/refusal_reply/log_result`；`agent_tools -> agent_decide`，通过控制决策进入answer；异常向上交Task6统一终态。
- 节点将BudgetState转换为序列化快照写回；请求截止时间来源于本轮初始化，外层90秒timeout及每次调用剩余时限双重约束；默认recursion_limit=64覆盖10决策/6工具及前后节点。

- [x] RED图轨迹：`test_policy_forces_retrieve_before_agent`、`test_return_policy_can_then_query_order`、`test_business_skips_retrieval_and_gate`、`test_low_evidence_records_once_and_never_calls_agent`；同时断言节点顺序与实际调用spy，不只检查最终文案。
- [x] RED固定出口：`test_complaint_two_actions_zero_agent_zero_ticket`、`test_chat_one_classifier_zero_answer_model`；RED循环：`test_graph_order_then_logistics`、`test_no_stale_evidence_or_budget_on_next_turn`、`test_foreign_control_never_executes_ticket`。
- [x] 运行 `uv run pytest tests/test_workflow_graph.py -q`见RED；实现固定边/条件边、序列化证据、拒答真实会话关联、固定话术、log_result结构日志。控制元数据只到actions，不作为token；所有成功路径到log_result。
- [x] GREEN运行本Task及Tasks1–4测试，复用Task2同一组脚本模型证明裸循环与图工具序列/停止原因一致；节点trace可审计，不输出用户原话/工具返回详情/内部推理；即补留痕。

### Task 6：聊天接线、真流式与会话归档恢复

**Files:** Expand `runtime.py`; Modify `app/chat_service.py`, `app/main.py`, `app/dependencies.py`, `app/session_store.py`（稳定消息ID/所需最小适配）、`app/static/chat-citations.js`; Create `tests/test_workflow_chat.py`, `tests/test_workflow_recovery.py`; Adapt `tests/test_chat_api.py` 的新模式覆盖。

**Interfaces:**
- `WorkflowRuntime.stream_turn(session_id: str, question: str, *, model) -> AsyncIterator[WorkflowEvent]`；`get_state(session_id) -> WorkflowState`、`save_actions(session_id, actions: dict) -> None`，后两者异步，caller持会话锁。
- `stream_chat(..., workflow_runtime=None)` 保持旧调用签名兼容并增加可选参数。`CHAT_WORKFLOW_ENABLED=true`时只走图；false保留旧链回退。启动lifespan打开单例checkpointer/runtime；测试显式注入临时runtime，不靠ASGITransport自动启动lifespan。
- 稳定历史消息ID优先使用MySQL message.id；无该字段的测试store使用确定性导入ID。图不存在时仅导入一次；每轮归档新增消息，已完成assistant只在成功终态写入。失败可留user归档但不能当完整assistant。

- [x] RED流式：`test_first_token_arrives_before_model_release`用事件屏障让模型发首chunk后等待，消费者在解除屏障前必须看到token；`test_classification_and_tool_json_never_streamed`、`test_actions_not_enabled_on_error`、`test_error_has_no_done_or_raw_exception`、`test_budget_exhaustion_no_final_model_call`。
- [x] RED恢复：`test_reopen_completed_thread_continues_without_duplicate_history`、`test_interrupted_turn_not_replayed`、`test_second_turn_clears_transients_preserves_old_action_map`、`test_chat_and_action_share_lock`；临时SQLite关闭重开，旧消息和工具对保留。
- [x] Context7核对FastAPI lifespan/StreamingResponse取消行为、LangGraph custom stream和SQLAlchemy现有事务接口，才实施对应修改；不得为测试启动app而误触真实checkpointer或写库。
- [x] 实现只转发agent_answer正文流与固定文本，不转发通用messages流里的分类/决策。先发可用citations、终态前actions、成功提交后done；cancel/error写中断状态并关闭generator释放锁，不自动重试或恢复旧轮。
- [x] GREEN运行本Task及 `tests/test_chat_service.py tests/test_chat_grounding.py tests/test_chat_api.py tests/test_session_store.py tests/test_kb_citations_api.py`；旧模式原单轮测试仍有效，新模式使用独立多步断言，不删除旧断言凑绿。记录结果及留痕。

### Task 7：独立确认工单接口与保守重试语义

**Files:** Create `app/workflow/actions.py`, `app/api/chat_actions.py`, `tests/test_chat_actions.py`; Modify `app/schemas.py`, `app/main.py`, `runtime.py`（实现Task6已声明状态接口）、`app/executor.py`（仅必要单次执行入口）。

**Interfaces:**
- `ConfirmTicketRequest` 仅session_id/action_id/confirmed，extra=forbid，confirmed必须True；`TicketActionResult` 含action_id/status/result（工单工具返回文本）。
- `async confirm_ticket(request: ConfirmTicketRequest, *, runtime: WorkflowRuntime, store, create_once) -> TicketActionResult`；`POST /api/chat/actions/ticket`调此函数。服务端建议含kind/description/ticket_type/status；status为available/creating/completed/uncertain，handoff不走此接口。
- `create_once(description, ticket_type, *, conversation_id: int) -> str`复用现有create_ticket.invoke，系统参数只在context通道传递；单次调用，不进入通用两次重试路径。若新增executor入口命名 `execute_tool_once`，限固定注册工具验证，不能产生任意执行能力。

- [x] RED：`test_unconfirmed_or_extra_conversation_id_rejected`、`test_wrong_session_or_kind_rejected`、`test_description_and_conversation_are_server_owned`、`test_chat_never_creates_ticket`、`test_completed_action_returns_original_result`、`test_two_concurrent_confirmations_create_once`。
- [x] RED崩溃窗口：`test_creating_persisted_before_tool_call`、`test_ticket_success_then_checkpoint_failure_is_uncertain`、`test_reopen_creating_does_not_reexecute`；工具返回前失败和返回后状态保存失败均不盲目重试，数据库写入结果不明提示核实。
- [x] 运行 `uv run pytest tests/test_chat_actions.py -q`见RED；先持共享锁，验证建议，持久化creating成功后才调用工具，成功保存completed再回报；超时不能取消底层线程则留不确定，不向用户说“未创建”鼓励重试。普通异常不泄漏原始内容。
- [x] GREEN复跑上述及 `tests/test_tools.py tests/test_executor.py tests/test_session_store.py`；真实MySQL测试使用现有事务夹具核对tickets关联和重复确认，不改表。仅明确本地合成验收才做一次持久写入；补留痕及已知跨库非原子边界。

### Task 8：两个独立按钮、确认与现有引用兼容

**Files:** Create `app/static/chat-actions.js`, `tests/test_chat_actions_page.py`; Modify `app/static/index.html`、必要时 `chat-citations.js`；现有页面冒烟测试保留。

**Interfaces:**
- `createActionView({container, sessionId, appendMessage, submitTicket})` 返回 `setActions(items)`, `complete()`, `fail()`；按action_id分别维护状态，服务器内容用textContent，不innerHTML。`submitTicket`只在确认事件中POST Task7接口。
- 手工模拟两句文案必须逐字为「已转接人工客服」「您好，我是客服小猫，请问有什么可以帮您的」。旧按钮不影响普通输入框/新轮；handoff只禁用自身，ticket创建中只锁对应ticket按钮。

- [x] 可单测页面接线先RED：`test_chat_page_loads_actions_module_and_confirmation_api`只覆盖加载/路由接线，不把字符串冒烟当交互验收；JS行为使用环境已有可用测试能力，不为此引入未经批准的新测试依赖。
- [x] 实现actions SSE缓存至对应消息，done后启用，error/断线则禁用未完成建议。点击ticket出现可取消确认框，确认才发送session_id/action_id/confirmed=true；网络不确定时显示核实提示，不自动重试。
- [x] 浏览器验证：投诉两个独立按钮；都不点继续聊天无请求；只点handoff出现两句且tickets零新增；ticket取消零请求；确认一次一票；重复点击无重写；先转人工后建工单以及反向顺序均正常；旧消息/新消息按钮不串；引用跨chunk点击、👍👎保留；错误轮不启用按钮。保存截图和请求/数据库对照，屏蔽敏感信息。
- [x] GREEN运行页面冒烟/相关API与JS检查，记录浏览器实际结果。不得沿用ch04的前端TDD/review豁免作本章授权；本章按用户TDD要求覆盖可测接线，交互用真实浏览器验证。即补留痕。

### Task 9：集中评审、真实演示、最终回归与交付

**Files:** Create `reports/ch05/demo.md`, `review.md`, `acceptance.json`, `summary.md`, `docs/CH05_CURRENT_PROGRESS.md`; Modify `docs/AI_HANDOFF.md`, `docs/OPERATIONS.md`, 本spec/plan及 `dev-notes/ch05.md`；必要修复限定前八任务文件。

- [x] 按所选执行方式完成独立code review：Native末尾一次集中评审，Subagent-driven另有任务级评审；评审覆盖实际未提交diff/新增文件，不只比较空commit range。重点核对Review Focus五项、写工具隔离、预算遗漏、断线/重启、无鉴权边界及SQLite锁。记录结论，不预填“通过”；问题按debugging/TDD修复后定向复验。
- [x] Context7及本地源码核对演示命令依赖的CLI用法；核实当前MySQL/Milvus和ch04最终集合/profile，真实启动使用 `CHAT_WORKFLOW_ENABLED=true` 与既有五项ch04配置及profile阈值，路径/端口按实际工作树填写。禁止打印DB_URL/密钥、不运行kb-reset/build迁移。
- [x] 用脚本/浏览器完成五项真实验收和弱证据/预算/真流式/重启扩展验收。组合题为「请先查询订单1001的当前状态，再查询它的物流轨迹，并分别告诉我结果」，现有物流工具按order_id查询，不杜撰查运单工具。记录实际先query_order后query_logistics两步；若模型未做到，修Prompt并重新跑固定样例，保留失败。
- [x] 重新运行28分类与8Agent固定样例，对比裸循环/图行为，记录实际调用/用量/服务失败；知识测试只读现有集合。投诉正常对话、handoff不写库、取消不写库、ticket确认才写一票以本地合成会话为界；仅记录相关ID，不能删除其他会话或清理现有数据来凑验收。
- [x] 运行针对性测试和必要最终 `uv run pytest -q`；记录本次passed/failed/skipped、原因/未验证范围。最终检查diff、`git diff --check`及新文件直接检查、Git状态，扫描本章交付文本的已知凭据模式但不输出命中秘密。SQLite文件/WAL/运行日志不入库，评估报告中不含客户对话或密钥。
- [x] 按 verification-before-completion 和 finishing-a-development-branch 完成交付；用户未授权提交/推送/合并则保留可审查工作树，不重复强求集成选择。更新handoff当前入口及ch05完成范围，旧ch04结论保留为历史；demo含裸循环命令、服务启动、五项演示、关闭方法、真实结果与限制。
- [x] 立即追记code review阶段结论、真实验收阶段与finish（各自完成即记录，不一次补造）；标记各任务真实勾选状态。本计划未获审批/未执行时不得写“已完成”。

## 自审与执行交接

覆盖核对：spec §1/4由Task2及5实现，§2/3由前置检查和Task1/4/6实现，§5/6由Task3/5实现，§7由Task1/2/5实现，§8/9由Task4/6实现，§10由Task7/8实现，§11/12由各任务及Task9实现。Review Focus五项均有具名验证。类型/方法在首次产出任务定义，后续不另起同义入口。

本计划选择按9个任务交付，依赖顺序为1→2（必须先演示裸循环）→3→4→5→6→7→8→9。Task2裸循环不依赖LangGraph或SQLite；Task4引入官方SQLite依赖后再启动图。真服务不可用时允许完成隔离单测，最终真实演示不能因此标通过。

本轮Context7查询支持LangChain tool_calls、控制schema及usage相关设计；最新流式文档含v3接口，不作为锁定版本可用的证据。执行前按各Task再次核对对应版本的具体接口。未知库兼容性用短探针解决，不能靠换技术栈绕过用户决定。

建议执行方式：**Native**，由当前代理按任务实施，末尾一次独立集中评审。9个任务共享State/gateway/runtime接口且顺序依赖明显，逐任务新代理会重复装载上下文；独立评审仍覆盖整条链及副作用。若用户选择Subagent-driven，则按对应技能安排逐任务实现与独立评审。

状态：用户已回复「计划通过，Native」。本次恢复后逐项核对实际进度，Tasks1–8完成，Task9集中评审四项Important修复、最终630项全量、28/28分类、8/8Agent、八条真实验收和重启/按钮验证完成。Task9官方关闭见账本，交付报告reports/ch05/summary.md。未提交/推送/合并/部署，保留工作树与账本。

Finish：2026-10-22，Task9官方complete，全量630 passed in35.79s；保留未提交工作树与账本，不清理/提交。
