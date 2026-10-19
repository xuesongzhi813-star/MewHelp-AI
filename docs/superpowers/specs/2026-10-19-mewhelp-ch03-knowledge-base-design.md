# MewHelp AI ch03 设计:知识库与向量语义检索

日期:2026-10-19
状态:待批准
前置:ch02-function-calling(Function Calling 工具链已交付,109 个测试通过)

## 1. 目标

把 `query_faq` 的内部实现从「MySQL `LIKE` 关键词查 `faq` 表」升级为「BGE-M3 向量语义检索」,工具的入参出参契约保持不变。围绕它建立一条完整的知识库管线:

1. 离线文档建库:Markdown 按标题层级结构感知切分,超长递归切,块间重叠并裁到最近句号,表格按行切时每块复制表头。
2. 离线对话挖知识:一条可直接敲命令运行的 job,分批读历史客服对话,LLM 抽取问答对,先进暂存表,整体去重后入知识库。
3. 双写落库:MySQL `knowledge_chunks` 为原文权威源,Milvus `knowledge` 集合存向量;先写 MySQL 记 `pending`,再写 Milvus 回填 `vector_id` 并转 `done`;按主键幂等,中断可续跑。
4. 在线检索:问题向量化 → Milvus Top-K → 回 MySQL 取原文 → 按现有「问题→答案」格式返回。

验收标准(用户原话):

1. 「邮费是多少」这类换说法的问题,现在能召回运费说明并答对。
2. 故意中断建库任务再重跑,漏向量化的块能被捡起补齐。

## 2. 已确认决策(用户裁决)

| 决策点 | 结论 |
|---|---|
| Milvus 运行方式 | Docker Desktop + Milvus Standalone(官方三容器) |
| 知识源文档 | 由本任务产出 3 份样例 Markdown,放 `data/kb/` |
| 挖掘 job 形态 | 能直接敲命令运行的一次性 job;部署方式由用户自行决定,代码不内嵌调度器 |
| 挖出去重口径 | 归一化精确去重 + BGE-M3 向量相似度阈值 |
| 切块参数 | 目标 500 字 / 重叠 100 字 |
| `is_key_clause` 判定 | 规则启发式(关键词 + 加粗/引用块) |
| 挖掘范围 | 全部会话,按 `conversation_id` 升序增量处理 |
| 挖掘条目分类 | 入库时用关键词规则归入现有五类,未命中归「其他」 |
| 在线召回阈值 | 设相似度阈值,可配置,低于阈值视为「无匹配」 |
| 整体方案 | A:MySQL 状态机 + 薄 Milvus 客户端(不用 `langchain-milvus`) |
| 浏览器建库页 | 新增 `/kb` 六块录入页 + `/admin` 聚合外壳;预览/录入/CLI 共用一份切块实现 |
| 作业执行方式 | 页面作业统一走 `make`,argv 写死、无 shell;`make` 缺失时安装 GnuWin32.Make |

## 3. 环境与既有事实(2026-10-19 实测)

- 本机 MySQL `melhelp` 库;`knowledge_chunks` 与 `qa_extraction_staging` **已存在且为空**,字段与需求一致:
  - `knowledge_chunks`: `id`(bigint unsigned 自增 PK)、`category` varchar(255)、`questions` text、`answer` text、`section_path` varchar(512) 可空、`content_type` varchar(32) 可空、`is_key_clause` tinyint(1) 默认 0、`prev_chunk_id` / `next_chunk_id` bigint unsigned 可空、`vector_id` varchar(64) 可空、`vectorize_status` enum('pending','done') 默认 'pending'、`created_at` / `updated_at`。
  - `qa_extraction_staging`: `id` PK、`batch_no` varchar(64)、`source_ref` varchar(255) 可空、`question` text、`answer` text、`status` enum('extracted','kept','discarded') 默认 'extracted'、`created_at`。
- `faq` 表现有 13 条;`knowledge_chunks` / `qa_extraction_staging` 均为 0 条。
- **不建表、不改表、不 `create_all`**;ORM 仅映射既有结构。
- Docker Desktop 已安装并已启动(7.9 GB 内存 / 16 CPU),19530 端口尚未监听。
- SiliconFlow BGE-M3:固定 1024 维;单请求最多 64 条(实践用 32);OpenAI 兼容端点 `https://api.siliconflow.cn/v1/embeddings`;`dimensions` 参数仅 Qwen3 系列支持,传给 BGE-M3 会报 400。
- Context7 MCP 已接入(`https://mcp.context7.com/mcp`);本设计已用其核对 PyMilvus `MilvusClient` 与 LangChain `with_structured_output` 当前接口。
- 本机原先没有 `make`;已通过 `winget install GnuWin32.Make` 安装 GNU Make 3.81,并把 `C:\Program Files (x86)\GnuWin32\bin` 加入用户 PATH。

