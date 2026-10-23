# MewHelp AI 开发交接文档（面向 OpenAI Codex）

> 2026-10-23 ch06功能与本章验收完成：Tasks1–8，最终700 passed；真实意图/严格JSON75/75、订单9/9，问题字面74/75、有限语义75/75。原生interrupt/Command重启选单续跑、聊天卡片/固定原因表单、真实合成工单取消0/确认1/重复1已验证；主力默认、降级仅惰性配置种子。质量限制：12政策题扩写未提升召回（均83.33%），短指代可能低分拒答。产品在同一工作树，已切分支ch06-router提交并推送，PR #6（base ch05-workflow-agent）待评审合并；8016为演示端口，尚未合并、部署。先读CH06_CURRENT_PROGRESS与产品reports/ch06/summary.md、demo.md；统一运维已更新。本条覆盖下文中断时“进行中”。


> 2026-10-23 ch06进行中，未finish：Tasks1–7完成；两项用户裁决为原生LangGraph interrupt/Command订单续跑、意图降级仅配置种子。独立后端review3项Important已一轮修复、相关52passed；修后全量及真实75条最终验收/新校准/真实浏览器仍需核实。产品仍在.worktrees/ch05-workflow-agent，未提交/合并。先读 docs/CH06_CURRENT_PROGRESS.md 和 dev-notes/ch06.md，不沿用下文ch05通过数作为ch06结果。


> 2026-10-23故障修复已验收：637项全量通过，四策略检索API和知识流式正常；125有效块双写一致。旧profile因知识变更正确失效，独立新校准文件为ch05工作树log/ch05/calibration-20261023-final.json（.25，严格匹配）。启动请先按OPERATIONS准备/核验新profile；报告与剩余Minor见工作树reports/ch05/bugfix-20261023/summary.md。ch05已提交并推送至分支ch05-workflow-agent，PR #5（base ch04-retrieval-quality）待评审合并；尚未合并、部署。

> 2026-10-22 ch05功能与验证完成：Tasks1–8及最终评审修复/真实验收已完成，630项全量通过、分类28/28、Agent8/8。唯一产品实现位于`E:/MewHelp AI/.worktrees/ch05-workflow-agent`；原根仅同步文档。先读[CH05当前状态](CH05_CURRENT_PROGRESS.md)、[交付报告](../reports/ch05/summary.md)及dev-notes/ch05.md。保留未提交工作树，未commit/push/merge/deploy；Task9官方关闭见专属账本。

整个项目的启动、检查、运行维护、停止与整套备份恢复统一见[运行维护手册](OPERATIONS.md)。现行手册统一覆盖截至ch06的准备、启动、停止、维护、校准及备份恢复。下文旧章节命令仅保留历史，不作为当前启动依据；不按章节拼接操作。

> 2026-10-21 ch04 v2修复/扩写已finish：最新559项通过；当前知识124块，最终集合knowledge_hybrid_ch04_v2_final、阈值0.25。先读CH04_CURRENT_PROGRESS.md及dev-notes/ch04.md阶段30起。旧v1 finish/65块/.65仅历史；以下ch03主体也是历史。未提交/推送/合并/DDL。

> 生成日期：2026-10-19。生成方式：基于仓库源码、Git 历史、`pyproject.toml`/`uv.lock`、`.env.example`、`docs/superpowers/` 下三章 spec 与 plan、`dev-notes/ch01~03.md` 的实际核查撰写。
> 原则：仅记录有代码、配置、测试或设计文档支持的事实；无法核实的标注 **待确认**。
> 本仓库**没有 README.md 和 CLAUDE.md**（已核实不存在），本文档即外部 AI 的唯一入口文档。

---

## ch04 当前架构与交付补充（2026-10-21 v2）

- query.py单轮理解；milvus.py原生BM25/chinese与dense各Top50、hybrid_search/RRF60；指定SiliconFlow bge-reranker-v2-m3取Top10；answering.py证据自评/首尾排布/引用/限一次格式修复/禁止承诺；chat_service.py知识意图执行前选FAQ，校验后才发布正文及引用，拒答落既有low_confidence_questions。business工具仍单轮一次。
- MySQL原文权威，Milvus为BM25必须存检索文本与标量副本（旧“只存ID与向量”是ch03历史）。最终124块，MySQL181总/57历史排除。原172行及三个旧集合完整哈希保留，见reports/ch04/v2/migration.json。
- 激活必须五个环境变量：KB_CORPUS_VERSION=ch04、KNOWLEDGE_COLLECTION=knowledge、HYBRID_COLLECTION=knowledge_hybrid_ch04_v2_final、KNOWLEDGE_STRATEGY=hybrid_rerank、KB_MIGRATION_MANIFEST=log/kb/ch04-v2-final-migration.json；RERANK_MIN_SCORE从log/kb/ch04-v2-final-calibration.json读0.25。不要修改.env，默认ch03保留用于回退。
- 当前真实72部署59回答、Faithfulness约.99758、误拒答2/60、正确拒答11/12；15日常回归符合标签。四策略288行及最终核查完成，无服务/评审失败；四策略MRR@10=.92986/.85782/.92333/.94405。独立路由增量复核无Critical/Important。报告入口reports/ch04/summary.md，人工核查v2/manual-audit.md。
- 前端已有可点引用→MySQL原文/章节→/kb定位及一次👍👎，仅sessionStorage，无反馈POST。Docker必须两个Compose文件启用external-etcd；没有升级依赖、DDL或清库。/admin、/kb、/api仍是演示无鉴权边界。

