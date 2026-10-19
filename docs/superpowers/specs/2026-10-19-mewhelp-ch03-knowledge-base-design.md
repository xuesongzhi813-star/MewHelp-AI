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
| 知识源文档 | 由本任务产出 3 份样例 Markdown,放 `docs/knowledge/` |
| 挖掘 job 形态 | 能直接敲命令运行的一次性 job;部署方式由用户自行决定,代码不内嵌调度器 |
| 挖出去重口径 | 归一化精确去重 + BGE-M3 向量相似度阈值 |
| 切块参数 | 目标 500 字 / 重叠 100 字 |
| `is_key_clause` 判定 | 规则启发式(关键词 + 加粗/引用块) |
| 挖掘范围 | 全部会话,按 `conversation_id` 升序增量处理 |
| 挖掘条目分类 | 入库时用关键词规则归入现有五类,未命中归「其他」 |
| 在线召回阈值 | 设相似度阈值,可配置,低于阈值视为「无匹配」 |
| 整体方案 | A:MySQL 状态机 + 薄 Milvus 客户端(不用 `langchain-milvus`) |

## 3. 环境与既有事实(2026-10-19 实测)

- 本机 MySQL `melhelp` 库;`knowledge_chunks` 与 `qa_extraction_staging` **已存在且为空**,字段与需求一致:
  - `knowledge_chunks`: `id`(bigint unsigned 自增 PK)、`category` varchar(255)、`questions` text、`answer` text、`section_path` varchar(512) 可空、`content_type` varchar(32) 可空、`is_key_clause` tinyint(1) 默认 0、`prev_chunk_id` / `next_chunk_id` bigint unsigned 可空、`vector_id` varchar(64) 可空、`vectorize_status` enum('pending','done') 默认 'pending'、`created_at` / `updated_at`。
  - `qa_extraction_staging`: `id` PK、`batch_no` varchar(64)、`source_ref` varchar(255) 可空、`question` text、`answer` text、`status` enum('extracted','kept','discarded') 默认 'extracted'、`created_at`。
- `faq` 表现有 13 条;`knowledge_chunks` / `qa_extraction_staging` 均为 0 条。
- **不建表、不改表、不 `create_all`**;ORM 仅映射既有结构。
- Docker Desktop 已安装并已启动(7.9 GB 内存 / 16 CPU),19530 端口尚未监听。
- SiliconFlow BGE-M3:固定 1024 维;单请求最多 64 条(实践用 32);OpenAI 兼容端点 `https://api.siliconflow.cn/v1/embeddings`;`dimensions` 参数仅 Qwen3 系列支持,传给 BGE-M3 会报 400。
- Context7 MCP 已接入(`https://mcp.context7.com/mcp`);本设计已用其核对 PyMilvus `MilvusClient` 与 LangChain `with_structured_output` 当前接口。

## 4. 分层架构与模块边界

```
app/
  knowledge/
    __init__.py
    splitter.py      # Markdown → Chunk(纯函数,零 I/O)
    metadata.py      # category / section_path / is_key_clause / 分类关键词规则
    embedding.py     # SiliconFlow BGE-M3 批量封装
    store.py         # MySQL 侧读写:插 pending / 扫 pending / 回填 done / 取原文 / 指纹查重
    milvus.py        # MilvusClient 封装:建集合 / upsert / search / flush / drop
    retriever.py     # 查询向量化 + Top-K + 阈值 + 拼文本
  ingest.py          # 建库 CLI(docs / faq / vectorize / all / status / --rebuild)
  mining.py          # 对话挖掘 CLI(--once / --limit / --interval)
  tools.py           # query_faq 内部替换,签名与返回格式不变
deploy/milvus/
  docker-compose.yml # 官方 v3.0.0 standalone(etcd + MinIO + milvus)
docs/knowledge/      # 3 份样例知识文档
docs/samples/ch03-mining-samples.md
scripts/eval_mining.py
```

| 模块 | 职责 | 对外接口 |
|---|---|---|
| `splitter.py` | 标题栈、段落/表格识别、超长切分、句号裁剪、重叠、块内序号 | `split_markdown(text: str, source_title: str) -> list[Chunk]` |
| `metadata.py` | 元数据推导与关键条款规则;挖掘分类关键词 | `build_metadata(...)`, `classify_category(question, answer)` |
| `embedding.py` | 批量调用、1024 维校验、重试 | `embed_texts(texts: list[str]) -> list[list[float]]` |
| `store.py` | MySQL 侧状态机 | `insert_chunks`, `iter_pending`, `mark_done`, `fetch_by_ids`, `find_existing_fingerprints`, `reset_all_pending` |
| `milvus.py` | 集合与向量操作,**不碰业务语义** | `ensure_collection`, `upsert`, `search`, `flush`, `drop`, `count` |
| `retriever.py` | 在线检索编排与文案 | `retrieve(query: str) -> str` |
| `ingest.py` | 断点续跑编排 | CLI |
| `mining.py` | 抽取、去重、入库 | CLI |

设计原则:

- Milvus 只负责「向量 → 主键」,不存原文与元数据;原文永远从 MySQL 取。
- `splitter.py` 与 `metadata.py` 是纯函数,可以脱离数据库和网络做完整单测。
- `embedding.py` / `milvus.py` 通过模块级名字暴露,测试用 Fake 替换,不引入额外抽象层。
- 不引入 `langchain-milvus`;不引入新的 ORM 表。

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

**关键条款规则**:标题或正文命中 `重要|注意|必须|禁止|不予|务必|仅限|免责`,或该 block 是整段加粗 / 引用块(`>`)→ `is_key_clause=1`。规则集中在 `metadata.py`,单测覆盖。

