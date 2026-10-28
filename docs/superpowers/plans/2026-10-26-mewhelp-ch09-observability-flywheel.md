> 2026-10-27执行完成：真实证据与边界见reports/ch09/summary.md、dev-notes/ch09阶段27–28；模板中的等待审批/未执行均为历史。

# ch09 Observability and Knowledge Flywheel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在现有客服上完成日本区Langfuse可观测性、三入口问题池、自动查重审核发布及版本化评估闭环。

**Architecture:** 回调为主、自定义Span补足；统一轮次身份贯穿Trace、checkpoint、反馈和问题池。MySQL已有表承载问题及评估结果，单后台消费者处理，私有不可变文件保存标注/profile/发布元数据，知识发布经双写与24题校准后原子生效。

**Tech Stack:** Python 3.12，既有FastAPI/LangChain/LangGraph 1.2.15/SQLAlchemy/MySQL/Milvus/SQLite checkpoint，新增官方Langfuse Python SDK及CallbackHandler，原生HTML/CSS/JavaScript。

**Spec:** [已批准设计](../specs/2026-10-26-mewhelp-ch09-observability-flywheel-design.md)。2026-10-26用户原话「批准」，随后选择Native执行。本计划任务1–13均已完成；任务步骤保留实施时的指令与验证要求，实际结果以dev-notes/ch09.md和reports/ch09/summary.md为准。

## Global Constraints

- 唯一产品目录`E:/MewHelp AI/.worktrees/ch05-workflow-agent`，现有分支ch07-context；先记录Git状态/已有改动hash，保留ch08成果。执行时读using-git-worktrees确认复用现有隔离工作树，不从旧HEAD新建而丢失未提交内容。
- 根目录共享`.venv`、私有`.env`、Docker及阅读文档。所有相对路径以产品目录为准，根仅同步文档；无授权不提交/推送/合并/部署，也不执行计划模板中的自动commit。
- 固定Langfuse Cloud Hobby日本站`https://jp.cloud.langfuse.com`，不自托管/替换栈。只新增兼容SDK及必需传递依赖；不升级原锁包。遇版本冲突停下请用户决定。
- 只映射已建low_confidence_questions/review_queue/eval_runs；禁止DDL/create_all/清库。真实写入只用核对过的本地目标；MySQL测试沿用外层事务+SAVEPOINT。
- 密钥只在后端私有配置，`.env.example`只占位符；不输出真实.env/客户对话/连接串。沿用现有配置装载，不在本计划阶段改真实.env。任何测试异常先脱敏再输出或写日志。
- Langfuse任何故障不能阻断问答；不得吞业务错误、假报上传成功、重复callback上报或对无usage报真实0。
- A已选：立即落池，后台自动标准化及全状态全量查重；命中驳回/通过均只累计事件数。默认快照Top5，旧轮反馈不串轮。
- ch04原96题/24校准+72保留及历史报告不改。人工确认影响后升标注版本；72题不参与调阈值，不混画跨版本趋势。
- 维护窗口只覆盖确认后的写入/校准；等待人工/写入前预检失败时旧版继续。写入后失败可续跑，不强改旧profile身份。业务/闲聊不中断，知识维护请求不入问题池。
- 前端任务11按Vibe直接做，免brainstorm/TDD/code review；后端每任务RED→GREEN；Prompt/数据改用冻结标注样例，不写镜像实现的测试。
- 每任务完成追加dev-notes/ch09.md四项过程+变更/验证/问题/下一步，更新整体OPERATIONS及阅读副本。额度任一可用窗口剩余低于10%先交接留痕后暂停。

## Review Focus

1. 同会话新轮已开始时对旧回答点👎：只取被评价轮的原话/证据，重试不多计（任务3）。
2. DB已提交而进程未记完成：重启归并/发布不重复次数或知识行；锁连接丢失不能继续提交（任务3/6/8）。
3. 只改变72题标签或judge协议：不影响校准选择，趋势断开；基线变化使人工确认过期（任务7/9）。
4. SSE取消、interrupt续跑、同步线程及Langfuse故障：不泄漏trace上下文、不重复父子计费、不阻断业务（任务1/2/10）。
5. 老/kb写入口、脚本或外部数据变化绕过发布：阻止使用失配profile；只读评估永不建工单/入池（任务8/9）。

## 文件与接口地图

新目录按职责划分，不重构无关旧模块：

