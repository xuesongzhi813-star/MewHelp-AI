# ch07 Context Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 仅在当前会话实现可观测的三层上下文、追加式异步摘要和可回载原文的多会话页面。

**Architecture:** 扩展现有LangGraph；完整messages/checkpoint与模型视图分离，以真实DB消息id划分层1/层2/摘要。后台每会话单飞，仅原子提交MySQL摘要和覆盖锚点，下一轮锁内同步进State；Agent、coref和意图识别共用上下文来源及统一计量。

**Tech Stack:** Python3.12、现有uv/.venv、FastAPI0.143.0、SQLAlchemy2.1.4/MySQL、LangGraph1.2.15/add_messages/AsyncSqliteSaver、langchain-core1.6.10/trim_messages、现有静态HTML/JS；Milvus与检索基础设施保持。

**Spec:** `docs/superpowers/specs/2026-10-23-mewhelp-ch07-context-design.md`（用户2026-10-23回复「批准」）。

**Status:** 2026-10-25 Native Task0–11 finish；独立review两项Important均RED→GREEN修复，最终全量758 passed in35.19s，真实默认/demo各2×21及浏览器通过。保留工作树和账本，无提交/推送/合并/部署。完整证据见reports/ch07/summary.md及rulings.md。

## Global Constraints

- 产品根为 `E:/MewHelp AI/.worktrees/ch05-workflow-agent`，当前branch `ch06-router`；主根 `E:/MewHelp AI` 仅同步本章文档。所有下文相对路径均相对产品根。
- 保留原6个源码/语料换行差异及既有未提交文档；不提交、推送、合并、部署、清理工作树或专属账本。不为技能默认commit步骤申请额外授权。
- 既定栈不可替换、不升级依赖、不加服务；MySQL表已手工创建，仅映射，不加create_all/DDL/迁移脚本，不清库或重建Milvus。
- 最近原文不动；用户原话不截短；层2客服仅前60个Unicode字符；摘要几十至200字、旧段不改写；容量不足明确失败而不丢原文。
- State.messages继续add_messages；保留完整checkpoint，不能把精简视图回写messages；新工具结果/纯工具调用AI消息不写MySQL messages。
- 唯一固定Agent system及固定只读工具+respond_to_user schema；可变摘要、订单、证据/指代实体不放system。
- 默认W128000、目标40轮×稳态2000；演示六变量及5650/3954/1695口径见Spec§8，实际计量超过保底则扣实际，不改计数凑值。
- 三处注入摘要；每轮history_ctx、每次Agent调用model_ctx写log/app.log；后台触发/开始/完成/跳过/失败都含边界和耗时。
- 会话按同浏览器localStorage匿名user_id归组，仅作演示隔离，不引入认证、跨会话记忆或用户画像。
- 库/框架/SDK/CLI用法先Context7并核对锁定版本；代码用有判别力TDD，Prompt/数据用冻结标注样例/真实模型评估替代TDD。
- 每任务完成立即追记dev-notes/ch07.md四要素及真实验证；额度任一适用窗口剩余不足10%且无法完成时，先更新进度/AI_HANDOFF/留痕，未完成不finish。

## Review Focus

1. 摘要在新一轮进行中提交：不得回退layer1边界、覆盖当前State或把尚未摘要消息当作已摘要（Tasks2/4/5竞争与重启测试）。
2. 全部摘要变长/工具artifact极大：按实际模型视图计量，artifact不发模型，预算不足明确失败，层1原文不可被静默截断（Tasks1/3/6）。
3. 同文消息、DB id间隙和归档后未checkpoint就崩溃：依靠轮起点及唯一终答槽恢复，不错位、重复插入或减1推边界（Tasks2/5）。
4. 最早订单已在摘要且后面又聊过别单：「最开始」可核验，「它」有歧义仍追问；不同会话的source_id不能串用（Task6及Task10标注集）。
5. 切换会话时旧SSE迟到/侧栏API故障/原生interrupt待续跑：界面不串线，基本聊天可用，旧选择/工单仍按原会话幂等（Tasks7/9/10）。

## 执行约定、文件边界与配置决策

