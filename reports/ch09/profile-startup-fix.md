# 按维护文档启动时profile失败：修复与验证（2026-10-28）

用户截图在旧demo_ch08.configure校验冻结profile时报code_hash、runtime_corpus_hash不匹配。只读复现相同字段差异；当前demo_ch09配置下validate_release严格检查成功，无未完成发布。根因是运维正文仍可复制旧章启动/校准命令，顶部历史提示未充分解决误用。

修复了docs/OPERATIONS.md及产品/根日常维护手册四份同步副本：完整日常流程统一ch09/8023/当前active，新增Test-MewHelpProfile与第5节逐类分流；旧正文单独归档，不再与当前步骤并列。check的active_exists不作为profile通过证据。第一张图逐题False是can_answer=False，不能按它判定进程失败。

本次验证：

- 18个PowerShell代码块语法解析成功；提取第1节和2.3节原代码，在本机权限下执行，不另写一套替代命令。
- 知识总184，在用127、旧版排除57、在用pending0；MySQL/Milvus逐ID/文本/元数据一致，missing/orphan/stale均空。
- 当前profile严格验证成功、无unfinished发布、owner启动前离线。
- 工具预算有效，7工具、discovery_diagnostics为空，default窗口128000。
- 临时受控应用的 /、/review、/quality、/admin、/health、/api/kb/overview、/api/review/releases、/api/observability/status 均HTTP200，Langfuse ready，日本区jp。
- 在实际应用ReleaseCoordinator获取知识read lease成功，未进入知识维护。
- 该临时探针在配置后明确关闭flywheel消费者与定时评估，没有发起问答生成、消费旧问题或发布知识；这项覆盖HTTP/生命周期与真实profile，不声称重新完成六项端到端验收。
- 正常should_exit关停后SDK shutdown_confirmed=true，旧ch08 profile、当前active与所引用profile的SHA256保持原值；本次自有8021/8022/8023退出，未停止其他进程。

无需重校准、改hash、清知识或删除active。未改产品业务源码、真实.env或依赖，没有DDL/提交/推送/部署。本次为文档修复，以真实命令和受控启动验证，不把此前1111项测试数当成本次新结果。

只读检查脱敏输出见log/ch09/profile-runbook-checks.log；临时验证脚本与前后hash在忽略的log/ch09内保留，规范步骤只引用当前维护手册。过程见dev-notes/ch09阶段31–33。