---

## 1. 项目目标、业务需求与当前实现范围

**项目**：MewHelp AI —— 面向一家二手古着店（复古服饰电商，店铺名 "MewHelp 商城"，客服人设 "小喵"）的**智能客服演示项目**。前端为聊天页，后端 FastAPI + DeepSeek LLM，逐迭代叠加能力：

| 章节 | 主题 | 状态 |
|---|---|---|
| ch01 | 纯对话：SSE 流式聊天、会话历史、token 预算裁剪、`/extract` 售后信息结构化提取 | ✅ 已交付并合入 master（PR #1） |
| ch02 | Function Calling：5 个业务工具、统一执行器（超时/重试/契约）、会话/消息/工单落 MySQL、`create_ticket` 的 `conversation_id` 系统注入 | ✅ 已交付，前 13 个提交合入 ch01-pure-chat（PR #2）；Task 11 追加提交在 ch02 分支待合并 |
| ch03 | 知识库：Markdown 结构感知切分、SiliconFlow BGE-M3 嵌入、Milvus 向量检索替换 `query_faq` 关键词查表、MySQL/Milvus 双写断点续跑、对话挖知识 job、`/kb` `/admin` 管理页、make 白名单作业 | ✅ 已交付并通过 AC1–AC4 验收，在 `ch02-function-calling` 分支待合并（PR #3 待创建） |

**业务需求**（来自三章 spec §1/§12，均有实现与测试对应）：
1. 聊天页流式问答，带工具调用轨迹徽章；
2. 模型自行决定调用工具：查订单/商品/物流（Mock 数据）、查 FAQ（现为向量语义检索）、转人工建工单（真实写库）；
3. 知识库建库链：文档切分入库 + 从历史对话挖知识，双写 MySQL/Milvus，中断可续跑；
4. 浏览器后台 `/kb`（手工录入、切块预览、向量化补漏、检索自测、作业按钮）与 `/admin` 聚合首页。

**验收结论**（dev-notes/ch03.md 阶段 9 有完整留痕）：AC1「邮费是多少」换说法召回运费说明（Top-1 余弦 0.6802）；AC2 中断建库后重跑补齐且无重复；AC3 页面录入→向量化→检索自测全通；AC4 作业确认/单飞/日志回显全通。

---

## 2. 技术栈、依赖版本、目录结构与模块职责

### 2.1 运行环境（已实测核实）

- Windows 11 + PowerShell；Python **3.12.10**；包管理 **uv**（`uv sync` / `uv run`）。
- **MySQL 8**（本机 `127.0.0.1:3306`，库名 `melhelp`，root；密码只存本地 `.env` 的 `DB_URL`）。
- **Milvus Standalone v3.0.0**，Docker 单容器（embedded etcd + local storage），端口 19530/9091/2379，compose 在 `deploy/milvus/`，数据卷 `deploy/milvus/volumes/`（gitignored）。
- **GNU Make 3.81**（winget 安装 GnuWin32.Make，路径 `C:\Program Files (x86)\GnuWin32\bin`）。make 不在 PATH 时 `.env` 的 `MAKE_EXE` 填绝对路径。
- LLM：**DeepSeek**（`deepseek-flash`，`extra_body={"thinking":{"type":"disabled"}}`），OpenAI 兼容协议。
- 嵌入：**SiliconFlow `BAAI/bge-m3`**（固定 1024 维，单请求上限 64 条，项目批量 32）。

### 2.2 依赖版本（`uv.lock` 锁定，venv 实测）

| 包 | 版本 | 用途 |
|---|---|---|
| fastapi | 0.143.0 | Web 框架 |
| langchain / langchain-core / langchain-openai | 1.4.5 / 1.6.10 / 1.7.1 | 工具定义、消息、ChatOpenAI |
| openai | 3.31.0 | **SDK 直连** SiliconFlow 嵌入端点（不用 LangChain 的 Embeddings） |
| pydantic-settings | 2.15.0 | 配置 |
| pymilvus | 3.0.2 | Milvus 客户端（对应 server v3.0.0） |
| pymysql + sqlalchemy | 1.2.3 + 2.1.4 | 同步 ORM |
| uvicorn[standard] | 0.54.0 | ASGI 服务 |
| dev: httpx / pytest / pytest-asyncio | 0.28.1 / 9.1.1 / 1.4.0 | 测试 |

pytest 配置：`asyncio_mode=auto`；marker `integration`（需真实 Milvus 的用例）。

### 2.3 目录结构与模块职责