## 4. 分层架构与模块边界

```
app/
  kb/
    __init__.py
    documents.py     # Markdown → Chunk(切分 + 元数据,纯函数,零 I/O)
                       #   build_chunks(): 页面预览、页面录入、离线 CLI 共用这一份
    sources.py       # 建库材料清单:哪些文件、各自 content_type(CLI 与页面同读)
    embedding.py     # SiliconFlow BGE-M3 批量封装
    store.py         # MySQL 侧读写:插 pending / 扫 pending / 回填 done / 取原文 / 指纹查重
    milvus.py        # MilvusClient 封装:建集合 / upsert / search / flush / drop / 列 id
    dualwrite.py     # 双写状态机:vectorize_pending(),断点续跑,一致性核对
    retriever.py     # 查询向量化 + Top-K + 阈值 + 拼文本
    ingest.py        # 建库 CLI(preview / docs / faq / vectorize / rebuild / build / status / reset)
    mining.py        # 对话挖掘 CLI(--once / --limit / --interval)
    seed_conversations.py  # seed-conv:灌演示用历史会话(幂等)
  api/
    kb.py            # /api/kb/* 路由
    jobs.py          # /api/jobs/* 路由
  core/
    jobs.py          # 作业运行器:白名单 argv、subprocess、日志、拒重入
  main.py            # 挂载 /admin、/kb、/static 与两个 router
  static/
    admin.js         # 共用导航 + 样式注入(注册表驱动)
    admin.html       # 聚合首页
    kb.html          # 知识库录入页
  tools.py           # query_faq 内部替换,签名与返回格式不变
deploy/milvus/
  docker-compose.yml # 官方 v3.0.0 standalone(etcd + MinIO + milvus)
data/kb/             # 3 份样例知识文档
docs/samples/ch03-mining-samples.md
scripts/eval_mining.py
Makefile             # kb-preview / kb-build / seed-conv / kb-mine / kb-vectorize / kb-reset
log/                 # 作业日志,gitignored
```

| 模块 | 职责 | 对外接口 |
|---|---|---|
| `kb/documents.py` | 标题栈、段落/表格识别、超长切分、句号裁剪、重叠、块内序号、元数据、关键条款规则 | `build_chunks(markdown: str, source: Source) -> list[Chunk]` |
| `kb/sources.py` | 建库材料清单与 `content_type` | `SOURCES`, `get_sources()` |
| `kb/embedding.py` | 批量调用、1024 维校验、重试 | `embed_texts(texts: list[str]) -> list[list[float]]` |
| `kb/store.py` | MySQL 侧状态机 | `insert_chunks`, `iter_pending`, `mark_done`, `fetch_by_ids`, `find_existing_fingerprints`, `reset_all_pending`, `overview_counts` |
| `kb/milvus.py` | 集合与向量操作,**不碰业务语义** | `ensure_collection`, `upsert`, `search`, `flush`, `drop`, `count`, `list_ids` |
| `kb/dualwrite.py` | 双写编排、一致性核对 | `vectorize_pending(limit=None)`, `consistency_snapshot()` |
| `kb/retriever.py` | 在线检索编排与文案 | `retrieve(query: str) -> str` |
| `kb/ingest.py` | 建库 CLI 与断点续跑 | CLI |
| `kb/mining.py` | 抽取、去重、入库 | CLI |
| `core/jobs.py` | 白名单作业运行器 | `JOBS`, `start_job(name, confirm)`, `job_status(name)` |
| `api/kb.py` | 知识库 API | 见 §16 |
| `api/jobs.py` | 作业 API | 见 §16 |

设计原则:

- Milvus 只负责「向量 → 主键」,不存原文与元数据;原文永远从 MySQL 取。
- **切块只有一份实现**:页面预览、页面录入、离线 CLI 全部调 `kb.documents.build_chunks`;材料清单只有 `kb.sources.SOURCES` 一处定义。预览什么样,入库就是什么样。
- `kb/documents.py` 与 `kb/sources.py` 是纯函数/纯数据,可以脱离数据库和网络做完整单测。
- `kb/embedding.py` / `kb/milvus.py` 通过模块级名字暴露,测试用 Fake 替换,不引入额外抽象层。
- 不引入 `langchain-milvus`;不引入新的 ORM 表。
- 目录按当前仓库的扁平结构落地:`app/kb/` 放领域逻辑,`app/api/` 放路由,`app/core/` 放基础设施,`app/static/` 放页面;不照搬设计稿里的 `app/kb/documents`+`app/core/jobs` 之外的假定路径。

