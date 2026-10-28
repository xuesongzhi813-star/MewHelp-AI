# ch08 本地演示

统一启动、检查、停止和保全见docs/OPERATIONS.md。产品目录E:/MewHelp AI/.worktrees/ch05-workflow-agent，共享解释器E:/MewHelp AI/.venv/Scripts/python.exe，真实.env不复制进报告。

先启动本地MySQL/Milvus和两个独立MCP模块：app.mcp_servers.logistics --port 8021、app.mcp_servers.aftersales --port 8022。第三终端运行scripts/demo_ch08.py budget，正常tool_count=7、discovery_diagnostics={}；profile有效后serve --port 8020。新环境或检索身份变化时先用冻结calibration生成本章独立profile。

default128k/history74548，demo24k/history6048（18k经实测不足一轮），decision2600；动态目录重计预算，检索语料/模型/Prompt/检索代码变更才需付费重校准，校准仍原单查询范围。

“请帮我建售后工单”先追问具体问题，补“订单1001显示签收但没收到”出预览，确认才真实写tickets，取消按权限拒绝审计。未知MCP/外部write默认拒绝，物流仅MCP提供。旧投诉/退款按钮保持一次确认。

结果待核实先查同conversation_id的tickets、tool_audit_logs和SQLite，不重复提交、不删除WAL/审计。真实合成验收记录保留，不清理真实数据。

2026-10-26最终自有监听快照：8020 PID22292、8021 PID4300、8022 PID38076；launcher/历史重启见log/ch08/owned-processes.json。停止前重新核对监听与命令，不能照抄PID；本章未停止旧8015/8016/8018。测试8023已退出，策略已按原字节恢复。

最终实际浏览器证据browser-real/与database.json，评审前证据归档browser-real-before-reference-fix。合成HTTP浏览器9项在browser-synthetic/，不代替真实后端证据。完整2000字用消息来源和draft_id引用，不让模型复写，真实成功证据见output-capacity-reference-real.json；literal旧调用仍兼容并严格校验。测量脚本会新增合成工单，运行前归档固定文件名报告。
