# ch06 正式分流器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 用户须先批准计划并选择执行方式；未授权自动提交。

**Goal:** 完成同会话问题理解、八类意图分流、确定性退款/售后流程，以及聊天内订单选择和演示退款申请。

**Architecture:** 基于现有 ch05 LangGraph 增加显式子流程；LLM只负责理解、分类、扩写及最终业务判断。订单选择与申请确认由服务器检查点状态和同会话锁管理，检索保留单份知识、统一去重重排。

**Tech Stack:** 沿用锁定 Python 3.12、FastAPI、LangChain、LangGraph 1.2.15、官方 SQLite checkpointer、SQLAlchemy/MySQL、Milvus 和静态页面；无新依赖/服务。

**Spec:** `docs/superpowers/specs/2026-10-23-mewhelp-ch06-router-design.md`（2026-10-23 用户已批准）。

## Global Constraints

- 产品执行目录固定为 `E:/MewHelp AI/.worktrees/ch05-workflow-agent`；下文源码相对路径均相对此目录，根目录仅为审批文档入口。
- ch05 已是 linked worktree，直接复用；不从 HEAD 新建工作树、不 stash/reset/覆盖既有未提交内容。每项变更用执行前文件清单与哈希区分。
- 不修改/输出真实 .env；不新增 MySQL 表/DDL、不清理或重建知识库、不升级依赖、不提交/推送/合并/部署。
- 默认同一聊天上游主力模型；低成本小模型升级路线仅留配置种子，本章不实现任何可启用执行分支，不能擅自更换技术栈或供应商。
- Prompt/数据以真实标注评估代替TDD；可单测后端行为走RED→GREEN；前端Vibe Coding，浏览器核验，不要求前端brainstorm/TDD/code review。
- 每任务完成即追记 dev-notes/ch06.md；根与产品工作树对应ch06文档同步。保留历史失败，不将未跑结果写成通过；额度不足先写交接。
- 复用 query_faq(keyword)、原 SSE 帧、MySQL原文权威和系统注入 conversation_id。演示申请复用售后tickets，仅用户确认后写入，不执行真实退款。

## Review Focus

1. 历史有两张订单时“它”不应被自动绑定最近订单；Task2测试歧义与来源验证。
2. 用户打开选择器后改聊物流，旧卡片不能恢复旧退款；Task5测试新消息使旧selection失效。
3. 双击/双标签页/断线重连不能重复续跑或建单；Task5/6测试串行消费、重复及不确定状态。
4. 一路检索报错，其他查询有证据不能掩盖服务故障；Task4测试部分失败及预算耗尽。
5. 演示订单随服务重启结果不同，或卡片、工具、物流互相矛盾；Task3测试固定数据与共享读取。

## 执行前协议

- 使用本机既有解释器：`E:/MewHelp AI/.venv/Scripts/python.exe`；在产品执行目录运行所有测试，禁止在根目录旧代码上跑出结果后冒充ch06验证。
- 每个涉及外库的任务开始前：Context7 resolve官方库→按单概念query→核对uv.lock与本地签名/源码；把来源/版本差异记入任务留痕。已查LangGraph状态更新、ChatOpenAI结构化输出不等于已验证全部接口。
- 若因沙箱/网络失败，按工具规则申请必要权限；不为绕过限制修改产品行为。真实服务不可用时记录未验证范围，不假造质量结果。
- 每任务结束核对diff、运行所属测试、记录变更文件/结果/拒绝或纠偏/翻车返工。用户未授权commit，技能中的commit步骤由留痕与diff审查替代。

### Task 1：固定基线、标注集和评估入口

**Files:** 新建 `docs/samples/ch06-router-dev.jsonl`、`docs/samples/ch06-router-acceptance.jsonl`、`scripts/eval_ch06.py`、`tests/test_eval_ch06.py`；执行生成 `reports/ch06/baseline.json`、`reports/ch06/baseline-tests.json`；同步本spec/plan/留痕。

