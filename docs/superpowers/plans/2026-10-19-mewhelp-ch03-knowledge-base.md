# MewHelp AI ch03 知识库与向量语义检索 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `query_faq` 从 MySQL `LIKE` 关键词查表升级为 BGE-M3 + Milvus 的 dense 向量检索,并交付离线建库、对话挖掘、双写断点续跑与 `/kb`、`/admin` 浏览器后台。

**Architecture:** MySQL `knowledge_chunks` 是原文权威源与状态机(`pending` → `done`),Milvus `knowledge` 只存 `id = str(chunk.id)` 与 1024 维向量;建库先写 MySQL 再批量向量化,断点续跑只扫 pending。页面、API、CLI 共用 `app/kb/documents.build_chunks` 一份切块实现;页面作业统一经 `app/core/jobs.py` 执行白名单 Makefile 目标。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2.0 + PyMySQL、LangChain + DeepSeek、SiliconFlow BGE-M3、Milvus Standalone v3.0.0 + pymilvus、GNU Make 3.81。

**Spec:** `docs/superpowers/specs/2026-10-19-mewhelp-ch03-knowledge-base-design.md`

## Global Constraints

- 不建表、不改表、不 `create_all`;ORM 只映射既有 MySQL 表。
- 真实密钥只进 gitignored `.env`;`.env.example` 只放占位符;任何 git 跟踪文件不得含真实密码或 API key。
- BGE-M3 固定 1024 维;SiliconFlow 单请求最多 64 条,本项目批量 32。
- Milvus 集合名 `knowledge`;PK `id` 为 VARCHAR(64),值必须是 `str(knowledge_chunks.id)`;索引 AUTOINDEX / COSINE。
- `query_faq` 的入参 `keyword: str` 与返回文本格式不变;`tool_call_schema` 仍只有 `keyword`。
- 页面作业只能执行注册表里的 Make 目标;`argv` 写死,`subprocess.Popen(..., shell=False)`。
- Windows + PowerShell 环境;所有 Python 命令用 `uv run`;测试命令 `uv run pytest`。
- 每个任务结束必须提交一次 git commit。

## Review Focus

1. **空/纯空白 query**:`query_faq("")` 不得调用嵌入 API,直接返回「无匹配」,不能把异常打进 SSE 流。Task 9 测试覆盖。
2. **无标题 / 纯表格 / 超长单行 Markdown**:切分必须在有限步内结束,产出不超过目标长度的块,表格块带表头。Task 3 测试覆盖。
3. **依赖不可达**:MySQL / Milvus / 嵌入 API 挂掉时,`/kb` 与 API 必须显示「读不到 / 读数失败」,不能伪装成「对不上」或「无匹配」。Task 8、13 测试覆盖。
4. **双写中断**:MySQL 已插 pending、Milvus 未写就中断;重跑必须只补 pending,不产生重复行/重复向量。Task 8 测试覆盖。
5. **挖掘空结果/畸形结果/重复问句**:LLM 返回空列表、非法结构或与库内重复时,不得写入脏暂存行或脏知识块。Task 11 测试覆盖。

---

### Task 1: 配置、依赖与 ORM 映射