```
app/
  config.py          # Settings（pydantic-settings，.env 驱动；所有可调参数见 §6.2）
  db.py              # 惰性 engine + sessionmaker 单例（lru_cache）
  models.py          # SQLAlchemy 映射既有 6 表（零 DDL，只映射）
  llm.py             # build_chat_model / build_extractor（with_structured_output）
  dependencies.py    # FastAPI 依赖注入（get_chat_model / get_extractor）
  prompts.py         # SYSTEM_TEMPLATE（客服人设 + 工具使用规范）
  schemas.py         # ChatRequest / ExtractRequest / AfterSalesInfo
  history.py         # trim_history（委托 langchain trim_messages，中文口径）+ SessionStore 协议 + InMemory 实现
  session_store.py   # MySQLSessionStore：get_or_create_conversation / get_history / append_message；毒历史防御
  chat_service.py    # stream_chat 编排：模型定工具→执行→回灌收敛，SSE 帧产出
  executor.py        # execute_tool：永不抛异常契约、超时重试 1 次、context 注入（conversation_id 覆盖伪造）
  tools.py           # 5 个 @tool + TOOL_REGISTRY；query_faq 委托 kb.retriever；create_ticket 注入参数
  seed.py            # faq 13 条种子幂等灌入（uv run python -m app.seed）
  main.py            # create_app：路由 + /static 挂载 + app.state.session_store
  api/
    kb.py            # /api/kb/*：overview/source/preview/ingest/vectorize/staging/search
    jobs.py          # /api/jobs/*：启动(202/403/404/409)、状态、日志尾部
  core/
    jobs.py          # 白名单作业运行器：JOBS 7 目标、argv 写死、shell=False、全局单飞、日志
  kb/
    documents.py     # 纯函数：split_sections + build_chunks + 元数据/指纹/分类/关键条款（唯一切块实现）
    sources.py       # SOURCES 建库材料清单（3 份 md，路径白名单）
    embedding.py     # embed_texts：批量 32、3 次尝试、1024 维校验、缺 key 明确报错
    milvus.py        # MilvusClient 薄封装：ensure_collection/upsert/search/flush/drop/count/list_ids/has_collection
    store.py         # MySQL 仓储：knowledge_chunks + qa_extraction_staging 读写、指纹/问题集合、挖掘会话查询
    dualwrite.py     # 双写状态机：vectorize_pending（先 ensure_collection）、rebuild/reset、consistency_snapshot
    retriever.py     # search_hits（阈值过滤保序）+ retrieve（拼「问题→答案」文本）
    ingest.py        # 建库 CLI：preview/docs/faq/vectorize/rebuild/build/status/reset
    mining.py        # 对话挖掘：LLM 抽取→暂存→精确+向量去重→入库；extracted 断点恢复；零抽取哨兵
    seed_conversations.py  # 3 组演示会话（seed-ch03-* 前缀，幂等）
  static/            # index.html(聊天页) admin.html kb.html admin.js
tests/               # 29 个测试文件，334 个用例（见 §9）
data/kb/             # 3 份知识源 Markdown（退货政策/商品FAQ/售后手册）
deploy/milvus/       # docker-compose.yml + embedEtcd.yaml + user.yaml + volumes/(gitignored)
scripts/             # eval_samples.py(ch02 样例评估) eval_mining.py(ch03 挖掘评估)
docs/superpowers/    # 三章 spec + 三章 plan（历史决策的权威来源）
docs/samples/        # 三章标注样例集
dev-notes/           # ch01~ch03.md 开发留痕（每阶段四要素）
Makefile             # 7 个 kb/seed 目标
log/acceptance/      # 作业日志（gitignored）
```

**开发方法论说明**：本项目用 Superpowers 流程（brainstorm→spec→plan→subagent 实现+独立评审+fix loop→终审→验收）开发，`dev-notes/` 与 spec 中的 Ruling/日期注记是理解"为什么这么写"的权威材料，**改代码前先读对应章节的 spec 与 dev-notes**。

---

## 3. 功能完成度