- `app/observability/{runtime,spans,costs}.py`：SDK生命周期/执行范围、自定义Span、统计。
- `app/flywheel/{contracts,locks,store,snapshots,worker,normalization,publishing}.py`：已有表持久化、命名锁、轮次快照、消费和发布。
- `app/kb/{evidence,calibration,self_check}.py`：评分/参数选择/生成前自评，复用现有retriever/reranker/answering。
- `app/evaluation/{contracts,versions,impact,artifacts,runner,scheduler}.py`：不可变数据版本、影响确认、本地持久记录、评估与调度；不替换ch04指标库。
- `app/api/{feedback,review,evaluations,observability}.py`与对应静态页：后端接口/人工操作及结果展示。
- `scripts/{test_ch09,demo_ch09,flywheel_ch09,eval_ch09,accept_ch09}.py`：脱敏测试、独立启动/校准、补跑、评估、合成验收。Make固定目标接这些脚本，`shell=False`。
- `data/local/ch09/`是实施时创建的gitignored运行状态，`log/ch09/`仅脱敏诊断，`reports/ch09/`仅合成验收报告，不复制真实会话。

跨任务类型在生产contracts定义，不让后续任务导入测试fixture：

- `TurnIdentity(session_id: str, conversation_id: int, user_message_id: int, request_id: str, trace_id: str, assistant_message_id: int|None=None, checkpoint_id: str|None=None)`。
- `EvidenceSnapshot(identity: TurnIdentity|None, snapshot_status: str, hits: list[dict], processing: dict, schema_version: int=1)`；status限定retrieved/no_hits/not_retrieved/snapshot_unavailable/legacy_unknown，hits最多5。
- `PoolEvent(identity: TurnIdentity|None, source: str, raw_question: str, reason: str, snapshot: EvidenceSnapshot)`；`RecordResult(id: int, created: bool)`；`NormalizedGap(question: str, suggested_answer: str)`；`MatchDecision(review_id: int|None, reason: str)`。
- `EvidenceFeatures(top1: float, count: int, margin: float, single_hit: bool)`；`EvidencePolicy(version: str, cutoff: float, weights: tuple[float,float,float], threshold: float, calibration_hash: str)`；`GateDecision(allow: bool, score: float, features: EvidenceFeatures, reason: str)`，定义于kb/evidence.py。`CalibrationCase(id: str, split: str, answerable: bool, relevant_ids: frozenset[int])`定义于kb/calibration.py，只是校准输入投影，不依赖ch04或ch09的来源schema。
- `VersionedCase`沿用原EvalCase的id/raw_query/bucket/difficulty/split/filters/answerable/ground_truth_answer/required_facts/forbidden_claims字段；relevant_chunk_refs扩展为file或review来源的判别联合，分别包含spec§7.1规定的字段，定义于evaluation/contracts.py。
- `DatasetVersion(version: str, parent: str|None, cases: list[VersionedCase], manifest: dict)`、`ImpactDraft(digest: str, base_release_id: str, review_ids: list[int], cases: list[dict])`、`ConfirmedImpact(draft_digest: str, dataset_version: str, edits: list[dict], unchanged_case_ids: list[str], confirmed_by: str, confirmed_at: str)`。dataset_version按确认后的完整标签内容哈希预先确定，freeze再复核，不依赖不确定的后续步骤。
- `ReleaseManifest(release_id: str, stage: str, base_identity: str, review_bindings: list[dict], dataset_version: str, profile_path: str|None, error_code: str|None)`；`ActiveRelease(release_id: str, dataset_version: str, corpus_hash: str, profile_path: str, profile_hash: str)`。
- `EvalRunResult(run_id: str, status: str, metrics: dict)`、`CostReport(window: dict, rows: list[dict], complete: bool, fetched_at: str, stale: bool)`。

## 验证命令约定

下面的`uv run pytest ...`是预期命令而非已跑结果。执行时优先沿用共享`.venv`并验证`app.__file__`来自产品目录，禁止为运行测试执行会重装editable项目的隐式sync。等价实际命令为根`.venv/Scripts/python.exe -m pytest ...`。任务1建立`test_ch09.py`：子进程`shell=False`、使用当前解释器，收集结果后先对当前配置秘密值脱敏，再输出和保存；不回显配置对象/连接串。后续经该runner执行文中测试。普通沙盒若再次asyncio挂起，终止自身挂起进程，按已有证据申请native执行，不修改业务绕过，也不把未启动当通过。

## Task 1：观测运行时、配置及启停隔离

**Files:** Create `app/observability/__init__.py`, `app/observability/runtime.py`, `scripts/test_ch09.py`, `tests/test_ch09_observability_runtime.py`; Modify `app/config.py`, `app/main.py`, `.env.example`, `pyproject.toml`, `uv.lock`。

**Interfaces:** `ObservabilityRuntime(settings, *, client_factory=None)`；`start() -> str`返回disabled/ready/degraded；`close() -> None`；`execution(name: str, *, trace_id: str, metadata: dict) -> ContextManager[TraceScope]`；`TraceScope.config(existing: dict|None=None) -> dict`去重callbacks，`TraceScope.update(**fields) -> None`不抛采集异常。