采用一个计划：预算、视图、存储、后台摘要和界面共享身份与锚点，拆成独立项目会留下不可验收接口。职责如下：
- 新建 `app/context/__init__.py`、`contracts.py`：纯数据契约，不放连接、模型、锁。
- 新建 `tokens.py`：实际模型消息估算；`budget.py`：窗口/历史/分层预算与自检。
- 新建 `identity.py`：临时与canonical身份；`store.py`：现有表的会话/原文/锚点/摘要仓储。
- 新建 `layers.py`：纯三层视图；`prompts.py`与`summary.py`：摘要/覆盖核验和后台管理器。
- 新建 `assembly.py`：三处模型装配及来源核验；`logging.py`：实际上下文与生命周期日志。
- 既有 `app/workflow/*` 只接线和保持编排/权限，MySQL ORM只在现有models.py扩展。
- 新建 `app/api/conversations.py`、`app/static/chat-conversations.js`、`scripts/demo_ch07.py`、`scripts/eval_ch07.py`；不重构无关检索模块。

新增Settings字段（snake_case自动对应大写环境变量）：
`model_context_window=128000`、`max_output_tokens=4000`、`max_user_input_tokens=4000`、`max_agent_steps=8`（ge2，含最终回答）、`tool_result_max_tokens=2400`、`rerank_top_k=10`（仅本章注入Top-K，不修改检索器底层默认）。
`context_target_turns=40`、`context_steady_turn_tokens=2000`、`context_system_reserve_tokens=2000`、`context_evidence_tokens_per_hit=300`、`context_summary_reserve_tokens=1000`、`context_protocol_reserve_tokens=400`、`context_safety_ratio=0.025`、`agent_decision_max_tokens=300`、`context_cjk_tokens_per_char=1.0`；所有容量正数、ratio有限且[0,1)、中文系数有限正数。

兼容：HISTORY_TOKEN_BUDGET保留给旧非workflow路径；AGENT_MAX_TOOL_CALLS和AGENT_MAX_DECISIONS仍为额外上限，实际取与新步骤限额较小者；AGENT_TURN_TIMEOUT保留；INTENT_MIN_CONFIDENCE与严格JSON不改。workflow默认新的 `context_turn_token_budget: int|None=None`：None时累计上限为 `(max_agent_steps+6)*model_context_window`（最多两次理解+两次分类+两次扩写及Agent步骤），不是窗口额度；显式设置该新值则严格使用。旧AGENT_TOKEN_BUDGET仍用于旧教学/非workflow loop，启动日志说明新路径实际累计上限，防止暗中突破显式新限制。所有调用仍先reserve后settle；新路径禁止额外隐式query rewrite。后台摘要另立有界调用预算，不占用户当轮累计额度。

自检与每次调用guard均使用统一计量；演示令W18000/O2000/用户2000/steps3/tool1200/Top-K5，其余上述分项不变。E总额1500容纳附注/订单/证据标签；摘要A取max(1000,实际全部摘要)。不要把整段standalone_question重复注入Agent。

测试从产品根执行：`& 'E:/MewHelp AI/.venv/Scripts/python.exe' -m pytest <测试文件> -q`。不运行uv sync升级/重建共享环境；必要时先确认现有.venv与uv.lock一致。需真实MySQL的测试复用外层事务+SAVEPOINT fixture，服务不可达记录原因，不能改用建表掩盖。

### Task 0：执行隔离与基线

**Files:** 仅阶段记录/基线清单，不改产品。
**Interfaces:** 产出本轮base SHA、初始status与原有差异哈希，供后续review在未提交工作区上隔离本章变更。
- [x] 使用using-git-worktrees技能，在产品根核对git-dir/common-dir、superproject、branch。现有linked worktree直接复用，不从主根ch04新建；保持现分支，不自动commit/切分支。
- [x] 确认当前实际源码、已批准Spec、计划、根配置定位；记录关键文件hash与未提交基线。只读检查目标MySQL/Milvus，本轮不停止共享8015/8016。
- [x] 实跑一次基线全量 `& 'E:/MewHelp AI/.venv/Scripts/python.exe' -m pytest -q`，记录结果。失败先定位并记录；不能沿用ch06的700 passed或擅自修无关缺陷。
- [x] 立即追加Task0留痕及进度，不能将基线完成算功能实现。

### Task 1：统一token口径与派生预算