## 5. 文档切分规则

输入:单个 Markdown 文件文本 + 文档标题(文件名 stem 或首个一级标题)。

1. 逐行扫描 ATX 标题(`#`～`######`),维护标题栈;每个标题开启一个 section。
2. section 内容按空行聚合成 block;Markdown 表格(`| ... |` + 分隔行)整体识别为 table block;列表项各自成 block。
3. section 总长 ≤ 500 字 → 整段成一块。
4. 超长 section:按 block 累加,超过 500 字就切;切点从目标位置向前回退到最近的句子结束符(`。！？；` 或换行);找不到才硬切。下一块开头带上一块末尾约 100 字,并对齐到句子边界,避免从半句开始。
5. 表格:整张表超长时按行分组;每组重复表头行 + 分隔行;单行本身超长时该行独占一块(不拆列)。
6. 每块记录文档内序号;全部切完后回填 `prev_chunk_id` / `next_chunk_id`,只在同一文档内串联,跨文档不连。
7. 空块、只有标题没有正文的 section(若正文为空且标题已作为 `questions`)不单独成块。

## 6. 元数据规则

| 字段 | 文档章节 | 商品 FAQ(`faq` 表) | 对话挖掘 |
|---|---|---|---|
| `category` | 上级标题路径(如 `退货政策 > 退款说明`);只有一级标题时取文档标题 | `faq.category` | 关键词规则归入现有五类,未命中「其他」 |
| `questions` | 当前章节标题;无标题的前言取文档标题 | `faq.question` | LLM 抽出的真实问句(一条暂存行一个问句) |
| `answer` | 章节正文(去掉标题行) | `faq.answer` | LLM 抽出的答案 |
| `section_path` | 一级到当前的完整路径,` > ` 连接 | `商品FAQ > {category}` | `对话挖掘 > 会话 {id}` |
| `content_type` | `文档章节` | `商品FAQ` | `对话挖掘` |
| `is_key_clause` | 规则命中 → 1,否则 0 | 0 | 0 |
| `prev/next` | 同文档内串联 | NULL | NULL |

**关键条款规则**:标题或正文命中 `重要|注意|必须|禁止|不予|务必|仅限|免责`,或该 block 是整段加粗 / 引用块(`>`)→ `is_key_clause=1`。规则集中在 `kb/documents.py`,单测覆盖。

**向量化文本模板**(只拼这三个字段,元数据不进向量):

```
分类:{category}
问题:{questions}
内容:{answer}
```

**切块指纹**:`sha256(normalize(questions) + "\n" + normalize(answer))`。

- 只按「问法 + 正文」判重,不把 `category` / `section_path` 算进去:同一节被表格按行拆或超长递归切时,多块共用节标题(问法一模一样)但正文不同,加入路径会把它们误判成不同块或重复块;只按问法又会误杀同一节的多块。问法 + 正文是唯一稳定的组合。
- 同一份正文重复录入时指纹全撞、全部跳过,录入天然幂等。
- 两份不同文档出现完全相同的问法与正文时视为同一条知识,合并是预期行为。

## 7. 双写落库与断点续跑

Milvus 集合 `knowledge`:

- `id` VARCHAR(64) 主键,值为 `str(knowledge_chunks.id)`
- `vector` FLOAT_VECTOR,dim=1024,索引 AUTOINDEX / COSINE
- 不存其他字段

建库两阶段:

```
阶段一(写 MySQL)
  文档切分 / faq 导入 / 挖掘入库
    → 逐块 INSERT knowledge_chunks, vectorize_status='pending', vector_id=NULL
    → 文档块插入后回填同文档内的 prev_chunk_id / next_chunk_id

阶段二(向量化,可反复重跑)
  SELECT ... WHERE vectorize_status='pending' ORDER BY id LIMIT 32
    → embed_texts(向量化文本)
    → Milvus upsert([{id: str(chunk.id), vector: vec}])
    → UPDATE knowledge_chunks SET vector_id=str(id), vectorize_status='done'
    → 每批一个事务,提交后才进入下一批
```

幂等与断点语义:

- Milvus 主键由 MySQL 主键决定,重跑对同一行是 `upsert`,不会产生第二条向量。
- 任一步失败(嵌入 API 超时 / Milvus 不可达 / 进程被杀)→ 该批保持 `pending`,`vector_id` 不写;重跑只扫 `pending`。
- 「Milvus 写成功但 MySQL 回填失败」→ 行仍为 `pending`;重跑对同一主键再 `upsert`,结果一致。
- 文档重复建库:指纹查重,已存在则跳过。
- `rebuild`:drop Milvus 集合、把**所有** MySQL 行重置为 `pending` 并清空 `vector_id`,再全量向量化;不删原文、不重新切分文档。用于 Milvus 数据丢失后恢复。
- `reset`:先把所有行的 `prev_chunk_id` / `next_chunk_id` 置 NULL(避免自引用外键阻塞删除),再删除 `knowledge_chunks` 全部行,并 drop Milvus 集合。**破坏性操作**,必须 `--yes` 且页面二次确认;不动 `qa_extraction_staging` 与 `faq`。
- 每次向量化批次结束后 `flush`,保证紧接的在线检索可见。