### 已完成（有测试或验收证据）
- **ch01 全部**：`/health`、`/`(聊天页)、`POST /chat` SSE 流式、`POST /extract` 结构化提取、历史裁剪、内存 store（测试用）。
- **ch02 全部**：五工具（query_order/query_product/query_logistics 为 Mock 随机+同参缓存；query_faq 现为向量检索；create_ticket 真实写库）；执行器契约（永不抛异常、超时 10s 重试 1 次）；MySQL 会话存储与历史重建（含 tool_calls JSON 三键往返、毒历史防御）；单轮工具调用三层保证；`create_ticket.conversation_id` 用 `InjectedToolArg` 系统注入。
- **ch03 全部**：
  - 文档切分：标题层级结构感知、目标 500 字/重叠 ≤100、句号边界裁剪、表格按行切且每块复制表头、关键条款规则识别（9 条）；
  - 双写：MySQL `pending`→嵌入→Milvus upsert(`str(id)` 主键)→flush→`done`；失败保持 pending；重跑收敛；`rebuild`(保留原文重刷向量)/`reset`(破坏性清空)；
  - 一致性核对：≤5000 行精确 id 比对，>5000 仅数量比对，服务不可达=`unknown`，集合未创建但两边皆空=`consistent(集合未创建)`；
  - 检索：dense 单路，Top-K=`FAQ_MAX_RESULTS`(3)，阈值 `KNOWLEDGE_MIN_SCORE`(0.5，验收校准过)，空 query 短路，异常冒泡不伪装"无匹配"；
  - 挖掘：`kb-mine` 一次/循环两种模式、`extracted` 断点恢复（每轮 ≤200 行）、零抽取写 discarded 哨兵防重复调用 LLM、精确归一化 + 向量 Top-1≥0.92 双重去重、失败回滚该批暂存；
  - 页面：`/kb` 六块（库存闸条/手工录入/建库材料/挖掘暂存/向量化双写/检索自测/最近入库）、`/admin` 聚合卡、共用 `admin.js` 导航；所有动态内容用 `textContent` 渲染（无存储 XSS）；
  - 作业：7 个 make 目标白名单，heavy（kb-mine/kb-reset）需 confirm，全局单飞，日志落 `log/acceptance/<job>.log`，子进程注入 PYTHONUTF8/UNBUFFERED。
- **测试基线**：`uv run pytest -q` → **334 passed**（Milvus 在线时；Milvus 停机时 2 个 integration 用例 skip，预期 332 passed——未实测，待确认）。

### 进行中
- 无。

### 尚未开发（spec §17 明确"不做"或终审延后）
- 关键词召回、混合检索、重排（`/kb` 检索自测的 route 下拉预留了 `dense` 之外的空位）；
- Agent Loop / 多轮工具调用；
- 后台登录与多用户权限（`/admin` `/kb` `/api/*` 完全不鉴权）；
- 作业历史持久化与定时调度（作业状态仅内存，重启即丢）；
- Langfuse 等可观测性；
- ch04 及之后的 spec **不存在**，下一阶段范围待产品侧定义（**待确认**）。

---

## 4. 核心架构

### 4.1 聊天链路（ch01+ch02）
```
POST /chat {session_id, message}
→ stream_chat(session_id, message, store=MySQLSessionStore, model=ChatOpenAI)
   1) 载历史 + user 消息落库
   2) [System] + trim_history(历史, 4000 token) + [Human]
   3) 调用① model.bind_tools(五工具, parallel_tool_calls=False).astream
      ├─ content chunk → SSE {"type":"token"}
      └─ chunk 累加 → 完整 tool_calls
   4) 无 tool_calls → 落库 assistant → {"type":"done"}
   5) 有 tool_calls（只执行第一个，双保险）：
      context = {"conversation_id": store.get_or_create_conversation(session_id)}  # start 帧之前取
      SSE {"type":"tool","phase":"start"}
      await asyncio.to_thread(execute_tool, name, args, context=context)  # 永不抛异常
      SSE {"type":"tool","phase":"end"}
      落库 AIMessage(tool_calls) + ToolMessage
   6) 调用② bind_tools(..., tool_choice="none").astream → token 帧 → 落库 → done
异常 → {"type":"error"} 并 return
```
- **单轮约束三层**：请求参数抑制并行 → 只执行第一个 tool_call → 调用② `tool_choice="none"` 服务器侧拒绝再调（ch02 Ruling 3：thinking-disabled DeepSeek 在无工具上下文时会吐 DSML 调用标记当正文）。
- **conversation_id 注入**：`InjectedToolArg` 把参数从模型可见 schema 摘除；`execute_tool` 里 `{**args, **context}` context 在后覆盖模型伪造值（ch02 Ruling 5：盲合并，未声明该参数的工具由 pydantic 忽略）。

### 4.2 知识库链路（ch03）
```
离线：data/kb/*.md ──build_chunks(纯函数)──> ChunkCandidate ──指纹查重──> MySQL(pending)
                                                                    │ vectorize_pending（可反复重跑）
                                                     embed_texts(BGE-M3) → Milvus upsert(str(id))
                                                     → flush → MySQL mark_done(vector_id=str(id))
faq 表 13 条 ──ingest_faq──> 同上（content_type=商品FAQ）
对话挖掘 kb-mine ──> DeepSeek 抽 QA 对(带 conversation_id) → staging(extracted)
   → resume 旧 extracted 行 → 新会话增量（staging_tail < max_message_id）
   → 归一化精确去重 + 向量 Top-1 ≥0.92 去重 → kept 入库(pending) → 立即 vectorize
   → 零抽取会话写 discarded 哨兵（防 --interval 模式重复烧钱）
在线：query_faq(keyword) → retrieve → embed(1次) → Milvus Top-K → 过滤 score<0.5
   → 回 MySQL 取原文 → "N. 问题\n→ 答案" 拼接；契约与 ch02 完全一致
```
- **切块只有一份实现**：页面预览、页面手工录入、离线 CLI 全部调 `kb.documents.build_chunks`；材料清单只有 `kb/sources.SOURCES` 一处定义。
- **降级语义**（页面与 API 一致）：MySQL 挂 → `mysql.ok=false` 数值 null，材料清单照常；Milvus 挂 → `consistency=unknown`（不是 mismatch）、检索 502（不伪装"无匹配"）；集合缺失但服务可达 → `ok=true,count=0,note="集合未创建"`。