**Files:** Create context/contracts.py、tokens.py、budget.py、__init__.py；Modify app/config.py、app/history.py、app/workflow/budget.py、.env.example；Test tests/test_context_budget.py、test_context_tokens.py，回归test_history.py/test_workflow_budget.py。
**Interfaces:** `ContextBudget` frozen数据含window/system/evidence/summary/output/safety/peak/history/layer1/layer2；`estimate_text(text:str)->int`、`count_model_tokens(messages:list[BaseMessage], *, schemas:Sequence[dict]=())->int`；`derive_budget(settings, *, system_tokens:int, summary_tokens:int)->ContextBudget`；`ensure_fits(messages, *, schemas, output_cap:int, settings)->int`（返回input估算或ContextBudgetError）；`validate_startup(settings, *, schemas)->ContextBudget`。ContextBudgetError的公开文案包含「上下文预算不足」，不得被泛化成无关重排错误。
- [x] RED写 `test_demo_budget_is_5650_3954_1695`，断言6个扣除分项为2000/1500/1000/2000/850/5000，H5650/L13954/L21695/拼接余量1；`test_system_and_summary_actual_can_shrink_history`；`test_no_single_exchange_startup_rejected`，以及H=0/1时各层预算不为负。
- [x] RED补 `test_tool_args_count_but_artifact_not_sent`、`test_unicode_mixed_estimate_and_fractional_cjk_rounding`、`test_actual_payload_cannot_exceed_window`，以及显式累计cap12000耗尽时仍拒绝，不能自动抬高显式context_turn_token_budget。
- [x] 运行上述新文件，确认因缺实现/行为断言失败；Context7核对LangChain公开的模型消息转换/trim计数接口后实现，不使用ChatOpenAI私有转换函数。provider视图只计真实发送role/content/tool_calls/tool_call_id/name等字段，不把完整artifact或内部id映射算进请求。
- [x] 委托history.estimate_tokens到同口径，trim_history旧接口保留；配置新增字段与累计额度兼容规则写入.env.example占位说明，真实.env不动。
- [x] 跑新文件及两个回归文件到GREEN；记录真实RED/GREEN与预算分项，不将算术测试当真实校准。追记Task1。

### Task 2：真实DB id、身份与追加式仓储

**Files:** Create context/identity.py、store.py；Modify app/models.py、app/history.py、app/session_store.py；Test test_context_store.py、test_conversation_identity.py；回归test_session_store.py。
**Interfaces:** contracts增加 `ConversationIdentity(user_id:str, conversation_id:int, session_id:str, legacy:bool)`、`StoredMessage(id:int,conversation_id:int,role:str,content:str,turn_from_msg_id:int)`、`SummaryRow(id,conversation_id,seq,from_msg_id,upto_msg_id,content)`、`ContextSnapshot(conversation_id,summary_upto_msg_id,layer1_from_msg_id,summaries)`（均可序列化数据）。
identity产出 `canonical_session_id(user_id:str, conversation_id:int)->str`、`parse_canonical(session_id:str)->ConversationIdentity|None`，新user_id严格32位hex。
`ConversationStore`包装现有MySQL会话层：`create_conversation(user_id)->ConversationIdentity`、`get_owned(conversation_id,user_id)->ConversationIdentity`、`read_context(conversation_id)->ContextSnapshot`、`read_messages(conversation_id, *, from_id=None, upto_id=None)->list[StoredMessage]`、`append_user(conversation_id,text)->StoredMessage`、`append_final(conversation_id,turn_from_msg_id,text)->StoredMessage`、`advance_layer1(conversation_id,from_msg_id)->None`、`append_summary(conversation_id, *, expected_upto, from_msg_id, upto_msg_id, content)->SummaryRow|None`。None代表竞争后skip，不代表成功。另产出list_conversations(user_id)->list[dict]。InMemory替身提供相同新接口；旧append_message/get_history签名继续兼容旧测试。
- [x] RED断言existing表映射、摘要段seq及CAS原子更新、旧summary列不动、DB id有间隙时正确闭区间、layer1只前进不回退；事务失败不得留下摘要已插入但锚点未更新。
- [x] RED写 `test_new_archive_skips_tool_and_pure_tool_call`、`test_recover_final_slot_after_db_commit_before_checkpoint`：新工作流只写user/可见终答；同轮终答重复请求返回同一DB行，冲突/多槽明确报错；保留旧tool行可读。
- [x] RED验证canonical不因status变化创建新会话；跨用户/不一致id拒绝；读未知id不创建；相同文本的两轮按真实用户起点分开；多会话列表倒序、首问预览80字符、has_summary使用成功摘要EXISTS。
- [x] Context7核对当前Session.begin/select.with_for_update/flush（已取得官方文档仍对照本机签名）；只映射既有列与表，不让测试create_all。按conversation行锁分配seq/比较expected_upto，在一个事务插摘要+更新锚点；明确同进程单飞而不声称多进程支持。
- [x] 跑三文件到GREEN，测试事务回滚；记录接口与实际表核对结果，追记Task2。