CLI(所有命令另有对应的 make 目标,见 §15):

```
uv run python -m app.kb.ingest preview    # dry-run:按材料清单切块,打印块数/关键条款/切块特性
uv run python -m app.kb.ingest docs       # 切分 data/kb/*.md → MySQL(pending)
uv run python -m app.kb.ingest faq        # faq 13 条 → knowledge_chunks(pending)
uv run python -m app.kb.ingest vectorize  # 只扫 pending 做向量化(断点续跑)
uv run python -m app.kb.ingest rebuild    # 保留原文,全部重置 pending 后重新向量化
uv run python -m app.kb.ingest build      # docs + faq + vectorize(kb-build)
uv run python -m app.kb.ingest status     # pending / done / Milvus 行数 / 一致性
uv run python -m app.kb.ingest reset --yes  # 清空 knowledge_chunks + drop Milvus 集合(heavy)
uv run python -m app.kb.mining --once     # 对话挖掘(kb-mine)
uv run python -m app.kb.seed_conversations  # 灌演示会话(seed-conv)
```

## 8. 在线检索与 `query_faq` 契约

```
query_faq(keyword)
  → embed_texts([keyword])
  → Milvus search(limit=faq_max_results, search_params={"metric_type": "COSINE"})
  → 过滤 distance < knowledge_min_score
  → 按命中顺序回 MySQL 取 questions/answer
  → "1. {questions}\n→ {answer}" 用 "\n\n" 拼接
```

- 入参出参不变:`keyword: str` → 多行文本;未命中返回 `FAQ 库中没有找到与「{keyword}」相关的条目`;异常交给 `execute_tool` 转成错误文本回灌;SSE 协议零改动。
- Field description 改为:「用户原话中的问题或关键词,完整问句效果更好」。
- 区分两种空:Milvus 正常但无命中/全低于阈值 → 返回「无匹配」;Milvus 不可达 → 抛异常,由执行器回灌错误文本,不伪装成「没查到」。
- Top-K 沿用 `faq_max_results`(默认 3);新增 `knowledge_min_score`(默认 0.5)。
- 查询多一次 HTTP 嵌入调用,`tool_timeout` 默认从 5s 调到 10s。
- `faq` 表运行期不再被读,只作为 `ingest faq` 的数据源保留。

## 9. 对话挖知识

命令:

```
uv run python -m app.kb.mining --once              # 跑一轮,处理所有待挖会话
uv run python -m app.kb.mining --once --limit 20   # 只处理前 20 个待挖会话
uv run python -m app.kb.mining --interval 600      # 常驻循环,每 10 分钟一轮
```

增量口径:

- 按 `conversation_id` 升序扫描;`source_ref = conv:{id}:msgs:{first}-{last}`。
- 每个会话取当前最大 `message.id`;暂存表已有该会话且 `last >= 当前最大 id` 则跳过。
- 只送 `user` / `assistant` 消息,过滤 `tool` 消息;单条截断 500 字,单批最多 5 个会话或 6000 字。

抽取:

- 现有 DeepSeek 模型 + `with_structured_output(QAPairList, method="function_calling")`。
- Prompt 硬约束:只从客服已作出的回答里提取、不推测;跳过寒暄、订单号、物流单号等一次性信息;问句改写成真实用户会问的说法;答案保留关键条件(时效、金额、比例)。
- LLM 调用成功后才写暂存表:`batch_no=M{时间戳}-{8位随机}`、`source_ref` 指向会话、`status='extracted'`。

去重与入库(每批抽完立即做):

1. 先跑 `vectorize_pending()`,保证库内知识都已进 Milvus。
2. 归一化:去空白、转小写、去中英文标点、全角转半角。
3. 精确去重:与 `knowledge_chunks.questions` 及本轮已 `kept` 的暂存行比较,相同则 `status='discarded'`。
4. 向量去重:问句向量化 → Milvus Top-1;`distance >= knowledge_dedup_score`(默认 0.92)则 `discarded`。
5. 入库:`category` 按关键词规则(优先级:售后政策 > 物流配送 > 支付优惠 > 账户会员 > 商品咨询 > 其他);`questions=问句`、`answer=答案`、`section_path=对话挖掘 > 会话 {id}`、`content_type=对话挖掘`、`is_key_clause=0`;写 MySQL(pending)后立即向量化。
6. 暂存行保留 `kept` / `discarded` 永久审计,不删。

