# MewHelp AI 项目开发规则

## 项目入口与事实来源

- 本仓库是二手古着店智能客服演示项目。开始新的开发阶段或修改代码前，先读 `docs/AI_HANDOFF.md`，再读所涉章节的 `dev-notes/ch*.md`、`docs/superpowers/specs/` 中的设计及对应计划、相关源码和测试。
- 当前仓库没有 `README.md`、`CLAUDE.md` 或 `CLAUDE.mid`，也没有 `docs/development-plan.md`、`docs/progress.md`。若日后新增，阅读并核对，但不要把不存在的文件当作流程前提。阶段记录以 `dev-notes/` 为准。
- 文档中的日期、分支、测试数和服务状态只是当时快照；实施前以当前代码、`pyproject.toml`、`uv.lock`、Git 状态和实际测试结果复核。设计稿与后来 `Ruling`、开发留痕或代码冲突时，先查明沿革，不照搬早期方案。
- 不把交接文档中的「待确认」、候选功能或延后技术债当成已批准的下一阶段需求；不把未实现功能描述成现有能力。

## 当前架构边界

- Python 版本依 `.python-version` 和 `pyproject.toml`（当前为 3.12），依赖由 `uv.lock` 锁定；沿用现有 `.venv`、`uv`、FastAPI、LangChain、SQLAlchemy、MySQL、Milvus 及前端静态页的组织方式。不要自行升级依赖或改基础设施。
- 聊天入口在 `app/main.py`，流式编排在 `app/chat_service.py`，工具与执行器在 `app/tools.py`、`app/executor.py`，会话持久化在 `app/session_store.py`。维护 `/chat` SSE 帧、单轮工具调用和 `create_ticket.conversation_id` 系统注入契约。
- 知识库的原文权威源是 MySQL，Milvus 只存 ID 与向量；双写顺序为 MySQL `pending` → Milvus upsert/flush → MySQL `done`，失败保留可续跑状态。`query_faq(keyword)` 的对外契约保持兼容。切块共用 `app/kb/documents.py` 的 `build_chunks`，建库材料清单共用 `app/kb/sources.py` 的 `SOURCES`。
- 数据库表由项目外手工维护；仓库中的 ORM 只映射现有表。未经明确授权，不建表、改表或加入 `create_all`、迁移脚本。`/admin`、`/kb` 和 `/api/*` 当前无鉴权，不将演示环境直接当作公网服务。
- 后台作业只执行 `app/core/jobs.py` 注册的固定 Make 目标；保持 argv 白名单与 `shell=False`。`kb-reset` 会清空知识库，运行真实写入、重建或清空命令前先核对目标环境与数据影响。

## 开发流程

1. 明确需求，检查当前分支和工作区已有改动；保留用户及其他人未提交的内容。定位相关实现、测试和历史决策。
2. 非平凡改动先形成可审查的实施计划；需求或架构不清时先做 Superpowers brainstorming。重大的架构、数据或破坏性变更，说明方案及风险并取得用户决定。
3. 涉及外部库、框架、SDK、API、CLI 或云服务的用法和版本兼容时，先用 Context7 查文档，优先匹配锁定版本，再与本仓库代码核对；不要凭记忆引入新接口。
4. 小步实现；对容易回归的行为写有判别力的测试，适用时先测后改。沿用既有依赖注入、异常语义、API 契约和数据库事务约定，不扩散修改到无关模块。
5. 在仓库根目录运行相关测试（例如 `uv run pytest tests/test_kb_retriever.py -q`）；需要真实服务的测试先确认 MySQL、Milvus 等依赖。必要时再运行 `uv run pytest -q`。记录真实结果、跳过项与失败原因，不沿用交接时的通过数充当本次结果。
6. 检查 diff 与 `git status`，确认无密钥、无误改、文档与实现一致。除非用户明确授权，不提交、推送、合并或部署。

## 配置与保密

- 不修改或输出 `.env` 的真实值；密钥和密码只留在本地配置中。需要说明配置时引用 `.env.example` 的占位符。
- 不把真实凭据、客户对话、数据库连接串或含敏感信息的命令输出写入代码、测试、日志、文档或回复。
- 不为验证而随意清理现有 MySQL/Milvus 数据；测试尽量沿用仓库的隔离 fixture。外部服务不可用时如实说明未验证范围。

## 阶段留痕与沟通

- 每完成一个开发阶段，就在对应的 `dev-notes/ch*.md` 追加记录；新章节先建立相应留痕文件，不在收尾时一次性补写。记录需求/用户裁决、关键产出、拒绝或纠偏、失败与返工，并列出完成任务、变更文件、测试及结果、已知问题和建议下一步。没有发生的项写「无」。
- 需要修订设计决策时，同步更新对应 spec/plan 或标注新 `Ruling`，使文档与代码一致。
- 用中文向开发者沟通；实施重大决策前说明原因和风险，完成时概述实际改动、验证结果及剩余问题。