### Task 3：完整轮划界和纯三层视图

**Files:** Create context/layers.py；Extend context/contracts.py；Test test_context_layers.py。
**Interfaces:** `MessageRef(db_id:int|None,turn_from_msg_id:int)`；`HistoryView(conversation_id:int,summaries:tuple[SummaryRow,...],messages:list[BaseMessage],text:str,tokens:int)`；`LayeredView(layer1:list,layer2:list,current:list,layer1_from_msg_id:int|None,layer2_tokens:int,history:HistoryView)`。
`build_layered_view(messages:list[BaseMessage], *, refs:dict[str,MessageRef], snapshot:ContextSnapshot, current_turn_from_msg_id:int, budget:ContextBudget)->LayeredView`，无I/O、无State写入。产出 `summary_batch(view, *, messages,refs,snapshot)->SummaryBatch|None`；SummaryBatch带conversation/expected_upto/from/upto/captured_layer1_from/messages/old_summaries/config_identity。
- [x] RED验证 `test_layer1_is_byte_identical_and_state_unchanged`、`test_layer2_preserves_user_and_first_60_assistant_chars`、`test_layer2_keeps_complete_tool_pairs_with_marker`：messages表行数相同时，checkpoint工具对增加仍会涨层1占用并引发降级；层2只计其实际标识，不把已替换正文/完整artifact重复算入。
- [x] RED覆盖超长单轮、尾部孤悬工具、中间不完整轮、空历史、非连续DB id、当前轮永不降级；已经降级的轮不因预算变大重新晋升。先前中断/被新输入终止的轮仍保留其用户原话，仅模型视图过滤不完整工具对，不伪造客服终答；等待interrupt的当前轮不进入摘要。
- [x] 使用trim_messages(strategy=last, allow_partial=False,自定义token_counter)选后缀，再向后对齐完整轮与配对。超预算不截断用户/层1消息；层2copy上截客服/替换工具正文，内部artifact保留原对象归档但不进入模型序列化。
- [x] 生成按seq排序的summary来源行与按时间顺序滑窗文本；不改写State.messages；模型视图超实际窗口由Task1 guard负责明确报错。
- [x] 运行该文件及test_history.py/test_workflow_recovery.py相关配对用例到GREEN；追记Task3。

### Task 4：摘要Prompt、保真核验与后台单飞

**Files:** Create context/prompts.py、summary.py、docs/samples/ch07/summary-dev.jsonl、summary-acceptance.jsonl；Test test_context_summary.py（调度/事务集成的代码行为）；评估脚本由Task10整合。
**Interfaces:** `SummaryCandidate(content:str)`、`CoverageCheck(grounded:bool,complete:bool,missing:list[str])`；`Summarizer.summarize(batch:SummaryBatch)->SummaryCandidate`、`verify(batch,candidate)->CoverageCheck`均async。独立生成/覆盖核验调用有各自窗口/usage/timeout。
`SummaryManager(store,summarizer, *, settings)`，`maybe_schedule(batch:SummaryBatch, *, layer2_tokens:int,layer2_budget:int)->bool`同步快速返回是否创建Task；`aclose()->None` async取消并await收尾；强引用任务按conversation_id保存，后台不拿聊天锁、不操作checkpointer。
- [x] RED用asyncio.Event控制摘要停住，断言调度立即返回、同会话第二任务skip、不同会话可并发；完成后原子提交；失败/取消不推进锚点，不泄漏任务异常。
- [x] RED验证快照边界不吸收新轮、竞争CAS skip、任务执行期间layer1变大仍不回退、子批提交后重启继续下一段。
- [x] Prompt/数据不用镜像单测：建立合成dev12条/acceptance18条，包含订单、手机号、SKU、明确诉求、否定/时间、未解决问题、寒暄及恶意指令；每条标expected_facts/forbidden_claims。冻结acceptance SHA后仅用dev调Prompt，保留真实输入输出、usage、服务错误与失败attempt。
- [x] 实现实体双向覆盖校验与独立语义覆盖核验；最多一次重试，再按完整轮拆批。单轮事实超过200字或输入窗口不足明确fact_capacity/input_capacity失败，不丢事实推进。旧摘要只作背景；新子批不把旧/刚生成摘要纳入待总结正文。
- [x] 后台模型设置沿用主力配置/既有构建器，不新增供应商；每子批最多生成2次+覆盖核验2次，各次窗口受guard；Task10先真实dev再真实acceptance。调度代码先跑test_context_summary.py/test_context_store.py到GREEN，Prompt质量待评估，不冒称本任务测试证明质量。追记Task4。