- [x] 记录已有改动hash/原包版本/解释器来源与基线测试结果；读取锁定版本，Context7核对SDK/CallbackHandler和FastAPI生命周期。选最新能保留原锁版本的官方SDK，新增依赖精确锁定；若解析要求升级原包则停，不自行换方案。安装后记录实际版本及签名，不能把在线文档版本当已安装版本。
- [x] 写RED：`test_disabled_creates_no_client`、`test_auth_failure_is_degraded_not_startup_failure`、`test_scope_failure_does_not_replace_business_exception`、`test_config_adds_handler_once`、`test_close_flushes_once`、`test_two_app_lifespans_do_not_reuse_shutdown_client`。断言业务返回相同、异常原对象不变、shutdown仅一次；脱敏runner用含合成secret的失败输出验证控制台/文件都无原值。
- [x] 经runner运行`uv run pytest tests/test_ch09_observability_runtime.py -q`，确认失败因缺少本任务功能，不是服务/环境失败。
- [x] 最小实现：配置`LANGFUSE_ENABLED=false`默认兼容旧入口；启用后日本URL必须显式配置，错误只降级；鉴权超时5s、SDK调用超时5s、关闭预算10s，超时记排空未确认。client在模型初始化前创建；测试注入factory隔离SDK单例，同public_key复用/已shutdown状态不得静默误用。`/health`旧存活响应不改，后续独立状态接口报告降级。
- [x] 上述定向测试GREEN，再跑`tests/test_chat_service.py tests/test_workflow_runtime.py`相关基线；记录实际差异及本章停机顺序，追加留痕，不commit。

## Task 2：全链Trace、轮次身份及自定义Span

**Files:** Create `app/observability/spans.py`, `app/flywheel/__init__.py`, `app/flywheel/contracts.py`, `tests/test_ch09_trace_graph.py`; Modify `app/workflow/{runtime,contracts,nodes,models,ticket_confirmation}.py`, `app/chat_service.py`, `app/main.py`, `app/kb/{retriever,reranker,embedding}.py`, `app/tooling/engine.py`（实施前核对实际执行方法）。

**Interfaces:** 生产`TurnIdentity`；`trace_span(name: str, *, input: dict|None=None) -> ContextManager[SpanScope]`；`SpanScope.finish(output: dict|None=None, *, error_type: str|None=None) -> None`；runtime把identity存入checkpoint并发出`turn` SSE帧，字段同TurnIdentity。

- [x] RED：两并发session的模型/工具只归各自trace；sync invoke、ainvoke和astream各有一次Generation；自定义to_thread检索仍为父trace子项；SSE取消关闭span；订单/工单续跑新trace含continuation_of且turn不变；trace_id在Langfuse关闭时仍可稳定产生；`/extract`无会话时不伪造业务session。
- [x] 运行`uv run pytest tests/test_ch09_trace_graph.py -q`确认RED。
- [x] 在实际顶层执行包住异步迭代而非仅包住生成器创建；trace_id由服务端request_id映射为32位hex，根trace后补最终intent/消息ID；只在入口注入一次handler。为DB回表/召回/rerank/原生工具补span，输出字段投影排除配置/连接与无关历史。summary后台及飞轮/评估另标purpose，保留来源session/trace联系，不依赖已关闭上下文。
- [x] 运行GREEN及`tests/test_ticket_interrupt.py tests/test_workflow_runtime.py`；用SDK测试导出sink核对树的父子关系与每次调用计数，不只测试mock方法被调用。
- [x] 留痕记录哪些步骤自动/手动采集，运维写清“本地有trace_id≠Cloud已收到”。

## Task 3：问题池快照与后端反馈幂等

**Files:** Create `app/flywheel/{locks,store,snapshots}.py`, `app/api/feedback.py`, `tests/test_ch09_pool.py`, `tests/test_ch09_feedback.py`; Modify `app/kb/low_confidence.py`, `app/models.py`, `app/workflow/{runtime,nodes}.py`, `app/main.py`。

**Interfaces:** `record_event(event: PoolEvent, *, session=None) -> RecordResult`；兼容旧`record_low_confidence(...) -> int`，新身份/快照仅keyword参数；`snapshot_for_turn(runtime, identity: TurnIdentity) -> EvidenceSnapshot`为async；`mysql_lock(name: str, *, timeout: int=5) -> ContextManager[LockLease]`，`LockLease.assert_owned() -> None`。POST `/api/feedback`输入session_id、user_message_id、assistant_message_id、rating（本章只接受down），输出event_id、created、snapshot_status。