### 4.3 数据库结构（6 张表，**全部由用户手工建表，仓库内无 DDL 脚本**）

> ⚠️ 重要：ORM 只映射不建表（`git grep create_all` 为空）。在新环境部署需按下列定义手工建表（依据：`app/models.py` + ch03 spec §3 实测 information_schema）。

| 表 | 关键列 |
|---|---|
| `conversations` | id BIGINT UNSIGNED AI PK; user_id VARCHAR(64) NOT NULL(索引); status ENUM('进行中','已转人工','已结束') DEFAULT '进行中'; created_at/updated_at DATETIME DEFAULT CURRENT_TIMESTAMP(后者 ON UPDATE) |
| `messages` | id PK AI; conversation_id BIGINT UNSIGNED FK→conversations.id; role ENUM('user','assistant','tool'); content TEXT NULL; tool_calls JSON NULL; tool_call_id VARCHAR(64) NULL; created_at |
| `faq` | id PK AI; question VARCHAR(512); answer TEXT; category VARCHAR(64)(索引); created_at/updated_at |
| `tickets` | ticket_no VARCHAR(32) PK(格式 T+YYYYMMDD+4位序号); conversation_id FK; description TEXT; ticket_type ENUM('售后','投诉','咨询'); status ENUM('待处理','已处理') DEFAULT '待处理'; created_at |
| `knowledge_chunks` | id BIGINT UNSIGNED AI PK; category VARCHAR(255)(索引); questions TEXT; answer TEXT; section_path VARCHAR(512) NULL; content_type VARCHAR(32) NULL; is_key_clause TINYINT(1) DEFAULT 0; prev_chunk_id/next_chunk_id BIGINT UNSIGNED NULL(自引用 FK,ON DELETE SET NULL,索引); vector_id VARCHAR(64) NULL; vectorize_status ENUM('pending','done') DEFAULT 'pending'(索引); created_at/updated_at |
| `qa_extraction_staging` | id PK AI; batch_no VARCHAR(64)(索引); source_ref VARCHAR(255) NULL(格式 `conv:{id}:msgs:{first}-{last}`); question TEXT; answer TEXT; status ENUM('extracted','kept','discarded') DEFAULT 'extracted'(索引); created_at |

### 4.4 API 接口一览

| 方法与路径 | 说明 |
|---|---|
| GET `/health` | 存活检查 |
| GET `/` / `/admin` / `/kb` | 聊天页 / 后台聚合页 / 知识库录入页 |
| `/static/*` | 静态资源（admin.js 等） |
| POST `/chat` | SSE 流式对话；帧：`{"type":"token"\|"tool"(name,phase)\|"done"\|"error"}` |
| POST `/extract` | 售后信息结构化提取（AfterSalesInfo） |
| GET `/api/kb/overview` | mysql/milvus/consistency/staging/sources/recent(含 created_at)/jobs 七块 |
| GET `/api/kb/source?path=` | 取建库材料原文；路径必须在 SOURCES 白名单内否则 404 |
| POST `/api/kb/preview` | dry-run 切块预览，不写库不调嵌入 |
| POST `/api/kb/ingest` | 手工录入 Markdown → 入库 pending；`vectorize:true` 顺手向量化，失败 502「已入库 N 块(pending)，补跑一次即可」不回滚；>200KB → 413 |
| POST `/api/kb/vectorize` | 补 pending；失败 502 |
| GET `/api/kb/staging?status=&limit=` | 暂存表三态查询 |
| POST `/api/kb/search` | 检索自测（route 仅 `dense`，未知 422）；返回 threshold/hits/message |
| GET `/api/jobs` · POST `/api/jobs/{name}` · GET `/api/jobs/{name}` | 作业注册表/启动(202；404 未知/403 heavy 未 confirm/409 单飞)/状态+日志尾部 |

### 4.5 CLI 与 make 目标

```
uv run python -m app.seed                     # faq 种子（幂等）
uv run python -m app.kb.ingest preview|docs|faq|vectorize|rebuild|build|status|reset --yes
uv run python -m app.kb.mining --once [--limit N] | --interval 秒
uv run python -m app.kb.seed_conversations    # 演示会话（幂等）
make kb-preview kb-build seed-conv kb-mine kb-vectorize kb-rebuild kb-reset
uv run python scripts/eval_mining.py          # 真实 DeepSeek 挖掘 Prompt 评估
uv run python scripts/eval_samples.py         # ch02 样例评估
```

---

## 5. 已确定的技术决策与约束（及原因）

改这些决策前先读对应 spec 注记 / dev-notes：