幂等:LLM 调用失败不写暂存;去重/入库中途失败时暂存行状态就是断点,重跑从 `extracted` 继续。

Prompt 类产出验证:`docs/samples/ch03-mining-samples.md` 标注 6 组会话(3 组应抽出、3 组应不抽),`scripts/eval_mining.py` 跑真实 DeepSeek,记录命中/误抽/漏抽。

`seed-conv`(`app/kb/seed_conversations.py`)灌入 3 组演示会话,写入现有 `conversations` / `messages` 表,`user_id` 以 `seed-ch03-` 开头;内容覆盖可抽取的售后/运费问答、含订单号的一次性信息、纯寒暄,供 `kb-mine` 演示。幂等口径:同 `user_id` 已存在则整组跳过。该脚本属于数据类产出,验证方式是跑 `kb-mine` 看抽取结果,不走 TDD。

## 10. Milvus 部署

- `deploy/milvus/docker-compose.yml` 采用官方 v3.0.0 standalone 配置(etcd + MinIO + milvus-standalone,19530 端口),数据卷放 gitignored 目录。
- 启动:`docker compose -f deploy/milvus/docker-compose.yml up -d`
- 验证:`docker compose -f deploy/milvus/docker-compose.yml ps` 三容器 healthy;`Test-NetConnection 127.0.0.1 -Port 19530` 为 True。
- 停止:`docker compose -f deploy/milvus/docker-compose.yml down`(保留卷);`down -v` 清数据。
- `.gitignore` 增加 `deploy/milvus/volumes/` 与 `log/`,容器数据与作业日志不进 git。
- Milvus 不可达时:建库 CLI 给出中文报错并非零退出;在线检索抛异常,由执行器回灌错误文本。

## 11. 配置与密钥

新增 `Settings` 字段:

| 字段 | 默认值 | 说明 |
|---|---|---|
| `embedding_base_url` | `https://api.siliconflow.cn/v1` | SiliconFlow OpenAI 兼容端点 |
| `embedding_api_key` | `""` | **只进 `.env`**;为空时 `embedding.py` 抛出明确中文错误 |
| `embedding_model` | `BAAI/bge-m3` | 固定 1024 维 |
| `embedding_dim` | `1024` | 与 Milvus 集合维度校验 |
| `embedding_batch_size` | `32` | 官方上限 64,取 32 |
| `milvus_uri` | `http://127.0.0.1:19530` | |
| `milvus_token` | `""` | 本地 standalone 留空 |
| `knowledge_collection` | `knowledge` | |
| `knowledge_min_score` | `0.5` | 在线召回阈值 |
| `knowledge_dedup_score` | `0.92` | 挖掘去重阈值 |
| `mining_batch_size` | `5` | 单次 LLM 调用的会话数 |
| `mining_max_chars` | `6000` | 单次 LLM 调用的字符上限 |
| `kb_sources_dir` | `data/kb` | 建库材料目录 |
| `kb_max_markdown_bytes` | `204800` | `/api/kb` 手工录入请求体上限(200 KB) |
| `job_log_dir` | `log/acceptance` | 作业日志目录 |
| `make_exe` | `make` | 解析 Make 可执行文件;不在 PATH 时作业报明确错误 |
| `tool_timeout` | `10` | 由 5 调整 |

- `.env.example` 只放占位符 `<你的硅基流动 API Key>`,真实 key 只进 gitignored `.env`。
- 新增依赖:`pymilvus`(Milvus 客户端)、`openai`(显式声明,嵌入选调用);不新增 `langchain-milvus`。
- 嵌入调用使用 `openai.OpenAI(base_url=..., api_key=...).embeddings.create(model=..., input=[...])`,直接发字符串,避免 LangChain `OpenAIEmbeddings` 默认把文本转 token 数组导致兼容端点 400。

## 12. 测试策略

1. **纯单测**(不连外部服务):切分器(标题层级、长段落句号切分、重叠、表格表头复制、prev/next)、元数据规则、关键条款规则、向量化文本模板、指纹(问法+正文)、分类器、归一化、检索结果拼装与阈值过滤。
2. **MySQL 事务回滚测试**:沿用 ch02 的 `tools_tx` 思路。插入 pending → Fake embedding + Fake Milvus → 断言 `done` / `vector_id`;失败保持 pending;重跑不重复;`upsert` 收到 `str(id)`。
3. **真实 Milvus 集成测试**:独立测试集合,建表 → upsert → flush → search → drop;连不上时 pytest 跳过并明确提示。
4. **API 测试**:`httpx.ASGITransport` 打 `/api/kb/*`,monkeypatch store/embedding/milvus:
   - `overview` 在 MySQL 不可达时 `mysql.ok=false`、材料清单仍返回;Milvus 不可达时 `consistency.status="unknown"` 而不是 `"mismatch"`;
   - `preview` 不写库、不调嵌入;
   - `ingest` 向量化失败返回 502 且消息含「已入库 N 块(pending)」,MySQL 行仍存在;
   - `search` 阈值过滤与命中顺序;
   - `staging` 三态计数。