**Interfaces:** 样例逐条含 id、chain_id（可空）、history、question、expected_intent、expected_question、expect_passthrough、expected_order_id、split。`evaluate_router(cases, *, gateway_factory) -> dict` 输出逐条实际结果、原始一次解析/修复/系统兜底计数、usage、模型标识、失败原因，不存凭据或真实对话。实际ModelGateway在Task2接入；本任务可交付标签及评估统计器。

- [ ] 记录当前分支/HEAD、git status和app/tests/scripts/docs等非敏感源码的SHA256；排除.env、log、数据库/WAL、venv及嵌套工作树。仅在已核实的产品位置生成新reports文件，不写ch05报告。
- [ ] 运行既有全量测试：`& 'E:/MewHelp AI/.venv/Scripts/python.exe' -m pytest -q`。记录真实通过/失败/跳过和服务条件；失败先定位基线，不直接继续实现或照搬637历史数。
- [ ] 准备至少60条独立固定验收样例（八类每类至少5条，另补边界），另设至少16条调试样例；至少3组物流→退款→物流，每轮单独标注。包括完整透传、两订单歧义、未知问题、伪造类别指令、否定/期限/金额、退款进度与物流边界。验收标签在调Prompt前冻结。
- [ ] 统计器TDD：`test_fallback_is_not_first_parse_success`断言格式失败后系统兜底不计原始解析成功，`test_chain_requires_every_turn_correct`断言三轮错一轮整链失败。先跑失败再实现：`... -m pytest tests/test_eval_ch06.py -q`。
- [ ] 编写CLI `python scripts/eval_ch06.py --split dev|acceptance --output PATH`，报告总体/逐类准确率、逐字透传、指代正确率、关键链、JSON一次/修复后解析率；支持mock仅验证统计逻辑，真实质量必须标注provider=real。记录任务完成。

### Task 2：历史理解、严格意图JSON及主力模型接入

**Files:** 新建 `app/workflow/understanding.py`、`app/workflow/router_prompts.py`；修改 `contracts.py`、`routing.py`、`models.py`、`nodes.py`、`config.py`、`llm.py`、`.env.example`；测试 `tests/test_workflow_understanding.py`、`test_workflow_routing.py`、`test_workflow_models.py`、`test_workflow_budget.py`。

**Interfaces:** `UnderstandingResult`：standalone_question:str(1..2000)、reference_status:literal(unchanged/resolved/ambiguous)、order_id:str|null(≤64)、source_message_id:str|null。`understand_question(question, *, history, gateway, budget) -> UnderstandingResult` 为async；`classify_intent(question, *, gateway, budget) -> IntentDecision` 为async，返回严格两字段意图对象。`IntentDecision`八类枚举+confidence有限float[0,1]，extra=forbid；`route_intent(intent: str) -> str`新增refund/other出口。ModelGateway新增 `understand(messages,budget)` 与 `expand(messages,budget)`；classify返回对象而非旧str，所有调用点/fixture同步迁移。

- [ ] Context7核对ChatOpenAI JSON mode与include_raw/usage、Pydantic严格字段及LangChain消息裁剪。分类独立配置 `intent_model: str|None=None`、`intent_min_confidence=0.7`（0..1）；默认None继承当前主力模型，不改真实配置。整个gateway共享budget计量，模型结构结果不得绕过计量。
- [ ] RED测试：完整问题逐字保留；两订单“它”→ambiguous且order_id=None；模型造的9999无来源拒绝；来源ID存在但对应消息没有该订单仍拒绝；明确指代已充分的问题不润色；SKU/否定/金额/时间不丢；低confidence→其他；额外字段/NaN/非法意图拒绝；异常只修复一次、超时走服务错误、所有修复计费。
- [ ] 实现一次理解调用并验证来源；history用既有trim_history+paired_history，不把已归档原话覆盖。原话完整由Prompt约束并用标注集验证；业务数字保真检查不直接套ch04禁止新增历史实体的旧规则。模糊指代不得从“最近订单”启发式猜测。
- [ ] 编写八选一Prompt+few-shot、JSON形状及其他兜底；classify低分返回其他，格式失败预算内修复一次仍失败返回其他/0，并保存独立解析状态用于真实报告。供应商故障不得吞为其他。
- [ ] 运行上述四组相关测试到GREEN；接通Task1评估CLI，真实跑dev集、记录基线与修订；Prompt不写镜像测试替代真实评估。固定验收暂不调参。记录任务完成。