1. **零 DDL**：不建表、不改表、无 `create_all`；表由 DBA/用户手工维护（三章 spec 一致约束）。
2. **密钥只进 `.env`**（gitignored）：MySQL 密码、DeepSeek key、SiliconFlow key、GitHub PAT 任何情况不进跟踪文件；`.env.example` 只放占位符（ch02 Ruling 1）。
3. **Milvus 单容器形态**：官方三容器 compose 的 `minio/minio` 镜像已从 Docker Hub 删除，故用 `ETCD_USE_EMBED=true` + `COMMON_STORAGETYPE=local` 单容器（ch03 Ruling，spec §10 日期注记）。代价：仅适合单机演示。
4. **Milvus 只存 `id`+向量**：`id` 为 VARCHAR(64)=`str(knowledge_chunks.id)`，AUTOINDEX/COSINE，dim=1024；原文永远从 MySQL 取。
5. **`query_faq` 契约不可变**：入参 `keyword: str`、返回「N. 问题 → 答案」多行文本、无匹配文案，均与 ch02 相同（有测试钉死）。
6. **单轮工具调用**：不做 Agent Loop；调用② `tool_choice="none"`（ch02 Ruling 3，防 DSML 泄漏）。
7. **`conversation_id` 系统注入**：InjectedToolArg 摘除 + context 后合并覆盖；无默认值（漏注入当场 ValidationError 而非脏数据）。
8. **同步 SQLAlchemy**：本机毫秒级，阻塞可忽略（ch02 用户裁决）；`asyncio.to_thread` 只用于工具执行。
9. **嵌入用 openai SDK 直连**：LangChain `OpenAIEmbeddings` 默认把文本转 token 数组，兼容端点会 400（ch03 spec §11）。
10. **切块/材料清单单一来源**：`build_chunks` + `SOURCES`；预览什么样入库就什么样。
11. **指纹 = 归一化(问法)+归一化(正文)**：不含 category/section_path（同节多块共用标题会误判），只按问法会误杀（ch03 spec §6）。
12. **build 向量化未完成即非零退出且不回滚**：防「零向量却报成功」；pending 行保留补跑即可（ch03 Ruling 3）。
13. **作业只跑白名单 make 目标**：argv 模块内写死、`shell=False`、前端只传作业名——页面有按钮但没有 shell。
14. **随机三工具的同参 dict 缓存**（ch02 Ruling 2，接受偏差：无淘汰，ch03 长驻进程需换 lru_cache）。
15. **`staging_tail` 增量口径**：`source_ref` 记录会话已挖到的最大 message id；同会话新消息会整段重抽，靠去重兜底。
16. **重试口径**：嵌入批失败最多 3 次总尝试（0.5s/1s 退避）；工具超时重试 1 次。

---

## 6. 本地环境配置与操作命令

### 6.1 从零搭建（新机器）

1. **前置**：Python 3.12+、uv、MySQL 8、Docker Desktop、GNU Make（Windows 用 `winget install GnuWin32.Make` 后把 bin 加入 PATH，或在 `.env` 设 `MAKE_EXE` 绝对路径）。
2. `uv sync` 安装依赖。
3. **建库**：MySQL 中创建 `melhelp` 库并按 §4.3 手工建 6 张表（**仓库无 DDL 脚本，这是唯一需要手工做的数据库步骤**——待确认：是否有原始 DDL 备份文件在仓库外）。
4. `copy .env.example .env`，填：`LLM_API_KEY`（DeepSeek）、`DB_URL`（含密码）、`EMBEDDING_API_KEY`（SiliconFlow）；其余有默认值。
5. 启动 Milvus：`docker compose -f deploy/milvus/docker-compose.yml up -d`（健康检查 ~90s）。
6. `uv run python -m app.seed`（faq 13 条）。
7. 建知识库：`make kb-build`（或分步 `kb-preview` → `docs`/`faq` → `vectorize`）。
8. 启动：`uv run uvicorn app.main:app --reload`，访问 `http://127.0.0.1:8000/`。

### 6.2 全部配置项（`.env.example` 为准）

LLM：`LLM_BASE_URL/LLM_API_KEY/LLM_MODEL/LLM_EXTRA_BODY/LLM_TEMPERATURE`；对话：`HISTORY_TOKEN_BUDGET(4000)/MAX_SESSIONS(1000)/SHOP_NAME/AGENT_NAME`；DB：`DB_URL`；工具：`TOOL_TIMEOUT(10)/FAQ_MAX_RESULTS(3)`；嵌入：`EMBEDDING_BASE_URL/EMBEDDING_API_KEY/EMBEDDING_MODEL/EMBEDDING_DIM(1024,勿改)/EMBEDDING_BATCH_SIZE(32)`；Milvus：`MILVUS_URI/MILVUS_TOKEN/KNOWLEDGE_COLLECTION(knowledge)/KNOWLEDGE_MIN_SCORE(0.5)/KNOWLEDGE_DEDEDUP…(0.92)`；挖掘：`MINING_BATCH_SIZE(5)/MINING_MAX_CHARS(6000)`；建库：`KB_SOURCES_DIR(data/kb)/KB_MAX_MARKDOWN_BYTES(204800)/JOB_LOG_DIR(log/acceptance)/MAKE_EXE`。