- [x] RED：同轮source两次写`assert second.id == first.id and not second.created`；不同source分别入池；Top5保留完整原文和原分数；旧NULL/数组可读；新轮后点旧回答仍取旧turn；无检索为空、缺checkpoint标unavailable；消息跨会话伪造422/404；服务端忽略/拒绝客户端原文或片段字段。
- [x] 运行`uv run pytest tests/test_ch09_pool.py tests/test_ch09_feedback.py -q`确认RED；MySQL依赖不可用单列阻塞。
- [x] 使用同专用连接持有GET_LOCK，锁名为短前缀+身份hash，事务结束仍显式RELEASE_LOCK；锁失效停止提交，释放失败invalidate连接不归池。长模型调用不在本任务短事务内。只有显式绑定identity的事件跨请求去重；历史无消息ID行不按文本误合并。
- [x] 反馈当前checkpoint匹配才用，否则历史只读匹配，5s回捞预算；不重放图、不重新检索。自动拒答落池失败仍返回兜底并发出`collection_status`失败帧，反馈入池失败返回503，不假报成功。
- [x] GREEN及`tests/test_low_confidence.py tests/test_db_models.py`；用两个真实连接验证命名锁/断连释放（仅锁不写业务），写入测试回滚自身样本。更新留痕/运维反馈重试说明。

## Task 4：正式证据评分与仅24题校准

**Files:** Create `app/kb/{evidence,calibration}.py`, `tests/test_ch09_evidence.py`, `tests/test_ch09_calibration.py`; Modify `app/workflow/knowledge.py`（保留旧模式兼容）。

**Interfaces:** `score_evidence(result: RetrievalResult, policy: EvidencePolicy) -> GateDecision`；`calibrate(cases: list[CalibrationCase], results: dict[str,RetrievalResult], *, identity: dict) -> EvidencePolicy`；返回profile另附全部候选诊断，校准入口拒绝非calibration split。任务7把已绑定的VersionedCase投影到CalibrationCase，旧ch04初始数据也用同一投影，不能要求新review来源塞进旧EvalCase schema。

- [x] RED：重复chunk只算一次；非法/NaN/越界重排分拒答；单hit margin=0；其他策略不套重排评分；分数等于阈值不放行；将test样本传入校准应报错；仅改变test样本不改变结果；缺任一标签类别在写库前拒绝校准。
- [x] 运行`uv run pytest tests/test_ch09_evidence.py tests/test_ch09_calibration.py -q`确认RED。
- [x] 冻结小网格：特征为`s1`、`min(n,3)/3`、`max(s1-s2,0)`，单hit最后项0；n按当前cutoff计数。4组权重为(.8,.1,.1)、(.7,.2,.1)、(.7,.1,.2)、(.6,.2,.2)。cutoff仅取24题有效重排分的0/25/50/75/100百分位（排序线性插值）；阈值取各候选评分相邻值中点加0和1；`allow = valid_nonempty and score > threshold`。候选选择按不可答误放数、可答误拒数、权重清单顺序、cutoff升序、threshold升序排列最小；不读取72题。保存特征/候选/选择原因，分数不是正确概率。有效分0..1之外拒绝而非截断伪造。
- [x] GREEN，再用冻结合成24题输入检查可重现和边界；真实校准由任务8初始化/任务12实跑，不用合成结果当真实阈值。
- [x] 更新spec实施细化附注与留痕，记录网格在真实结果前冻结，任何改动须升算法版本再评估。

## Task 5：主图证据闸与生成前自评接通

**Files:** Create `app/kb/self_check.py`, `tests/test_ch09_knowledge_flow.py`, `docs/samples/ch09-self-check.jsonl`; Modify `app/workflow/{nodes,models,graph,contracts}.py`, `app/context/assembly.py`。

**Interfaces:** `SufficiencyDecision(can_answer: bool, reason: str)`；`ModelGateway.check_sufficiency(messages, budget) -> SufficiencyDecision`为async；运行时注入EvidencePolicy，禁用ch09时维持原gate。

- [x] RED：低证据在任何正文token/Agent调用前拒答且一次入池；闸通过但自评false使用self_check并保存同轮证据；预算/JSON失败不伪装self_check；自评true之后才允许真实生成；业务/闲聊不做知识自评；工具后来取得FAQ证据也走同一检查。
- [x] 运行`uv run pytest tests/test_ch09_knowledge_flow.py -q`确认RED。
- [x] 用现有模型网关计量/预算/回调，结构化自评输出上限512，仍受整轮剩余时间/token预算；充分性只判断当前证据，不另写最终答案。保持原工具权限、工单确认、引用和流式输出契约。
- [x] GREEN及`tests/test_workflow_knowledge.py tests/test_agent_loop.py tests/test_context_runtime.py`。Prompt单独冻结至少12例（充分/冲突/只支持部分/缺关键条件各3），按人工预期跑真实模型，保存合成样例逐项实际结果，失败先修Prompt再重跑，不改标签凑分。
- [x] 留痕及运维标明这是生成前自评，不是全文事实审查。

