# MewHelp AI 整体启动、停止与维护手册

适用版本：截至 ch07，Windows 本机，更新于 2026-10-25。本文是整个项目的统一操作入口；按下列顺序即可准备环境、启动、检查、维护和停止，无需拼接历史章节命令。

当前实际应用在 `E:\MewHelp AI\.worktrees\ch05-workflow-agent`。根目录提供共享 `.venv`、私有 `.env`、Docker 配置和数据；根目录的旧应用不用于启动 ch07。分支和工作树名称仍含 ch05，不代表应运行旧启动器。当前未提交修改也属于待保全的项目内容。

维护源为仓库docs/OPERATIONS.md，根目录《MelHelp AI日常运行维护手册.md》和产品工作树中的同页同步更新。此次仅更新文档并进行只读核对，没有实际启停、付费校准、备份恢复或清库。

日常使用：执行第1节准备区，再按2.3检查、2.4启动；只重启应用看6.1，整套关机看6.3；知识/日志维护看第4节；profile失效看第5节；整套备份恢复看第7节。服务状态和PID随时会变化，不照搬交接快照。

## 1. 环境、路径与唯一应用入口

常驻组件是 MySQL80（3306）、Docker Desktop 内的外部 etcd 与 Milvus（19530/9091），以及一个 ch07 FastAPI 进程（8018）。静态聊天页、`/kb` 和 `/admin` 都由该进程提供；SQLite 是应用读写的本地文件，不需要另起服务，也不需要前端 Node 服务或额外 worker。

历史共享8015、8016可能仍在运行，它们不是ch07当前入口。不要因启动8018就结束它们；但整套基础设施关停或一致性备份前，必须核对所有服务是否仍使用同一MySQL/Milvus，以及它们各自的SQLite路径。

在每个新 PowerShell 终端先执行以下准备区。服务启停部分需要管理员 PowerShell，其他操作通常用普通终端。

```powershell
$projectRoot = 'E:\MewHelp AI'
$projectApp = Join-Path $projectRoot '.worktrees/ch05-workflow-agent'
$projectPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
$projectDocker = 'C:/Users/20968/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
$projectMysql = 'E:/MySQL/bin/mysql.exe'
$projectMysqlDump = 'E:/MySQL/bin/mysqldump.exe'
$projectComposeBase = Join-Path $projectRoot 'deploy/milvus/docker-compose.yml'
$projectComposeEtcd = Join-Path $projectRoot 'deploy/milvus/docker-compose.external-etcd.yml'
$projectCompose = @('-f', $projectComposeBase, '-f', $projectComposeEtcd)
$projectState = Join-Path $projectApp 'log/ch07'
$projectContextProfile = 'default' # 日常大窗口；小窗口级联演示改为'demo'
$projectManifest = Join-Path $projectRoot 'log/kb/ch04-v2-final-migration.json'
$projectProfile = if ($env:CH07_CALIBRATION_PROFILE) {
    if ([IO.Path]::IsPathRooted($env:CH07_CALIBRATION_PROFILE)) {
        $env:CH07_CALIBRATION_PROFILE
    } else {
        Join-Path $projectApp $env:CH07_CALIBRATION_PROFILE
    }
} else {
    Join-Path $projectState 'calibration-profile.json'
}

foreach ($requiredPath in @($projectApp, $projectPython, $projectDocker,
    $projectComposeBase, $projectComposeEtcd, $projectManifest, (Join-Path $projectRoot '.env'))) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "缺少必要路径：$requiredPath。先核对或恢复，不创建空数据绕过检查。"
    }
}
Set-Location -LiteralPath $projectApp
$env:PYTHONIOENCODING = 'utf-8'

# 供手工知识检查使用，与 ch07 启动器所选的现有知识库保持一致。
$env:KB_CORPUS_VERSION = 'ch04'
$env:KNOWLEDGE_COLLECTION = 'knowledge'
$env:HYBRID_COLLECTION = 'knowledge_hybrid_ch04_v2_final'
$env:KNOWLEDGE_STRATEGY = 'hybrid_rerank'
$env:KB_MIGRATION_MANIFEST = $projectManifest
$env:CH07_CALIBRATION_PROFILE = $projectProfile

# 仅定义入口；调用时加载根 .env 到 Python 子进程，不输出真实配置。
function Invoke-MewHelpKb {
    param([Parameter(Mandatory=$true)]
          [ValidateSet('status', 'verify-hybrid', 'preview', 'vectorize')][string]$Command)
    & $projectPython -c "import sys; from dotenv import load_dotenv; load_dotenv(sys.argv[1]); from app.kb.ingest import main; raise SystemExit(main(sys.argv[2:]))" (Join-Path $projectRoot '.env') $Command
    if ($LASTEXITCODE -ne 0) { throw "知识命令失败：$Command，请先处理实际错误。" }
}
```

