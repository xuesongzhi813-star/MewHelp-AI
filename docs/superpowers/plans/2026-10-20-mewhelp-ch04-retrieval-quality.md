# MewHelp AI ch04 Retrieval Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在现有客服系统交付原生 BM25 混合检索、BGE 重排、可核查引用及拒答入池，并用固定评估集比较四种策略。

**Architecture:** 延续薄 Milvus 客户端与 MySQL 原文仓储，在新集合 knowledge_hybrid 中建立检索副本；通过 QueryPlan、RetrievalResult 和工具 artifact 连接检索与聊天。生成先判断证据充足性并校验引用再发布，离线评估与线上调用复用同一检索及生成模块。

**Tech Stack:** Python 3.12、FastAPI 0.143.0、SQLAlchemy 2.1.4、langchain-core 1.6.10、pymilvus 3.0.2、既有 DeepSeek、SiliconFlow BGE-M3/BAAI/bge-reranker-v2-m3。运行中 Milvus 版本待服务恢复后核实，部署文件声明 v3.0.0。

**Spec:** `docs/superpowers/specs/2026-10-20-mewhelp-ch04-retrieval-quality-design.md`（用户已批准）。

状态：用户选择 Native，计划获准执行；用户明确选择当前功能分支就地实现。完成状态以任务复选框及 dev-notes/ch04.md 的真实验证记录为准。

2026-10-21最终检查点：Task1–14完成，最后全量551 passed in35.44s，无skip；真实24校准、72x4比较、72部署和逐桶人工核查已完成。迁移65当前/48历史保护通过，已使用冻结阈值0.65完成聊天拒答入池、引用及前端验收。真实指标与已知格式/误拒答限制见reports/ch04/summary.md，不声明100%可靠。保留未提交工作区，恢复入口docs/CH04_CURRENT_PROGRESS.md。

## Global Constraints

- 原生 BM25，text 字段 BM25 函数，内置 chinese analyzer；dense/BM25 各 Top-50，hybrid_search + RRFRanker(k=60)，融合最多 100，指定 reranker Top-10。
- MySQL 原文权威源；Milvus 可存检索副本；旧 knowledge 集合保留。零 DDL，不 create_all、不重置旧数据，不用 kb-reset/kb-rebuild 代替新集合迁移。
- 业务按原三份中文知识文档统一；普通地区满 99 元包邮，否则 10 元；现货通常 24 小时发货；积分 100 抵 1，最高抵 20%，有效 12 个月。不保证到账时限。
- 新资料只写 data/kb/ch04，不覆盖用户暂存的六份参考文档、admin.js 和 ch03 留痕。MySQL pending → Milvus upsert/flush → MySQL done 不变。
- query_faq 对模型可见参数仅 keyword；保留普通 invoke 的字符串契约、execute_tool -> str、单轮业务工具及 create_ticket.conversation_id 系统注入。
- 四策略为 dense/bm25/hybrid/hybrid_rerank；96 条标注，六桶各 16，24 校准/72 测试。正式比较共用 QueryPlan、语料、生成硬闸；在线 rerank 阈值另报。
- 后端 TDD；纯 Prompt/数据以标注样例验证代替 TDD；前端按用户要求 Vibe Coding，免 brainstorm/TDD/code-review 子流程，但做真实交互验证。
- 不新增指代消解、多轮改写、LangGraph、反馈后端、faith_cases 功能或基础设施；依赖不升级，真实密钥只留本地配置，不打印 .env。
- 未明确获准不提交、推送、合并或部署。技能模板里的 commit 步骤统一替换为 diff 检查及追记留痕。
- 当前 checkout 有用户未提交/暂存文件。执行时先读 using-git-worktrees，不能创建遗漏已有材料/已批准 spec/plan 的隔离副本，也不能 stash 或重置用户文件；按技能支持的方式保留并访问所需输入。
- 每个 Task 完成即在 dev-notes/ch04.md 追记用户关键原话、产出/评审、纠偏、返工、变更文件及真实验证结果；不能待收尾一次补记。计划获批时另记阶段，不能提前声称已评审通过。
- 每次开始库专属实现前沿用/补充 Context7 查证，优先核对本地锁定版本；遇指定技术不兼容则暂停相关任务并向用户说明。

## Review Focus

1. 老 MySQL 行已 done 但新集合为空：必须回填所选语料，不能因旧状态跳过；旧集合和排除旧 ID 不变（Task 4）。
2. 失败重试的旧线程晚返回：只能发布被等待的那次 artifact，不能串引用、重复入池（Task 8/9）。
3. SKU 连字符、相近型号和否定条件：改写保真，chinese analyzer 原生 BM25 真正命中正确型号（Task 2/5/14）。
4. 检索命中但证据缺条件/有冲突：不凭相似度作答，草稿不得先流出，明确拒答并保留原话/真实会话（Task 7/9）。
5. 切分或语料改变、全部拒答、judge 失败：评估绑定错误须失败，拒答不获 Faithfulness 满分，分母/失败数可审计（Task 11/12）。

## 文件与依赖地图