## Task 6：自动标准化、全量语义查重及消费者恢复

**Files:** Create `app/flywheel/{normalization,worker}.py`, `scripts/flywheel_ch09.py`, `tests/test_ch09_worker.py`, `docs/samples/ch09-dedup.jsonl`; Modify `app/flywheel/store.py`, `app/models.py`, `app/main.py`。

**Interfaces:** `normalize(event: PoolEvent) -> NormalizedGap`、`match(gap: NormalizedGap, candidates: list[dict]) -> MatchDecision`为async；`merge_event(event_id: int, gap: NormalizedGap, match: MatchDecision, *, lease: LockLease) -> int`返回review_id；`FlywheelWorker.run_once(limit: int=20) -> dict`、`start()/aclose()`为async。

- [x] RED：分页到最后仍能命中；待审/通过/驳回均参与；命中驳回次数+1且状态不变；提交后崩溃重跑不再+1；坏返回ID/模型超时不新建行；一条失败不饿死后续；两个消费者不能插同一缺口；锁断开后不得提交。
- [x] 运行`uv run pytest tests/test_ch09_worker.py -q`确认RED。
- [x] ORM精确映射既有review_queue；一个全局归并命名锁保护扫描/选择/提交，模型调用不持DB事务，提交前复查锁和matched_review_id。候选按id分页20条全扫，单条过长按token预算减少批量不截断语义；所有批返回后才终决。命中多行时再判最贴切，仍平分选最小ID；禁止自动合并已有审核行。
- [x] 消费者提交即唤醒且30s扫尾；单模型超时30s；失败后自动重试最多2次，分别退避30/120s，第3次失败进入needs_retry，不再自动重试；持久记录processing错误码/次数。手动retry重置调度但保留累计诊断。旧未归并行按顺序纳入，legacy快照不伪造。脚本`--once`复用同一服务，不能与应用抢锁破坏幂等。
- [x] GREEN；Prompt冻结至少16例覆盖不同型号/否定、同义、相似非同义、驳回、跨批命中及注入文本，真实跑并人工比对；更新留痕与失败重试维护步骤。

## Task 7：不可变评估版本、影响预览与人工确认

**Files:** Create `app/evaluation/{__init__,contracts,artifacts,versions,impact}.py`, `tests/test_ch09_versions.py`, `tests/test_ch09_impact.py`, `docs/samples/ch09-label-impact.jsonl`; Modify `.gitignore`。

**Interfaces:** `VersionStore(root: Path)`提供`bootstrap(ch04_root: Path) -> DatasetVersion`、`load(version: str) -> DatasetVersion`、`freeze(parent: str, draft: ImpactDraft, confirmation: ConfirmedImpact) -> DatasetVersion`；`prepare_impact(review_ids: list[int], *, base: ActiveRelease) -> ImpactDraft`为async；`confirm_impact(digest: str, *, edits: list[dict], unchanged_case_ids: list[str], confirmed_by: str) -> ConfirmedImpact`。`ArtifactStore.write_json(relative: str, data: dict) -> None`原子写、`read_json(relative: str) -> dict`严格路径/哈希校验。

- [x] RED：原ch04文件hash不变；96题/原话/ID/24与72清单不可改；改变calibration与改变test互相隔离；未经人工确认不能freeze；同digest重复确认幂等；核准答案/基线变了409；路径穿越/坏版本/缺来源拒绝；复用chunk不改其原来源。
- [x] 运行`uv run pytest tests/test_ch09_versions.py tests/test_ch09_impact.py -q`确认RED。
- [x] 实现spec§6.3/7.1文件结构和两类来源绑定；私有目录gitignored，不复制真实原话。影响扫描96题，模型仅建议；确认输入含逐项差异及“其余不变”确认。记录人工填写标识（不假装鉴权）、UTC确认时间和base identity。无变更复用标签版本；有来源/标签变更新建不可变版本，标签哈希沿现有loader规范统一换行。
- [x] GREEN；影响Prompt用至少8例验证：库外转可答、仍不可答、新同义来源、禁止承诺、负例没改变、模型漏报由人工补正、原答案冲突、test-only变更。测试数据用合成核准知识，不改正式96题。
- [x] 写清版本丢失恢复/备份清单及留痕。此任务不写knowledge_chunks或Milvus。

## Task 8：审核API、知识发布状态机和独立ch09入口

**Files:** Create `app/flywheel/publishing.py`, `app/api/review.py`, `scripts/demo_ch09.py`, `tests/test_ch09_publishing.py`, `tests/test_ch09_review_api.py`, `tests/test_ch09_launcher.py`; Modify `app/kb/{dualwrite,migration}.py`, `app/api/kb.py`, `app/core/jobs.py`, `app/main.py`, `app/workflow/knowledge.py`, `app/workflow/nodes.py`, `Makefile`。