准备区故意不要求 profile 已存在，也不直接从未经核验的 JSON 读取阈值。profile 缺失时，仍能先启动依赖、检查知识，再按第 5 节重校准；应用启动前必须完成严格校验。

日常沿用根 `.venv`，无需激活或重新安装。Python 版本依 `.python-version`（3.12），依赖依实际应用的 `pyproject.toml` 和 `uv.lock`；不要顺手升级依赖。真实密钥/密码只在根 `.env`，不复制进命令、Git 或公开日志。换电脑时需另行准备匹配的环境和既有数据，不能只拷启动脚本。

`--profile default|demo`选择上下文预算预设，`CH07_CALIBRATION_PROFILE`选择检索校准JSON，两者用途不同。启动器会在子进程覆盖六个窗口/峰值相关环境变量；切配置用这个参数，不只改MODEL_CONTEXT_WINDOW。语料版本仍是ch04，章节到ch07不代表需要迁移知识库。MySQL的conversations/messages/conversation_summaries等表由项目外维护，应用不自动建表。

## 2. 从全部关闭到可用

### 2.1 启动 MySQL

管理员 PowerShell：已为 Running 时跳过启动。

```powershell
Get-Service -Name MySQL80
if ((Get-Service -Name MySQL80).Status -ne 'Running') { Start-Service -Name MySQL80 }
Get-Service -Name MySQL80
```

普通终端验证本地既有数据库；密码由客户端提示输入，账号以本机实际授权为准：

```powershell
& $projectMysql -h 127.0.0.1 -P 3306 -u root -p melhelp -e 'SELECT 1 AS ok;'
if ($LASTEXITCODE -ne 0) { throw 'MySQL登录失败，先核对服务、账号或权限。' }
```

### 2.2 启动 Docker、etcd 与 Milvus

从开始菜单打开 Docker Desktop，等引擎就绪，再在已执行准备区的终端运行：

```powershell
& $projectDocker info
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop引擎未就绪。' }
& $projectDocker compose @projectCompose config --quiet
if ($LASTEXITCODE -ne 0) { throw '两份Compose合并配置校验失败。' }
& $projectDocker compose @projectCompose up -d --wait --wait-timeout 120
if ($LASTEXITCODE -ne 0) { throw '检索服务未按时就绪，先看日志，不继续启动应用。' }
& $projectDocker compose @projectCompose ps
Invoke-RestMethod 'http://127.0.0.1:9091/healthz' -TimeoutSec 5
```

始终按上述顺序合并根目录的两份 Compose。第二份启用外部 etcd，等待其健康后再启动 Milvus；数据分别在根 `deploy/milvus/volumes/etcd-external` 与 `volumes/milvus`。`etcd-external` 缺失时应找回匹配备份，不创建空目录绕过保护，也不切回单文件嵌入 etcd 方案。

### 2.3 核对现有知识和校准身份

```powershell
Invoke-MewHelpKb status
Invoke-MewHelpKb verify-hybrid
& $projectPython -c "import sys; from scripts.demo_ch07 import configure; configure(profile=sys.argv[1]); print('ch07 profile identity OK')" $projectContextProfile
if ($LASTEXITCODE -ne 0) { throw 'profile缺失或身份不匹配，请按第5节处理后再启动。' }
& $projectPython scripts/demo_ch07.py budget --profile $projectContextProfile
if ($LASTEXITCODE -ne 0) { throw '上下文预算自检失败，先核对所有分项，不继续启动。' }
```

这些命令只检查现有数据，不是初始化建库。启动器逐项核对原有语料/模型/Prompt/代码身份与额外 ch07 管线 hash，再读取阈值；不能只从旧文件取一个 0.25 就启动。125 有效块、阈值 0.25 是 ch07 验收快照，后续合法变更后应重新核对，不能删数据凑旧数量。

### 2.4 启动整个应用

```powershell
Set-Location -LiteralPath $projectApp
$chapterListeners = @(Get-NetTCPConnection -LocalPort 8018 -State Listen -ErrorAction SilentlyContinue)
if ($chapterListeners.Count) { throw '8018已占用，按6.2核对现有进程，不重复启动。' }
& $projectPython scripts/demo_ch07.py serve --profile $projectContextProfile --port 8018
```

保持此终端打开。启动器加载根 `.env`，显式启用工作流及既有知识集合，绑定 `log/ch07/checkpoints.sqlite` 和已选择 profile；不会改写 `.env`。只运行一个 8018 进程，不使用 `--reload` 或多 worker，不从根目录直接运行旧应用。