### Task 5：运行时贯穿、归档恢复与生命周期

**Files:** Modify workflow/contracts.py、runtime.py、graph.py、nodes.py、main.py；Test test_context_runtime.py；回归test_workflow_runtime.py/test_workflow_recovery.py/test_workflow_chat.py。
**Interfaces:** State增加 `message_refs`、`summary_upto_msg_id`、`layer1_from_msg_id`、`summary_rows`、`current_turn_from_msg_id`、`history_context`、`context_budget`，全部纯数据；messages reducer不变。
runtime新增 `resolve_identity(session_id, *, user_id=None,conversation_id=None)->ConversationIdentity` async（临时key单飞/缓存）；`stream_turn(session_id,question, *, model,user_id=None,conversation_id=None)`保持旧调用兼容；构造器接收可注入context_store/summary_manager。
- [x] RED验证用户先DB再入图，HumanMessage稳定db:<id>，终答assistant:<user_db_id>；所有内部消息由节点包装器/运行时关联当前轮，不修改内容；模型裁剪后checkpoint仍完整。
- [x] RED在graph持久化完成→final落DB→映射checkpoint各边界注入故障并重启，恢复唯一终答槽，不自动重放业务工具、不重复原话；DB有终答而metadata未checkpoint时可补映射。
- [x] RED验证后台提交时前台可继续产token/done，下一轮读取MySQL新锚点而不丢State新消息；等待原生interrupt时不得用aupdate_state(as_node=log_result)破坏待执行节点。订单续跑用Command.update同步上下文元数据并沿用原用户轮，不新插用户话。
- [x] lifespan创建管理器并调用Task1启动预算自检，退出await manager.aclose；Task8再接统一日志配置。本轮摘要失败不作为用户本轮错误；对ContextBudgetError发明确SSE error文本。仅在会话锁内正常终态更新metadata；后台绝不aupdate_state。
- [x] 旧checkpoint缺DB映射时仅按同会话有序DB行和角色/内容/工具签名对齐；无法对齐明确报错不猜，不能仅按数组索引当DB id。新checkpoint丢失可从MySQL回载可见原文，说明工具轨迹不可恢复。
- [x] 运行新文件及三个回归文件到GREEN，包含默认21轮状态长度只增长的fake模型回归；追记Task5。

### Task 6：固定前缀、三处装配与摘要来源核验