5. **作业运行器测试**:未知作业 404;heavy 未确认 403;同名/全局单飞 409;argv 写死且 `shell=False`;日志尾部可读;`make` 缺失时失败信息明确。
6. **静态页冒烟**:`GET /admin`、`GET /kb`、`GET /static/admin.js` 均 200;页面依赖的 API 路径与 §16 一致。
7. **Prompt 评估**:挖掘样例集 + `scripts/eval_mining.py`,替代挖掘 Prompt 的 TDD。
8. **既有 109 个测试必须全绿**;`query_faq` 的 schema/返回格式断言不动。

## 13. 验收演示

```
docker compose -f deploy/milvus/docker-compose.yml up -d
make kb-preview                             # dry-run:材料清单 + 切块结果
make kb-build                               # 入库 + 向量化
uv run python -m app.kb.ingest status       # pending=0, done=N
make seed-conv && make kb-mine              # 先灌演示会话,再挖一轮
uv run uvicorn app.main:app --reload
# 浏览器:
#   /admin  → 知识库卡显示块数/pending/暂存/Milvus
#   /kb     → 手工贴 Markdown → 预览 → 入库 → 向量化 → 检索自测
#   /       → POST /chat 问「邮费是多少」→ 观察 tool 帧 query_faq + 回答
```

- **AC1**:`data/kb/` 的配送文档包含运费说明章节;`retrieve("邮费是多少")` 命中运费块;聊天页回答正确。
- **AC2**:`ingest docs` 写完 MySQL 后中断 `ingest vectorize`(只跑第一批就 kill);重跑 `ingest vectorize` 后 `status` 显示 pending=0,Milvus 行数 == MySQL `done` 行数。
- **AC3(页面)**:`/kb` 手工录入一段新 Markdown → 预览块数与元数据 → 入库(勾选顺手向量化)→ 库存闸条 pending 归零、Milvus 条数增加 → 在检索自测里用换说法的问题召回该块。
- **AC4(作业)**:页面上按 `kb-preview` / `kb-build` / `seed-conv` / `kb-mine` / `kb-vectorize` / `kb-rebuild`,日志尾部回显与终端执行一致;`kb-reset` 未二次确认时 403,确认后清空;同名作业运行中再次点击 409。

交付物:功能演示命令、测试结果、`dev-notes/ch03.md`(按阶段追记)、本 spec、实现计划、3 份知识文档、挖掘样例集、Milvus compose、`Makefile`、`/admin` 与 `/kb` 页面。

## 14. 知识库录入页 `/kb` 与后台外壳 `/admin`

建库链不该只能在终端敲:知识长什么样、切成几块、有没有块卡在 pending、换个说法能不能召回,都要能在浏览器里反复看。落成一页后台 `/kb`,外壳一次搭好。

### 14.1 `/kb` 的六块

| 区块 | 看什么 / 按什么 |
|---|---|
| 库存闸条 | 知识块数、待向量化、Milvus 条数、关键条款数、双写是否一致 |
| ① 手工录入 | 贴一段 Markdown → 切块预览(dry-run)→ 录入入库(可勾选顺手向量化) |
| ② 建库材料 | `data/kb/*.md` 清单 + 就地切块结果(各切多少块、关键条款几块、触发了哪些切块特性),逐份可送预览区细看;作业按钮 `kb-preview` / `kb-build` |
| ③ 对话挖知识 | 暂存表三态计数(抽出 / 保留 / 丢弃)+ 批次数,可展开逐条看;作业按钮 `seed-conv` / `kb-mine` |
| ④ 向量化与双写 | pending / done / Milvus 三个数摆一起 + 「向量化待补块」;作业按钮 `kb-vectorize` / `kb-rebuild` / `kb-reset` |
| ⑤ 检索自测 | 输一句话、选路线、取 Top-K,当场看召回哪几块、各自多少分 |
| ⑥ 最近入库 | 按 id 倒序看最近入库的块与它们的状态 |

页面交互约定:

