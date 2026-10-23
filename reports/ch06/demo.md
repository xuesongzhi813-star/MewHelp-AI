# ch06 演示与复验

在PowerShell中，从**产品工作树**启动。MySQL与现有Milvus须已运行，根`.env`继续保留，不修改真实配置。

```powershell
Set-Location -LiteralPath 'E:\MewHelp AI\.worktrees\ch05-workflow-agent'
& 'E:\MewHelp AI\.venv\Scripts\python.exe' scripts/demo_ch06.py serve
```

聊天地址：http://127.0.0.1:8016/ 。本轮已启动该端口，已有实例时无需再启动；未停止共享8015。
启动器只接受本地DB，使用`log/ch06/checkpoints.sqlite`与独立profile，校验语料/模型/Prompt/代码及ch06管线身份；单进程，不使用多worker。第一次启动或身份变化才按原固定calibration集生成**新路径**，保留旧文件：

```powershell
$env:CH06_CALIBRATION_PROFILE = 'E:\MewHelp AI\.worktrees\ch05-workflow-agent\log\ch06\calibration-new.json'
& 'E:\MewHelp AI\.venv\Scripts\python.exe' scripts/demo_ch06.py calibrate
& 'E:\MewHelp AI\.venv\Scripts\python.exe' scripts/demo_ch06.py serve
```

校准会调用真实模型/嵌入/重排并产生费用；只读现有知识，不建库、不改标签。不要强改hash或把旧profile复制成新身份。日常启动不必重新校准。

## 页面演示

1. 新会话输入“这个能退吗”，看到理解阶段与三张演示订单卡；此时原生interrupt等待，尚未执行退款判断。
2. 点1001卡片，自动继续同一请求，先读订单、扩写并查政策，再给基于订单/政策的条件说明。商品状态未知时由主力Agent说明/澄清，不追问退款原因。
3. 点击“提交演示退款申请”，原因下拉固定五类，先取消不会建票。再次填写并确认会向本地已有tickets表新增一条“售后”演示工单；**没有资金退款**。重复/刷新不会重复建票。
4. 可用另一新会话输入“订单1001的物流查一下。”→“它能退吗？”→“先不退了，这单包裹在哪？”；类别按物流/退款退货/物流切换。短问退款可能因低证据分安全拒答，这是已验证限制，不能演示成全部都会获批。
5. 待选时刷新或重启8016，保持同一浏览器sessionStorage/session_id与同一SQLite即可恢复卡片；不自动重绘所有历史聊天。新会话不继承旧会话。

演示只使用固定订单1001/1002/1003。每次真正确认都会建立相应新申请，日常健康检查不要随意确认；本次验收仅新增合成工单T202610230001。

## 可复跑命令

```powershell
& 'E:\MewHelp AI\.venv\Scripts\python.exe' -m pytest -q
& 'E:\MewHelp AI\.venv\Scripts\python.exe' scripts/demo_ch06.py router-eval --split dev --output reports/ch06/router-dev-new.json
& 'E:\MewHelp AI\.venv\Scripts\python.exe' scripts/demo_ch06.py router-eval --split acceptance --output reports/ch06/router-acceptance-new.json
& 'E:\MewHelp AI\.venv\Scripts\python.exe' scripts/eval_ch06_policy.py
```

最后一条默认写`reports/ch06/policy-comparison.json`，复跑前先另存该已交付报告。模型评估使用仓库合成数据，不上传客户对话。新一次结果可能波动，不能沿用700项或75/75代替新日志。

本轮证据：`summary.md`、`router-acceptance.json`、`policy-comparison.json`、`browser-e2e.json`与独立phase文件；最终日志`log/ch06/final-suite.log`。退出本轮服务用其终端Ctrl+C，只停止8016对应进程；不要结束全部Python或共享8015。完整基础设施启停/备份见`../../docs/OPERATIONS.md`。
