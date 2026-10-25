# ch07 当前状态与交接入口

2026-10-25：**Task0–11已finish，并已完成 Git 交付。** 设计/计划已批准，Native执行。此前暂停、进行中、旧通过数均为历史；功能见dev-notes/ch07.md阶段1–25，随后整项目运维文档统一及核验见阶段26–28。产品已切分支 ch07-context，5 个提交推送 origin/ch07-context，PR #7（base ch06-router）待评审合并。

## 产品现场与保全

- 唯一产品：E:/MewHelp AI/.worktrees/ch05-workflow-agent，交付分支 ch07-context，base/上一章 HEAD c4453dcea2f8867e496c8e217713466ae44e5df4。本章 5 个提交已推送，PR #7（base ch06-router）待评审合并；尚未合并/部署。根 E:/MewHelp AI 仅镜像文档。
- 原6项kb/语料换行差异与基线hash一致且未纳入提交；本章已提交推送但未合并/部署，未执行DDL/create_all/迁移/清库、依赖升级或真实.env改动。旧profile和旧checkpoint保留。
- Python：E:/MewHelp AI/.venv/Scripts/python.exe。沙箱执行可能需工具权限流程，不重建环境。
- 本章独立8018运行最终demo版；本轮当时PID44000/工具session35784只是快照，后续核对实际PID。原8015/8016未停。

## 完成能力

三层纯视图与双真实DB消息id锚点、追加式异步摘要、完整LangGraph State/checkpoint、固定首system+工具schema、三处摘要注入、实际上下文日志、只读会话API、多会话侧栏均已完成。最近原文与完整历史不裁；工具只在checkpoint、未新增工具结果落messages；摘要独立表。匿名分组只限同浏览器，不跨会话记忆。

## 最终证据

- 修后全量758 passed in35.19s，无skip；.superpowers/sdd/2026-10-23-mewhelp-ch07-context/final-suite.log。
- 独立gpt-6-astra全新上下文review：0 Critical/2 Important/0 Minor，恢复普通动作/状态与终答提交前失败补归档均先RED再GREEN；不派复评，不把作者验证当独立复评。
- 真实summary dev12/12、acceptance18/18；旧75条意图75/75、订单9/9、字面74/75、有限助词语义75/75；原失败attempt与冻结标签保留。
- 默认acceptance两组21轮：0降级0摘要；demo两组21轮：11/10次降级、各2段摘要，末轮1001/未收到/核实签收人诉求正确，无服务错误。
- summary-timeline.json两次真实user done先于summary done；受控Event证明后台阻塞不阻塞前台/不覆写checkpoint。最终prefix实际1531、估算1682≤保底2000；12条token样例获usage，CJK系数1.0；demo预算5650/3954/1695。
- 最终真实8018/模型/Chrome与合成API Chrome均通过原文回载/摘要续聊/会话切换/刷新/新标签/侧栏失败/390px；合成用例另含HTML不执行、迟到流、元数据竞争、普通动作恢复/取消0/确认1/完成与uncertain禁用。
- log-audit.json核实每轮history_ctx、三处摘要、Agent一个打头system、固定schema hash及所有实际Agent估算guard。5份冻结数据SHA、8对文档镜像、JSON解析/凭据模式检查、两工作区diff --check及修后JS语法通过。

## 演示与资料

- 命令：产品reports/ch07/demo.md；统一运维docs/OPERATIONS.md。从产品根运行现有Python scripts/demo_ch07.py budget --profile demo；scripts/demo_ch07.py serve --profile demo --port 8018，端口已用勿重复启动。
- 报告：reports/ch07/summary.md；独立结论与作者修复验证review.md；全部Ruling及成本rulings.md，无Deferred minors。
- 整项目运维文档已复核至ch07：docs/OPERATIONS.md为维护源，根《MelHelp AI日常运行维护手册.md》完整同步；AI_HANDOFF/Milvus入口及根/产品镜像一致。只读命令/19段PowerShell/链接核验见reports/ch07/operations-check.json，没有实际启停/备份恢复或重跑功能验收。
- 当前独立profile：log/ch07/calibration-profile.json；checkpoint：log/ch07/checkpoints.sqlite；实际上下文：log/app.log。代码变化严格失效需从冻结calibration重校准，不强改旧hash。
- Spec/Plan在docs/superpowers/；逐阶段事实dev-notes/ch07.md。专属progress.md、原始RED/GREEN、baseline/旧暂停hash/评审包/审计文件保留，不删除账本。

## 边界与后续

本机单进程匿名演示，非认证/公网服务；工具内部轨迹依checkpoint备份，MySQL/SQLite无跨库原子事务。摘要后台关闭取消，未提交范围下一轮可重试；估算与有限质量样例不构成任意输入/模型绝对正确保证。任务已完结，没有待实施ch07项；后续新需求先由用户指定，不把这些边界自行扩展为下一阶段任务。