**向量化文本模板**(只拼这三个字段,元数据不进向量):

```
分类:{category}
问题:{questions}
内容:{answer}
```

**切块指纹**:`sha256(category|section_path|questions|answer)`,用于文档重复建库时跳过已存在块,避免重复插行。

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
- `--rebuild`:drop 集合、所有行重置为 `pending` 并清空 `vector_id`,再全量向量化;不重新切分文档。
- 每次向量化批次结束后 `flush`,保证紧接的在线检索可见。

CLI:

```
uv run python -m app.ingest docs        # 切分 docs/knowledge/*.md → MySQL(pending)
uv run python -m app.ingest faq         # faq 13 条 → knowledge_chunks(pending)
uv run python -m app.ingest vectorize   # 只扫 pending 做向量化(断点续跑)
uv run python -m app.ingest all         # docs + faq + vectorize
uv run python -m app.ingest status      # pending / done / Milvus 行数
uv run python -m app.ingest --rebuild   # 重建向量
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
uv run python -m app.mining --once              # 跑一轮,处理所有待挖会话
uv run python -m app.mining --once --limit 20   # 只处理前 20 个待挖会话
uv run python -m app.mining --interval 600      # 常驻循环,每 10 分钟一轮
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

## 10. Milvus 部署

- `deploy/milvus/docker-compose.yml` 采用官方 v3.0.0 standalone 配置(etcd + MinIO + milvus-standalone,19530 端口),数据卷放 gitignored 目录。
- 启动:`docker compose -f deploy/milvus/docker-compose.yml up -d`
- 验证:`docker compose -f deploy/milvus/docker-compose.yml ps` 三容器 healthy;`Test-NetConnection 127.0.0.1 -Port 19530` 为 True。
- 停止:`docker compose -f deploy/milvus/docker-compose.yml down`(保留卷);`down -v` 清数据。
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
| `tool_timeout` | `10` | 由 5 调整 |

- `.env.example` 只放占位符 `<你的硅基流动 API Key>`,真实 key 只进 gitignored `.env`。
- 新增依赖:`pymilvus`(Milvus 客户端)、`openai`(显式声明,嵌入选调用);不新增 `langchain-milvus`。
- 嵌入调用使用 `openai.OpenAI(base_url=..., api_key=...).embeddings.create(model=..., input=[...])`,直接发字符串,避免 LangChain `OpenAIEmbeddings` 默认把文本转 token 数组导致兼容端点 400。

## 12. 测试策略

1. **纯单测**(不连外部服务):切分器(标题层级、长段落句号切分、重叠、表格表头复制、prev/next)、元数据规则、关键条款规则、向量化文本模板、指纹、分类器、归一化、检索结果拼装与阈值过滤。
2. **MySQL 事务回滚测试**:沿用 ch02 的 `tools_tx` 思路。插入 pending → Fake embedding + Fake Milvus → 断言 `done` / `vector_id`;失败保持 pending;重跑不重复;`upsert` 收到 `str(id)`。
3. **真实 Milvus 集成测试**:独立测试集合,建表 → upsert → flush → search → drop;连不上时 pytest 跳过并明确提示。
4. **Prompt 评估**:挖掘样例集 + `scripts/eval_mining.py`,替代挖掘 Prompt 的 TDD。
5. **既有 109 个测试必须全绿**;`query_faq` 的 schema/返回格式断言不动。

## 13. 验收演示

```
docker compose -f deploy/milvus/docker-compose.yml up -d
uv run python -m app.ingest all
uv run python -m app.ingest status          # pending=0, done=N
uv run python -m app.mining --once          # 需先造几条历史会话
uv run uvicorn app.main:app --reload
# POST /chat 问「邮费是多少」→ 观察 tool 帧 query_faq + 回答
```

- **AC1**:`docs/knowledge/` 的配送文档包含运费说明章节;`retrieve("邮费是多少")` 命中运费块;聊天页回答正确。
- **AC2**:`ingest docs` 写完 MySQL 后中断 `ingest vectorize`(只跑第一批就 kill);重跑 `ingest vectorize` 后 `status` 显示 pending=0,Milvus 行数 == MySQL `done` 行数。

交付物:功能演示命令、测试结果、`dev-notes/ch03.md`(按阶段追记)、本 spec、实现计划、3 份知识文档、挖掘样例集、Milvus compose。

## 14. 风险与不做

**风险**:

- Milvus standalone 官方要求 8 GB 内存,本机 WSL 上限 7.9 GB,三容器同时运行可能偏紧;若启动失败,先降 MinIO/etcd 资源或改用 Milvus Lite(WSL 内),届时停下来问用户。
- BGE-M3 对「邮费/运费」的语义匹配预期较好,但 `knowledge_min_score=0.5` 需用真实数据校准;校准结果写进 dev-notes。
- 知识文档指纹不含文档来源列(表结构无 `source_doc`),两份文档出现完全相同的 `category|section_path|questions|answer` 时会误判重复;样例文档可控,后续如需要可加列。
- 挖掘增量以「会话最大 message id」为准;同一会话后续新增消息会重新整段抽取,可能重复产出,靠去重兜住。
- SiliconFlow 嵌入 API 有速率限制;批量 32、失败重试 3 次(指数退避)。
- `query_faq` 多一次网络调用,`tool_timeout=10s` 仍可能被慢网络击穿;验收时记录真实延迟。

**不做**:

- 关键词召回 / 混合检索 / 重排(本章只跑 dense 单路)。
- Agent Loop、多轮工具调用。
- 表结构变更(不新增列、不建新表)。
- 图形化知识库管理界面。
- Langfuse 等可观测性接入。
