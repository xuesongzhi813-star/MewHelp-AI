# ch05 功能演示

在 PowerShell 中从**唯一实现工作树**运行；原根目录尚未接入本章产品代码。沿用根目录已有 `.venv`、本地 `.env`、MySQL/Milvus 与 ch04 最终集合/校准文件，不重建知识库或修改 `.env`。

```powershell
Set-Location 'E:\MewHelp AI\.worktrees\ch05-workflow-agent'
& 'E:\MewHelp AI\.venv\Scripts\python.exe' scripts/demo_ch05.py
```

打开 <http://127.0.0.1:8015/>。启动器仅绑定本地单进程，显式启用工作流、ch04最终集合/profile阈值0.25；检查数据库为 localhost/127.0.0.1，不输出凭据。其他终端运行裸循环：

```powershell
& 'E:\MewHelp AI\.venv\Scripts\python.exe' scripts/demo_ch05.py agent --message '请先查询订单1001的当前状态，再查询它的物流轨迹，并分别告诉我结果。'
```

裸循环无 LangGraph/SQLite，无聊天归档、无工单写入。会输出工具顺序、回答、stop_reason及用量。模型预算包含 FAQ 所需查询改写。

依次尝试：

1. 「你们家的退货流程是什么？」：日志含 retrieve_knowledge→evidence_gate→agent_decide，正文流式并有可点引用。
2. 「订单1001的物流到哪了？」：Agent 自己调用 query_logistics。
3. 「我要投诉」：两个独立按钮；都不点可继续聊天。点转人工显示「已转接人工客服」「您好，我是客服小猫，请问有什么可以帮您的」。建工单另有确认/取消；**确认会真实写本地 tickets 表**，不要把演示确认当无副作用。先转人工再建工单与反向顺序均可。
4. 「你好！」：固定话术，仅分类调用，回复阶段不再调用模型。
5. 以上裸循环命令中的组合问题：先 query_order 再 query_logistics，不用一次查询假装多步。

额外：「月球采矿许可需要什么条件？」触发弱证据兜底/问题池；「帮我查物流，我还没有提供订单号」追问。终端节点日志为 `log/ch05/nodes.log`，检查点为 `log/ch05/checkpoints.sqlite`，均不入库。上限默认6次业务工具、10次决策、累计12,000 tokens、90秒；无usage时保守估算。

固定集评估（只读业务，调用现有模型，输出使用新路径以免覆盖历史）：

```powershell
& 'E:\MewHelp AI\.venv\Scripts\python.exe' scripts/demo_ch05.py eval --suite intents --output reports/ch05/intents-new.json
& 'E:\MewHelp AI\.venv\Scripts\python.exe' scripts/demo_ch05.py eval --suite agent --output reports/ch05/agent-new.json
```

测试需载入根本地配置且不输出值；现有隔离夹具会回滚测试业务写入：

```powershell
& 'E:\MewHelp AI\.venv\Scripts\python.exe' -c "from dotenv import load_dotenv; load_dotenv('E:/MewHelp AI/.env'); import pytest; raise SystemExit(pytest.main(['-q']))"
```

本机沙箱可能阻止异步socket/本地服务连接，应在获准的正常终端执行，不能据沙箱失败判断服务停机。不要无理由升级/同步已有依赖。锁定版本 LangGraph1.2.15、checkpoint4.3.0、官方SQLite3.1.2。

关闭：启动终端 Ctrl+C，确认该端口退出；不停止共享MySQL/Milvus，不清检查点。重开相同工作树与文件可恢复后端会话；页面刷新会生成新前端 session，因此**同session_id的恢复应以 restart.json/接口测试为准**，不宣称刷新会重绘旧聊天。进程内同会话串行，不支持多进程锁。

实际结果见 summary.md / acceptance.json / restart.json / browser-db.json；首轮政策预算失败、固定集失败报告保留。当前界面不是接真人系统；除工单确认外不执行handoff业务写入。A方案没有全文引用/承诺硬校验保证，MySQL/SQLite跨库状态不原子，无鉴权页面只供本地演示。
