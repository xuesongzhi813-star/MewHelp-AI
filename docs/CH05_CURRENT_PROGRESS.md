# ch05 当前状态与恢复入口

2026-10-22：Tasks1–8完成，Task9功能/评审修复/真实验收与交付文档完成。不是旧Task2 RED状态。Task9官方task-done已成功记录complete（630 passed in35.79s），本章finish。

## 唯一实现位置

`E:/MewHelp AI/.worktrees/ch05-workflow-agent`，branch ch05-workflow-agent；BASE/HEAD b1e6906d07b908ecc3efaee610c8f09dcecc1b8d，无本章提交。用户禁止自动commit/push/merge/deploy，保留全部未提交改动及专属账本。原根目录E:/MewHelp AI仅同步文档；不要在根目录误跑旧产品实现。手工worktree不是可自动归档的受管附件。

用户审批链：「2.官方SQLite」「A」「通过」「批准」「计划通过，Native」；恢复指示要求先只读核对再继续。本轮按实际文件/测试恢复，没有假定失效测试进程通过，未覆盖用户修改。

## 产出与证据

- 先裸while循环，后LangGraph明确节点；七类JSON意图、固定四出口、前置检索/证据闸/拒答池、只读多步Agent、SSE、官方AsyncSqliteSaver、会话串行、独立用户选项及确认工单。
- 最终630 passed / 0 failed / 0 skipped，35.79s；分类28/28，Agent8/8；新评审3条复现先RED后GREEN；旧两条中断时修复本次复验通过。
- 最终真实八条HTTP、服务重启同session历史恢复、真实FAQ改写用量、浏览器双按钮及取消/确认/重复检查均已完成。真实票仅合成验收bc263f5a的2张；cddf458e本轮handoff+取消仍0。
- 报告入口reports/ch05/summary.md，评审review.md，命令demo.md，原始acceptance.json、restart.json、browser-db.json、verification.json、固定集*-final.json。失败attempt报告保留。
- 开发留痕dev-notes/ch05.md；批准spec/plan在docs/superpowers/。账本`.superpowers/sdd/2026-10-21-mewhelp-ch05-workflow-agent/progress.md`及brief/log需保留，因没有提交，不能删除该目录。

## 环境与运行

先看reports/ch05/demo.md，从本工作树执行根venv解释器的scripts/demo_ch05.py；服务本地8015，当前最后启动PID25560只是本次快照，重连应核对端口，不假定进程仍在。关闭启动终端Ctrl+C，不清SQLite，不停共享数据库/向量库。

根.env只加载进程内，无复制/改写/输出；没有升级旧锁定依赖。LangGraph1.2.15/checkpoint4.3.0/官方SQLite3.1.2。知识使用已有ch04最终集合和root/log/kb最终migration/profile(.25)，不运行kb-reset/build。MySQL无DDL。

沙箱可能卡asyncio fallback_socketpair或阻本地端口，需要获准正常终端测试，不能改产品绕过。PowerShell使用login=false避开profile挂起。product-specs.md Git可能显示M但diff为空，原始混合换行已恢复为根目录原字节，不能git restore转成CRLF再改冻结hash。

## 剩余范围与后续

本章无未解决Critical/Important、无延期Minor；不继续扩正式分类/指代、MCP、上下文、Langfuse/飞轮/真人客服。SQLite单机与无鉴权演示、A方案没有全文硬校验、跨库不原子及不能强杀同步线程均已记录批准边界。评审未覆盖新增demo脚本与最新真实报告，由主执行者静态/实际执行验证，没有伪称独立评审覆盖。

未提交、推送、合并、部署；原根用户未跟踪.codex/、旧运行维护手册、reports/ch04/v1/snapshot/保持原状。后续集成需用户明确授权。不要重新brainstorm或重做已验证任务。


维护文档更新（2026-10-22）：docs/OPERATIONS.md第0节已覆盖ch05启动/停止/SQLite冷备与恢复、实际运行快照；旧根目录简表标为历史。此更新仅文档，只读核验，未启停服务或改数据；不沿用630通过作为本次重跑结果。


维护手册纠偏（2026-10-22）：用户要求整个项目一套现行操作流程，docs/OPERATIONS.md已整体重写，替代此前第0节+历史各章并列写法；根目录日常手册同步全文。旧文档在专属scratch/before-unified-ops保全。应用代码/数据不变。


2026-10-23修复：BM25外检索/知识客服现已真实恢复；入库pending提示和嵌入/重排错误诊断已完善。637项通过，当前125块双写一致。请按更新后的OPERATIONS第1节选择worktree/log/ch05/calibration-20261023-final.json并严格核验，不再用旧124块语料的冻结profile作当前校准凭据。旧文件保留。报告在ch05工作树reports/ch05/bugfix-20261023/，留痕阶段23–26。