- 预览与入库共用 `kb.documents.build_chunks`;预览只做切分与元数据,不写库、不调嵌入。
- 入库先写 MySQL 记 pending;勾选「顺手向量化」时再走 `dualwrite.vectorize_pending`。向量化失败返回 502 并明说「已入库 N 块(pending),补跑一次即可」——**不回滚**,块在权威源里躺着,重跑捡 pending 就补齐。
- 手工录入只接受 Markdown 文本 + 标题 + `content_type`;不接受任意文件路径。请求体上限 200 KB。
- 检索自测路线下拉本章只有 `dense` 一项,为后续混合检索留位置;未知路线返回 422。
- 作业运行时每 3 秒轮询一次作业状态与日志尾部,空闲时 15 秒刷新库存;页面不做 WebSocket。
- 依赖缺失是一种状态,不是错误:Milvus 离线时「双写是否一致」显示「读不到」而不是「对不上」;MySQL 没起也只让库存那几个数变空,材料清单与作业按钮照样可用。
- 最近入库只取 20 条;暂存表按状态筛选、默认 50 条。

### 14.2 后台外壳

- `app/static/admin.js`:共用导航 + 样式注入。导航项来自一份注册表(`{label, href, key}`),`/admin` 与 `/kb` 通过 `data-page` 属性高亮当前页。样式限定在 `.admin-*` 命名空间内,页面自身的样式内联也不打架。
- `app/static/admin.html`:`/admin` 聚合首页。每块一张卡,给几个关键数与一句结论;知识库卡读 `/api/kb/overview`,客服对话卡只放入口。
- `app/static/kb.html`:`/kb` 六块;页面脚本可以是内联模块脚本,复用 `admin.js` 的 `api()` / `pollJob()` 工具。
- 故障隔离:每张卡独立 fetch,某块依赖没起只让它自己那张卡显示「读数失败」,不连坐整页。后续新增后台模块 = 往导航注册表加一行 + 加自己的页面/API。
- 原有聊天页 `/` 不动;导航里给它一个入口。

## 15. 作业运行器与 make 目标

**Makefile**(仓库根,页面与终端共用同一份配方):

```make
.PHONY: kb-preview kb-build seed-conv kb-mine kb-vectorize kb-rebuild kb-reset

kb-preview:
	uv run python -m app.kb.ingest preview

kb-build:
	uv run python -m app.kb.ingest build

seed-conv:
	uv run python -m app.kb.seed_conversations

kb-mine:
	uv run python -m app.kb.mining --once

kb-vectorize:
	uv run python -m app.kb.ingest vectorize

kb-rebuild:
	uv run python -m app.kb.ingest rebuild

kb-reset:
	uv run python -m app.kb.ingest reset --yes
```

**运行器**(`app/core/jobs.py` + `app/api/jobs.py`):

- 注册表 `JOBS: dict[str, JobSpec]`,`JobSpec = {name, target, heavy, description}`;`argv` 由 `["make", "-f", str(ROOT / "Makefile"), target]` 在模块里写死,前端只传作业名——**页面有重跑按钮,但没有 shell**。
- 启动:`subprocess.Popen(argv, cwd=ROOT, shell=False, stdout=log_fp, stderr=STDOUT, text=True, encoding="utf-8", errors="replace")`;日志落 `log/acceptance/<job>.log`,每次运行前截断。
- 状态:内存字典保存 `status`(idle/running/succeeded/failed)、`started_at`、`finished_at`、`exit_code`;轮询 `Popen.poll()` 更新。
- 单飞:同一时刻只允许一个作业运行(不只是同名),避免 `kb-build` 与 `kb-vectorize` 并发写。运行中再发起返回 409。
- heavy:`kb-mine`(调 LLM)与 `kb-reset`(清空知识库)必须带 `confirm=true`,否则 403;页面二次确认后才发。
- 未知作业 404;`make` 不在 PATH 时作业立即失败,日志写明「未找到 make,请安装 GnuWin32.Make 并加入 PATH」。
- 应用重启后内存状态丢失,日志文件保留;页面显示 idle 并允许重跑。演示项目接受,不做持久化作业表。
- 录入这类写操作走 API 而不是作业:它没有对应的 make 目标,正文是浏览器里现给的;两条路最终都落到同一套 `build_chunks` + `dualwrite` 逻辑上。

## 16. API 契约

所有接口 JSON;错误统一 `{"detail": "..."}`。