**Files:**
- Modify: `app/config.py`
- Modify: `app/models.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `.gitignore`
- Modify: `tests/test_db_models.py`
- Modify: `.env`(仅本地,不提交)

**Interfaces:**
- Produces: `Settings.embedding_base_url/embedding_api_key/embedding_model/embedding_dim/embedding_batch_size/milvus_uri/milvus_token/knowledge_collection/knowledge_min_score/knowledge_dedup_score/mining_batch_size/mining_max_chars/kb_sources_dir/kb_max_markdown_bytes/job_log_dir/make_exe`;`Settings.tool_timeout` 默认 10。
- Produces: `models.KnowledgeChunk`、`models.QaExtractionStaging`。
- Consumes: 既有 `Base`、`get_session_local`。

- [ ] **Step 1: 用 Context7 核对 pymilvus 当前版本与 Python 依赖**

Run:
```
claude mcp list
```
Expected: `context7 ... ✔ Connected`。然后用 Context7 查询 `/milvus-io/pymilvus` 的 `MilvusClient` 构造与 `create_collection` 签名,确认 `pymilvus>=2.6` 可用。把结论写进本任务提交信息或 dev-notes(不新建文档)。

- [ ] **Step 2: 写失败的配置测试**

在 `tests/test_db_models.py` 增加:

```python
def test_knowledge_settings_defaults(env_settings, monkeypatch):
    for key in ("EMBEDDING_API_KEY", "EMBEDDING_DIM", "KNOWLEDGE_MIN_SCORE",
                "MILVUS_URI", "TOOL_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)
    assert s.embedding_base_url == "https://api.siliconflow.cn/v1"
    assert s.embedding_model == "BAAI/bge-m3"
    assert s.embedding_dim == 1024
    assert s.embedding_batch_size == 32
    assert s.milvus_uri == "http://127.0.0.1:19530"
    assert s.knowledge_collection == "knowledge"
    assert s.knowledge_min_score == 0.5
    assert s.knowledge_dedup_score == 0.92
    assert s.kb_sources_dir == "data/kb"
    assert s.kb_max_markdown_bytes == 204800
    assert s.tool_timeout == 10
```

并把 `test_tool_timeout_default` 的断言从 `5` 改为 `10`。

- [ ] **Step 3: 写失败的模型对齐测试**

在 `tests/test_db_models.py` 的 `MODEL_BY_TABLE` 中加入:

```python
"knowledge_chunks": models.KnowledgeChunk,
"qa_extraction_staging": models.QaExtractionStaging,
```

Run: `uv run pytest tests/test_db_models.py -v`
Expected: FAIL,`AttributeError: module 'app.models' has no attribute 'KnowledgeChunk'`。

- [ ] **Step 4: 实现 Settings 与模型**

`app/config.py` 增加上表字段;`embedding_api_key: str = ""`(不在代码里放真值),`tool_timeout: int = 10`。

`app/models.py` 增加两个映射:

```python
class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    questions: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    section_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_key_clause: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    prev_chunk_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    next_chunk_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    vector_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vectorize_status: Mapped[str] = mapped_column(
        Enum("pending", "done", name="vectorize_status"), nullable=False,
        default="pending", server_default="pending",
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp(), server_onupdate=func.current_timestamp()
    )

class QaExtractionStaging(Base):
    __tablename__ = "qa_extraction_staging"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_no: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("extracted", "kept", "discarded", name="staging_status"),
        nullable=False, default="extracted", server_default="extracted",
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
```

`pyproject.toml` 的 `dependencies` 增加 `"pymilvus>=3.0"`(对应 Milvus v3.0.0)、`"openai>=3.31"`(当前 venv 已安装 3.31.0,显式声明)。Run `uv sync`。

`.env.example` 增加 `EMBEDDING_API_KEY=<你的硅基流动 API Key>`;`.env` 已由主会话写入真实值,实现者不得把真实值复制到任何跟踪文件。`.gitignore` 增加 `log/`、`deploy/milvus/volumes/`。

- [ ] **Step 5: 运行测试并提交**

Run: `uv run pytest tests/test_db_models.py tests/test_config.py -v`
Expected: PASS。

```bash
git add app/config.py app/models.py pyproject.toml uv.lock .env.example .gitignore tests/test_db_models.py
git commit -m "feat(ch03): 知识库配置与 knowledge_chunks/staging ORM 映射"
```

---

### Task 2: Milvus Standalone 部署与连通性

**Files:**
- Create: `deploy/milvus/docker-compose.yml`
- Modify: `.gitignore`(若 Task 1 未加)
- Test: 命令验证,无单测

**Interfaces:**
- Produces: 本机 `http://127.0.0.1:19530` 可连接的 Milvus;后续 Task 6、8、15 使用。

- [ ] **Step 1: 下载官方 compose**

Run:
```powershell
New-Item -ItemType Directory -Force deploy\milvus | Out-Null
Invoke-WebRequest -Uri "https://github.com/milvus-io/milvus/releases/download/v3.0.0/milvus-standalone-docker-compose.yml" -OutFile deploy\milvus\docker-compose.yml -UseBasicParsing
```

Expected: 文件存在,包含 `milvusdb/milvus:v3.0.0`、`19530:19530`、`etcd`、`minio` 三个服务。

- [ ] **Step 2: 启动并等待健康**

Run:
```powershell
docker compose -f deploy/milvus/docker-compose.yml up -d
Start-Sleep -Seconds 20
docker compose -f deploy/milvus/docker-compose.yml ps
```
Expected: `milvus-etcd`、`milvus-minio`、`milvus-standalone` 均为 `Up`;standalone 的 healthcheck 在 90 秒内变 `healthy`。若内存不足导致退出,停下来记录 `docker logs milvus-standalone`,向用户报告,不要自行换方案。

- [ ] **Step 3: 验证端口与 pymilvus 连接**

Run:
```powershell
uv run python -c "from pymilvus import MilvusClient; c=MilvusClient('http://127.0.0.1:19530'); print(c.list_collections())"
```
Expected: 输出 `[]` 或已有集合列表,不抛异常。

- [ ] **Step 4: 提交**

```bash
git add deploy/milvus/docker-compose.yml .gitignore
git commit -m "chore(ch03): Milvus Standalone v3.0.0 compose 部署"
```

---

### Task 3: 文档切分器(结构识别与切块)

**Files:**
- Create: `app/kb/__init__.py`
- Create: `app/kb/documents.py`
- Create: `tests/test_kb_documents.py`

**Interfaces:**
- Produces:
  - `CHUNK_TARGET = 500`、`CHUNK_OVERLAP = 100`、`SENTENCE_ENDS = "。！？；"`
  - `@dataclass(frozen=True) class Section: ordinal: int; heading_path: tuple[str, ...]; body: str; features: tuple[str, ...]`
  - `split_sections(markdown: str, document_title: str) -> list[Section]`
  - 特性常量:`FEATURE_RECURSIVE = "recursive_split"`、`FEATURE_SENTENCE = "sentence_trimmed"`、`FEATURE_OVERLAP = "overlap_applied"`、`FEATURE_TABLE_HEADER = "table_header_replicated"`

- [ ] **Step 1: 写失败的切分测试**

在 `tests/test_kb_documents.py` 写以下断言(可合并成多个 test 函数):

```python
from app.kb.documents import Section, split_sections

def test_heading_paths_and_ordinals():
    md = "# 售后政策\n\n## 退货\n\n正文一。\n\n### 退款\n\n正文二。\n"
    sections = split_sections(md, "售后手册")
    assert [s.heading_path for s in sections] == [("售后政策", "退货"), ("售后政策", "退货", "退款")]
    assert [s.ordinal for s in sections] == [0, 1]
    assert sections[0].body.strip() == "正文一。"

def test_content_before_first_heading_uses_document_title():
    sections = split_sections("前言正文。\n\n# 第一章\n\n第一章正文。", "手册")
    assert sections[0].heading_path == ("手册",)
    assert "前言正文" in sections[0].body

def test_long_section_splits_at_sentence_boundary():
    md = "# 条款\n\n" + "这是一句测试文本。" * 120  # 960 字,必然超 500
    sections = split_sections(md, "手册")
    assert len(sections) >= 2
    for s in sections:
        assert len(s.body) <= 500
        assert s.body.rstrip().endswith(("。", "！", "？", "；"))

def test_long_section_overlap_at_most_100():
    md = "# 条款\n\n" + "甲乙丙丁。" * 200
    sections = split_sections(md, "手册")
    # 后一块开头应与前一块末尾有非空交集,且不超过 100 字
    prev_end = sections[0].body[-100:]
    assert any(prev_end[i:i + 20] and prev_end[i:i + 20] in sections[1].body
               for i in range(0, 80, 10))
    assert len(sections[1].body) <= 600

def test_table_split_repeats_header():
    table = "| 商品 | 尺码 | 价格 |\n|---|---|---|\n" + "\n".join(
        f"| 商品{i} | M | {i}.00 |" for i in range(80)
    )
    md = f"# 价目表\n\n{table}\n"
    sections = split_sections(md, "商品FAQ")
    assert len(sections) >= 2
    for s in sections:
        assert "| 商品 | 尺码 | 价格 |" in s.body
        assert "|---|---|---|" in s.body

def test_huge_single_line_terminates():
    sections = split_sections("# 长文\n\n" + "字" * 2000, "手册")
    assert sections
    assert all(len(s.body) <= 600 for s in sections)

def test_empty_and_table_only_markdown():
    assert split_sections("", "手册") == []
    table_only = "| a | b |\n|---|---|\n| 1 | 2 |\n"
    sections = split_sections(table_only, "表格")
    assert len(sections) == 1
    assert "| a | b |" in sections[0].body
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_kb_documents.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'app.kb'`。

- [ ] **Step 3: 实现 `split_sections`**

在 `app/kb/documents.py` 实现:

- 逐行解析 ATX 标题,维护 `heading_stack: list[tuple[int, str]]`。
- 每个标题开启新 section;标题行不进入 body;遇到**任意级别**的新标题就结束当前 section(子标题内容归子 section,不重复计入父 section)。
- body 按空行聚合成 block;列表项按行成 block;遇到 `|` 表格识别为 table block(连续以 `|` 开头的行,且第二行是 `|---|---|`)。
- section 长度 ≤ 500 直接成一块;超长按 block 累加,超过 500 时从目标位置向前找 `。！？；` 或换行切;没有则硬切。
- 下一块开头带上一块末尾最多 100 字,并对齐到句子开头。
- 表格超长按行分组,每组重复表头 + 分隔行;单行超长独占一块。
- 每块设置对应 features。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_kb_documents.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/kb/__init__.py app/kb/documents.py tests/test_kb_documents.py
git commit -m "feat(ch03): Markdown 结构感知切分器"
```

---

### Task 4: 元数据、指纹、材料清单与样例文档

**Files:**
- Modify: `app/kb/documents.py`
- Create: `app/kb/sources.py`
- Create: `data/kb/退货政策.md`
- Create: `data/kb/商品FAQ.md`
- Create: `data/kb/售后手册.md`
- Create: `tests/test_kb_sources.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Source: path: Path; title: str; content_type: str`
  - `@dataclass(frozen=True) class Chunk: ordinal:int; category:str; questions:str; answer:str; section_path:str; content_type:str; is_key_clause:bool; features:tuple[str,...]`
  - `build_chunks(markdown: str, source: Source) -> list[Chunk]`
  - `normalize_text(text: str) -> str`
  - `chunk_fingerprint(questions: str, answer: str) -> str`
  - `embedding_text(category: str, questions: str, answer: str) -> str`
  - `classify_category(question: str, answer: str) -> str`
  - `sources.SOURCES: list[Source]`、`sources.get_sources(base_dir: Path | None = None) -> list[Source]`

- [ ] **Step 1: 写失败的元数据与指纹测试**

在 `tests/test_kb_documents.py` 追加:

```python
from pathlib import Path
from app.kb.documents import (
    Chunk, Source, build_chunks, chunk_fingerprint, classify_category,
    embedding_text, normalize_text,
)

def test_build_chunks_metadata_for_nested_headings():
    md = "# 退货政策\n\n## 退款说明\n\n重要:必须原路退回。\n"
    chunks = build_chunks(md, Source(Path("x.md"), "退货政策", "文档章节"))
    assert chunks[0].category == "退货政策"
    assert chunks[0].section_path == "退货政策 > 退款说明"
    assert chunks[0].questions == "退款说明"
    assert chunks[0].content_type == "文档章节"
    assert chunks[0].is_key_clause is True

def test_top_level_section_category_is_document_title():
    md = "# 配送\n\n正文。\n"
    chunk = build_chunks(md, Source(Path("x.md"), "售后手册", "文档章节"))[0]
    assert chunk.category == "售后手册"
    assert chunk.section_path == "配送"

def test_fingerprint_ignores_punctuation_and_whitespace():
    a = chunk_fingerprint("怎么 退货?", "7 天无理由。")
    b = chunk_fingerprint("怎么退货", "7天无理由")
    c = chunk_fingerprint("怎么退货", "15 天无理由")
    assert a == b
    assert a != c

def test_embedding_text_template():
    assert embedding_text("售后", "怎么退货?", "7 天无理由。") == (
        "分类:售后\n问题:怎么退货?\n内容:7 天无理由。"
    )

def test_classify_category_priority():
    assert classify_category("怎么退货", "7 天无理由") == "售后政策"
    assert classify_category("多久发货", "48 小时") == "物流配送"
    assert classify_category("优惠券怎么用", "结算时抵扣") == "支付优惠"
    assert classify_category("积分怎么用", "抵现") == "账户会员"
    assert classify_category("尺码怎么选", "看尺码表") == "商品咨询"
    assert classify_category("今天天气", "很好") == "其他"
```

- [ ] **Step 2: 写失败的材料清单测试**

在 `tests/test_kb_sources.py`:

```python
from pathlib import Path
from app.kb.sources import SOURCES, get_sources

def test_sources_are_three_markdown_files():
    assert len(SOURCES) == 3
    assert {s.content_type for s in SOURCES} == {"文档章节", "商品FAQ"}
    assert all(s.path.exists() for s in SOURCES)

def test_shipping_fee_section_exists():
    text = (Path("data/kb/退货政策.md")).read_text(encoding="utf-8")
    assert "运费" in text or "邮费" in text

def test_get_sources_respects_base_dir(tmp_path):
    (tmp_path / "a.md").write_text("# 标题\n\n正文", encoding="utf-8")
    # 自定义目录只返回该目录下的 md,content_type 默认 文档章节
    result = get_sources(tmp_path)
    assert len(result) == 1
    assert result[0].content_type == "文档章节"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/test_kb_documents.py tests/test_kb_sources.py -v`
Expected: FAIL on missing `build_chunks` / `app.kb.sources`。

- [ ] **Step 4: 实现元数据与材料清单**

- `build_chunks` 调 `split_sections`,跳过 `body.strip()` 为空的 section(只含子标题的父 section 不成块),把其余 section 转成 Chunk:
  - `category`:去掉当前标题后的父路径,`" > "` 连接;父路径为空时用 `source.title`。
  - `section_path`:完整 `heading_path` 用 `" > "` 连接。
  - `questions`:当前标题(`heading_path[-1]`);前言 section 用 `source.title`。
  - `answer`:`section.body.strip()`。
  - `content_type`:`source.content_type`。
  - `is_key_clause`:标题或正文命中 `重要|注意|必须|禁止|不予|务必|仅限|免责`,或正文整段被 `**...**` / `> ` 包裹。
- `normalize_text`:全角转半角、小写、去空白、去中英文标点。
- `chunk_fingerprint`: `hashlib.sha256((normalize_text(q) + "\n" + normalize_text(a)).encode()).hexdigest()`。
- `classify_category`:关键词规则,优先级售后 > 物流 > 支付 > 账户 > 商品 > 其他。
- `sources.py`:`SOURCES` 指向 `data/kb/退货政策.md`(文档章节)、`data/kb/商品FAQ.md`(商品FAQ)、`data/kb/售后手册.md`(文档章节);`get_sources(base_dir)` 在传入目录时扫描 `*.md`,标题取文件名 stem,`content_type` 默认 `文档章节`,文件名含「FAQ」时用 `商品FAQ`。
- 三份样例文档必须包含:多级标题、一段 >500 字的长文、一张 >20 行的大表格、一段含「运费/邮费」的说明、至少一条「重要/注意」条款。`商品FAQ.md` 用 `## 问题?` 作为章节标题。

- [ ] **Step 5: 运行测试并提交**

Run: `uv run pytest tests/test_kb_documents.py tests/test_kb_sources.py -v`
Expected: PASS。

```bash
git add app/kb/documents.py app/kb/sources.py data/kb tests/test_kb_documents.py tests/test_kb_sources.py
git commit -m "feat(ch03): 知识元数据、指纹与建库材料清单"
```

---

### Task 5: BGE-M3 嵌入封装

**Files:**
- Create: `app/kb/embedding.py`
- Create: `tests/test_kb_embedding.py`

**Interfaces:**
- Produces: `class EmbeddingError(RuntimeError)`;`embed_texts(texts: Sequence[str]) -> list[list[float]]`。
- Consumes: `get_settings()`。

- [ ] **Step 1: 用 Context7 核对 OpenAI SDK 调用姿势**

查询 `/openai/openai-python` 的 `client.embeddings.create(model=..., input=[...])` 与 `base_url` 构造;确认返回 `response.data[i].embedding`。把结论用于实现,不新建文档。

- [ ] **Step 2: 写失败的嵌入测试**

`tests/test_kb_embedding.py`:

```python
from types import SimpleNamespace
import pytest
import app.kb.embedding as emb

class FakeEmbeddings:
    def __init__(self, dim=1024, fail_times=0):
        self.calls = []
        self.dim = dim
        self.fail_times = fail_times
    def create(self, *, model, input):
        self.calls.append({"model": model, "input": input})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("temporary")
        return SimpleNamespace(data=[
            SimpleNamespace(embedding=[0.1] * self.dim, index=i)
            for i in range(len(input))
        ])

def test_empty_input_returns_empty_without_call(monkeypatch):
    fake = FakeEmbeddings()
    monkeypatch.setattr(emb, "_client", SimpleNamespace(embeddings=fake))
    assert emb.embed_texts([]) == []
    assert fake.calls == []

def test_batches_of_32(monkeypatch):
    fake = FakeEmbeddings()
    monkeypatch.setattr(emb, "_client", SimpleNamespace(embeddings=fake))
    vectors = emb.embed_texts([f"t{i}" for i in range(65)])
    assert len(vectors) == 65
    assert [len(c["input"]) for c in fake.calls] == [32, 32, 1]

def test_dimension_mismatch_raises(monkeypatch):
    fake = FakeEmbeddings(dim=768)
    monkeypatch.setattr(emb, "_client", SimpleNamespace(embeddings=fake))
    with pytest.raises(emb.EmbeddingError, match="1024"):
        emb.embed_texts(["x"])

def test_retries_three_times_then_raises(monkeypatch):
    fake = FakeEmbeddings(fail_times=3)
    monkeypatch.setattr(emb, "_client", SimpleNamespace(embeddings=fake))
    with pytest.raises(emb.EmbeddingError):
        emb.embed_texts(["x"])
    assert len(fake.calls) == 3

def test_missing_api_key_raises_clear_error(monkeypatch):
    monkeypatch.setattr(emb, "get_settings",
                        lambda: SimpleNamespace(embedding_api_key="",
                                                embedding_base_url="x",
                                                embedding_model="m",
                                                embedding_dim=1024,
                                                embedding_batch_size=32))
    monkeypatch.setattr(emb, "_client", None)
    with pytest.raises(emb.EmbeddingError, match="EMBEDDING_API_KEY"):
        emb.embed_texts(["x"])
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/test_kb_embedding.py -v`
Expected: FAIL,module not found。

- [ ] **Step 4: 实现 `embed_texts`**

- `_client` 模块级懒加载:`openai.OpenAI(base_url=settings.embedding_base_url, api_key=settings.embedding_api_key)`。
- 空列表直接返回 `[]`。
- 按 `settings.embedding_batch_size` 切片;每批 `client.embeddings.create(model=settings.embedding_model, input=batch)`。
- 按 `data[i].index` 排序,校验每条长度等于 `settings.embedding_dim`,否则抛 `EmbeddingError("...1024...")`。
- 每批失败重试 3 次,指数退避 0.5/1/2 秒;3 次都失败抛 `EmbeddingError`,消息含原始异常。
- `embedding_api_key` 为空时抛 `EmbeddingError("未配置 EMBEDDING_API_KEY,请写入 .env")`。

- [ ] **Step 5: 真实 API 探针(不进测试)**

Run:
```powershell
uv run python -c "from app.kb.embedding import embed_texts; v=embed_texts(['邮费是多少']); print(len(v), len(v[0]))"
```
Expected: `1 1024`。若返回 401/404,停下检查 `.env` 的 `EMBEDDING_API_KEY` 与 base_url,不要改代码硬扛。

- [ ] **Step 6: 运行测试并提交**

Run: `uv run pytest tests/test_kb_embedding.py -v`
Expected: PASS。

```bash
git add app/kb/embedding.py tests/test_kb_embedding.py
git commit -m "feat(ch03): BGE-M3 嵌入批量封装与重试"
```

---

### Task 6: Milvus 客户端封装

**Files:**
- Create: `app/kb/milvus.py`
- Create: `tests/test_kb_milvus.py`
- Create: `tests/test_kb_milvus_integration.py`

**Interfaces:**
- Produces:
  - `class MilvusUnavailable(RuntimeError)`
  - `get_client()`
  - `ping() -> bool`
  - `ensure_collection() -> None`
  - `upsert(ids: Sequence[str], vectors: Sequence[Sequence[float]]) -> None`
  - `search(vector: Sequence[float], limit: int) -> list[tuple[str, float]]`
  - `flush() -> None`
  - `drop() -> None`
  - `count() -> int`
  - `list_ids(max_count: int = 5000) -> list[str]`
- Consumes: `Settings.milvus_uri/milvus_token/knowledge_collection/embedding_dim`。

- [ ] **Step 1: 写失败的封装测试**

`tests/test_kb_milvus.py` 用 Fake client 替换 `app.kb.milvus._client`:

```python
def test_ensure_collection_skips_when_exists():
    fake = FakeClient(has=True)
    monkeypatch.setattr(milvus, "_client", fake)
    milvus.ensure_collection()
    assert fake.created == []

def test_ensure_collection_schema_dim_and_metric():
    fake = FakeClient(has=False)
    monkeypatch.setattr(milvus, "_client", fake)
    milvus.ensure_collection()
    assert fake.created[0]["collection_name"] == "knowledge"
    assert fake.created[0]["schema"].has_field("id")
    assert fake.created[0]["schema"].has_field("vector", dim=1024)

def test_upsert_stringifies_ids_and_batches():
    fake = FakeClient()
    monkeypatch.setattr(milvus, "_client", fake)
    milvus.upsert([1, 2], [[0.1] * 1024, [0.2] * 1024])
    assert fake.upserts[0] == [
        {"id": "1", "vector": [0.1] * 1024},
        {"id": "2", "vector": [0.2] * 1024},
    ]

def test_search_returns_id_score_pairs_in_order():
    fake = FakeClient(search_result=[[
        {"id": "7", "distance": 0.81},
        {"id": "3", "distance": 0.62},
    ]])
    monkeypatch.setattr(milvus, "_client", fake)
    assert milvus.search([0.0] * 1024, 3) == [("7", 0.81), ("3", 0.62)]

def test_ping_returns_false_on_error():
    fake = FakeClient(raise_on="list_collections")
    monkeypatch.setattr(milvus, "_client", fake)
    assert milvus.ping() is False

def test_list_ids_uses_query():
    fake = FakeClient(query_result=[{"id": "1"}, {"id": "2"}])
    monkeypatch.setattr(milvus, "_client", fake)
    assert milvus.list_ids() == ["1", "2"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_kb_milvus.py -v`
Expected: FAIL,module not found。

- [ ] **Step 3: 实现封装**

- `get_client()` 懒加载 `MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token or None)`。
- `ensure_collection()`:`has_collection` 为真直接返回;否则 `create_schema(auto_id=False)` + `add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)` + `add_field("vector", DataType.FLOAT_VECTOR, dim=settings.embedding_dim)`;`IndexParams().add_index("vector", index_type="AUTOINDEX", metric_type="COSINE")`;`create_collection(collection_name, schema=..., index_params=...)`;`load_collection`。
- `upsert`:校验 `len(ids)==len(vectors)`,`data=[{"id": str(i), "vector": list(v)} ...]`,调用 `client.upsert(collection_name, data)`。
- `search`:调用 `client.search(collection_name, data=[list(vector)], limit=limit, output_fields=["id"], search_params={"metric_type": "COSINE"})`,把 `res[0]` 转成 `(str(hit["id"]), float(hit["distance"]))`。
- `ping()`:调用 `list_collections()`,异常返回 False。
- `list_ids`: `client.query(collection_name, filter='id != ""', output_fields=["id"], limit=max_count)`。
- `flush/drop/count` 直接代理;`count` 用 `query` 的 count(*) 或 `get_collection_stats` 的 `row_count`。

- [ ] **Step 4: 注册 integration marker 并写真实集成测试**

在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 增加 `markers = ["integration: 需要真实外部服务的集成测试"]`。

`tests/test_kb_milvus_integration.py`:

```python
import pytest
from app.kb import milvus as milvus_mod

@pytest.mark.integration
def test_milvus_roundtrip():
    if not milvus_mod.ping():
        pytest.skip("Milvus 未启动")
    test_collection = "knowledge_test"
    # 用 monkeypatch 把 settings.knowledge_collection 指到测试集合
    ...
```

实现时用 `monkeypatch.setattr` 覆盖 `milvus_mod.get_settings` 返回的 collection 名;建集合 → upsert 2 条 → flush → search → 断言 PK/分数 → drop。

- [ ] **Step 5: 运行测试并提交**

Run: `uv run pytest tests/test_kb_milvus.py -v`
Run: `uv run pytest tests/test_kb_milvus_integration.py -v`(Milvus 已启动则应 PASS,否则 SKIP)
Expected: unit PASS。

```bash
git add app/kb/milvus.py tests/test_kb_milvus.py tests/test_kb_milvus_integration.py
git commit -m "feat(ch03): Milvus 客户端封装与集成测试"
```

---

### Task 7: MySQL 知识仓储

**Files:**
- Create: `app/kb/store.py`
- Create: `tests/test_kb_store.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class ChunkCandidate: category:str; questions:str; answer:str; section_path:str; content_type:str; is_key_clause:bool`
  - `@dataclass(frozen=True) class PendingChunk: id:int; category:str; questions:str; answer:str`
  - `@dataclass(frozen=True) class StoredChunk: id:int; category:str; questions:str; answer:str; section_path:str|None; content_type:str|None; is_key_clause:bool; vectorize_status:str; vector_id:str|None; prev_chunk_id:int|None; next_chunk_id:int|None`
  - `@dataclass(frozen=True) class StagingCandidate: batch_no:str; source_ref:str|None; question:str; answer:str`
  - `@dataclass(frozen=True) class StagingRow: id:int; batch_no:str; source_ref:str|None; question:str; answer:str; status:str; created_at`
  - `@dataclass(frozen=True) class ConversationForMining: id:int; user_id:str; max_message_id:int; turns:tuple[tuple[int,str,str],...]`(每个 turn 为 `(message_id, role, content)`,按 message_id 升序)
  - `insert_chunks(candidates, session=None) -> list[int]`
  - `link_siblings(ids, session=None) -> None`
  - `iter_pending(limit=32) -> list[PendingChunk]`
  - `mark_done(ids, session=None) -> None`
  - `fetch_by_ids(ids) -> dict[int, StoredChunk]`
  - `existing_fingerprints() -> set[str]`
  - `existing_questions_normalized() -> set[str]`
  - `done_ids() -> list[str]`
  - `overview_counts() -> dict[str, int]`
  - `list_recent(limit=20) -> list[StoredChunk]`
  - `reset_all_pending() -> None`
  - `delete_all_chunks() -> None`
  - `staging_counts() -> dict[str, int]`(键:`extracted`、`kept`、`discarded`、`batches`)
  - `list_staging(status=None, limit=50) -> list[StagingRow]`
  - `insert_staging(rows) -> list[int]`
  - `mark_staging(ids, status) -> None`
  - `list_conversations_for_mining() -> list[ConversationForMining]`
  - `staging_tail(conversation_id: int) -> int | None`

- [ ] **Step 1: 写失败的仓储测试**

使用与 `tests/test_tools.py` 同款的回滚 fixture,但替换 `app.kb.store.get_session_local`:

```python
@pytest.fixture
def kb_tx(monkeypatch):
    with get_engine().connect() as conn:
        outer = conn.begin()
        session_local = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
        monkeypatch.setattr(store, "get_session_local", lambda: session_local)
        try:
            yield conn
        finally:
            outer.rollback()
```

测试至少覆盖:

```python
def test_insert_pending_and_link_siblings(kb_tx):
    ids = store.insert_chunks([
        store.ChunkCandidate("售后", "问1", "答1", "售后 > 节1", "文档章节", False),
        store.ChunkCandidate("售后", "问2", "答2", "售后 > 节2", "文档章节", False),
    ])
    store.link_siblings(ids)
    rows = store.fetch_by_ids(ids)
    assert rows[ids[0]].next_chunk_id == ids[1]  # 若 StoredChunk 含 prev/next,请一并暴露
    assert store.iter_pending()[0].id == ids[0]

def test_mark_done_and_overview_counts(kb_tx):
    ids = store.insert_chunks([store.ChunkCandidate("售后", "问", "答", "路径", "文档章节", False)])
    store.mark_done(ids)
    counts = store.overview_counts()
    assert counts["done"] == 1 and counts["pending"] == 0

def test_existing_fingerprints_uses_questions_and_answer(kb_tx):
    store.insert_chunks([store.ChunkCandidate("售后", "怎么 退货?", "7 天无理由。", "路径", "文档章节", False)])
    from app.kb.documents import chunk_fingerprint
    assert chunk_fingerprint("怎么退货", "7天无理由") in store.existing_fingerprints()

def test_staging_roundtrip(kb_tx):
    ids = store.insert_staging([store.StagingCandidate("B1", "conv:1:msgs:1-3", "问", "答")])
    assert store.staging_counts()["extracted"] == 1
    store.mark_staging(ids, "kept")
    assert store.list_staging("kept")[0].question == "问"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_kb_store.py -v`
Expected: FAIL,module not found。

- [ ] **Step 3: 实现仓储**

- 所有函数接受可选 `session: Session | None`;为 None 时 `with get_session_local()() as s, s.begin():`。
- `insert_chunks` 用 `session.add_all`,然后 `session.flush()` 拿自增 id,按输入顺序返回;新行 `vectorize_status` 交给数据库默认 `pending`。
- `mark_done(ids)`:对每个 id 执行 `UPDATE knowledge_chunks SET vector_id=CAST(id AS CHAR), vectorize_status='done' WHERE id IN (...)`(或逐行赋值),保证 `vector_id == str(id)`。
- `link_siblings`:按传入顺序把第 i 条 `next_chunk_id` 设为 i+1 的 id,第 i+1 条 `prev_chunk_id` 设为 i 的 id;首尾为 NULL。
- `existing_fingerprints`: `select(KnowledgeChunk.questions, KnowledgeChunk.answer)`,用 `chunk_fingerprint` 计算集合。
- `existing_questions_normalized`: `select(KnowledgeChunk.questions)`,用 `normalize_text` 计算集合,供挖掘精确去重。
- `done_ids`: `select(KnowledgeChunk.id).where(vectorize_status == "done")`,返回 `[str(id) ...]`。
- `delete_all_chunks`:先 `update(KnowledgeChunk).values(prev_chunk_id=None, next_chunk_id=None)`,再 `delete(KnowledgeChunk)`。
- `list_conversations_for_mining`:查 `conversations` 按 id 升序;每个会话查 `messages` 中 role in ('user','assistant') 的内容,过滤空 content,按 message.id 升序;`max_message_id` 取该会话最大 message.id。只返回至少一条 user 和一条 assistant 的会话。
- `staging_tail`:`select(QaExtractionStaging.source_ref).where(source_ref.like(f"conv:{id}:msgs:%"))`,解析 `-` 后的 last 取最大值;没有返回 None。

- [ ] **Step 4: 运行测试并提交**

Run: `uv run pytest tests/test_kb_store.py -v`
Expected: PASS,且结束后 `knowledge_chunks` / `qa_extraction_staging` 行数为 0(回滚)。

```bash
git add app/kb/store.py tests/test_kb_store.py
git commit -m "feat(ch03): 知识块与暂存表 MySQL 仓储"
```

---

### Task 8: 双写状态机与一致性核对

**Files:**
- Create: `app/kb/dualwrite.py`
- Create: `tests/test_kb_dualwrite.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class VectorizeResult: processed:int; failed:int; pending_left:int`
  - `@dataclass(frozen=True) class Consistency: status:str; missing:tuple[str,...]; orphan:tuple[str,...]; note:str`
  - `vectorize_pending(limit: int | None = None) -> VectorizeResult`
  - `rebuild_all() -> VectorizeResult`
  - `reset_all() -> None`
  - `consistency_snapshot() -> Consistency`

- [ ] **Step 1: 写失败的测试**

用真实 MySQL 回滚 + Fake embedding/milvus:

```python
def test_pending_becomes_done_and_upserts_str_id(kb_tx, monkeypatch):
    ids = store.insert_chunks([store.ChunkCandidate("售后", "问", "答", "路径", "文档章节", False)])
    fake_milvus = FakeMilvus()
    monkeypatch.setattr(dualwrite, "embed_texts", lambda texts: [[0.1] * 1024 for _ in texts])
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)
    result = dualwrite.vectorize_pending()
    assert result.processed == 1 and result.pending_left == 0
    assert fake_milvus.upserted[0]["id"] == str(ids[0])
    assert store.fetch_by_ids(ids)[ids[0]].vectorize_status == "done"

def test_embedding_failure_leaves_pending(kb_tx, monkeypatch):
    store.insert_chunks([store.ChunkCandidate("售后", "问", "答", "路径", "文档章节", False)])
    monkeypatch.setattr(dualwrite, "embed_texts", lambda texts: (_ for _ in ()).throw(RuntimeError("boom")))
    result = dualwrite.vectorize_pending()
    assert result.failed == 1 and result.pending_left == 1

def test_rerun_after_failure_converges(kb_tx, monkeypatch):
    ... # 第一次失败,第二次成功;断言只有一行、一条向量

def test_consistency_unknown_when_milvus_down(kb_tx, monkeypatch):
    fake = FakeMilvus(raise_on_ping=True)
    monkeypatch.setattr(dualwrite, "milvus", fake)
    assert dualwrite.consistency_snapshot().status == "unknown"

def test_consistency_mismatch_lists_missing_and_orphan(kb_tx, monkeypatch):
    ... # MySQL 有 done 行,Milvus list_ids 少一个/多一个
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_kb_dualwrite.py -v`
Expected: FAIL,module not found。

- [ ] **Step 3: 实现双写**

- `vectorize_pending(limit=None)`:
  - 循环 `store.iter_pending(settings.embedding_batch_size)`,直到没有或达到 limit。
  - 每批:对每个 `PendingChunk` 调 `embedding_text(p.category, p.questions, p.answer)`;`embed_texts`;`milvus.upsert([str(p.id) ...], vectors)`;`store.mark_done(ids)`;`milvus.flush()`。
  - 捕获单批异常:累计 `failed += len(batch)`,继续下一批(不吞掉日志,打印错误)。
  - 返回 `processed`、`failed`、`pending_left = len(store.iter_pending(limit=10**9))` 或 `overview_counts()["pending"]`。
- `rebuild_all()`:`store.reset_all_pending()`;`milvus.drop()`;`milvus.ensure_collection()`;`vectorize_pending()`。
- `reset_all()`:`store.delete_all_chunks()`;`milvus.drop()`。
- `consistency_snapshot()`:
  - `milvus.ping()` False → `Consistency("unknown", (), (), "Milvus 读不到")`。
  - MySQL 查询失败 → `unknown`。
  - 两边都可达:`done_ids = {str(id) ...}`(store 需提供 `done_ids()`;若没有,用 `fetch` 全量),`milvus_ids = set(milvus.list_ids())`;相等 → `consistent`;否则 `mismatch`,missing/orphan 排序返回。
  - `note` 说明「精确 id 比对」或「仅数量比对」。

- [ ] **Step 4: 运行测试并提交**

Run: `uv run pytest tests/test_kb_dualwrite.py -v`
Expected: PASS。

```bash
git add app/kb/dualwrite.py tests/test_kb_dualwrite.py
git commit -m "feat(ch03): MySQL/Milvus 双写状态机与断点续跑"
```

---

### Task 9: 在线检索与 `query_faq` 替换

**Files:**
- Create: `app/kb/retriever.py`
- Modify: `app/tools.py`
- Modify: `tests/test_tools.py`
- Create: `tests/test_kb_retriever.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Hit: chunk_id:int; score:float; category:str; questions:str; answer:str; content_type:str; section_path:str`
  - `search_hits(query: str, top_k: int | None = None, min_score: float | None = None) -> list[Hit]`
  - `retrieve(query: str) -> str`
- Consumes: `embed_texts`、`milvus.search`、`store.fetch_by_ids`、`get_settings`。

- [ ] **Step 1: 写失败的检索测试**

`tests/test_kb_retriever.py`:

```python
def test_empty_query_returns_no_match_without_embedding(monkeypatch):
    called = []
    monkeypatch.setattr(retriever, "embed_texts", lambda texts: called.append(texts) or [[0.0] * 1024])
    assert "没有找到" in retriever.retrieve("   ")
    assert called == []

def test_filters_by_min_score_and_preserves_order(monkeypatch):
    monkeypatch.setattr(retriever, "embed_texts", lambda texts: [[0.0] * 1024])
    monkeypatch.setattr(retriever, "milvus", FakeMilvus([("2", 0.81), ("1", 0.49)]))
    monkeypatch.setattr(retriever, "store", FakeStore(...))
    hits = retriever.search_hits("邮费是多少", top_k=3, min_score=0.5)
    assert [h.chunk_id for h in hits] == [2]

def test_retrieve_formats_question_answer_blocks(monkeypatch):
    ...
    assert result == "1. 运费怎么算?\n→ 满 99 包邮。"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_kb_retriever.py -v`
Expected: FAIL,module not found。

- [ ] **Step 3: 实现检索**

- `search_hits`: `query.strip()` 为空返回 `[]`;`embed_texts([query])[0]`;`milvus.search(vector, top_k)`;过滤 `score >= min_score`;`store.fetch_by_ids`;按命中顺序组装 `Hit`,跳过 MySQL 查不到的 id(记 warning)。
- `retrieve`: hits 为空 → `f'FAQ 库中没有找到与「{query}」相关的条目'`;否则 `"\n\n".join(f"{i}. {h.questions}\n→ {h.answer}" ...)`。
- 不捕获 `MilvusUnavailable` / `EmbeddingError`;让它们冒泡到执行器。

- [ ] **Step 4: 替换 `app/tools.py` 的 `query_faq`**

```python
from app.kb.retriever import retrieve

@tool(args_schema=QueryFaqArgs)
def query_faq(keyword: str) -> str:
    """检索店铺常见问题知识库(FAQ),返回与问题最相关的问答条目。..."""
    return retrieve(keyword)
```

- 删除 `Faq` import 与 `select` 中不再使用的部分(保留 `_next_ticket_no` 需要的 `func`/`select`)。
- `QueryFaqArgs.keyword` 的 description 改为:「用户原话中的问题或关键词,完整问句效果更好,例如 退货政策、邮费怎么算、尺码怎么选」。

- [ ] **Step 5: 改写 `tests/test_tools.py` 的 query_faq 用例**

删除依赖 LIKE 的 5 个用例(`test_query_faq_hits_return_policy`、`test_query_faq_matches_answer_only_keyword`、`test_query_faq_shipping_cost_no_match`、`test_query_faq_max_results_limit`、`test_query_faq_escapes_like_wildcards`),替换为:

```python
def test_query_faq_delegates_to_retriever(monkeypatch):
    seen = []
    monkeypatch.setattr(tools_mod, "retrieve", lambda q: seen.append(q) or "1. 问\n→ 答")
    assert query_faq_tool.invoke({"keyword": "邮费怎么算"}) == "1. 问\n→ 答"
    assert seen == ["邮费怎么算"]

def test_query_faq_description_mentions_full_question():
    schema = convert_to_openai_tool(query_faq_tool)
    assert "完整问句" in schema["function"]["description"] or "用户原话" in schema["function"]["description"]
```

`EXPECTED_ARGS["query_faq"] == {"keyword"}` 保持不变;`INJECTED_ARGS` 保持不变。清理因删除测试而不再使用的 import(`Faq`、`select` 等,若 `create_ticket` 仍用则保留)。

- [ ] **Step 6: 运行测试并提交**

Run: `uv run pytest tests/test_kb_retriever.py tests/test_tools.py -v`
Expected: PASS。

```bash
git add app/kb/retriever.py app/tools.py tests/test_kb_retriever.py tests/test_tools.py
git commit -m "feat(ch03): query_faq 切换为 Milvus 语义检索"
```

---

### Task 10: 建库 CLI

**Files:**
- Create: `app/kb/ingest.py`
- Create: `tests/test_kb_ingest.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class IngestResult: inserted:int; skipped:int; chunk_ids:tuple[int,...]`
  - `run_preview() -> list[dict]`
  - `ingest_docs() -> IngestResult`
  - `ingest_faq() -> IngestResult`
  - `build_all() -> IngestResult`
  - `rebuild() -> VectorizeResult`
  - `status_report() -> dict`
  - `reset_all(force: bool) -> None`
  - `main(argv: Sequence[str] | None = None) -> int`

- [ ] **Step 1: 写失败的 CLI 测试**

使用 `kb_tx` 同款回滚;monkeypatch `ingest.get_session_local`、`ingest.vectorize_pending`:

```python
def test_preview_does_not_write(kb_tx):
    preview = ingest.run_preview()
    assert len(preview) == 3
    assert all(item["chunks"] > 0 for item in preview)
    assert store.overview_counts()["chunks"] == 0

def test_docs_ingest_is_idempotent_by_fingerprint(kb_tx):
    first = ingest.ingest_docs()
    second = ingest.ingest_docs()
    assert first.inserted > 0
    assert second.inserted == 0
    assert second.skipped == first.inserted

def test_faq_ingest_maps_category_and_content_type(kb_tx):
    result = ingest.ingest_faq()
    assert result.inserted == 13
    rows = store.list_recent(limit=20)
    assert any(r.content_type == "商品FAQ" and r.category == "售后政策" for r in rows)

def test_build_calls_docs_faq_vectorize(kb_tx, monkeypatch):
    calls = []
    monkeypatch.setattr(ingest, "ingest_docs", lambda: calls.append("docs") or ingest.IngestResult(0, 0, ()))
    monkeypatch.setattr(ingest, "ingest_faq", lambda: calls.append("faq") or ingest.IngestResult(0, 0, ()))
    monkeypatch.setattr(ingest, "vectorize_pending", lambda: calls.append("vec") or None)
    ingest.build_all()
    assert calls == ["docs", "faq", "vec"]

def test_reset_requires_force(kb_tx):
    with pytest.raises(ValueError, match="--yes"):
        ingest.reset_all(force=False)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_kb_ingest.py -v`
Expected: FAIL,module not found。

- [ ] **Step 3: 实现 CLI**

- `run_preview`:遍历 `get_sources()`,读文件,`build_chunks`,返回每份 `{path, title, content_type, chunks, key_clauses, features}`。
- `ingest_docs`:先 `seen = store.existing_fingerprints()`;对每份 source 切块,过滤 `fingerprint in seen`;`store.insert_chunks(new)`;把新指纹并入 `seen`(避免同一轮多份材料之间重复插入);`store.link_siblings(ids)`;返回 inserted/skipped/ids。若同一份材料 `inserted>0 且 skipped>0`(部分块已存在),日志警告「该材料的前后块指针只覆盖新插入部分」,样例文档重跑时 `inserted=0` 不受影响。
- `ingest_faq`:查 `Faq` 全部行,构造 `ChunkCandidate(category=row.category, questions=row.question, answer=row.answer, section_path=f"商品FAQ > {row.category}", content_type="商品FAQ", is_key_clause=False)`,按指纹跳过,插入,返回。
- `build_all`:docs + faq + `dualwrite.vectorize_pending()`。
- `rebuild`:直接调 `dualwrite.rebuild_all()`(保留原文、重置全部 pending、重刷向量)。
- `status_report`:返回 `store.overview_counts()` + `milvus.ping()`/`count()` + `consistency_snapshot()`;Milvus 不可达时 `milvus_ok=False`。
- `reset_all(force)`:force 为假抛 `ValueError("reset 是破坏性操作,必须显式传 --yes")`;否则 `dualwrite.reset_all()`。
- `main`:argparse 子命令 `preview/docs/faq/vectorize/rebuild/build/status/reset`,reset 带 `--yes`;`rebuild` 调 `rebuild()`;打印中文结果并返回 0;异常打印到 stderr 返回 1。

- [ ] **Step 4: 运行测试与 CLI 冒烟**

Run:
```
uv run pytest tests/test_kb_ingest.py -v
uv run python -m app.kb.ingest preview
```
Expected: 测试 PASS;preview 打印 3 份材料的块数,不写库。

- [ ] **Step 5: 提交**

```bash
git add app/kb/ingest.py tests/test_kb_ingest.py
git commit -m "feat(ch03): 建库 CLI(preview/docs/faq/vectorize/build/status/reset)"
```

---

### Task 11: 对话挖掘 job 与演示会话

**Files:**
- Create: `app/kb/mining.py`
- Create: `app/kb/seed_conversations.py`
- Create: `tests/test_kb_mining.py`
- Create: `tests/test_kb_seed_conversations.py`
- Create: `docs/samples/ch03-mining-samples.md`
- Create: `scripts/eval_mining.py`

**Interfaces:**
- Produces:
  - `class QAPair(BaseModel): conversation_id: int; question: str; answer: str`
  - `class QAPairList(BaseModel): pairs: list[QAPair]`
  - `@dataclass(frozen=True) class MiningResult: conversations:int; extracted:int; kept:int; discarded:int`
  - `build_mining_model(settings) -> Runnable`
  - `run_once(limit: int | None = None) -> MiningResult`
  - `main(argv: Sequence[str] | None = None) -> int`
  - `seed_conversations.main() -> int`

- [ ] **Step 1: 用 Context7 核对 LangChain 结构化输出**

查询 `/websites/langchain_oss` 的 `with_structured_output(PydanticModel, method="function_calling")`,确认返回对象可直接 `.pairs`。实现使用 `build_chat_model(settings).with_structured_output(QAPairList, method="function_calling")`。

- [ ] **Step 2: 写失败的挖掘测试**

```python
class FakeExtractor:
    def __init__(self, result):
        self.result = result
        self.calls = 0
    def invoke(self, messages):
        self.calls += 1
        return self.result

def test_empty_pairs_writes_nothing(kb_tx, monkeypatch):
    monkeypatch.setattr(mining, "build_mining_model", lambda s: FakeExtractor(QAPairList(pairs=[])))
    result = mining.run_once()
    assert result.extracted == 0
    assert store.staging_counts()["extracted"] == 0

def test_extracted_pairs_go_to_staging(kb_tx, monkeypatch):
    ... # 预插一条 conversation + user/assistant 消息
    result = mining.run_once()
    assert store.staging_counts()["extracted"] + store.staging_counts()["kept"] == 1

def test_exact_duplicate_marked_discarded(kb_tx, monkeypatch):
    ... # 库内已有同问句;断言 status='discarded'

def test_vector_duplicate_marked_discarded(kb_tx, monkeypatch):
    ... # 假 milvus.search 返回 score 0.99;断言 discarded

def test_skips_already_mined_conversation(kb_tx, monkeypatch):
    ... # staging.source_ref last == max message id;断言 extractor.calls == 0

def test_llm_failure_writes_no_staging(kb_tx, monkeypatch):
    ... # extractor.invoke 抛异常;断言 staging 行数 0,异常冒泡
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/test_kb_mining.py -v`
Expected: FAIL,module not found。

- [ ] **Step 4: 实现挖掘**

- `build_mining_prompt(conversations: Sequence[ConversationForMining]) -> str`:每个会话以 `### 会话 {id}` 开头,下面把 `(message_id, role, content)` 轮次拼成 `用户:` / `客服:` 文本;单条截断 500 字,总量截断 `mining_max_chars`。Prompt 要求每对问答必须带 `conversation_id`,并只从客服已作出的回答里提取。
- `run_once(limit=None)`:
  1. `dualwrite.vectorize_pending()`。
  2. `store.list_conversations_for_mining()`,按 `staging_tail(conv.id) >= conv.max_message_id` 跳过;取前 `limit` 个。
  3. 按 `settings.mining_batch_size` 分组;每组调用 extractor;成功才 `store.insert_staging`。每对问答按 `conversation_id` 找到对应会话;找不到的 id 记 warning 并丢弃。暂存行 `source_ref = f"conv:{conv.id}:msgs:{conv.turns[0][0]}-{conv.max_message_id}"`,`batch_no` 每组一个 `M{YYYYMMDDHHMMSS}-{8位随机}`。
  4. 对每条 staging:`normalize_text(question)` 与 `store.existing_questions_normalized()` 及本轮 kept 比较;命中 → `mark_staging(discarded)`。
  5. 否则 `embed_texts([question])` → `milvus.search(vec, 1)`;Top-1 score >= `knowledge_dedup_score` → discarded。
  6. 其余:`classify_category` → `store.insert_chunks` → `mark_staging(kept)` → `dualwrite.vectorize_pending()`。
- `main`:argparse `--once`(默认)/`--limit`/`--interval`;`--interval` 时循环 `run_once` + `time.sleep`。
- `seed_conversations.main()`:插入 3 组 `user_id` 以 `seed-ch03-` 开头的会话,内容覆盖可抽取问答、含订单号的一次性信息、纯寒暄;同 user_id 已存在则跳过。用 `get_session_local()`。

- [ ] **Step 5: 写 Prompt 样例与评估脚本**

`docs/samples/ch03-mining-samples.md`:6 组会话,3 组应抽出(售后/运费/尺码),3 组应不抽(纯寒暄、订单查询、无答案)。`scripts/eval_mining.py` 读取样例,调用真实 DeepSeek,打印每组抽出的问答对与预期对比。运行并记录结果(不要求自动断言全部通过,人工判定)。

- [ ] **Step 6: 运行测试与提交**

Run:
```
uv run pytest tests/test_kb_mining.py tests/test_kb_seed_conversations.py -v
uv run python -m app.kb.seed_conversations
```
Expected: 测试 PASS;seed 脚本幂等。

```bash
git add app/kb/mining.py app/kb/seed_conversations.py tests/test_kb_mining.py tests/test_kb_seed_conversations.py docs/samples/ch03-mining-samples.md scripts/eval_mining.py
git commit -m "feat(ch03): 对话挖知识 job 与演示会话"
```

---

### Task 12: 作业运行器与 Makefile

**Files:**
- Create: `app/core/__init__.py`
- Create: `app/core/jobs.py`
- Create: `app/api/__init__.py`
- Create: `app/api/jobs.py`
- Create: `Makefile`
- Create: `tests/test_jobs.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class JobSpec: name:str; target:str; heavy:bool; description:str`
  - `@dataclass class JobState: status:str; started_at:str|None; finished_at:str|None; exit_code:int|None; process:object|None; log_path:Path|None`
  - `JOBS: dict[str, JobSpec]`
  - `start_job(name: str, *, confirm: bool = False) -> JobState`
  - `job_status(name: str) -> dict`
  - `list_jobs() -> list[dict]`
  - `reset_jobs_for_tests() -> None`
  - 异常:`UnknownJob`、`ConfirmationRequired`、`JobAlreadyRunning`、`MakeNotFound`

- [ ] **Step 1: 写失败的作业测试**

```python
def test_registry_has_seven_jobs():
    assert set(JOBS) == {"kb-preview", "kb-build", "seed-conv", "kb-mine",
                         "kb-vectorize", "kb-rebuild", "kb-reset"}

def test_unknown_job_raises():
    with pytest.raises(jobs.UnknownJob):
        jobs.start_job("rm-rf")

def test_heavy_requires_confirm():
    with pytest.raises(jobs.ConfirmationRequired):
        jobs.start_job("kb-reset", confirm=False)

def test_single_flight(monkeypatch):
    ... # 假 Popen 不结束;第二次 start_job 抛 JobAlreadyRunning

def test_argv_is_fixed_and_shell_false(monkeypatch):
    captured = {}
    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv; captured["kwargs"] = kwargs
        def poll(self): return 0
    monkeypatch.setattr(jobs.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(jobs.shutil, "which", lambda name: "C:/make.exe")
    jobs.start_job("kb-preview")
    assert captured["argv"][-1] == "kb-preview"
    assert captured["kwargs"]["shell"] is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_jobs.py -v`
Expected: FAIL,module not found。

- [ ] **Step 3: 实现运行器与 Makefile**

- `ROOT = Path(__file__).resolve().parents[2]`(app/core/jobs.py → 仓库根)。
- `JOBS` 七个目标,`heavy=True` 仅 `kb-mine`、`kb-reset`。
- `start_job`:校验名字 → heavy 确认 → 全局单飞 → `shutil.which(settings.make_exe)`(None 抛 `MakeNotFound`)→ 建 `log/acceptance` 目录 → 打开日志文件 `w` → `Popen([make, "-f", str(ROOT/"Makefile"), spec.target], cwd=ROOT, shell=False, stdout=log, stderr=STDOUT, text=True, encoding="utf-8", errors="replace")`。
- `job_status`:poll 进程,更新 `succeeded/failed`,`log_tail` 读最后 200 行。
- `list_jobs`:注册表 + 状态。
- `Makefile` 内容按 spec §15;配方行用 Tab。
- `app/api/jobs.py` 把异常映射为 404/403/409,返回 202。

- [ ] **Step 4: 运行测试与终端冒烟**

Run:
```
uv run pytest tests/test_jobs.py -v
make kb-preview
```
Expected: 测试 PASS;make 输出 3 份材料的切块统计。

- [ ] **Step 5: 提交**

```bash
git add app/core app/api/__init__.py app/api/jobs.py Makefile tests/test_jobs.py
git commit -m "feat(ch03): 白名单作业运行器与 Makefile"
```

---

### Task 13: 知识库 API

**Files:**
- Create: `app/api/kb.py`
- Modify: `app/main.py`
- Create: `tests/test_kb_api.py`

**Interfaces:**
- Produces: `router = APIRouter(prefix="/api/kb")`,供 `app.main` include。
- Request models:`PreviewRequest`、`IngestRequest`、`VectorizeRequest`、`SearchRequest`。
- Consumes: `documents.build_chunks`、`sources.get_sources`、`store`、`dualwrite`、`milvus`、`retriever`、`core.jobs`。

- [ ] **Step 1: 用 Context7 核对 FastAPI router**

查询 `/websites/fastapi_tiangolo` 的 `include_router` 与 `StaticFiles`;确认 `app.include_router(router)` 与 `app.mount("/static", StaticFiles(...))`。

- [ ] **Step 2: 写失败的 API 测试**

`tests/test_kb_api.py` 用既有 `client` fixture / `httpx.ASGITransport`:

```python
async def test_preview_is_dry_run(client, monkeypatch):
    ... # POST /api/kb/preview;断言 200、counts>0、store.overview_counts() 不变

async def test_ingest_vectorize_failure_returns_502_with_inserted(client, monkeypatch):
    monkeypatch.setattr(kb_api, "vectorize_pending", lambda: (_ for _ in ()).throw(RuntimeError("milvus down")))
    resp = await client.post("/api/kb/ingest", json={
        "markdown": "# 测试\n\n正文。", "source_title": "测试", "content_type": "文档章节", "vectorize": True})
    assert resp.status_code == 502
    assert "pending" in resp.json()["detail"]

async def test_overview_degrades_when_mysql_down(client, monkeypatch):
    ... # 让 store 抛 OperationalError;断言 mysql.ok=false, sources 非空

async def test_overview_consistency_unknown_when_milvus_down(client, monkeypatch):
    ... # milvus.ping False;断言 consistency.status == "unknown"

async def test_search_threshold_and_order(client, monkeypatch):
    ... # 假 hits;断言只返回 >=0.5 且顺序不变

async def test_markdown_too_large_returns_413(client):
    resp = await client.post("/api/kb/ingest", json={
        "markdown": "x" * 204801, "source_title": "大", "content_type": "文档章节"})
    assert resp.status_code == 413

async def test_source_whitelist(client):
    ok = await client.get("/api/kb/source", params={"path": "data/kb/退货政策.md"})
    assert ok.status_code == 200 and "markdown" in ok.json()
    bad = await client.get("/api/kb/source", params={"path": "C:/Windows/win.ini"})
    assert bad.status_code == 404
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/test_kb_api.py -v`
Expected: FAIL,404/import error。

- [ ] **Step 4: 实现 API**

- `overview` 逐块 try/except:MySQL 失败 → `mysql={"ok": False, "chunks": None, ...}`,`staging`/`recent` 返回空或 `null`;Milvus 失败 → `milvus={"ok": False, "count": None, ...}`、`consistency.status="unknown"`;`sources` 始终现场切分返回;`jobs` 调 `core.jobs.list_jobs()`。
- `overview` 的 `staging` 调 `store.staging_counts()`(extracted/kept/discarded/batches);`recent` 调 `store.list_recent(20)`。
- `overview` 的 `sources` 逐份 `build_chunks`;某份文件读取失败时该项返回 `{path, title, content_type, chunks: 0, key_clauses: 0, features: [], error: "读不到"}`,不连坐其他材料。
- `source`:从 `sources.SOURCES` 按 `path` 查找;找到则读取文件返回 `{path, title, content_type, markdown}`;找不到返回 404。禁止把用户传的路径直接 `read_text`。
- `preview`:校验字节数;构造 `Source(Path("inline.md"), source_title, content_type)`;返回 `counts`、`features`、`chunks`(answer 截断 200 字给页面显示,但入库用完整正文;接口返回完整 `answer` 也可,页面自行截断)。
- `ingest`:校验字节数 → `build_chunks` → 指纹跳过 → `store.insert_chunks` → `link_siblings`;`vectorize=True` 时调 `vectorize_pending`,异常用 `JSONResponse(status_code=502, content={"detail": f"已入库 {n} 块(pending),补跑一次即可", "inserted": n})`。
- `vectorize`:调 `dualwrite.vectorize_pending(limit)`;异常 502。
- `staging`:调 `store.staging_counts/list_staging`;MySQL 异常 502。
- `search`:route 仅 `dense`;调 `retriever.search_hits`;返回 `threshold`、`hits`、`message`。
- `app/main.py`:`application.include_router(kb_router)` 与 `application.include_router(jobs_router)`(作业路由在 Task 12 创建,这里统一挂载)。
- `overview` 的 `jobs` 字段调 `core.jobs.list_jobs()`;作业运行器不可用时该字段返回空列表并带 `jobs_ok=false`。
- 不在此任务挂静态页与 `/admin`、`/kb`(Task 14)。

- [ ] **Step 5: 运行测试并提交**

Run: `uv run pytest tests/test_kb_api.py tests/test_chat_api.py -v`
Expected: PASS。

```bash
git add app/api/kb.py app/main.py tests/test_kb_api.py
git commit -m "feat(ch03): /api/kb 知识库接口与降级语义"
```

---

### Task 14: `/admin` 与 `/kb` 页面

**Files:**
- Create: `app/static/admin.js`
- Create: `app/static/admin.html`
- Create: `app/static/kb.html`
- Modify: `app/main.py`
- Create: `tests/test_admin_pages.py`

**Interfaces:**
- Produces: `GET /admin`、`GET /kb`、`GET /static/admin.js`。
- Consumes: `/api/kb/overview`、`/api/kb/preview`、`/api/kb/ingest`、`/api/kb/vectorize`、`/api/kb/staging`、`/api/kb/search`、`/api/jobs/*`。

- [ ] **Step 1: 写失败的页面冒烟测试**

```python
async def test_admin_pages_served(client):
    for path in ("/admin", "/kb", "/static/admin.js"):
        resp = await client.get(path)
        assert resp.status_code == 200
    assert "知识库" in (await client.get("/admin")).text
    assert "手工录入" in (await client.get("/kb")).text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_admin_pages.py -v`
Expected: FAIL,404。

- [ ] **Step 3: 实现 `admin.js`**

- 立即执行 IIFE,注入 `<style>`(只作用于 `.admin-*` 类)。
- `NAV = [{key:"admin", label:"总览", href:"/admin"}, {key:"kb", label:"知识库", href:"/kb"}, {key:"chat", label:"客服对话", href:"/"}]`。
- 读取 `document.currentScript.dataset.page`,在 `<body>` 顶部插入 `<nav class="admin-nav">`。
- 暴露 `window.Admin = {api(path, options), pollJob(name, onUpdate), fmt(n)}`;`api` 统一处理 JSON 错误并把 `detail` 抛给调用方。

- [ ] **Step 4: 实现 `admin.html`**

- 引入 `/static/admin.js`(`data-page="admin"`)。
- 卡片:知识库(读 `/api/kb/overview`,显示块数/pending/Milvus/暂存,链接 `/kb`)、客服对话(链接 `/`)、工单(占位「后续模块」)。
- 每张卡独立 `fetch`,失败只显示「读数失败」。
- 沿用现有复古琥珀 + 青配色(`--amber:#fbbf24`、`--teal:#2dd4bf`、`--bg:#0d0a06`)。

- [ ] **Step 5: 实现 `kb.html`**

六块按 spec §14.1:
- 库存闸条;① 手工录入(textarea + 标题 + content_type + 预览/入库 + 勾选向量化);② 材料清单(每份材料显示块数/关键条款/特性,预览按钮先 GET `/api/kb/source` 取回正文填入录入区,再调 `/api/kb/preview`;作业按钮);③ 暂存三态 + 展开;④ 向量化与双写 + 按钮;⑤ 检索自测;⑥ 最近入库。
- 所有 API 路径与 §16 一致;作业按钮调 `Admin.pollJob`,运行中禁用。

- [ ] **Step 6: 挂路由与静态目录**

`app/main.py`:
```python
from fastapi.staticfiles import StaticFiles
application.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
application.add_api_route("/admin", admin_page, include_in_schema=False)
application.add_api_route("/kb", kb_page, include_in_schema=False)
```
(用 `FileResponse` 返回 `admin.html` / `kb.html`;原有 `/` 不动。)

- [ ] **Step 7: 运行测试并提交**

Run: `uv run pytest tests/test_admin_pages.py -v`
Expected: PASS。

```bash
git add app/static/admin.js app/static/admin.html app/static/kb.html app/main.py tests/test_admin_pages.py
git commit -m "feat(ch03): /kb 知识库录入页与 /admin 后台外壳"
```

---

### Task 15: 端到端验收、阈值校准与收尾

**Files:**
- Modify: `dev-notes/ch03.md`
- Modify: `docs/superpowers/specs/2026-10-19-mewhelp-ch03-knowledge-base-design.md`(仅在阈值/口径需要按实测修订时加日期注记)
- 可能修复前序任务暴露的问题

**Interfaces:**
- 无新接口;验证 Task 1–14 的组合行为。

- [ ] **Step 1: 跑全量测试**

Run: `uv run pytest -v`
Expected: 全绿;Milvus 未启动时集成测试 SKIP,其余 PASS。

- [ ] **Step 2: 建库与状态核对**

Run:
```
make kb-preview
make kb-build
uv run python -m app.kb.ingest status
```
Expected: preview 3 份材料均有块;build 后 `pending=0`、`done=N`、Milvus 行数 == done;`consistency=consistent`。

- [ ] **Step 3: AC1 检索验证**

Run:
```powershell
uv run python -c "from app.kb.retriever import retrieve; print(retrieve('邮费是多少'))"
```
Expected: 命中 `data/kb/` 里运费说明块,文本含「运费/包邮/邮费」且不是「没有找到」。记录返回的 score;若 score < 0.5 或明显无关,用 `search_hits` 打印 Top-10 分数,校准 `knowledge_min_score`(改 `.env` 或默认值),把校准结果写进 dev-notes。

- [ ] **Step 4: AC2 中断续跑验证**

Run:
```
uv run python -m app.kb.ingest docs
# 启动 vectorize 后在前 3 秒内 Ctrl+C / 关闭进程
uv run python -m app.kb.ingest vectorize
uv run python -m app.kb.ingest status
```
Expected: 第一次中断后 `pending>0`;重跑后 `pending=0`,`done == Milvus 行数`,consistency=consistent;`knowledge_chunks` 无重复指纹行。

- [ ] **Step 5: AC3/AC4 页面与作业验证**

Run: `uv run uvicorn app.main:app --reload`
浏览器:
- `/admin` 知识库卡读数正确;
- `/kb` 手工贴一段新 Markdown → 预览 → 入库(勾选向量化)→ 检索自测召回;
- 作业按钮 `kb-preview`/`kb-build`/`seed-conv`/`kb-mine`/`kb-vectorize`/`kb-rebuild` 日志回显正确;
- `kb-reset` 未确认 403、确认后清空;作业运行中再点 409。

- [ ] **Step 6: 聊天页真实链路**

Run: `uv run uvicorn app.main:app --reload`,聊天页问「邮费是多少」。
Expected: SSE 出现 `tool` 帧 `query_faq` start/end;回答引用运费说明;无 error 帧。

- [ ] **Step 7: 挖掘链路**

Run:
```
make seed-conv
make kb-mine
uv run python -m app.kb.ingest status
```
Expected: 暂存表出现 extracted/kept/discarded;`knowledge_chunks` 新增 `content_type='对话挖掘'` 的块;重复跑不重复入库。

- [ ] **Step 8: 更新 dev-notes 并提交**

在 `dev-notes/ch03.md` 追记阶段 3 起每个任务的完成情况、评审结论、AC1–AC4 结果、阈值校准值、翻车与返工。

```bash
git add dev-notes/ch03.md docs/superpowers/specs/2026-10-19-mewhelp-ch03-knowledge-base-design.md
git commit -m "docs(ch03): 端到端验收结果与 dev-notes 追记"
```

- [ ] **Step 9: 最终 code review 与 finish**

使用 `superpowers:requesting-code-review` 对整条 `ch03-knowledge-base` 分支做最终评审;处理 Critical/Important;使用 `superpowers:finishing-a-development-branch` 决定合并/PR。评审结论与修复提交写进 dev-notes。

---

## Self-Review Notes

- **Spec coverage:** §1→Task 15;§2→Task 1/2/4/5/6/10/11/12/14;§3→Task 1;§4→Tasks 3–14;§5→Task 3;§6→Task 4;§7→Tasks 7/8/10;§8→Task 9;§9→Task 11;§10→Task 2;§11→Task 1;§12→Tasks 3–14;§13→Task 15;§14→Task 14;§15→Task 12;§16→Task 13;§17→Review Focus 与各任务的降级测试。
- **Type consistency:** `build_chunks` 在 Task 4 定义、Task 10/13/14 使用;`ChunkCandidate`/`PendingChunk`/`staging` 在 Task 7 定义、Task 8/10/11 使用;`embedding_text(category, questions, answer)` 在 Task 4 定义、Task 8 使用;`Hit` 在 Task 9 定义、Task 13 使用;`JobSpec`/`JobState` 在 Task 12 定义、Task 13 使用。
- **Review Focus tests:** 空 query→Task 9;无标题/表格/超长行→Task 3;依赖不可达→Task 8/13;双写中断→Task 8;挖掘空/畸形/重复→Task 11。