### 6.3 测试

```
uv run pytest -q                     # 全量 334 passed（需本机 MySQL；Milvus 停机时 2 个 integration skip）
uv run pytest -m integration -v      # 仅真实 Milvus 集成用例
uv run pytest tests/test_kb_mining.py -v   # 单文件示例
```
- 测试对真实 MySQL 使用「外层事务 + SAVEPOINT」回滚，不留脏数据；`conftest.py` 用环境变量兜底必填配置，fresh clone 可直接跑。
- 测试已与 committed 数据隔离（fixture 事务内清理相关表），生产库有数据时仍绿。

### 6.4 部署注意事项
- **必须单进程**：作业运行器的单飞与状态都在进程内存，`uvicorn --workers N` 会破坏；`--reload` 下代码改动也会丢作业状态。
- **CWD 必须是仓库根**：`data/kb` 相对路径、Makefile、`log/` 均按根目录解析。
- Windows 优先：make 为 GnuWin32、脚本未在 Linux 验证（**待确认**）；`deploy/milvus/volumes/` 与 `log/` 已 gitignored。
- Milvus 数据卷随容器目录走，`docker compose down -v` 会清空向量库（MySQL 原文还在，跑 `make kb-rebuild` 可全量重刷）。

---

## 7. 已知 Bug、技术债与潜在风险

### 已知缺陷/技术债（均为终审及各任务评审记录的延后项，非阻塞，无 issue 跟踪）
1. `is_key_clause` 仅 section 级识别（整段加粗/引用才标记；混合段落漏标）；仅识别 `**` 不识别 `__`。
2. `/kb` 一致性 UI 忽略 `note`：>5000 行数量比对时仍显示"主键完全对齐"（过度声明）。
3. `KB_SOURCES_DIR` 是死配置：`sources.py` 硬编码 SOURCES，未读该设置。
4. API 入参校验薄：`top_k` 接受负数/超大值；`source_title`/`content_type` 无长度校验（超 varchar 会被 DB 拒绝）；`_persist_chunks` 把一切写错误统称"MySQL 不可达"。
5. 页面测试仅冒烟（不验证 JS 实际调用的 API 路径/请求形状）；页面打开时不从 `overview.jobs` 恢复运行中作业（另一 tab 起的作业本 tab 显示空闲，点击会 409）；瞬时网络错误会把轮询中的作业标成失败。
6. 挖掘批次失败回滚会**删除该批已 resolved(kept/discarded) 的审计行**（spec §9 承诺的永久审计对失败批次不成立——已知的取舍）。
7. `vectorize_pending` 失败重扫是 O(N²)（`iter_pending(limit=batch+seen)` 逐轮放大）。
8. `embed_texts` 对 4xx（含 402 余额不足）也重试 3 次；`EMBEDDING_BATCH_SIZE` 无 ≤64 上限校验。
9. `MiningResult` 计数不含哨兵行与恢复行（CLI 摘要可能显示"处理 0 个会话"但 staging 有新 discarded 行）；恢复哨兵识别靠问题文本精确相等；恢复每轮上限 200 行。
10. `vectorize_pending` 在 0 pending 时也会 `ensure_collection`（空库也触 Milvus，Milvus 挂则 502）。
11. 陈旧注释：`models.py` 首行仍说"四表"（现六表）；`seed.py` 注释提"LIKE 检索"（已换向量）；`milvus.py`/`documents.py` 个别 docstring 与实现不符。
12. ch02 遗留：随机工具缓存无淘汰；`append_message` 逐条写（无批量单事务接口）；store 同步调用阻塞 event loop（建议统一收敛线程池）；error 帧文案含原始异常（可能泄内部信息）；单日工单序号 >9999 会字典序撞号。
13. 仓库**无 README**（对外说明缺失，本文档承担入口职责）。

### 风险
- **凭据轮换未确认**：DeepSeek key、GitHub PAT、SiliconFlow key 均曾在聊天/agent transcript 中暴露过，多次建议轮换，**未确认已执行**。Git 跟踪文件已扫描确认无真实凭据。
- **PR #3 未创建**：ch03（含 ch02 Task 11 的 3 个提交）尚未合并到 `ch01-pure-chat`。
- 无 CI/CD；测试强依赖本机 MySQL（无容器化方案）。
- `/admin` `/kb` `/api/*` 无鉴权（spec 明确演示项目不做，但**绝不可直接公网部署**）。
- Milvus 单容器 local 存储 = 单点，无备份策略。
- PyMilvus 3.0.2 两处版本敏感用法：`IndexParams` 从 `pymilvus.milvus_client.index` 导入、`query(limit=)` 走 kwargs——升级 pymilvus 需回归 `tests/test_kb_milvus*`。

---

## 8. Git 分支状态与下一阶段计划

### 8.1 当前状态（2026-10-19 实测）