**Files:** Create context/assembly.py；Modify workflow/nodes.py、models.py、agent.py、contracts.py、routing.py、understanding.py、orders.py、router_prompts.py；Test test_context_assembly.py、test_context_references.py；回归test_workflow_understanding.py/test_workflow_routing.py/test_workflow_models.py/test_workflow_refund.py/test_agent_loop.py。
**Interfaces:** `build_agent_context(state, *, settings)->list[BaseMessage]`；`build_history_context(view:LayeredView)->HistoryView`；`fixed_agent_schemas()->list[dict]`。
`understand_question(question, *, history,gateway,budget,context:HistoryView|None=None,source_lookup=None)`，source_lookup为async(from_id,upto_id)->list[StoredMessage]，绑定当前会话；`classify_intent(question, *, gateway,budget,context:HistoryView|None=None)`。`validate_summary_reference(order_id,source_id, *, question,context,source_rows)->bool`纯校验；原无context路径兼容旧评估。
- [x] RED断言顺序唯一System→层2→层1→当前原话→一条摘要/附注/订单/证据Human→当前轨迹；改变摘要/检索/退款路径时system字节和schema hash恒定，answer也绑定同一schema并tool_choice=none。
- [x] RED验证preloaded_order调用/结果整对只在Agent当前视图排除，完整State保留；普通工具只执行一次且结果content限TOOL_RESULT_MAX_TOKENS、完整raw进入artifact；最终回答不能调用工具/绕过确认。
- [x] RED验证已摘要最早1001、后续1002：「最开始那个订单」1001可通过原范围核验；无明确指代的「它」仍保守；跨会话/未注入summary来源/编造订单拒绝；当前显式订单优先。完整改写不重复注入Agent，E包含全部附注标签。
- [x] 实现理解与分类共享同一HistoryView.text；格式修复提示转非system，严格JSON/置信度/原话保真仍保持。摘要来源需要回查覆盖范围，只查当前会话；不做历史向量检索。
- [x] BudgetState新增contextual:bool=False；agent.new_budget(*, contextual:bool=False)->BudgetState，runtime使用True应用新累计cap/步骤/输出上限，旧教学loop默认False保留原512决策/2048回答及agent_token_budget。新workflow调用上限接为max_agent_steps-1次决策+一次回答，额外旧上限取min；最后一次普通工具结果后直接收敛，即使control为None也不崩；缺参数仍明确追问，不虚构动作。
- [x] 新workflow预算按Task1新累计cap；分类/扩写计费与deadline不变。读取/渲染证据限定本轮E，只取rerank_top_k个有序命中，可提供有界摘录及原文引用；发给页面的引用编号与实际注入证据一致，不展示本轮未注入的额外引用，不在层1上剪。raw检索结果仍保留State，/kb继续按原chunk_id读取MySQL权威原文。
- [x] 新文件和列出的回归到GREEN；Prompt变化另由Task10真实ch06固定链+ch07集评估，不靠单测宣称语义保真。追记Task6。

### Task 7：多会话请求、只读API与动作关联

**Files:** Create api/conversations.py；Modify schemas.py、main.py、chat_service.py、api/chat_actions.py、workflow/actions.py；Test test_conversations_api.py、test_conversation_chat.py；回归test_chat_api.py/test_chat_actions.py/test_order_selection_api.py/test_refund_actions.py。
**Interfaces:** ChatRequest新增可选user_id/conversation_id（默认None）；GET列表返回对象items:list，每项字段id:int、session_id:str、preview:str、status:str、created_at/updated_at:ISO时间字符串、has_summary:bool；GET消息返回conversation_id:int、session_id:str、messages:list，每项id:int、role:user|assistant、content:str、created_at:ISO时间字符串。仅user/可见assistant，原文不截短。
新协议SSE首帧 `{"type":"conversation","user_id":...,"conversation_id":...,"session_id":...}`，旧请求不新增该帧以保留旧精确测试。未知/不归属404、格式不合法422、服务不可用503；不回传原始DB异常。
- [x] Context7已核对FastAPI Query/HTTPException，实施时确认当前Pydantic optional默认与StreamingResponse依赖，不安装新包。
- [x] RED验证同用户两会话列表全量/倒序/首问/摘要标记，取原文排序；GET未知用户/会话无INSERT/UPDATE；按user A只返回A数据，user A与conversation B组合404；合法canonical与显式字段不一致拒绝。此处是匿名归组过滤，不宣称客户端user_id具有认证能力。
- [x] RED用并发首问证明同进程临时key只建一个会话，首元数据后canonical用于所有聊天/动作/恢复；列表读和动作GET不调用get_or_create；旧session_id请求仍按旧路径工作。
- [x] 动作/订单续跑共用identity验证；confirm_ticket把现有get_or_create改为canonical只读定位（legacy仍兼容），确认时系统conversation_id不可由客户端覆盖。确认不确定仍不重试，不新增写操作。
- [x] 运行新文件及四个回归文件到GREEN，SSE token/tool/citations/actions/error/done格式不变，新增元数据不破坏客户端旧帧。追记Task7。

### Task 8：实际上下文日志与调用边界审计