### Task 3：固定演示订单与选择来源

**Files:** 新建 `app/demo_orders.py`、`app/workflow/orders.py`、`tests/test_workflow_orders.py`；修改 `app/tools.py`、`tests/test_tools.py`。

**Interfaces:** `DemoOrder`只含order_id、product、sku、status、amount、placed_at、signed_at（可null）、condition（可null）、is_demo=True。`list_demo_orders() -> list[dict]`、`get_demo_order(order_id: str) -> dict|None`返回副本；`verified_order_id(understanding, *, messages, selected_order_id=None) -> str|None`只接受可验证原话/历史引用或已验证选择。`prepare_order_selection(state) -> dict`生成服务器selection_id、request_id、候选快照、status=available。

- [ ] RED：相同ID跨模块/重新加载结果相同，修改返回对象不污染源；卡片/query_order/query_logistics对演示1001/1002/1003一致；多候选无唯一来源不自动选；伪造点选ID不作为可信selected_order_id。
- [ ] 固定三张合成订单，时间为明确演示日期并注入测试now（避免测试随真实日期漂移），具备已签收/已发货/退款中差异。query_order和query_logistics对这些ID读取同源数据，其他旧mock行为维持；列表不从随机缓存枚举。
- [ ] 验证 `... -m pytest tests/test_workflow_orders.py tests/test_tools.py -q` 到GREEN；记录数据字段缺失时不作退款获批推断，无真实订单访问。记录任务完成。

### Task 4：检索侧扩写、政策候选合并及统一重排

**Files:** 新建 `app/kb/multi_query.py`、`tests/test_kb_multi_query.py`；修改 `app/workflow/knowledge.py`、`app/workflow/models.py`、`app/workflow/contracts.py`，必要时对 `app/kb/query.py`抽取纯QueryPlan构造；不改变query_faq外部签名。

**Interfaces:** `QueryExpansion`仅queries:list[str]，2..3条，每条1..500字，extra=forbid；`expand_policy_queries(question, *, order, gateway, budget) -> list[str]`async，返回去重的原问题+扩写，最多4条，结构错误修复一次后退回[question]并记fallback。`retrieve_policy_queries(question, queries, *, plan, retrieve, rerank) -> RetrievalResult`sync；`retrieve_refund_knowledge(question, *, order, gateway, budget, retrieve) -> RetrievalResult`async。简单FAQ的`retrieve_knowledge`保留接口但使用已理解问题直接构造plan，无重复模型改写。

- [ ] Context7核对Milvus现有标量过滤/搜索接口及当前锁定版本；读取现有材料metadata确认政策类别。固定只检索“退货政策”和“售后手册”材料类别，使用既有参数绑定过滤，不拼接用户表达式；若实际ch04类别不同，先以manifest/source核对映射，不靠正文关键词过滤冒充材料限制。
- [ ] RED：原问题始终入列表；重复/空串/超长/多字段修复或降级；只在refund路径扩写；无第二次改写调用；相同chunk_id多路返回只留一次；raw dense/BM25分数不混比；一路失败整轮报错；重排/预算错误不伪装知识缺口。
- [ ] 对每个query、每个已核实政策类别调用现有retrieve_result：部署hybrid_rerank时候选阶段用hybrid避免先截Top10，其余策略按原配置；每路最多50候选，候选合并按Σ1/(60+rank)排序，chunk_id去重，稳定同分按chunk_id。共最多4查询×2类别，合并至最多50个候选，再对原独立问题统一rerank Top10（仅hybrid_rerank部署）。其他部署策略保持各自score_type，不伪造rerank分数；最终candidate_ids为全部合并候选并集，hits为最终排序结果。
- [ ] 继续使用MySQL Hit原文、existing rerank和证据闸；保留通用政策+商品品类过滤。每次外部调用受总deadline约束，线程迟到结果不写状态。最终引用统一编号不重复。
- [ ] 验证 `... -m pytest tests/test_kb_multi_query.py tests/test_workflow_knowledge.py tests/test_kb_tool_artifacts.py tests/test_kb_contracts.py -q` 到GREEN；记录任务完成。