```
origin/master            c7e904e  = ch01 全部（PR #1 已合并）
origin/ch01-pure-chat    942e575  = ch01 + ch02 前 13 个提交（PR #2 已合并，合到 d5d1cec）
origin/ch03-knowledge-base 513c7b9 = ch01 + ch02 全部 + ch03 全部（ch03 工作的正确分支，已推送）
origin/ch02-function-calling 513c7b9 = 同上（历史原因仍指向 513c7b9；见下方说明）
本地 ch03-knowledge-base = origin（推荐在此分支继续开发）
本地 master / ch01-pure-chat 落后各自 origin（27/13 个提交），下次使用前先 git pull
```

- **分支模型**：master ← ch01-pure-chat ← ch03-knowledge-base 链式 PR。
- **下一步 Git 动作**：创建 **PR #3（base: `ch01-pure-chat` ← compare: `ch03-knowledge-base`）**，diff = ch02 Task 11 的 3 个提交（76ed3cf/a5ba626/89f3382）+ ch03 的 34 个提交，共 37 个。
- **origin/ch02-function-calling 说明**：早前误将 ch03 内容推到了该分支（也指向 513c7b9）。无实际危害（与 ch03 分支内容相同，无独有提交）；若要回退到 PR #2 合并点 d5d1cec 需 `git push --force-with-lease`（强推重写远端，需用户明确批准——**待确认**）。
- 开发流程约定：每章从上一章分支顶端切新分支（如 ch04 从 ch02-function-calling 或合并后的 ch01-pure-chat 切，**待确认**）；沿用 Superpowers 流程，dev-notes 逐阶段追记。

### 8.2 交接时数据快照（本机）
- `faq` 13（种子）；`knowledge_chunks` 45（docs 32 + faq 13，全部 done）；Milvus `knowledge` 45，一致性 consistent；
- `qa_extraction_staging` 5（kept 4 + 零抽取哨兵 discarded 1）；
- `conversations` 4（3 个 `seed-ch03-*` 演示会话 + 1 个浏览器验收会话 `dfa39839`）、`messages` 18、`tickets` 0。

### 8.3 下一阶段候选（无 spec，均为记录在案的延后项；范围**待确认**）
§7 技术债的清理（优先：API 入参校验、O(N²) 扫描、页面作业恢复、README）；spec 预留的扩展位：混合检索/重排（route 参数）、`/admin` 的问题池/审核队列/看板模块（导航注册表机制已就绪）；ch02 遗留的线程池收敛与批量写。

---

## 9. 建议 Codex 首先执行的验证任务

按顺序执行，全部通过即视为交接成功：

1. **环境自检**：`uv sync` → `uv run python -c "import app.main"` 无报错；`git status` 干净、在 `ch03-knowledge-base` 分支且与 origin 同步。
2. **安全自检**：`git grep -nIE 'sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{20,}'` 应无输出；确认 `.env` 被 ignore（`git check-ignore .env`）。
3. **全量测试**：MySQL 与 Milvus 均启动后 `uv run pytest -q` → **334 passed**（Milvus 停机时 2 个 integration skip 属预期）。
4. **建库链路**：`make kb-preview`（3 材料 32 块）→ `make kb-build` → `uv run python -m app.kb.ingest status`（45 块 done、Milvus 45、consistent；若库里已有 45 块则 build 显示"新增 0 跳过 45"，同为通过）。
5. **检索冒烟**：`uv run python -c "from app.kb.retriever import retrieve; print(retrieve('邮费是多少'))"` → 返回运费说明而非"没有找到"。
6. **服务冒烟**：`uv run uvicorn app.main:app --port 8010` → `GET /health` 200、`GET /admin` `GET /kb` 200 且含"知识库/手工录入"字样 → `POST /chat {"session_id":"codex-check","message":"邮费是多少"}` SSE 含 `query_faq` tool 帧与正确运费回答 → **测试后删除该会话数据**（conversations/messages 按 user_id 删）。
7. **阅读材料**（改任何代码之前）：三章 spec（`docs/superpowers/specs/`）+ `dev-notes/ch03.md` + 本文 §5 决策清单。

---

## 附：待确认事项汇总

| # | 事项 | 现状 |
|---|---|---|
| 1 | 6 张表的原始 DDL 备份是否存在于仓库外 | 仓库内无 DDL，本文 §4.3 列定义可作建表依据 |
| 2 | 三个已暴露凭据（DeepSeek/SiliconFlow/GitHub PAT）是否已轮换 | 未确认 |
| 3 | Milvus 停机时的精确测试数 | 预期 332 passed + 2 skipped，未实测 |
| 4 | Linux 部署兼容性 | 仅 Windows 验证过 |
| 5 | ch04 及后续章节的需求范围 | 无任何 spec/文档 |
| 6 | PR #3 由谁创建、何时 | ch03-knowledge-base 分支已推送，PR 未创建 |
| 7 | origin/ch02-function-calling 是否回退到 d5d1cec | 需强推重写远端历史，待用户批准 |
| 8 | 工作区未跟踪文件 AGENTS.md、dev-notes/codex-migration.md 的来源与是否入库 | 非本次会话创建，内容未审 |