**Files:** Create context/logging.py；Modify main.py、workflow/models.py、nodes.py、summary.py；Test test_context_logging.py。
**Interfaces:** `configure_context_logging(path:Path)->None`（同绝对路径只一个UTF-8 handler）；`log_history_context(session_id:str,conversation_id:int,request_id:str, *, context:HistoryView,phase:str,payload:list[BaseMessage]|None=None)->None`；`log_model_context(session_id:str,conversation_id:int,request_id:str, *, messages:list[BaseMessage],schemas:Sequence[dict],budget:ContextBudget,phase:str)->None`；summary生命周期采用同app logger。
- [x] RED用tmp_path+捕获fake模型：log_model_ctx反序列化后与fake收到的模型视图逐字段一致，含summary全文/滑窗/当前轨迹/schema hash/tokens；每次决策与最终回答都记录，不只首轮。
- [x] RED验证闲聊/投诉/其他无Agent仍有history_ctx；coref和intent收到同一summary+window文本；JSON修复调用也记录实际payload；重复app lifespan只一个handler。
- [x] RED验证trigger/start/done/skip/fail均包含conversation/request、from/upto/captured layer1、seq（适用时）、elapsed_ms、tokens/预算；异常只留类名/可公开原因，不输出连接串/key。
- [x] 记录在真正调用前的边界而非构造前；artifact不作为已发payload打印，原始用户/摘要/消息内容依用户要求本地完整记录。新log路径仍gitignored；本轮无日志轮转/脱敏服务等扩展需求。
- [x] 跑新文件及test_context_summary.py到GREEN；追记Task8。

### Task 9：会话侧栏与原文回载

**Files:** Create static/chat-conversations.js；Modify static/index.html、chat-actions.js、chat-refund.js（仅必要接线）；浏览器脚本/证据写reports/ch07/browser-*，不增前端框架或项目依赖。
**Interfaces:** `window.MewHelpConversations` 提供 `init({onSelect,onNew})`、`applyIdentity(event)`、`refresh()`、`currentRequestFields()`；所有状态变更由index的viewVersion/AbortController隔离。localStorage键mewhelp-user-id（32hex）；sessionStorage保留当前canonical id与已有旧session键。
- [x] 实现侧栏倒序列表/首问预览/已摘要标记；新对话只清当前视图及临时key，旧列表保留；首conversation帧更新canonical，后续请求与动作都用该会话。
- [x] 点击切换先取原文成功再更换窗口，恢复公开动作/订单选择；旧流取消+viewVersion挡迟到帧，组件共享当前会话getter，不缓存上一个session闭包。
- [x] 侧栏加载失败静默，已有聊天不清空；消息回载失败保留原视图；旧sessionStorage会话仅按可证明归属保留，不批量收编旧user_id数据。
- [x] 浏览器用合成后端/真实前端验证两会话、刷新、新标签共用user、回载完整长答、续聊、切换中的迟到帧、一次GET500；390px无横向溢出，桌面可用。文本用textContent并用含HTML字样的合成原话验证不执行。
- [x] 对新增与修改JS跑现有Node语法检查；记录浏览器实际工具/runner及限制，不写只查HTML字符串的镜像测试。真实全链由Task10另验，追记Task9。

### Task 10：冻结评估、演示profile与真实21轮验收