**Interfaces:** `Publisher.preview(review_ids: list[int]) -> ImpactDraft`、`publish(confirmation: ConfirmedImpact) -> ReleaseManifest`、`retry(release_id: str) -> ReleaseManifest`为async；`ReleaseCoordinator.read_lease() -> AsyncContextManager[ActiveRelease]`、`maintenance() -> AsyncContextManager[None]`；`vectorize_ids(ids: list[int]) -> VectorizeResult`只处理限定IDs。接口GET `/api/review`与`/{id}`，POST `/{id}/approve`、`/{id}/reject`，POST `/api/review/impact`、`/impact/confirm`，GET `/api/review/releases/{id}`、POST `/api/review/releases/{id}/retry`。

- [x] RED：审核状态CAS与幂等；无有效确认不写知识；复用块及新块不会重复；发布仅处理本批IDs；在pending/flush/done/profile/active每个边界注入崩溃后续跑一致；旧服务读lease结束前不写；新知识请求维护提示不入池；写入前失败旧版仍答、写入后失败知识降级；旧/kb及作业绕路被拦。
- [x] 运行`uv run pytest tests/test_ch09_publishing.py tests/test_ch09_review_api.py tests/test_ch09_launcher.py -q`确认RED。
- [x] 实现先journal后写入、build_chunks→指纹幂等→限定双写→24题校准→active原子替换；MySQL命名锁跨脚本串行，同进程read lease覆盖整个用知识轮次（含后续FAQ工具路径），维护标志先设再等待旧读者避免饥饿。评估也用lease；同一线程/轮次重复读不能死锁。旧/kb在ch09启用时写操作必须经协调器，无法确认版本的写入拒绝而非绕过。
- [x] 跨进程协调不能只靠应用内读锁：ch09应用持有数据库命名owner锁，第二应用拒绝成为共享知识写入口；脚本在线时通过该应用的本机管理API触发发布/评估，旧直接写入CLI检测owner锁后拒绝并给出正确入口。离线脚本须先取得owner锁再做发布/评估，因此无在线读者；按owner→发布锁顺序取锁。固定后台作业不得在父进程持有发布锁时等待另一个抢同锁的子进程。补`test_offline_cli_cannot_mutate_online_readers`与`test_job_dispatch_does_not_deadlock_parent_lock`。
- [x] `demo_ch09.py`提供`check`（只读脱敏）、`bootstrap`（建立基线版本并真实24题校准）、`calibrate`（当前已确认版本重校准）、`serve --port 8023`；独立`log/ch09/checkpoints.sqlite`和`data/local/ch09`，沿用已核对ch04集合/清单和ch08工具配置，不覆写ch08profile/SQLite/启动器。8023先查占用，冲突则显式选空闲端口，不杀旧服务。首轮bootstrap检查至少一正一负标签、来源一致；校准失败不创建active。
- [x] GREEN及`tests/test_kb_api.py tests/test_jobs.py tests/test_ticket_interrupt.py`。首次真实bootstrap和审核写库放任务12确认目标后进行；更新整体维护窗口/恢复手册及留痕。

## Task 9：真实部署链评估、定时运行记录及趋势接口

**Files:** Create `app/evaluation/{runner,scheduler}.py`, `app/api/evaluations.py`, `scripts/eval_ch09.py`, `tests/test_ch09_eval_runner.py`, `tests/test_ch09_schedule.py`; Modify `app/models.py`, `app/main.py`, `app/core/jobs.py`, `Makefile`。

**Interfaces:** `run_evaluation(*, trigger: str, slot: str|None=None) -> EvalRunResult`为async；`EvalScheduler.tick(now: datetime) -> None`为async；`comparison_key(metrics: dict) -> str`纯函数。POST `/api/evaluations/run`、GET `/api/evaluations/runs`、GET `/api/evaluations/trends`；触发只接受既有手动/定时枚举。

- [x] RED：72题计划数固定、不可答Recall/MRR为N/A、拒答Faithfulness为N/A；judge失败单列；真实模型调用链使用现有graph而非旧generate_answer旁路；写工具/会话/问题池依赖调用直接fail；运行中版本变动为invalid；只有同comparison_key算delta；两个调度器/重启不会同slot重跑，遗留running标interrupted。
- [x] 运行`uv run pytest tests/test_ch09_eval_runner.py tests/test_ch09_schedule.py -q`确认RED。
- [x] 创建精确EvalRun ORM，开始一行running、结束更新同一行，保存全部spec§7.2身份/分母。评估runtime注入隔离内存会话存储/临时SQLite及拒绝写操作工具依赖，仍调用实际图/模型/检索；传入样例filters且不得丢失；所有副作用调用测试硬失败。错误不写成0分，实际失败原因脱敏。
- [x] 默认Asia/Shanghai每日02:00，`CH09_EVAL_ENABLED`可关，tick60s、最多补最近槽；仅应用在线。使用全局eval命名锁+数据库slot查重防并发，无DDL唯一索引假设。固定Make目标`ch09-eval`用脚本`--trigger 手动`，调度直接复用runner。启动能恢复失联状态，不误标仍持锁运行的进程。
- [x] GREEN及`tests/test_kb_evaluation.py tests/test_eval_retrieval_runner.py tests/test_jobs.py`；记录定时前置条件/补跑/版本差异，真实两轮放任务12。

