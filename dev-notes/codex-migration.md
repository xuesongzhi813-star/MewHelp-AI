# Codex 迁移核查留痕

## 2026-10-19 阶段 1：项目、环境与测试基线核查

- **用户需求**：将本项目从 Claude Code 迁移到 Codex 后，阅读项目、检查环境、运行测试并报告现状。
- **关键产出**：
  - 阅读 `AGENTS.md`、`docs/AI_HANDOFF.md`、`dev-notes/ch01~03.md` 的阶段脉络，核对三章 spec/plan、`pyproject.toml`、`uv.lock`、Makefile、部署配置、应用入口、聊天/工具链、知识库双写/检索/作业及相应测试。当前实现覆盖 ch01 纯对话、ch02 单轮工具调用、ch03 知识库与管理页；ch04 尚无获批需求。
  - 当前分支 `ch02-function-calling`，HEAD `513c7b9`；本地分支显示跟踪 `origin/ch02-function-calling`。未执行远端 fetch，不能据此断言远端或 PR 的实时状态。开始核查时有未跟踪的 `.codex/`、`AGENTS.md`、`docs/AI_HANDOFF.md`，均未清理或改写。
  - 环境：`uv 0.13.0`；项目虚拟环境和 `uv run --no-sync` 均使用 Python 3.12.10；依赖锁定版本与交接文档一致。MySQL80 服务运行且 3306 可连接；Milvus 19530 可连接、9091 `/healthz` 返回 200。`make.exe` 位于 `C:\Program Files (x86)\GnuWin32\bin`，版本 3.81；当前终端的 PATH 未提供 `python`、`docker`、`make` 命令。8000 端口没有运行中的 Web 服务。
  - 只读 `python -m app.kb.ingest status`：知识切片 45、pending 0、done 45、关键条款 9；Milvus 向量 45，一致性 `consistent`。这只是执行时快照，未重建或清空知识库。
- **测试及结果**：先检查 MySQL 测试的事务回滚 fixture 与 Milvus 独立测试集合；随后现有 `.venv` 执行 `python -m pytest -q`，**334 passed in 20.17s**；再执行 `uv run --no-sync pytest -q`，**334 passed in 21.20s**。未调用真实 DeepSeek/SiliconFlow API，也未运行建库、挖掘或破坏性命令。
- **拒绝/纠偏**：受限沙箱内直接运行用户目录中的 Python 被拒绝、uv 缓存目录不可写；经执行许可后使用现有虚拟环境完成验证，没有重建 `.venv` 或升级依赖。交接文档的历史测试数和分支说明只作对照，本条记录采用本次实测值。
- **失败与返工**：无代码或测试失败。终端命令输出中的中文有编码显示问题；状态数值与一致性结果可辨识。
- **变更文件**：仅新增本留痕文件；应用源码、测试、依赖和基础设施未修改。
- **已知问题/边界**：后台与 API 当前无鉴权；作业状态仅在进程内存；部分知识库 API 参数校验、前端作业恢复和大库一致性提示等技术债见 `docs/AI_HANDOFF.md` §7。标准端口 8000 无服务运行，因此本次未做真实 HTTP/浏览器或外部模型端到端验收。远端 PR 状态未核查。
- **建议下一步**：确定 ch04 或技术债处理的具体范围后，再读取对应设计并建立计划；若要做真实端到端验收，先明确使用的环境和可写数据范围，再启动服务与外部 API 探针。