### Task 5：确定性子图、订单选择续跑与恢复API

**Files:** 修改 `app/workflow/graph.py`、`nodes.py`、`contracts.py`、`runtime.py`、`agent.py`、`app/api/chat_actions.py`、`schemas.py`；新建 `app/workflow/continuation.py`、`tests/test_workflow_refund.py`、`tests/test_order_selection_api.py`；更新既有graph/chat/recovery测试。

**Interfaces:** State新增 confidence、understanding、order、selection、expansion_queries、expansion_fallback、continuation_of。selection包含id、request_id、intent、question、candidates、status(available/consumed/expired/interrupted)。节点 order_context→read_order→expand_queries→retrieve_policy→evidence_gate→agent_decide；缺ID从order_context（提交稳定selection）到await_order，调用interrupt(payload)暂停，不进入log_result。其他intent到other_reply固定范围提示。

`SelectOrderRequest`：session_id(1..128)、selection_id(1..64)、order_id(1..64)，extra=forbid。`POST /api/chat/actions/order-selection`返回SSE；`GET /api/chat/actions/state?session_id=...`返回经裁剪公开selection/actions，禁止返回历史全文、内部prompt或budget。`WorkflowRuntime.stream_order_selection(request, *, model)`为async iterator；`public_session_state(session_id)`async返回可恢复的公开元数据。

- [ ] Context7核对FastAPI StreamingResponse输入/错误响应及LangGraph aupdate_state/as_node/ainvoke(None)真实异步签名；避免会话锁递归获取死锁。
- [ ] RED：节点轨迹严格先order再expand/retrieve再Agent；无订单只输出卡片不查政策/回答资格；无证据不进Agent；退款进度亦先政策；awaiting_order不被log_result覆盖为completed；原话只归档一次。
- [ ] 实现同会话锁内验证最新StateSnapshot.interrupts、selection及候选→Command(resume=已验证选择,update=新budget/continuation关联)在原thread恢复await_order→校验interrupt返回值并提交consumed→进入read_order。不得用aupdate_state/as_node模拟恢复，不得执行START再次分类；tool调用计入次数、order数据作为系统提供数据进入Agent上下文，禁止Agent改订单或重复获取已加载证据。
- [ ] RED并实现：新消息使旧available过期；双击/并发只一条续跑（重复已消费返回409明确已处理，客户端恢复状态）；断线/失败标interrupted且不自动重放；重启恢复available；恢复时候选重新验证；输入别会话/伪造ID为404或409；schema错误422；字段不泄露。
- [ ] SSE保留旧帧，新增workflow(stage, 可选验收question)与order_selection(selection_id,items,is_demo=true)；done仅代表流结束，业务state仍准确。验收question默认不对生产日志输出；actions只正常终态启用，等待卡片在持久化成功后发布。
- [ ] 验证 `... -m pytest tests/test_workflow_refund.py tests/test_order_selection_api.py tests/test_workflow_graph.py tests/test_workflow_chat.py tests/test_workflow_recovery.py tests/test_workflow_runtime.py -q` 到GREEN；记录任务完成。

### Task 6：演示退款表单后端及确认幂等

**Files:** 新建 `app/workflow/refund_actions.py`、`tests/test_refund_actions.py`；修改 `actions.py`（仅共享必要写入状态机）、`nodes.py`、`runtime.py`、`app/api/chat_actions.py`、`app/schemas.py`；保留旧ticket接口行为。

**Interfaces:** `REFUND_REASONS`固定五项：不喜欢/不合适、尺码问题、商品质量问题、与描述不符、其他。`SubmitRefundRequest`仅session_id、action_id、reason(枚举)、note(str≤500,默认空)、confirmed:StrictBool=True且必须显式传入；extra=forbid。`confirm_refund(request, *, runtime, store, create_once=create_ticket_once) -> TicketActionResult`async。`POST /api/chat/actions/refund`返回含原工单号的结构结果。