## Task 10：按意图的真实Token与成本统计

**Files:** Create `app/observability/costs.py`, `app/api/observability.py`, `tests/test_ch09_costs.py`; Modify `app/main.py`, `app/config.py`。

**Interfaces:** `collect_costs(start: datetime, end: datetime, *, purpose: str='chat') -> CostReport`为async；GET `/api/observability/status`、GET `/api/observability/costs?from=...&to=...`，默认最近24h。

- [x] RED：分类前Generation归最终trace intent；父Span汇总不能再加一次；同observation跨页仅计一次、真实重试两次都计；无usage/价格为unknown；退款续跑归原turn意图；目的隔离；分页中途失败complete=false，缓存错误不改成新鲜0；恶意超长时间窗拒绝。
- [x] 运行`uv run pytest tests/test_ch09_costs.py -q`确认RED。
- [x] Context7确认实际锁定SDK的Metrics/Observations接口，优先服务端过滤与聚合，不支持最终trace metadata关联则官方分页后端join。15分钟缓存、单飞刷新、Hobby Metrics请求预算最多96次/日留余量，多请求刷新也计入；预算计数存私有运行元数据跨重启保留，分页API最多20请求/分钟并尊重实际429 Retry-After。窗口限定最多30天，用户选择更老数据返回不可用。观测行按真实Generation计，缓存token不重复加总；价格按明确来源/日期/币种配置，无价保留unknown。
- [x] GREEN；Task12云端真实至少2种意图/一条重试或多调用核对，保留合成报告的usage来源和成本完整性，不用预算估算当账单。
- [x] 留痕及运维记录Cloud延迟/限额/缓存时间和“unknown≠0”。

## Task 11：审核、反馈、趋势与成本前端（Vibe例外）

**Files:** Create `app/static/review.html`, `app/static/review.js`, `app/static/quality.html`, `app/static/quality.js`; Modify `app/static/{index.html,chat-citations.js,admin.js,admin.html}`, `app/main.py`。

**Interfaces:** 使用任务3/8/9/10已验证API；GET `/review`、`/quality`提供静态页；沿用安全文本渲染与现有导航。

- [x] 直接实现：回答绑定turn元数据，👎提交成功后禁重复，失败可重试；旧无ID答案给可理解提示。审核列表分页/状态筛选，详情原话+入口+快照+分数；核准答案可编辑，示例答案明确只备查。
- [x] 直接实现：影响预览/逐题差异确认及其余不变确认，状态区分通过与已生效，发布失败可重试；质量页3指标趋势、有效分母/失败/N/A与版本筛选，成本按意图排序且显示未知/缓存时间。
- [x] 用真实浏览器核验旧轮👎、多标签重复、跨会话切换、核准/驳回/待确认/维护中/失败、长文本与注入文本、无数据/失败图表。前端不跑TDD或独立code review；浏览器结果逐项记录，不能仅以HTTP200算验收。
- [x] 同步留痕和完整日常操作说明；不引入前端框架或额外部署服务。

## Task 12：真实全流程、Cloud readback及两轮评估

**Files:** Create `scripts/accept_ch09.py`, `reports/ch09/{summary,demo,trace-audit,evaluation,failures}.md`; 使用上述既有脚本，合成数据明细放reports/ch09子目录。

**Interfaces:** `accept_ch09.py --base-url http://127.0.0.1:8023 --run-id <unique>`，只写本次合成验收身份/已核准内容，报告中无私有配置；不得自动删除真实数据。