**Files:** Create scripts/demo_ch07.py、eval_ch07.py、docs/samples/ch07/dialogue-dev.jsonl、dialogue-acceptance.jsonl、token-calibration.jsonl；Test test_eval_ch07.py、test_ch07_demo_profile.py；Report reports/ch07/{summary-eval,token-calibration,default-dialogue,demo-dialogue,browser-e2e,summary-timeline}.json及demo.md。
**Interfaces:** CLI `demo_ch07.py budget|serve|calibrate|evaluate`，serve接受--profile default|demo、--port默认8018；budget只读输出分项。calibrate分检索profile与token估算报告，旧ch06 profile/身份不重绑；evaluate调用eval_ch07.py，支持--split dev|acceptance、--profile default|demo、--output PATH、--provider real|mock。真实模式才能给质量结论。
- [x] RED测评估器：缺任一订单/手机号/关键诉求、出现禁编造结果、服务失败、不满足全轮日志、默认发生任何降级/摘要均passed=false；mock只证明统计器。启动器不修改.env、旧profile、旧checkpoint，非本地DB停止。
- [x] 冻结对话dev至少2组、acceptance至少2组，每组至少21轮；首轮引入合成订单1001及明确未解决诉求，后续穿插商品/另一订单/礼貌闲聊，末轮问最开始订单及处理情况。设同一标注事实，两种profile复跑；记录固定数据SHA。合成手机号使用占位标签或明确合成值，不写真实个人信息。
- [x] 新启动器沿用根.env加载及ch04知识库/严格校准校验模式，新增独立ch07检索profile包含本章检索/理解管线hash；使用原冻结检索calibration重新生成，不覆盖旧profile或冻结语料。token校准样例至少12条混合中英/工具args/schema，记录estimate/input_tokens及误差，若改中文系数同时重算预算分项并复验。
- [x] 先真实summary-dev、dialogue-dev及ch06原75条关键理解/意图回归，失败修Prompt/逻辑留attempt，不改acceptance标签；再跑summary-acceptance及两种21轮，准确率/事实覆盖/拒答/服务错误分别报，不以HTTP200替代答对。
- [x] 默认21轮必须无降级/摘要；演示出现层1降级→层2超预算trigger→新增第N段，至少覆盖最早订单；等待本会话后台完成后再问最早订单，记录等待时间但后台等待不属于此前用户回复时延。若真实固定前缀超过S导致H低于参考，公开分项/差异，按Spec守真实窗口，不能假写5650。
- [x] 受控Event测试证实用户done早于summary done，再真实记录trigger/start/user首token/user done/summary done时间线；summary快于回复也可正常，但不能仅凭create_task声称无阻塞。grep日志逐轮核对三处摘要和模型实际上下文。
- [x] 真实浏览器8018只用合成会话验证双会话回载/刷新/续聊/摘要标记/侧栏故障；原生订单等待跨重启续跑及确认幂等优先用隔离替身回归，真实写工单若不是本章必要不再额外创建；不停止旧8015/8016，不清旧数据。
- [x] 输出实际命令、配置分项、所有真实/跳过/失败结果与限制。追记Task10，未满足条件不可finish。

### Task 11：独立代码评审、修复与finish交付

**Files:** Report reports/ch07/review.md、summary.md、demo.md；Update dev-notes/ch07.md、docs/CH07_CURRENT_PROGRESS.md、docs/AI_HANDOFF.md、docs/OPERATIONS.md及Spec/Plan状态；根同步文档。
**Interfaces:** 消费Task0基线清单与所有任务产出；产出可复现的最终命令/测试成绩/限制。没有提交时review以基线hash+当前工作区diff、新文件和批准Spec/Plan为准，不能只读HEAD比较漏掉实现。
- [x] 使用requesting-code-review技能派独立reviewer；Native执行则实现结束一次整章review，SDD则按其任务门禁再做整章review。审阅竞态、预算和实际payload、跨会话来源、恢复、API只读/无tool落库、原生interrupt/确认不回归；不把Prompt单测当质量证据。
- [x] 用receiving-code-review处理结论；Critical/Important先RED复现后修复再GREEN，Minor逐项说明；新变更涉及Prompt/计量时复跑相关冻结评估，不泛跑所有真实调用。
- [x] 运行必要完整pytest一次并记录本次真实数/skip/耗时；独立检查报告JSON、JS语法/真实浏览器、diff --check、无敏感内容；所有验收项逐条对照，未验证明确标记。
- [x] 按finishing-a-development-branch保留现有工作树，不提交/推送/合并/部署，不清专属账本。同步spec/plan最终Ruling、统一运维启动/停止/故障/备份入口、真实演示命令。
- [x] 立即写code review结论与finish两段留痕；若额度不足先交接真实任务状态和下一条命令，不标finish。最终回复给功能演示命令、测试结果、dev-notes路径及剩余问题。

## 计划自审与执行交接

覆盖映射：Spec§1–3→Tasks0/2/5/7；§4→1/3；§5→2/4/5；§6–7→6；§8→1/6/10；§9→8；§10→7/9；§11→所有任务及10/11；§12→各任务Context7核对。Review Focus五项各有对应RED/浏览器/真实评估步骤。

接口方向：contracts/tokens/budget → identity/store及layers → summary/runtime → assembly/API/logging → UI/eval → review/finish。assembly不反向依赖runtime；summary不调用checkpointer；store不调用LLM。统一使用summary_upto_msg_id/layer1_from_msg_id及StoredMessage真实id，不出现“按索引即DB id”的替代名称。

执行方式已选择Native：用户原话「Native执行」。由同一执行者逐任务实现、TDD/标注评估验证，完成后独立整章review；不改为SDD，不逐任务重复询问是否继续。

用户已审阅并选择Native，Execution Handoff审批已满足。当前遵从用户主动暂停；明确恢复后从Task2继续，不再重复审批。