- [ ] RED：未确认/取消无写入；原因非法、客户端额外order_id/conversation_id拒绝；从服务器action取订单及真实会话；重复返回同工单号；预写状态失败不建票；写入后状态保存失败进入uncertain且不重试；并发提交一写；超长note拒绝。
- [ ] 正常完成退款/售后分析后创建kind=refund_application action，绑定order_id与本轮证据关联；未获证据或中断不生成可执行表单。可提交申请不代表符合条件，Agent不可擅自执行此写操作。
- [ ] 复用现有create_ticket_once以ticket_type=售后构造description，包含“演示退款申请”、订单、合法原因及note，conversation_id系统取得。保持available→creating→completed/uncertain，与旧工单使用同一会话锁但不嵌套获取。
- [ ] 返回固定成功说明“演示申请已提交，待审核；未执行真实退款”，旧ticket按钮独立运行。验证 `... -m pytest tests/test_refund_actions.py tests/test_chat_actions.py -q` 到GREEN；记录任务完成。

### Task 7：聊天页订单卡片、阶段展示及退款表单（Vibe Coding）

**Files:** 修改 `app/static/index.html`，新建 `app/static/chat-refund.js`；必要时调整现有聊天脚本的SSE分发，不引入框架/组件库。

**Interfaces:** `renderOrderSelection(container, payload)`、`renderRefundAction(container, action)`及 `restoreChatActions(sessionId)`；共用同一SSE解析/流式展示入口处理/chat与点选续跑响应。原因列表由服务器state/actions公开元数据提供，与后端枚举同源。

- [ ] 直接实现聊天内卡片：商品/订单/状态/金额、演示标记，点击后自动POST，禁用重复点击，显示后续查订单/查政策/正文。收到新消息使旧卡片在UI过期，后端仍有最终校验。
- [ ] 直接实现简表单：只读订单、固定原因下拉、可选说明、确认/取消；不以聊天追问原因。成功显示工单号和演示待审核说明；错误/uncertain不显示成功或自动重试。textContent安全渲染模型/商品/用户字段。
- [ ] 页面重载同session恢复可用卡片/actions；未完成正文标记中断；点击selection得到409时刷新公开state并提示，不能重复开始退款流程。
- [ ] 使用浏览器工具实际检查正常点选、取消/确认、双击、失败、窄屏与恢复；按用户反馈迭代。前端不新增TDD或独立code-review门槛；后端接口验收仍必需。记录任务完成。

### Task 8：真实评估、后端终审、修复与交付

**Files:** 新建 `scripts/demo_ch06.py`、`reports/ch06/{summary,demo,review}.md`及真实评估JSON；更新 `docs/OPERATIONS.md`、`docs/AI_HANDOFF.md`、`docs/CH06_CURRENT_PROGRESS.md`、`dev-notes/ch06.md`；新增复现回归测试放入对应所属文件。