| 方法与路径 | 请求 | 响应要点 |
|---|---|---|
| `GET /api/kb/overview` | — | `mysql`、`milvus`、`consistency`、`staging`、`sources`、`recent`、`jobs` 七块 |
| `POST /api/kb/preview` | `{markdown, source_title, content_type}` | `{counts, features, chunks[]}`;dry-run |
| `POST /api/kb/ingest` | `{markdown, source_title, content_type, vectorize}` | `{inserted, skipped, chunk_ids, vectorized, pending}`;向量化失败 502 |
| `POST /api/kb/vectorize` | `{limit?}` | `{processed, failed, pending_left}` |
| `GET /api/kb/staging` | `?status=&limit=` | `{counts, rows[]}` |
| `POST /api/kb/search` | `{query, top_k, route}` | `{threshold, hits[], message}` |
| `GET /api/jobs` | — | 注册表 + 各自状态(含 `kb-preview` / `kb-build` / `seed-conv` / `kb-mine` / `kb-vectorize` / `kb-rebuild` / `kb-reset`) |
| `POST /api/jobs/{name}` | `{confirm?}` | 202 已启动;404/403/409 |
| `GET /api/jobs/{name}` | — | `{status, started_at, finished_at, exit_code, log_tail}` |

`overview` 字段语义:

- `mysql = {ok, chunks, pending, done, key_clauses}`;MySQL 不可达时 `ok=false`,数值为 `null`。
- `milvus = {ok, count, collection}`;不可达时 `ok=false`,数值为 `null`。
- `consistency = {status, missing_in_milvus[], orphan_in_milvus[], note}`;`status ∈ consistent|mismatch|unknown`。两边都可达且行数 ≤ 5000 时做精确 id 集合比对;超过 5000 只比数量,`note` 说明口径;Milvus 不可达时必须是 `unknown`(「读不到」),不能是 `mismatch`。
- `sources[]` 由 `kb.sources.SOURCES` + 文件现场切分得到,不依赖 MySQL:`{path, title, content_type, chunks, key_clauses, features}`。
- `recent[]`:按 id 倒序 20 条 `{id, category, questions, content_type, vectorize_status, created_at}`。
- `jobs`:每个注册作业的当前状态。

`search` 响应:

```
{"threshold": 0.5,
 "hits": [{"chunk_id": 12, "score": 0.71, "category": "...", "questions": "...",
           "answer": "...", "content_type": "...", "section_path": "..."}],
 "message": null}
```

无命中或全低于阈值时 `hits=[]`、`message="无匹配"`;Milvus/嵌入不可达时 502,不伪装成「无匹配」。

**错误与依赖语义**:

- `markdown` 超过 `kb_max_markdown_bytes`(200 KB)→ 413,不截断、不入库。
- MySQL 不可达:`overview` 返回 `mysql.ok=false` 与 `null` 数值,`sources` 仍返回;`staging` / `ingest` / `vectorize` 返回 502。
- Milvus 不可达:`overview` 的 `milvus.ok=false`、`consistency.status="unknown"`;`search` / `vectorize` / `rebuild` 返回 502。
- `ingest` 的 502 响应体:`{"detail": "已入库 N 块(pending),补跑一次即可", "inserted": N}`;不删除已写入的行。
- `preview` 不依赖 MySQL / Milvus / 嵌入 API,三者全挂也能用。

## 17. 风险与不做

**风险**:

- Milvus standalone 官方要求 8 GB 内存,本机 WSL 上限 7.9 GB,三容器同时运行可能偏紧;若启动失败,先降 MinIO/etcd 资源或改用 Milvus Lite(WSL 内),届时停下来问用户。
- BGE-M3 对「邮费/运费」的语义匹配预期较好,但 `knowledge_min_score=0.5` 需用真实数据校准;校准结果写进 dev-notes。
- 指纹只按「问法 + 正文」:不同文档里完全相同的问答会合并为一条,这是预期行为;若后续需要按来源区分,要加 `source_doc` 列(本章不改表)。
- 挖掘增量以「会话最大 message id」为准;同一会话后续新增消息会重新整段抽取,可能重复产出,靠去重兜住。
- SiliconFlow 嵌入 API 有速率限制;批量 32、失败重试 3 次(指数退避)。
- `query_faq` 多一次网络调用,`tool_timeout=10s` 仍可能被慢网络击穿;验收时记录真实延迟。
- 作业运行器状态只在内存;应用重启后页面显示 idle,正在跑的 make 子进程不会随应用退出而终止(Windows 下需手动处理)。演示项目接受,日志仍可查。
- GNU Make 3.81 已通过 winget 安装并加入用户 PATH;若换机器部署,需要先确认 `make` 可用,否则页面作业按钮只会给出明确的失败日志。

**不做**:

- 关键词召回 / 混合检索 / 重排(本章只跑 dense 单路)。
- Agent Loop、多轮工具调用。
- 表结构变更(不新增列、不建新表)。
- 后台登录 / 多用户权限(演示项目,`/admin` 与 `/kb` 不鉴权)。
- 作业历史持久化、定时调度器(调度交给外部)。
- Langfuse 等可观测性接入。