- app/kb/contracts.py：QueryPlan/过滤/检索结果/证据/生成决策等共享边界，无网络 I/O。
- app/kb/catalog.py、data/kb/ch04/*.md：SKU/品类确定规则及六类统一语料；sources.py 保持材料清单唯一入口。
- app/kb/milvus.py：集合/schema、单路/混合召回；filters.py 只生成白名单参数化过滤。
- app/kb/migration.py：新集合迁移快照/排除列表/恢复检查；store.py 与 dualwrite.py 提供范围内读写和状态。
- app/kb/query.py、reranker.py、retriever.py：单轮改写、重排 HTTP、四策略编排；保留旧 dense 入口。
- app/kb/answering.py、low_confidence.py：证据首尾排布/自评/引用校验与问题池仓储。
- tools.py、executor.py、chat_service.py：artifact 兼容调用、最终输出/入池；不扩展 Agent Loop。
- app/kb/evaluation.py、evaluation_judge.py、scripts/eval_retrieval.py：数据绑定、指标/缓存/报告及真实模型评审。
- app/api/kb.py、app/static/index.html、app/static/kb.html：检索自测/原文接口、聊天引用和单次反馈、原文定位。
- docs/samples/ch04-*.jsonl：Prompt 开发验证样例；data/eval/ch04/：固定正式样例；reports/ch04/：真实交付报告；运行清单/模型缓存进入 gitignored log/。

任务顺序：1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14。Task 3/4 的真实 Milvus 探针若因服务离线不能执行，明确暂存该验证，允许继续独立的 fake/纯函数任务；Task 14 不允许跳过真实服务验收。执行方式由用户选择后按对应技能推进。

---

### Task 1：共享契约、配置和现有低置信度表映射

**Files:** Create app/kb/contracts.py、app/kb/low_confidence.py、tests/test_kb_contracts.py、tests/test_low_confidence.py；Modify app/config.py、app/models.py、app/kb/retriever.py、.env.example、tests/test_config.py、tests/test_db_models.py。

**Interfaces:**
- RetrievalStrategy = Literal['dense','bm25','hybrid','hybrid_rerank']。
- RetrievalFilters：category/product_category/content_type 可空，include_general=False；拒绝额外字段和未知品类；字符串长度分别 <=255/64/32。
- QueryPlan：raw_query/normalized_query/lexical_query、intent（knowledge/chat/business）、filters、rewrite_reason、rewrite_fallback。模型只产出候选，raw_query 由系统固定。
- Hit 保留旧字段及位置兼容，在末尾添加 product_category='通用'、score_type='dense'、dense_score/bm25_score/fusion_score/rerank_score=None；RetrievalResult(plan,strategy,hits,candidate_ids,reason)；Evidence(number,hit)；AnswerDecision(can_answer,reason,answer,used_citation_numbers)。Hit 移到 contracts 后，retriever 继续重新导出 Hit，避免破坏旧导入；不得反向导入 retriever 造成循环。
- record_low_confidence(raw_question: str, conversation_id: int | None, source: Literal['retrieval_low_conf','self_check'], reason: str, *, session: Session | None = None) -> int；传入 session 时只 flush 不 commit。
- 新配置：kb_corpus_version='ch03'，knowledge_strategy='dense'，hybrid_collection='knowledge_hybrid'，kb_migration_manifest='log/kb/ch04-migration.json'，rerank_model='BAAI/bge-reranker-v2-m3'，rerank_base_url='https://api.siliconflow.cn/v1'，rerank_api_key=''（调用时回退 embedding key），rerank_min_score=0.5；query_timeout=12、rerank_timeout=15、embedding_timeout=10、knowledge_tool_timeout=90、answer_timeout=45（秒）。dense/BM25/混合请求各10秒；embedding保留应用层最多3次总尝试、禁用SDK叠加重试，单请求10秒。旧 tool_timeout=10 不变。

- [x] 写 test_low_confidence_uses_existing_table_and_outer_transaction：插入唯一原话及真实测试会话，断言 source/关联/数据库时间；监听 commit 确认注入 session 不提交；回滚后无该条记录。写过滤额外字段/非法品类及所有新配置边界测试。
- [x] Run `uv run pytest tests/test_kb_contracts.py tests/test_low_confidence.py tests/test_config.py tests/test_db_models.py -q`；新接口缺失应红，不能以 MySQL 无法连接当成功的 red。核心断言为 `row.raw_question == raw_question`、`row.conversation_id == test_conversation_id`、`row.source == 'self_check'`、`commits == []`。
- [x] 按接口新增实现，沿用既有 session 范围约定，仅映射已存在表。类型验证与网络调用分离；配置日志只显示是否配置 key。
- [x] 重跑同命令及 `uv run pytest tests/test_kb_retriever.py -q`，全部通过后检查 diff 并追记 Task 1；不提交。

### Task 2：六类古着语料、SKU/品类规则及开发标注样例

**Files:** Create data/kb/ch04/{returns-policy,product-faq,after-sales-manual,product-specs,member-benefits,billing-shipping}.md、app/kb/catalog.py、docs/samples/ch04-query.jsonl、docs/samples/ch04-answer.jsonl；Modify app/kb/sources.py、tests/test_kb_sources.py；Create tests/test_kb_catalog.py。

**Interfaces:** get_sources(base_dir: Path | None = None, *, corpus_version: str | None = None) -> list[Source] 保留旧位置参数；SOURCES 是唯一版本化源声明。infer_product_category(section_path: str, questions: str, answer: str) -> str，仅从明确章节或目录 SKU 判定，其余「通用」。SKU 采用 MH-DJ101/MH-DJ102 等 12 个合成型号，两个相似型号至少有尺寸/材质差异，品类五类并含通用。

- [x] 先建立业务检查清单和至少 18 条 query/18 条 answer 开发样例：正确改写、SKU/否定/金额保真、证据缺失、冲突、禁止到账承诺、文档指令注入。此数据任务按用户要求用样例验证替代 TDD。
- [x] 编写六份文档，用 build_chunks 预览逐条绑定章节；核对 99/10、24 小时非保证、积分20%/12月，不引入无依据会员等级数字。保留用户已有文件和 ch03 原文。
- [x] 写并先运行 test_source_versions_are_explicit_and_do_not_scan_reference_files、test_unknown_sku_has_no_guessed_product_category；断言 ch03 三源/ch04 六源、相似 SKU 区分、普通文件名不会被自动收入；红后实现源版本及 catalog。
- [x] Run `uv run pytest tests/test_kb_sources.py tests/test_kb_catalog.py tests/test_kb_documents.py -q`，再运行 ch04 preview（Task 4 CLI 完成后补真实命令验证）；留存人工逐条核对结果。开发样例不是正式72条测试的替代品。
- [x] 追记语料完成项、样例数、尚未模型验证的范围；不虚报 Prompt 通过率。

### Task 3：Milvus 原生 BM25、过滤和服务端 RRF

**Files:** Modify app/kb/milvus.py；Create app/kb/filters.py、tests/test_kb_hybrid_milvus.py、tests/test_kb_hybrid_integration.py。

**Interfaces:** ensure_hybrid_collection(collection_name: str) -> None；upsert_records(records: Sequence[dict], *, collection_name: str) -> None；search_dense(vector, limit, *, collection_name, filters: RetrievalFilters) -> list[tuple[str,float]]；search_bm25(text, limit, *, collection_name, filters) 同返回；search_hybrid(vector,text,*,collection_name,filters) 同返回，固定两路50、融合100；compile_filter(filters) -> tuple[str,dict]。旧 ensure_collection/upsert/search 保留 legacy 调用兼容，旧 search 显式 anns_field='vector'。

- [x] Context7 核对 Function/BM25、chinese analyzer、client.hybrid_search 和 expr_params；本地签名比对。写 fake client 测试断言 schema id/vector/text/sparse/三个标量、UTF-8 字节边界、BM25 函数、索引、ranker 参数；schema 不符必须抛明确错误而非静默复用。
- [x] 测试断言 `reqs[0].limit == reqs[1].limit == 50`、`kwargs['limit'] == 100`、`ranker.dict()['params']['k'] == 60`、`reqs[0].expr_params == reqs[1].expr_params`；恶意值不能拼成表达式。Run `uv run pytest tests/test_kb_hybrid_milvus.py tests/test_kb_milvus.py -q`，确认新增测试红。
- [x] 实现薄封装；text 超65535字节在请求前失败，不截断原文。schema 不含 dynamic field，过滤字符串值用参数绑定；collection_name 显式传递，避免迁移改写全局目标。
- [x] 同命令全绿。真实测试使用 `ch04_test_<uuid>`，run_analyzer + 两条相似 SKU 的 BM25/混合/过滤 roundtrip；finally 仅删该 UUID 集合。Run `uv run pytest tests/test_kb_hybrid_integration.py -m integration -q`，离线可 skip 但须记录，不能视为真实兼容性通过。
- [x] 追记锁定接口、真实服务状态及测试结果；不改 Docker 配置或自行换 analyzer。

### Task 4：范围感知迁移、双写恢复和 CLI

**Files:** Create app/kb/migration.py、tests/test_kb_migration.py；Modify app/kb/store.py、dualwrite.py、ingest.py、Makefile、tests/test_kb_dualwrite.py、tests/test_kb_ingest.py。

**Interfaces:** MigrationManifest 字段 version=1、target_collection、source_hashes、legacy_snapshot_ids、reused_ids、legacy_excluded_ids、phase、completed_ids；phase 为 snapshot/ingested/indexing/validated。prepare_migration(*,dry_run:bool=True) -> dict；migrate_hybrid() -> dict；load_manifest() -> MigrationManifest；eligible_chunk_ids() -> set[int]；store.iter_chunks_by_ids(ids: Sequence[int]) -> Iterator[StoredChunk]。正常 dualwrite 依据 corpus_version 选择 legacy 或 hybrid 路线，ch04 只处理 eligible IDs。

- [x] 写 test_existing_done_rows_are_backfilled_into_empty_hybrid_collection、test_resume_keeps_original_snapshot、test_missing_manifest_with_existing_target_fails_closed、test_flush_failure_does_not_mark_done、test_legacy_collection_never_dropped。用临时清单、fake embedding/Milvus及事务隔离 MySQL，核心断言 `set(target_ids) == eligible_ids`、`legacy_snapshot_before == legacy_snapshot_after`、`excluded_ids.isdisjoint(target_ids)`；失败批次仍pending。
- [x] Run `uv run pytest tests/test_kb_migration.py tests/test_kb_dualwrite.py tests/test_kb_ingest.py -q` 确认新增测试红；实现先原子保存 snapshot 再入库，以指纹复用标准块，再逐 ID 回填新集合。清单重跑目标/hash 不符中止；每步失败保留可续跑 phase。
- [x] 新 CLI：`uv run python -m app.kb.ingest migrate-hybrid --dry-run` 只读；`... migrate-hybrid` 写选定语料；`... verify-hybrid` 逐 ID/文本指纹及计数校验；`... preview --corpus-version ch04` 只预览。写入命令失败非零退出。manifest 缺失时禁止从已存在新集合重新猜测基线；前置 schema 不符先失败，不先写 MySQL。
- [x] 适配 status/consistency：mysql_total、eligible_total、legacy_excluded、eligible_pending/done、target_count；>5000 ID 时分页精确校验，不能用截断列表宣称主键全对齐。新集合恢复只处理 eligible IDs；ch04 路线拒绝旧 reset 命令并指明范围，不扩大清空权限。
- [x] Run 同组测试；新增 make kb-ch04-preview、kb-ch04-migrate、kb-ch04-verify 固定目标。此阶段不自动加入可从网页运行的迁移按钮，避免未审查目标环境的远程启动。
- [x] 追记 Task 4；实际非测试库迁移留至 Task 14，不能在单元验证时清生产库。

### Task 5：本轮 Query 理解、有限同义词扩展及意图保护

**Files:** Create app/kb/query.py、tests/test_kb_query.py；Modify app/prompts.py、app/llm.py；使用 Task 2 开发样例。

**Interfaces:** build_query_plan(raw_query: str, *, filters: RetrievalFilters | None=None, model=None) -> QueryPlan；validate_rewrite(raw_query: str, normalized_query: str) -> bool；显式传入 filters 优先于模型推测。QueryPlan 不接收 history。

- [x] 写 test_rewrite_preserves_sku_negation_amount_and_deadline_origin：原话含 MH-DJ101、不要/不是、99元、签收后7天，任何关键项丢失则原话回退；test_rewrite_failure_does_not_drop_explicit_filter；test_pronoun_does_not_expand_using_history。Run `uv run pytest tests/test_kb_query.py -q` 确认红。
- [x] 用已查证的 with_structured_output(method='function_calling') 生成候选，保留 thinking disabled；以系统固定 raw_query、白名单过滤及保真检查构造最终 QueryPlan。12秒有界改写，失败记录 fallback；同义词表最多每概念3个扩展，BM25 保留原 SKU，dense 不堆关键词。
- [x] 同命令通过；对 docs/samples/ch04-query.jsonl 跑真实改写验证并记结果，纯 Prompt 迭代在校准/开发样例上进行。不得使用正式保留测试调 Prompt。
- [x] 追记误判和迭代；特别记录 knowledge/business/chat 误路由样例及防旁路所需信号。

### Task 6：SiliconFlow 重排与四策略检索编排

**Files:** Create app/kb/reranker.py、tests/test_kb_reranker.py、tests/test_kb_strategies.py；Modify app/kb/retriever.py、app/kb/embedding.py、tests/test_kb_embedding.py、pyproject.toml、uv.lock、.env.example（httpx 从 dev 提升运行依赖，保留0.28.1）。

**Interfaces:** rerank(query: str, documents: Sequence[str], *, top_n: int=10) -> list[tuple[int,float]]；retrieve_result(query: str, *, strategy: RetrievalStrategy|None=None, plan: QueryPlan|None=None, filters: RetrievalFilters|None=None) -> RetrievalResult。search_hits 的旧参数及 retrieve(query)->str 保留；legacy corpus 走原 dense 路线。Hit 排序分数与 score_type 一致，最终 candidate_ids 保存重排前至多100 ID。

- [x] 用 httpx MockTransport 写测试，断言 POST /rerank 的指定模型/query/documents/top_n=10/return_documents=false；空文档不调用网络，响应乱序 index 正确映射；重复、越界、NaN、错误 JSON、401/402/429/5xx/超时明确分类，不回显认证头。Run `uv run pytest tests/test_kb_reranker.py tests/test_kb_strategies.py tests/test_kb_retriever.py -q` 确认新增用例红。
- [x] 实现 rerank 单次HTTP、15秒超时、无 provider 内部重试；由工具执行器统一控制重试次数。固定 Top-10，不把返回 relevance_score 称为概率；缺 key 明确报配置错误，不访问 .env 原文。
- [x] Context7核对已有OpenAI SDK embedding客户端的timeout/max_retries后，设置单请求10秒、SDK重试0，仅保留既有应用层最多3次总尝试及0.5/1秒退避；用fake SDK断言确切请求次数和超时值，保留批量/乱序index/维度回归。Run `uv run pytest tests/test_kb_embedding.py -q`，真实网络延迟不作为单测。
- [x] 实现四策略：bm25 不调用 embedding；dense 一次 embed；hybrid/hybrid_rerank 使用 Task 3；去重候选回 MySQL 并跳过缺失 ID。plan 已传入时不再改写；空白原话短路。纯策略不复用旧0.5余弦过滤以免误切 RRF/BM25。
- [x] 运行同命令全绿；另用少量合成文本真实 rerank 探针确认 index/分数及延迟（无密钥输出），失败停在当前选型，不换模型。追记依赖锁未升级和实际结果。

### Task 7：证据排布、生成自评、引用与禁止承诺

**Files:** Create app/kb/answering.py、tests/test_kb_answering.py；Modify app/prompts.py、app/llm.py、tests/test_prompts.py；使用 Task 2 answer 样例。

**Interfaces:** make_evidence(hits: Sequence[Hit]) -> list[Evidence]（先按相关性编号）；reorder_evidence(evidence: Sequence[Evidence]) -> list[Evidence]；async generate_answer(plan: QueryPlan,result: RetrievalResult,*,model,apply_score_gate:bool=True) -> AnswerDecision；validate_answer(decision: AnswerDecision,evidence: Sequence[Evidence]) -> AnswerDecision。decision.reason 是内部原因，最终拒答正文由固定安全文案渲染。

- [x] 写 test_order_keeps_citation_numbers、test_no_hit_or_low_rerank_score_short_circuits_model、test_unknown_or_missing_citation_blocks_draft、test_missing_condition_or_conflict_refuses、test_payment_eta_promise_blocked_but_negated_promise_allowed。示例断言：`reorder_evidence(1..10)` 的编号为 `[1,3,5,7,9,10,8,6,4,2]`；不能只把「不承诺3天到账」因含数字误杀。
- [x] Run `uv run pytest tests/test_kb_answering.py tests/test_prompts.py -q` 确认新增测试红；实现纯函数及结构化 AnswerDecision，method='function_calling'、45秒预算；不可答/格式错不泄草稿，provider失败抛可区分服务异常。
- [x] 校验 used_citation_numbers 与正文 [N] 集合一致、属于本轮证据；事实段落需引用。生成 Prompt 隔离文档指令；允许说明一般规则，禁止兑现到账/赔付/必达/真伪/必然合身等承诺。score_gate 只在 hybrid_rerank 且开启时检查阈值，其他分数不可套。
- [x] 同命令通过；用真实模型跑开发 answer 样例，逐条记录自评、拒答理由、引用及禁止承诺，迭代不改保留测试集。追记 Task 7，测试不能只验证 Prompt 包含关键词。

### Task 8：工具 artifact、隐藏 QueryPlan 注入及执行器兼容

**Files:** Modify app/tools.py、app/executor.py、tests/test_tools.py、tests/test_executor.py；Create tests/test_kb_tool_artifacts.py。

**Interfaces:** query_faq 使用 content_and_artifact，隐藏 query_plan: Annotated[QueryPlan|None,InjectedToolArg] 默认 None；模型可见 schema 仍只有 keyword。execute_tool_result(name: str,args: dict,*,context: Mapping|None=None,tool_call_id: str|None=None) -> ToolExecutionResult(content: str,artifact: RetrievalResult|None,status: str)；execute_tool 为 content 兼容包装。

- [x] 写 test_plain_invoke_still_returns_original_string、test_toolcall_contains_retrieval_artifact、test_plan_injection_hidden_and_context_wins、test_legacy_execute_tool_returns_str。ToolCall 显式 type='tool_call'，不能误用普通参数 dict 后希望得到 artifact。Run `uv run pytest tests/test_kb_tool_artifacts.py tests/test_tools.py tests/test_executor.py -q` 确认新增测试红。
- [x] 执行器新增结构化路径，保留原错误文案及校验错误不重试；FAQ 90秒/次，其它工具10秒/次，总尝试仍最多2。各阶段内部超时受预算约束；FAQ 纯检索不含45秒生成。上下文在模型参数后覆盖；未知工具、provider异常、timeout均归一但不伪装无召回。
- [x] 用 threading.Event 控制第一次 timeout 后晚完成、第二次成功且不同 artifact，断言 `result.artifact == second_artifact` 且 `pool_writes == []`；任务不触碰问题池或全局引用。并发两会话 artifact 不能串；create_ticket 注入及原测试全绿。
- [x] 追记接口兼容和超时证据，禁止用“线程可取消”作设计假设。

### Task 9：聊天质量关口、引用 SSE 和低置信度入池

**Files:** Modify app/chat_service.py、app/dependencies.py、app/main.py、tests/test_chat_service.py、tests/test_chat_api.py；Create tests/test_chat_grounding.py。

**Interfaces:** stream_chat 原签名保留，可增加仅关键字依赖钩子用于注入 planner/answerer；内部使用 Task 5 QueryPlan、Task 8 ToolExecutionResult、Task 7 AnswerDecision、Task 1 record_low_confidence。citations 帧 items 字段为 number/chunk_id/section_path/text/original_url，仅发布实际使用引用。

- [x] 写 fake 分阶段模型测试：首轮猜测正文不进入 token；knowledge 意图未调用 FAQ 时系统补一次；已有 business 工具不追加第二次；命中但缺证据 source=self_check；空召回/低分 source=retrieval_low_conf；问题池保留原话、真实conversation_id而非模型keyword。
- [x] Run `uv run pytest tests/test_chat_grounding.py tests/test_chat_service.py tests/test_chat_api.py tests/test_session_store.py -q` 确认新增用例红；实现只在执行器最终结果后发布引用/入池。生成通过后先 citations 再按片段 token/done；拒答一次入池后再发送固定文案；池写失败发送 error，无“已记录”成功声称。
- [x] 核心事件断言 `frame_types.index('citations') < frame_types.index('token')`；拒答路径 `pool_writes == [(raw_question, real_conversation_id, 'self_check', reason)]`，任何 `draft_text` 均不在已发token中。模拟用户中途断开，不能触发第二次入池或把未校验草稿持久化成最终回答。
- [x] 测试 provider/数据库失败不当作知识缺口；验证首轮route.content、最终无引用草稿及其它工具到账保证都不能先流出。明确闲聊无引用可正常结束；知识回答必须有证据决策。任何独立的结构化输出调用不执行第二个业务工具。
- [x] 完整检查 user → assistant tool_call → tool → final 的持久化顺序、create_ticket 注入、历史裁剪和 tool_choice='none' 原保障。Run 同命令全绿并追记 Task 9。

### Task 10：检索自测 API、当前语料统计与原文定位接口

**Files:** Modify app/api/kb.py、tests/test_kb_api.py；Create tests/test_kb_citations_api.py。

**Interfaces:** SearchRequest.route 支持四策略，top_k 范围1..50（仅展示裁剪，不改变底层各路50）；filters 为 Task 1 类型。GET /api/kb/chunks/{chunk_id} 返回 MySQL id/questions/answer/section_path/category/content_type；original_url 由服务端生成。overview/status 使用 Task 4 目标范围计数，source 白名单以当前 get_sources() 为准。

- [x] Context7 查询 FastAPI 请求模型/路径校验及响应异常语义，匹配现有 FastAPI；写 invalid route/top_k/filter 422，chunk不存在404、MySQL不可达503、合法chunk200、原文中的脚本只是JSON文本；输入不能读取任意磁盘路径。
- [x] Run `uv run pytest tests/test_kb_citations_api.py tests/test_kb_api.py tests/test_jobs.py -q` 确认新增测试红；实现并修正旧 source 路由直接迭代 SOURCES 的假设，确保页面/CLI使用同一版本。搜索服务失败502，不回无匹配；overview仍按块降级。
- [x] 单测有已排除旧done行时，overview呈现总量与eligible量区别，不能误报 mismatch。迁移未validated时 ch04 搜索明确不可用，不偷偷搜索旧集合。
- [x] 同命令全绿，追记 Task 10。不新增 schema、任意shell作业或反馈写库接口。

### Task 11：正式评估数据与纯指标实现

**Files:** Create data/eval/ch04/cases.jsonl、split.json、app/kb/evaluation.py、tests/test_kb_evaluation.py；使用 Task 2 文档及 Task 6 检索。

**Interfaces:** EvalCase 包含 spec 全字段及 split；bind_cases(cases: Sequence[EvalCase],sources: Sequence[Source],stored: Sequence[StoredChunk]) -> list[BoundCase]；retrieval_metrics(ranked_ids: Sequence[int],relevant_ids: set[int],*,k:int) -> dict；aggregate_results(rows: Sequence[dict]) -> dict。

EvalCase/BoundCase/FaithfulnessResult 均定义在 evaluation.py，避免 runner 与 judge 循环导入。BoundCase 保留 EvalCase，并加 relevant_ids: set[int]、corpus_hash: str；FaithfulnessResult 含 facts（text:str、supported:bool、evidence_numbers:list[int]、reason:str）、supported_count:int、fact_count:int、score:float|None、error:str|None；分数由计数计算，不采信模型任意自报总分。

- [x] 数据任务先写96条标注，六桶各16，每桶前4固定校准/余12测试；不要把开发样例原句复制成保留测试。每条 related refs 按source+section+chunk fingerprint绑定，合成库外题无相关集合。人工逐条核对ground truth、难度、过滤及禁止承诺；不靠待测检索器生成正确标签。
- [x] 指标代码TDD：相关集合{2,4}、排序[9,2,4]应 Recall@1=0、Recall@2=0.5、MRR@3=0.5；空相关题检索指标为None且不计分母；重复ID不得重复计命中；绑定0匹配/多匹配/hash变化明确失败。Run `uv run pytest tests/test_kb_evaluation.py -q` 确认红后实现。
- [x] 预校验96总数、每桶/每split数、标签齐全、可答题必有相关块、不可答题无相关块、ID不重复、source存在；指标测试全绿，数据检查产出可审阅摘要。正式真实评估留到 Task 12/14，不能把数据校验当检索成绩。
- [x] 追记 Task 11；逐条标签与语料 hash 一并冻结，后续变更要改变评估版本并重报。

### Task 12：四策略 runner、Faithfulness、阈值校准与报告

**Files:** Create app/kb/evaluation_judge.py、scripts/eval_retrieval.py、tests/test_eval_retrieval_runner.py；Modify app/kb/evaluation.py；输出 reports/ch04/summary.md、details.jsonl、config.json、manual-audit.md。

**Interfaces:** judge_faithfulness(query:str,answer:str,evidence:Sequence[Evidence],*,model) -> FaithfulnessResult（逐事实 supported/unsupported、引用与理由）；judge_required_facts(answer:str,required_facts:Sequence[str],*,model) -> list[bool]（只判断标注事实是否被答案覆盖，与证据支持评审分开）；run_evaluation(*,split:str,mode:str,output_dir:Path,cache_dir:Path) -> dict；mode 为 compare/deployment；compare关闭score_gate，deployment只跑hybrid_rerank冻结配置。

- [x] fake runner TDD：同query四策略只做一次QueryPlan/embedding，纯BM25单独运行不依赖embedding；缓存键含语料/模型/Prompt/QueryPlan/过滤/策略/门控版本；judge超时计failed，拒答Faithfulness=None，全部拒答coverage=0，相关桶分母正确；test_calibrate_rejects_test_split 拒绝以test标签校准。Run `uv run pytest tests/test_eval_retrieval_runner.py tests/test_kb_evaluation.py -q` 先红后实现。
- [x] judge Prompt 用开发标注答案样例验证支持/不支持及原文注入；结构化function_calling，温度0、45秒有界调用、记录模型/逐事实结果。仅评事实支持，不把正确拒答写成完美Faithfulness。
- [x] CLI：`uv run python scripts/eval_retrieval.py --validate-only`；`... --split calibration --calibrate`；`... --split test --mode compare`；`... --split test --mode deployment --profile log/kb/ch04-calibration.json`。默认不写业务会话/池，不在第一次校准命令执行保留测试。
- [x] 校准阈值在0.1..0.9（步长0.05）及默认0.5中选拒答F1最高者，同分优先误拒答更低、再离0.5更近；评分使用校准标签，所有候选及分母落盘。冻结profile包含语料/Prompt/model/hash；测试禁止重新选阈值，hash不同明确失败。
- [x] 报告 Recall@5/@10、MRR@10、candidate Recall@50、全证据命中率，桶/难度/全局样本数，Faithfulness与coverage/误拒答/正确拒答/required_facts覆盖及服务失败数。复用同一生成代码，最多10证据；rerank最终@50标不适用。
- [x] Run fake测试全绿；真正96条校准/四策略/部署执行留至 Task 14。报告预留manual-audit明确“待实际抽查”，不得出现假通过数字。追记 Task 12。

### Task 13：聊天引用、原文跳转与单次反馈（前端 Vibe Coding）

**Files:** Modify app/static/index.html、app/static/kb.html；必要时仅新增 app/static/chat-citations.js，不修改已有用户 admin.js 改动。

**Interfaces:** 消费 Task 9 citations SSE 和 Task 10 chunk接口；每条回答独立 message key/引用map/feedback；sessionStorage key只含本页会话与message key，值为up/down。

- [x] 按用户描述直接实现引用编号按钮、来源抽屉/关闭/章节/原文/定位原文；编号通过已收到map渲染，原文使用textContent，不接模型URL。支持流式 [ 和 1] 跨片段，不影响原有加粗/换行与工具徽章。
- [x] 正常done后左下角显示👍/👎，点击一次点亮所选、显示「已反馈」、禁用两键，重绘保持状态；error/未完成不显示成功反馈。不开反馈网络请求。
- [x] /kb?chunk_id 显示/突出指定MySQL块，404友好提示；现有录入/暂存明细/作业交互保持。使用当前版本源清单与eligible统计，避免显示旧总量为新集合量。
- [x] 浏览器实测两条回复不串引用、[N]切片、开/关/再开、定位原文、非法编号不成链接、含HTML原文不执行、👍后👎不可选、新消息反馈独立、网络错误和手机窄屏。用浏览器状态/截图记录结果；不套前端TDD或code-review流程。
- [x] 追记 Task 13 与实际用户纠偏；纯前端行为不声称入池。

### Task 14：后端评审、真实迁移、四策略与聊天验收及 finish

**Files:** dev-notes/ch04.md、reports/ch04/*、docs/AI_HANDOFF.md；必要修复限已实现范围并补测试。完整演示命令写 reports/ch04/demo.md。

**Interfaces:** 使用前13项接口、固定calibration/test分割及新集合迁移清单；不新增产品功能。

- [x] 使用 requesting-code-review 按所选执行路径检查后端接口/迁移/安全/指标，前端保持用户豁免。评审问题按 receiving-code-review 核实，必要时 systematic-debugging + 失败测试复现后修复；完成就追记独立 code review 结论，不等finish补写。
- [x] Run `uv run pytest -q`；记录本轮真实通过/跳过/失败数，不沿用334。外部依赖失败单列，Fake通过不替代真实兼容验证。禁止运行具有全库清空影响的验收脚本。
- [x] 只读复查MySQL表、目标Milvus版本/健康/旧集合数；确认原生chinese/BM25/SKU隔离测试通过后，运行migrate-hybrid --dry-run，核对目标knowledge_hybrid、ID范围、源hash、排除旧行。按已批准方案运行真实迁移并验证；旧集合计数/hash和旧行不变。失败恢复只在独立测试集合模拟中断，真实迁移失败依清单续跑，不能清库。
- [x] 运行validate-only、24条校准、72条四策略测试及72条部署配置，产出真实数值和失败明细；至少每桶人工抽查2条，并核对所有高分却有冲突例，记录判断理由。预算或provider不可用时明确未完成，不用离线fake填报告。
- [x] 用单独进程环境变量激活：KB_CORPUS_VERSION=ch04、HYBRID_COLLECTION=knowledge_hybrid、KNOWLEDGE_COLLECTION=knowledge、KNOWLEDGE_STRATEGY=hybrid_rerank，启动 `uv run uvicorn app.main:app --port 8010`。旧集合配置保持knowledge以防新旧目标相撞；这是Task10发现并纠正的示例错误。不修改 .env 真实值；若profile不存在则拒绝声称已校准，使用0.5开发值的运行须明确标记。
- [x] AC：BM25查询两个相似SKU均命中正确规格；聊天回答带有效编号→弹层原文/章节→/kb原文；库外问题明确拒答，SQL只查本次唯一验收会话对应low_confidence行验证raw_question/source/reason/time；缺条件self_check及低分retrieval_low_conf均覆盖。不得输出用户真实会话内容。正常回答👍/👎一次锁定，无反馈POST。
- [x] 更新AI_HANDOFF的实际能力/配置/运行步骤/限制；demo.md提供preview、dry-run、迁移、verify、评估、服务启动、SKU提问、库外提问和回退命令。回退仅选择旧集合+dense+ch03，不drop任何集合。
- [x] 读 finishing-a-development-branch 及 verification-before-completion，核对diff/status、密钥扫描、测试和验收证据；遵循用户不提交/推送/合并/部署约束，保留可审阅工作区。逐项勾验已完成任务后追记finish，剩余问题如实记录并交付。

## 计划自审与审阅交接

已将spec各项映射到Task 1–14，Review Focus五项均有对应验证；函数名及数据流由contracts统一。发现并纠正了：新集合迁移不能复用旧done语义；请求ToolCall和普通invoke返回不同；单轮QueryPlan必须复用；正式生成比较门控一致；SOURCES由列表变版本映射时source白名单同步更新；配置激活必须显式，不能误把新schema建到旧knowledge；嵌入SDK重试不能与应用三次尝试叠加成九次；Hit移位保留旧导出；评估类型统一避免judge/runner循环导入。

计划只涉及一条检索→生成→评估闭环，前端消费其契约。未拆成可独立上线的多个项目；任务可各自验证，但真实端到端依赖任务先后。96条为数据交付，单测只验证指标/绑定逻辑，不用实现镜像测试评价文档质量。

建议 Subagent-driven：检索/迁移、执行器/会话、评估分母有多个易错边界，逐任务独立评审更易在联调前发现偏差；成本高于Native。Native由当前会话逐任务实现后集中独立后端评审，成本更低。此段为计划初稿时的选择建议；用户随后已选择Native并批准，最终Task1–14完成，以本文件最新检查点和dev-notes/ch04.md为准。

## Task14 review Ruling（2026-10-20）
自动SKU推断的product_category过滤包含通用政策(include_general=True)，显式过滤不被改写。QueryPlan.requires_knowledge与业务路由独立；带订单号的政策问题不能绕过引用/拒答关口，单个业务工具已经执行时拒答缺少证据部分。评估identity/profile/cache新增实际eligible MySQL全字段/ID及目标集合hash，并要求MySQL/Milvus一致，执行起止快照改变则报告失效中止。共享embedding失败记录三条向量策略失败行，BM25及后续样本继续，不中断整场。


## Finish Ruling（2026-10-21）

后续用户批准的v2修复与知识扩写另见dev-notes/ch04.md阶段30起；剩余校准、真实评估、聊天验收和文档同步继续沿用Task14。新增路由回归已RED：知识尺寸误选query_product，改为执行前选FAQ；business意图及单轮上限不变。最终集合knowledge_hybrid_ch04_v2_final，禁止将初轮v2草稿集合或v1报告作为当前成果。

v2最终完成：559项通过、五表快照不变；24校准冻结.25、288四策略、72部署无服务/评审失败；15日常及真实引用/双入口入池通过。最终语料124块、历史数据哈希未改，新增路由独立只读复核无Critical/Important。当前报告reports/ch04/summary.md、运行维护docs/OPERATIONS.md，详细失败/纠正/finish见dev-notes/ch04.md阶段30–37。保持当前分支未提交，不开启新范围。
用户已选择当前分支并要求未经明确授权不提交/推送/合并，采用keep-as-is收尾，不重复展示集成菜单，不删未提交工作所需的gitignored执行账本。计划中的提交步骤由用户授权约束替代。真实环境未故意杀服务制造中断：断点/flush失败/恢复由隔离事务和fake服务用例验证，实际迁移只写新集合且逐ID与历史哈希验证；不以破坏真实数据补验。前端依用户Vibe豁免，Prompt/数据按标注样例/评估验证。