统一入口：[聊天](http://localhost:8018/)、[知识库](http://localhost:8018/kb)、[后台](http://localhost:8018/admin)、[API 文档](http://localhost:8018/docs)。若MySQL、Docker/检索服务原本已运行，日常可直接做2.3、2.4。启动器提供整个项目的后端和静态前端，不再另起旧章应用。

## 3. 启动检查与日常使用

另一个已准备的终端执行只读检查：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8018/health' -TimeoutSec 5
Invoke-RestMethod 'http://127.0.0.1:8018/api/kb/overview' -TimeoutSec 5
Invoke-RestMethod 'http://127.0.0.1:8018/api/jobs' -TimeoutSec 5
```

`/health` 只说明应用响应，不证明数据库、检索和上游模型全部可用。需要端到端检查时，可在聊天页问订单 1001 物流，或新会话问“这个能退吗”：后者应出现三张演示订单卡，点击后自动继续订单→扩写→政策→判断。聊天会调用付费模型并保存合成消息，弱证据问题还可能写低置信度池，不属于完全无副作用的健康检查。

订单选择使用LangGraph原生interrupt，暂停期间网络响应结束不等于退款判断已完成；点卡由相同会话恢复。ch07刷新同标签或点击侧栏旧会话会回载MySQL用户/客服历史原文，并恢复待选、退款申请、建工单/转人工建议；已完成或待核实的工单按钮不能当作可重复提交。调试启动器会展示理解后的本轮问题，不代表新增跨会话记忆。

退款表单原因从固定类目选择，取消不建票；确认会向本地已有 `tickets` 表真实创建一条售后**演示申请**，不执行资金退款。同一 action 重复提交不重复建票，新的申请仍可能建新票。不要为了日常检查反复确认。投诉的转人工、建工单也各自独立，转人工按钮本身不自动建票。

意图识别默认使用主力模型；`INTENT_SMALL_MODEL` 和 `INTENT_ESCALATION_THRESHOLD` 仅是惰性配置种子，填写后也不会启用小模型路线。低置信度归“其他”和最多一次 JSON 格式修复是当前行为，不能把它们当作低分升级模型。

### 3.1 ch07上下文与多会话

普通启动默认W128000，验收的代表性21轮无需压缩；并不保证任意长度的21轮都装得下。切换小窗口前按6.1正常停掉当前8018，在同一准备好的终端执行：

```powershell
$projectContextProfile = 'demo' # 切回日常时改'default'，停掉旧进程后再启动
& $projectPython scripts/demo_ch07.py budget --profile $projectContextProfile
if ($LASTEXITCODE -ne 0) { throw '预算不足，先核对输入/输出/工具/步骤等开销。' }
& $projectPython scripts/demo_ch07.py serve --profile $projectContextProfile --port 8018
```

另一个终端查看上下文与级联日志：

```powershell
Select-String -Path log/app.log -Pattern 'model_ctx|history_ctx|层1 降级|summary trigger|summary start|summary done|summary skip|summary fail'
```

演示六变量为MODEL_CONTEXT_WINDOW=18000、MAX_OUTPUT_TOKENS=2000、MAX_USER_INPUT_TOKENS=2000、MAX_AGENT_STEPS=3、TOOL_RESULT_MAX_TOKENS=1200、RERANK_TOP_K=5。当前扣除system2000、evidence1500、summary1000、output2000、safety850、peak5000后，历史5650、层1预算3954、层2预算1695；实际系统/累计摘要变大时继续按实际扣减。default预设为128000/4000/4000/8/2400/10，当前历史80000、层1预算55999、层2预算24000。中文系数1.0与预算口径一起校准，不单改一个参数凑窗口。

聊天页“新对话”保留旧会话，发送第一条消息后新会话出现在侧栏。摘要仅当前会话使用，checkpoint完整历史不裁。切回大窗口不会回写或撤销已提交摘要、回退锚点；验证默认不压缩时开全新合成会话，不复用已经摘要的会话。

model_ctx含实际Agent payload、全部摘要、逐条窗口；history_ctx每轮都有，也记录理解/分类实际输入。日志仅本地保管。后台摘要追加完成后下一轮同步，关闭取消未完成任务，未提交边界的下一轮仍可重试。只读接口为GET /api/conversations?user_id=...、GET /api/conversations/{id}/messages?user_id=...。

### 3.2 浏览器会话归组与地址

localStorage的mewhelp-user-id是同一浏览器归组键，sessionStorage的mewhelp-chat-session保存该标签页当前会话。刷新、同源新标签和侧栏可继续旧会话；无痕窗口、换浏览器或清站点数据会失去原归组键，数据库原文不会因此删除。

固定使用同一个地址：localhost:8018与127.0.0.1:8018、不同端口属于不同origin，localStorage不共享。侧栏突然为空时先回到原地址核对，不清MySQL或新建数据库。同一浏览器备份/迁移需私下保全这两个键及原origin；恢复到原origin后再刷新。匿名标识不是登录认证。

## 4. 日志、知识与作业维护

### 4.1 查看状态与空间

```powershell
& $projectDocker compose @projectCompose ps -a
& $projectDocker compose @projectCompose logs --tail 100 standalone
& $projectDocker compose @projectCompose logs --tail 100 etcd
Get-ChildItem -LiteralPath $projectState -File | Select-Object Name,Length,LastWriteTime
Get-PSDrive -PSProvider FileSystem
```

应用异常看启动终端，节点日志在实际应用的 `log/app.log`；网页作业日志默认在 `log/acceptance/`，质量报告在 `reports/ch07/`。日志、SQLite、SQL 备份可能包含对话和工单，按业务资料保存，不公开上传。

需要归档app.log时先正常停应用并确认文件不再写入，再复制到受控备份；关闭句柄后才移动/删除已归档旧日志，再启动会建立新日志。运行中不要直接改名期待自动轮转。保留故障时间、conversation/request及摘要覆盖边界，排查完再按保留策略处理日志；不得清原文、摘要或WAL腾空间。

### 4.2 知识维护

先运行 `Invoke-MewHelpKb status`、`Invoke-MewHelpKb verify-hybrid`。仅当已确认有 pending、停掉其他知识写入方并备份后，才执行补漏：

```powershell
Invoke-MewHelpKb vectorize
Invoke-MewHelpKb verify-hybrid
```

vectorize 会写 Milvus 并产生嵌入费用，不是启动必需步骤。录入返回“已入库 pending”但向量化失败时，保留原文、先修服务再补 pending，不重复粘贴录入，也不手工改成 done。新入库知识或源字节变化后需检查身份和校准，不能把旧阈值永久沿用。

日常不运行 build、rebuild、migrate 或 reset；`kb-reset` 会清知识库。不要通过清库、删 Docker 卷或删除 SQLite WAL 处理启动错误。

### 4.3 网页Make作业与会话摘要

`/admin`、`/kb` 只能调用既有固定 Make 目标：预览、建库、演示会话、挖掘、向量化、重建、重置；网页不能接收任意 shell 命令。预览只读，其余应按实际写入/费用范围处理，重置具有破坏性。Make 路径由现有 `MAKE_EXE` 控制，本机常用 `C:/Program Files (x86)/GnuWin32/bin/make.exe`。

网页作业在单应用进程内互斥，不能阻止另一个终端或旧服务同时写知识。重启应用后作业内存状态会重置，显示 idle 不能证明原子进程已退出；维护和停机前还要核对原终端及实际任务，不要重复启动。

会话摘要是应用进程内独立异步任务，不属于Make注册表，也不会出现在/api/jobs列表中；不需要另外启动worker。层1超额先移动边界，层2用量超过额度才触发摘要；summary done才代表新段与覆盖边界已提交。生成/核验/容量失败不前移摘要覆盖范围，下一轮仍保留并按预算重试；不要手改锚点或旧段“修复”。

## 5. 校准、评估与代码验证

### 5.1 profile 缺失或身份变化

先保证 MySQL、Milvus 和既有清单可用。缺文件时优先找回与当前源码/数据匹配的备份；如果确需生成新校准，使用原固定 24 条 calibration，不使用 test 调阈值，保留旧 profile 和报告：

```powershell
$calibrationStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$previousCalibrationReport = Join-Path $projectApp 'reports/ch07/calibration'
$calibrationArchive = Join-Path $projectApp "reports/ch07/calibration-before-$calibrationStamp"
if (Test-Path -LiteralPath $previousCalibrationReport) {
    Copy-Item -LiteralPath $previousCalibrationReport -Destination $calibrationArchive -Recurse -ErrorAction Stop
}
$projectProfile = Join-Path $projectState "calibration-$calibrationStamp.json"
$env:CH07_CALIBRATION_PROFILE = $projectProfile
& $projectPython scripts/demo_ch07.py calibrate --profile $projectContextProfile
if ($LASTEXITCODE -ne 0) { throw '校准失败，保留旧文件与失败报告，不启用新profile。' }
& $projectPython -c "import sys; from scripts.demo_ch07 import configure; configure(profile=sys.argv[1]); print('ch07 profile identity OK')" $projectContextProfile
if ($LASTEXITCODE -ne 0) { throw '新profile严格身份验证失败。' }
```

校准会调用真实模型、嵌入和重排并计费，当前入口固定写 `reports/ch07/calibration`，所以先归档。新终端需重新设置选定的 `CH07_CALIBRATION_PROFILE`，否则启动器回到默认文件。不得强改 hash、把旧 identity 填进新文件，或删除新增知识迁就旧 profile。

profile 额外绑定 ch07 管线，并不表示已经对多查询独立校准；目前校准范围仍是原单查询链。发布质量需另看独立测试与抽查，不以校准成绩代替。

### 5.2 按需复验

```powershell
$evaluationStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
& $projectPython -m pytest -q
if ($LASTEXITCODE -ne 0) { throw '测试失败，保留输出先定位。' }
& $projectPython scripts/eval_ch07.py --task router --split dev --output "reports/ch07/router-dev-$evaluationStamp.json"
& $projectPython scripts/eval_ch07.py --task router --split acceptance --output "reports/ch07/router-acceptance-$evaluationStamp.json"
& $projectPython scripts/eval_ch07.py --task summary --split dev --output "reports/ch07/summary-dev-$evaluationStamp.json"
& $projectPython scripts/eval_ch07.py --task summary --split acceptance --output "reports/ch07/summary-acceptance-$evaluationStamp.json"
& $projectPython scripts/eval_ch07.py --task tokens --output "reports/ch07/tokens-$evaluationStamp.json"
& $projectPython scripts/eval_ch07.py --task dialogue --split acceptance --profile default --output "reports/ch07/default-dialogue-$evaluationStamp.json"
& $projectPython scripts/eval_ch07.py --task dialogue --split acceptance --profile demo --output "reports/ch07/demo-dialogue-$evaluationStamp.json"
```

除pytest外，上述评估按需单独执行，默认使用真实上游并计费，不要求日常全部跑。dialogue会在MySQL新增独立合成会话，并在log/ch07新建独立eval-*.sqlite；不会覆盖常驻服务checkpoints.sqlite。逐条命令检查退出码，非零时看该JSON及日志，不因后续命令成功就抹去失败。对话需有效profile，校准/评估不与知识写入并行。--provider mock不证明模型质量。

默认两组各21轮应零降级/摘要；demo应看到层1降级→层2超过1695→第N段追加，末轮保留订单1001及核实签收人的诉求。验收数据在docs/samples/ch07，SHA保持冻结；先dev迭代再acceptance，不改标签凑通过。原始失败attempt和旧报告保留。旧政策比较仍在scripts/eval_ch06_policy.py，不改旧profile与报告；浏览器复验命令见产品reports/ch07/demo.md，运行前先归档其固定文件名报告。

ch07功能验收快照为758 passed、摘要18/18、意图75/75、订单9/9、问题字面74/75及有限助词语义75/75，默认/demo各两组21轮及真实浏览器通过。它们来自功能开发验收，不是本次文档更新重跑的成绩；后续以当前输出为准，完整证据见产品reports/ch07/summary.md。

## 6. 重启与停止

### 6.1 正常停止或只重启应用

停止新请求，等待聊天、申请确认、Make作业及手工评估结束。会话摘要无需手工改边界或另停worker；正常shutdown会取消未完成后台任务，已提交的摘要段保留，未提交覆盖范围在以后轮次按预算重试。若已进入数据库提交，以实际持久化段和锚点为准，不回退边界。回到**启动8018的原终端**按Ctrl+C，等待shutdown完成；在另一个终端确认端口已释放：

```powershell
Get-NetTCPConnection -LocalPort 8018 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

无输出才表示该端口停止。重启应用无需关闭MySQL/容器，使用同一SQLite和已选检索profile，再执行 `& $projectPython scripts/demo_ch07.py serve --profile $projectContextProfile --port 8018`。新终端需先重做第1节并恢复原default/demo选择与自定义profile路径。原生interrupt待选可保留；工具异常中断不会作为新请求自动重放，已经生成但暂未落库的终答会在新用户入库前恢复。

### 6.2 原终端找不到时

先查询上面命令返回的**当前** PID，检查其实际程序和命令；不要给 PowerShell 自带 `$PID` 赋值：

```powershell
$serverProcessId = [int](Read-Host '输入刚查到的8018 OwningProcess编号')
Get-CimInstance Win32_Process -Filter "ProcessId = $serverProcessId" |
    Select-Object ProcessId,Name,ExecutablePath,CommandLine
```

核实为本项目 Python、`demo_ch07.py serve` 或已确认的本项目入口。相对命令还须结合启动记录确认所属工作树。确认没有处理中请求后，只有该 PID 仍监听 8018 才停止：

```powershell
$serverListeners = @(Get-NetTCPConnection -LocalPort 8018 -State Listen -ErrorAction SilentlyContinue)
if ($serverProcessId -notin $serverListeners.OwningProcess) { throw 'PID归属已变化，请重新查询。' }
Stop-Process -Id $serverProcessId -Confirm
Get-NetTCPConnection -LocalPort 8018 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

这是强制终止兜底，不等于 Ctrl+C 正常关闭，可能未执行 lifespan 清理。权限不足时在管理员终端重新查询归属；不要结束全部 Python。保留 SQLite 主文件及现有 WAL/SHM；若当时正在建票，应核对 MySQL 与 creating/uncertain 状态，不因页面报错就再次提交。

### 6.3 整套停止或关机

先确认所有使用同一MySQL/Milvus的应用和任务均已停止，包括可能仍在使用它们的历史8015、8016及手工CLI；不能只停8018就关闭共享基础设施。分别按实际PID/入口正常停止相关服务，不根据旧端口列表批量结束进程。随后先停Milvus，再停外部etcd：

```powershell
& $projectDocker compose @projectCompose stop -t 120 standalone
if ($LASTEXITCODE -ne 0) { throw 'Milvus停止失败，不继续停止etcd。' }
& $projectDocker compose @projectCompose stop -t 120 etcd
if ($LASTEXITCODE -ne 0) { throw 'etcd停止失败，请先检查。' }
& $projectDocker compose @projectCompose ps -a
```

只有 MySQL 无其他使用方且确实需要关闭时，再用管理员 PowerShell：

```powershell
Stop-Service -Name MySQL80
Get-Service -Name MySQL80
```

最后可退出 Docker Desktop。日常 stop 保留数据；不使用 `down -v`、prune、删除 volumes 或清库作为关闭方式。

## 7. 整套备份与恢复

### 7.1 同一停写窗口的冷备

整套恢复点必须同时包括 MySQL、Milvus 与外部 etcd、ch07 SQLite、实际源码/未提交内容、根配置、迁移清单及**当前选定**的 profile。Git HEAD 不足以保存未提交代码；单独备份 SQLite 也不是整个项目备份。

先停所有应用/作业/评估/调试连接，并确保没有其他程序或数据库事件继续修改相关数据。历史8015、8016若仍使用同一数据，也必须纳入停写核对。先保留MySQL和检索服务运行，以便最后检查一致性；/api/jobs的idle不代表摘要、旧子进程或手工作业已结束。

MySQL整库备份包括既有知识/工单表和conversations、messages、conversation_summaries及双锚点；SQLite保存完整LangGraph历史、工具轨迹、原生interrupt和操作状态。两边必须来自同一停写窗口。仅备份消息表或仅复制SQLite都不能完整恢复当前会话。

```powershell
Invoke-MewHelpKb verify-hybrid
& $projectPython -c "import sys; from scripts.demo_ch07 import configure; configure(profile=sys.argv[1]); print('backup identity OK')" $projectContextProfile
if ($LASTEXITCODE -ne 0) { throw '备份前身份校验失败，先核对，不标记完整恢复点。' }

$projectBackup = Join-Path 'E:\MewHelp-Backups' (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Path $projectBackup -ErrorAction Stop | Out-Null
$projectDump = Join-Path $projectBackup 'melhelp.sql'
& $projectMysqlDump -h 127.0.0.1 -P 3306 -u root -p --single-transaction --routines --events --triggers --hex-blob --no-tablespaces --set-gtid-purged=OFF "--result-file=$projectDump" melhelp
if ($LASTEXITCODE -ne 0) { throw 'MySQL导出失败，不标记完整备份。' }

& $projectDocker compose @projectCompose stop -t 120 standalone
if ($LASTEXITCODE -ne 0) { throw 'Milvus未停止，不能冷拷贝。' }
& $projectDocker compose @projectCompose stop -t 120 etcd
if ($LASTEXITCODE -ne 0) { throw 'etcd未停止，不能冷拷贝。' }
Copy-Item -LiteralPath (Join-Path $projectRoot 'deploy/milvus/volumes') -Destination (Join-Path $projectBackup 'volumes') -Recurse -ErrorAction Stop

$sqliteBackup = Join-Path $projectBackup 'sqlite'
New-Item -ItemType Directory -Path $sqliteBackup -ErrorAction Stop | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $projectState 'checkpoints.sqlite'))) {
    throw '检查点文件缺失，请核对，不创建空文件冒充备份。'
}
foreach ($stateName in @('checkpoints.sqlite','checkpoints.sqlite-wal','checkpoints.sqlite-shm')) {
    $stateFile = Join-Path $projectState $stateName
    if (Test-Path -LiteralPath $stateFile) {
        Copy-Item -LiteralPath $stateFile -Destination $sqliteBackup -ErrorAction Stop
    }
}