- [x] 核对本地MySQL/Milvus目标、集合/清单、两MCP服务、端口与原服务；SDK兼容已通过后，从后端私有配置读取Langfuse日本站凭据，不回显。若未配置只记录缺哪些变量，由后端私有配置补齐，绝不把聊天中的值嵌进脚本/命令记录。执行`demo_ch09 check`后`bootstrap`，确认24题profile实际成功，保留原profile和失败记录。
- [x] 启动独立ch09入口；真实问已知知识题，Cloud读回并在Dashboard逐项核对意图→检索→精排→Agent→LLM→回答、Prompt/输入输出、usage、耗时和异常标记。记录trace ID/安全链接和合成数据截图；SDK缓冲/关停flush后读回，不以本地trace_id冒充成功。
- [x] 使用不在原96题中的合成知识缺口，验证兜底→原话/Top5入池→自动标准化全量查重→审核详情。人工明确核准合成答案及标签影响确认→双写/新profile/active→同问正确；重复核准不得新增知识。另以合成夹具证明“原库外题转可答”版本流程，不改正式ch04标签凑闭环。
- [x] 真实对旧轮点👎（当前轮已改变）、闲聊无检索👎、重复点击；核对问题池来源、目标轮片段、matched_review_id、队列次数。用合成新提问覆盖self_check入口；若模型未按预期拒答如实留失败并修Prompt，不能以mock替代验收。
- [x] 执行两次`eval_ch09 --trigger 手动 --no-cache`，同一已确认版本各72题，至少两条成功有效eval_runs并有可比趋势；提供真实Recall@K/MRR/Faithfulness与分母/失败，不要求第二轮虚构提高。
- [x] Cloud按意图token/成本核对、Langfuse断连降级问答、自有应用正常停止/重启与积压恢复；仅操纵本章进程/SDK测试出口，不停用户旧服务。完成后跑脱敏全量`uv run pytest -q`；报告真实通过/失败/跳过，保留所有未解决项。
- [x] 完成阶段留痕及统一启动/停止/维护、Langfuse日常观察完整操作流程。最终命令以实际`--help`与运行结果修订，不交付未运行的“成功命令”。

## Task 13：后端整章Review、修复及Finish

**Files:** Create `reports/ch09/review.md`; Modify 本章实际发现问题文件与相应tests、spec/plan执行裁决、dev-notes/ch09、CH09_CURRENT_PROGRESS、AI_HANDOFF、OPERATIONS及根阅读副本。

**Interfaces:** 评审范围为本章后端/Prompt/数据处理与验收证据，排除前端实现review及ch08已有改动；提供开始时保存的差异基线，不能把整个工作树旧修改算本章。

- [x] 按用户选择的执行方式运行对应技能；Native最后一次fresh独立后端整章review，Subagent-driven按其任务门槛并最终review。评审模型按届时技能要求选择，不擅自改变执行方式。
- [x] 反馈先核实；可测试缺陷先RED再修GREEN，Prompt问题重跑冻结样例。无代码变化不反复跑全量；有实质修复时完成定向及必要全量/真实受影响路径复验。
- [x] 核对六项验收与新增版本裁决逐项证据，补任何缺口；仍有Critical/Important或必需验收未完成不宣称finish，记录可恢复阻塞与下一步。
- [x] 使用verification-before-completion和finishing-a-development-branch；用户未授权Git操作，保留工作树/数据/账本，不执行默认commit/push/merge/删除。交付演示命令、真实测试/Cloud/评估结果、限制、留痕与维护手册。

## 作者自检与执行交接

本节在计划完成后填写检查结果，不是实现进度。规范覆盖映射：spec§3→任务1/2/10；§4→3/4/5；§5→6；§6→7/8；§7→7/9；§8→后端3/8/9/10及Vibe11；§9→12/13；§10/11→各任务Context7/依赖门槛、版本裁决与最终文档。

依赖顺序：1→2→3；4→5依赖2/3；6依赖1/3；7依赖6；8依赖4/5/7；9依赖8；10依赖2；11依赖3/8/9/10；12→13。本章共享身份、发布/版本和后台生命周期，采用一份按依赖串行的计划，不拆成接口重复的独立子项目。

作者自检（2026-10-26）已执行：spec各节均有上述任务；13个任务各有明确交付/接口/验证（前端按用户例外）；五项Review Focus均落到具体测试。修正了新review来源不能套旧BoundCase的类型问题、确认记录缺少人工edits的问题、重试次数与退避不一致，以及跨进程脚本仅共享写锁仍可穿透应用读锁的问题。并补上成本API预算跨重启持久化。没有派额外计划审阅者，没有运行任何产品测试或实施任务；这些是计划作者的静态自检，不是功能验收。

计划完成后请用户审阅并选择本章Native或Subagent-driven。推荐Native：任务间共享契约多且工作树已有ch08改动，单实施者更易保持上下文一致并节省额度，最后做独立后端整章review。计划批准前不实施；设计批准不重复询问。


## 2026-10-27 Native review执行Ruling

唯一整章后端review发现5Important，作者定向与1110项全量GREEN；详见reports/ch09/review.md。实施细化：profile绑定检索运行配置hash；所有校准入口共用unfinished与安装前租约检查；不可变文件原子create-if-absent；官方SDK mask投影图State、保留实际LLM Prompt；ch09 judge明确JSON schema并纳入Prompt身份。旧原集/24-72分割/历史成绩保留，旧profile重校准，失败/中断轮不计完整验收。前端仍Vibe免review但需UI验证；当前尚未finish。
