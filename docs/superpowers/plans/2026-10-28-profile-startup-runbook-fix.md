# Profile启动故障修复计划（2026-10-28）

用户要求：按日常文档启动时profile失败，排查解决，并记录同类问题的具体处理。属于既有运维缺陷修复，不改变已批准架构或知识数据。

已复现：demo_ch08.configure的旧profile与当前code_hash、runtime_corpus_hash不同；demo_ch09配置下validate_release严格检查通过，且无未完成发布/在线owner。旧评估逐题False表示can_answer=False，不是Python命令失败。主因是OPERATIONS正文仍含可复制的ch08校验/启动/重校准步骤，顶部历史提示不足以阻止误用。

1. [x] 核对截图、当前入口、代码、数据身份及服务归属，保留旧profile，不校准凑匹配。
2. [x] 将旧运维全文只读归档；当前OPERATIONS正文统一ch09，覆盖准备、基础设施/MCP、严格profile验证、启动/停止、校准决策、审核与Langfuse观察、评估及配套备份。四份日常维护阅读副本同步。
3. [x] 按新文档原命令执行配置检查、严格profile与发布journal检查、预算检查；在关闭飞轮消费者和定时评估的临时受控进程中实测HTTP启动与正常退出，避免消费旧问题。保持产品代码不变。
4. [x] 在dev-notes即时记诊断、修复及最终结果，更新交接。核对文档命令/本地签名/Context7、敏感值与diff，不提交或清数据。

结果：文档18个PowerShell代码块全部语法解析通过；按文档原代码运行status/verify-hybrid/check/Test-MewHelpProfile/budget成功，127条在用知识一致，7工具/空diagnostics。关闭消费者/定时评估的临时应用8个HTTP检查200，观测ready、知识read lease有效、SDK正常排空；旧profile与当前active/profile字节hash不变，自有8021/8022/8023已停止。详细reports/ch09/profile-startup-fix.md。

验证以真实本地状态和按文档执行结果为准；文档修复不添加镜像实现的单元测试，不沿用上一轮1111项结果充当本次结果。未新增或修改库API；临时Uvicorn冒烟用法已查Context7官方Config/Server/should_exit及本地签名。