- [ ] 基于既有demo启动方式创建ch06入口，使用独立`log/ch06/checkpoints.sqlite`和本地8016端口，不停止共享8015或改.env。启动前校验本地DB目标、migration/profile identity，不仅从profile取阈值；若code_hash变化，按原固定集生成独立ch06校准文件，不覆盖旧冻结结果。
- [ ] 真实跑dev集后固定Prompt，再运行 `... scripts/eval_ch06.py --split acceptance --output reports/ch06/router-acceptance.json`；关键多轮全对、怪问题其他、总体准确率≥95%，分别报告原始JSON/修复/系统兜底；不达标如实修订并保留attempt，重大模型能力矛盾请用户决定。
- [ ] 选至少12条已标注核心政策问题对比原问题单查与多查询：实际chunk命中、期望条款、拒答、时延、usage与服务失败；总体不以候选数量增加宣称质量提升。校准/评估CLI具体参数先读取现有scripts，不凭空编造；不得改标签/语料迁就结果。
- [ ] 按选定执行方法完成后端独立终审：范围为本章后端新增差异及与ch05基线交互，排除用户豁免的前端code review。记录Critical/Important/Minor、覆盖范围；有问题先复现测试RED，再修复GREEN，不把自审冒称独立review。
- [ ] 运行最终 `... -m pytest -q` 并保存真实结果。新增修复后再测相关范围，有影响才重跑全量；服务问题与代码失败分开记录，不照搬637。
- [ ] 本地浏览器完整演示“这个能退吗”→无ID卡片→点选→先订单再政策→Agent；有历史对象的“它”先补全；再聊物流分类正确；表单取消0新增、确认新增1条合成售后工单、重复不增。核对目标环境和合成session后才进行真实写入，避免打印真实客户数据，不随意清理数据。
- [ ] 更新实际命令、数据/流程限制、模型/评估结果、review覆盖、已知问题、当前分支/工作树/启动与停止方式。同步根审批文档及恢复入口，检查diff与git status，无密钥/DDL/误改；运行verification-before-completion与finishing-a-development-branch，用户禁止提交/合并优先，保留工作区交付。finish阶段即追记四要素。

## 计划自审与审批状态

已按spec逐节映射：理解/分类Task2，订单Task3，检索Task4，图/续跑Task5，表单Task6，前端Task7，真实验证/评审/finish Task8；Task1为基线及评估数据，所有任务有明确接口和验证出口。五项Review Focus均有所属测试。更正了旧路径“退款走knowledge/售后走business”和旧无confidence Provider fixture的兼容点；不保留过时断言伪装回归。

本计划已完成主执行者自审，尚无独立评审，尚待用户批准并选择执行方法。推荐Native：状态、预算与续跑共享接口多，连续实施可减少上下文重复，最后安排独立后端评审；Subagent-driven也可选，每任务独立实现/评审，开销更高。实施前不自动派发子任务或修改产品。


## 用户补充裁决：Task5原生interrupt（2026-10-23）

覆盖Task5的应用层续跑方案，必须从langgraph.types导入interrupt和Command，沿用锁定版本及官方SQLite。order_context准备稳定selection后提交，await_order仅执行可重入校验/interrupt，不在interrupt前做随机ID/LLM/工具/业务写入。runtime以custom+updates读取真正__interrupt__，恢复以相同thread_id的Command(resume,update)，不伪造节点完成。暂停后的业务状态为awaiting_order，网络done不等于退款完成；原运行finally不得标为错误中断。

增加验证：真正pending interrupt存在、等待节点恢复从头执行但prepare/分类/模型/原消息不重放、同thread重启恢复、过期/重复/跨会话resume拒绝、旧工单确认不破坏pending interrupt、GraphInterrupt不被宽泛捕获误报。tests/test_langgraph_interrupt_contract.py为锁定版本表征测试已1passed；产品Task5仍未开始。前端/API协议维持已批设计，不额外设置审批门槛。


## 用户补充裁决：降级仅留配置种子（2026-10-23）

Task2增加intent_small_model: str|None=None、intent_escalation_threshold: float=0.7（0..1有限值），只作未来扩展占位，不增加开关或调用分支。即使设置小模型种子，当前仍仅主力大模型；当前低分归其他、原模型JSON格式修复与未来低分升级是不同机制。新增tests/test_intent_config_seeds.py验证显式种子默认及配置小模型后仍只调用主力一次。此用户裁决直接覆盖原文“显式启用”可能暗示本章已提供降级模式的表述。


## 最终实施状态（2026-10-23）

本计划此前已获用户批准并选Native；Tasks1–8实际完成，700项全量通过、真实评估与浏览器证据见产品reports/ch06/summary.md。所有早期“待批准/未开始”句子保留为历史。独立后端review0Critical/3Important/0Minor，三项RED→GREEN；补充启动绑定/统计由主执行者验证，前端按Vibe例外语法+浏览器。评估有限等价及校准scope裁决见spec新末节；不改冻结标签或隐去失败attempt。finishing按用户规则保留分支/工作树/账本，不提交、推送、合并或部署。
