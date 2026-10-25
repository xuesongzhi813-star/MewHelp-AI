# ch07 演示与复验

在PowerShell中使用既有本机MySQL/Milvus及根.env，不重建知识库，不改.env。产品根：

```powershell
Set-Location 'E:/MewHelp AI/.worktrees/ch05-workflow-agent'
$chapterPython = 'E:/MewHelp AI/.venv/Scripts/python.exe'
& $chapterPython scripts/demo_ch07.py budget --profile demo
& $chapterPython scripts/demo_ch07.py serve --profile demo --port 8018
```

打开http://localhost:8018/。budget输出H5650、层13954、层21695；serve默认使用独立log/ch07/checkpoints.sqlite和匹配profile。本次8018为开发验收进程，已有服务时勿重复启动；原8015/8016不停止。默认大窗口演示改用--profile default。

profile失效/缺失时先保留旧文件与报告，指定新文件名（不强改hash）：

```powershell
$env:CH07_CALIBRATION_PROFILE = 'log/ch07/calibration-recheck.json'
& $chapterPython scripts/demo_ch07.py calibrate --profile demo
& $chapterPython scripts/demo_ch07.py serve --profile demo --port 8018
```

校准使用冻结24条检索calibration，真实调用有费用；reports/ch07/calibration为固定输出，复跑前保全原报告。清除上述变量会回默认profile路径。

复验命令（会新增独立合成会话，保留旧报告后运行）：

```powershell
& $chapterPython -m pytest -q
& $chapterPython scripts/eval_ch07.py --task summary --split acceptance --output reports/ch07/recheck-summary.json
& $chapterPython scripts/eval_ch07.py --task dialogue --split acceptance --profile default --output reports/ch07/recheck-default.json
& $chapterPython scripts/eval_ch07.py --task dialogue --split acceptance --profile demo --output reports/ch07/recheck-demo.json
& $chapterPython scripts/eval_ch07.py --task tokens --output reports/ch07/recheck-tokens.json
& $chapterPython scripts/eval_ch07.py --task router --split acceptance --output reports/ch07/recheck-router.json
Select-String -Path log/app.log -Pattern 'model_ctx|history_ctx|层1 降级|summary trigger|summary start|summary done|summary skip|summary fail'
```

冻结21轮样例见docs/samples/ch07/dialogue-acceptance.jsonl，两profile使用同一数据SHA。首轮订单1001未收货/核实签收人，中间插商品和另一订单，末轮问最开始那单；后台待完成的等待单独记在时间线，不算此前回复时间。默认应0降级0摘要，演示应看到层1降级→层2超预算→第N段追加，末轮保留1001及诉求。

浏览器验证脚本沿用已装Node/Playwright/Chrome，无项目依赖安装：

```powershell
$env:NODE_PATH = 'C:/Users/20968/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
node scripts/browser_ch07.cjs
node scripts/browser_real_ch07.cjs reports/ch07/demo-dialogue.json
```

真实runner只取评估报告的合成匿名用户。可在页面开新对话后切回带“已摘要”标记的旧会话，回载原文后继续追问。接口GET /api/conversations?user_id=32hex及GET /api/conversations/{id}/messages?user_id=32hex均只读；匿名标识不是认证。

停止/重启/备份恢复统一看docs/OPERATIONS.md。必须连同conversations/messages/conversation_summaries、双锚点、SQLite及侧文件、当前代码/配置/profile一起保全；不删除账本或旧会话。