$metadataBackup = Join-Path $projectBackup 'metadata'
New-Item -ItemType Directory -Path $metadataBackup -ErrorAction Stop | Out-Null
Copy-Item -LiteralPath $projectManifest -Destination (Join-Path $metadataBackup 'migration-manifest.json') -ErrorAction Stop
Copy-Item -LiteralPath $projectProfile -Destination (Join-Path $metadataBackup 'active-calibration-profile.json') -ErrorAction Stop
@{Application=$projectApp;Manifest=$projectManifest;Profile=$projectProfile;State=$projectState;ContextProfile=$projectContextProfile;BrowserOrigin='http://localhost:8018'} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $metadataBackup 'locations.json') -Encoding utf8
```

MySQL用--result-file，避免PowerShell输出重定向的编码问题；密码只在交互提示输入。--single-transaction仅为InnoDB提供事务快照，不替代整套跨库停写，不允许导出期间并发DDL，也不保证非事务表的一致性。账号需具备相应SELECT/视图/触发器/例程/事件导出权限；权限失败时修正授权与备份范围，不把缺表/空SQL标记为完整备份。

上述数据复制完成后，还必须保存以下材料才算完整备份：

- **实际应用工作树**：app、data、scripts、tests、docs、reports、dev-notes、Makefile、pyproject.toml、uv.lock、.python-version、.env.example、.gitignore、存在的 AGENTS.md 与 `.superpowers` 账本；包含未跟踪源码。
- **根目录**：未提交/未跟踪内容与根 `.git` 仓库元数据；根 `deploy/milvus/` 的两份 Compose、user.yaml、embedEtcd.yaml 以及其他现存配置；根 `.env` 受控保存。不要遗漏根目录尚未同步到工作树的独立改动。
- **当前会话与历史运行状态**：ch07 SQLite 文件集已单独保存；如果还需恢复旧服务/旧会话，还要在其连接全部关闭后保存其实际 SQLite，不把旧文件覆盖到 ch07 路径。可选保存相应日志和缓存。
- **浏览器归组与标签页**：按3.2私下记录原origin、mewhelp-user-id和当前mewhelp-chat-session；换机器/清浏览器数据后仅恢复服务器文件不会自动恢复旧列表归组。评估eval-*.sqlite若需复现实验也一并保留，与常驻服务检查点分开标注。

把上述源码材料复制到本次备份目录后，记录源码分支/HEAD、实际未提交状态、文件清单与 SHA256，并核对 SQL/volumes/SQLite/metadata/源码/私有配置齐全。工作树 `.git` 是链接文件，不能靠它替代根仓库元数据；换位置恢复时还需核对 Git 工作树路径。虚拟环境可重建，不必当作唯一环境备份。

SQLite 最后连接正常关闭通常会合并 WAL 并移除侧文件；异常退出仍可能留下 WAL。WAL 是持久状态的一部分：冷备时保留存在的整套文件，不手动删除，也不在运行中逐个复制主文件与侧文件。本手册提供的是停写冷备，不是在线一致性备份。SQL、根 `.env` 和备份中的对话资料须限制访问；完成后另存独立磁盘，同 E 盘副本不能抵御整盘故障。

### 7.2 恢复顺序

恢复涉及覆盖数据，先明确恢复点、目标机器和覆盖范围，保全当前现场，再按同一备份包逐项执行；它不是每日启动步骤。

1. 停全部应用/任务及 SQLite 连接；停止 Milvus/etcd。不要混用不同时间的 SQL、volumes 或 WAL。
2. 恢复匹配的实际应用源码、根配置和基础设施配置，核对路径、锁定依赖和 Git 工作树元数据；不要仅恢复 HEAD 而丢掉未提交内容。
3. 由有权限的操作者确认目标数据库后导入 SQL；恢复整套已停止的 Milvus/外部 etcd 数据。原数据先整体保全，不递归删除未经核实的路径。本次文档更新未授权或执行建表/覆盖库。
4. 在无连接时整体恢复对应 SQLite 及其现有侧文件，旧主文件/侧文件先一并保全，不将旧 WAL 与另一份主文件混搭。恢复 metadata 记录的迁移清单位置与选定 profile；自定义 profile 需重新设置环境变量。
5. 按第 1–3 节启动并校验双写一致性、严格 profile 身份、同一 session_id 的待选/工单状态，再开放使用。换电脑若身份或依赖变化，先查原因，不能强改 hash。

仅在第3步的覆盖范围已由操作者确认后，用mysql客户端交互导入已核对的备份：先执行 `& $projectMysql -h 127.0.0.1 -P 3306 -u root -p melhelp`，在mysql提示符执行 `SOURCE E:/MewHelp-Backups/实际备份目录/melhelp.sql;`，检查错误后退出。路径替换为真实备份，不通过PowerShell把SQL全文转码管道导入。dump可能包含DROP/CREATE等覆盖语句，这不是启动、健康检查或建表步骤；此处只记录恢复方法，本次没有执行。

恢复后用原origin/匿名归组键验证：旧会话列表、完整原文、已摘要标记、最初订单回忆、待选卡及已完成/待核实工单状态；无需为了验收再创建一张真实工单。应用正常关闭后保全新的恢复记录和文件SHA，再恢复正常请求。

MySQL还需完整保全conversations/messages/conversation_summaries及两锚点；浏览器localStorage匿名user_id和sessionStorage当前canonical会话决定列表归组。不要只备份SQLite遗漏会话摘要。MySQL工单写入与SQLite状态保存不是跨库原子事务。恢复/强制终止后的 creating 或 uncertain 应核实实际工单，不能保证 exactly-once，也不能删除状态后盲目重试。本手册不提供清空现有数据的一键恢复脚本。

## 8. 常见故障的处理顺序

- **8018 占用**：按6.2查询实际PID/入口，不重复启动或结束全部Python；8015/8016的旧响应不能当ch07成功。
- **MySQL 登录失败**：核对 MySQL80、账号权限、本地 DB_URL 编码；端口能连不等于能访问表，不打印完整连接串。
- **Docker/Milvus 初始化失败**：先确认 Desktop 引擎，再查两份 Compose、外部 etcd 健康和已有数据目录；保留日志，不清卷或切回旧嵌入模式。
- **BM25 能用而 dense/hybrid 失败**：先查嵌入模型网络/密钥/余额；只有 hybrid_rerank 失败再查重排。401/403 查鉴权，402 查余额，429 查限流，timeout 查网络/代理，不能一律归为迁移错误。
- **ONLINE 但聊天失败**：`/health` 不覆盖真实依赖；按 MySQL→Milvus/profile→模型服务排查。修改本地配置后正常重启应用，刷新页面不能清掉进程缓存。
- **profile 不匹配/缺失**：先看是否语料、模型、Prompt、generic 或 ch07 代码变更；恢复正确文件或按第 5 节新校准。不把阈值单独拷进环境绕过校验。
- **缺订单号停住**：应有聊天卡片和 pending interrupt；点选后同会话恢复。选择 409 会刷新公开 state；检查实际 sessionStorage/session_id 和 SQLite，不发第二条退款问题假装续跑。
- **退款问题安全拒答**：查证据闸与重排分数；短指代和部分口语存在误拒边界。扩写回退会保留原问，不为演示正答直接跳过政策检索或降阈值。
- **申请 creating/uncertain 或提交报错**：保存发生时间与会话/action 关联，核对 MySQL 工单及 SQLite；不自动重试或删状态重建。
- **SQLite locked/打不开/磁盘满**：查第二进程、调试连接、绝对路径、权限及磁盘；不改 `:memory:`、不删 WAL/SHM、不加多 worker。
- **侧栏空白/无法回载**：先核对原origin、匿名键与/api/conversations响应；列表故障会静默降级，聊天仍可用。不要用清浏览器或清库排障；换回原地址或恢复原归组键后重试。
- **摘要迟迟不出现/失败**：先看层2实际用量是否超过预算，再看summary trigger/start/done/skip/fail、边界和耗时。默认代表性短会话不触发属于预期；未提交的旧消息仍在，模型/核验失败后保留并重试，不手改summary_upto_msg_id。
- **原文回载缺答/回复后提示错误**：查MySQL写入与checkpoint状态。终答提交前失败时后续会先补旧答，持续失败会阻止插入新轮，先修DB而非跳过槽位或删消息。
- **作业显示 idle 但似乎仍运行**：核对旧进程/手工终端，重启不代表取消。网页互斥不覆盖其他进程。
- **预算兜底**：核对节点与工具循环；新workflow按模型窗口派生历史/层级与累计额度，默认步骤8（含最终回答）；旧AGENT_TOKEN_BUDGET=12000仅旧loop使用。启动budget可列分项；上下文预算不足时核对系统/证据/摘要/输出/安全/峰值，不能只调窗口。

## 9. 当前边界与资料

系统仍为本机演示，无鉴权的 `/admin`、`/kb`、`/api/*` 不直接当公网服务。SQLite 文件及进程内会话锁按单应用进程运行，不支持多实例共享。退款只建立待审核演示申请；不训练分类模型、不新增跨会话记忆。本次文档维护不提交、推送、合并或部署。

开发事实与完整结果见[当前交接](CH07_CURRENT_PROGRESS.md)、[开发留痕](../dev-notes/ch07.md)。实际应用的[交付报告](<E:/MewHelp AI/.worktrees/ch05-workflow-agent/reports/ch07/summary.md>)和[演示步骤](<E:/MewHelp AI/.worktrees/ch05-workflow-agent/reports/ch07/demo.md>)有质量限制与验证证据；运行报告只保留在产品工作树，没有在根目录复制另一份。

本手册按现有源码/锁文件核对。外部接口依据已通过 Context7 获取的官方说明：[Compose 多文件合并](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)、[Compose up](https://docs.docker.com/reference/cli/docker/compose/up/)、[Compose stop](https://docs.docker.com/reference/cli/docker/compose/stop/)、[MySQL 8 mysqldump](https://dev.mysql.com/doc/refman/8.0/en/mysqldump.html)、[SQLite WAL](https://www.sqlite.org/wal.html)。启动/停止参数还应与实际安装的 CLI 帮助核对，不因文档更新升级基础设施。

2026-10-25本次文档核对：Docker Compose v5.5.0的--wait/--wait-timeout及stop --timeout、两份Compose config --quiet、本机MySQL客户端8.0.45、ch07 CLI help及两profile预算已只读验证。服务当时MySQL Running、etcd/Milvus healthy、8018监听；这只是当时快照，启动/停止/恢复的写操作未演练，本次未重跑758项或真实模型评估。
