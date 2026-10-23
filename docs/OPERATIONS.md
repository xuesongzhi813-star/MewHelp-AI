# MewHelp AI 整体启动、停止与维护手册

适用版本：截至 ch06，Windows 本机，更新于 2026-10-23。本文是整个项目的统一操作入口；按下列顺序即可准备环境、启动、检查、维护和停止，无需拼接历史章节命令。

当前实际应用在 `E:\MewHelp AI\.worktrees\ch05-workflow-agent`。根目录提供共享 `.venv`、私有 `.env`、Docker 配置和数据；根目录的旧应用不用于启动 ch06。分支和工作树名称仍含 ch05，不代表应运行旧启动器。当前未提交修改也属于待保全的项目内容。

本次仅更新文档和执行只读验证，未替你启停服务、备份、恢复、清库或提交代码。以下操作命令供实际需要时执行。

## 1. 环境、路径与唯一应用入口

常驻组件是 MySQL80（3306）、Docker Desktop 内的外部 etcd 与 Milvus（19530/9091），以及一个 ch06 FastAPI 进程（8016）。静态聊天页、`/kb` 和 `/admin` 都由该进程提供；SQLite 是应用读写的本地文件，不需要另起服务，也不需要前端 Node 服务或额外 worker。

历史共享 8015 可能仍在运行，它不是 ch06 的当前入口。不要因启动 8016 就结束它；但整套基础设施关停或一致性备份前，必须核对它是否仍使用同一 MySQL/Milvus。

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
$projectState = Join-Path $projectApp 'log/ch06'
$projectManifest = Join-Path $projectRoot 'log/kb/ch04-v2-final-migration.json'
$projectProfile = if ($env:CH06_CALIBRATION_PROFILE) {
    $env:CH06_CALIBRATION_PROFILE
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

# 供手工知识检查使用，与 ch06 启动器所选的现有知识库保持一致。
$env:KB_CORPUS_VERSION = 'ch04'
$env:KNOWLEDGE_COLLECTION = 'knowledge'
$env:HYBRID_COLLECTION = 'knowledge_hybrid_ch04_v2_final'
$env:KNOWLEDGE_STRATEGY = 'hybrid_rerank'
$env:KB_MIGRATION_MANIFEST = $projectManifest
$env:CH06_CALIBRATION_PROFILE = $projectProfile

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
& $projectPython -c "from scripts.demo_ch06 import configure; configure(); print('ch06 profile identity OK')"
if ($LASTEXITCODE -ne 0) { throw 'profile缺失或身份不匹配，请按第5节处理后再启动。' }
```

这些命令只检查现有数据，不是初始化建库。启动器逐项核对原有语料/模型/Prompt/代码身份与额外 ch06 管线 hash，再读取阈值；不能只从旧文件取一个 0.25 就启动。125 有效块、阈值 0.25 是 ch06 验收快照，后续合法变更后应重新核对，不能删数据凑旧数量。

### 2.4 启动整个应用

```powershell
Set-Location -LiteralPath $projectApp
& $projectPython scripts/demo_ch06.py serve
```

保持此终端打开。启动器加载根 `.env`，显式启用工作流及既有知识集合，绑定 `log/ch06/checkpoints.sqlite` 和已选择 profile；不会改写 `.env`。只运行一个 8016 进程，不使用 `--reload` 或多 worker，不从根目录直接运行旧应用。

统一入口：[聊天](http://127.0.0.1:8016/)、[知识库](http://127.0.0.1:8016/kb)、[后台](http://127.0.0.1:8016/admin)、[API 文档](http://127.0.0.1:8016/docs)。若 MySQL、Docker/检索服务原本已运行，日常可直接做 2.3、2.4；端口已占用时先核对进程，不重复启动。

## 3. 启动检查与日常使用

另一个已准备的终端执行只读检查：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8016/health' -TimeoutSec 5
Invoke-RestMethod 'http://127.0.0.1:8016/api/kb/overview' -TimeoutSec 5
Invoke-RestMethod 'http://127.0.0.1:8016/api/jobs' -TimeoutSec 5
```

`/health` 只说明应用响应，不证明数据库、检索和上游模型全部可用。需要端到端检查时，可在聊天页问订单 1001 物流，或新会话问“这个能退吗”：后者应出现三张演示订单卡，点击后自动继续订单→扩写→政策→判断。聊天会调用付费模型并保存合成消息，弱证据问题还可能写低置信度池，不属于完全无副作用的健康检查。

订单选择使用 LangGraph 原生 `interrupt()`，暂停期间网络响应结束不等于退款判断已完成；点卡由相同会话恢复。刷新同标签会沿用 sessionStorage 并恢复待选/申请操作，不重绘所有历史；新会话不会继承旧会话。调试启动器会展示理解后的本轮问题，不代表新增跨会话记忆。

退款表单原因从固定类目选择，取消不建票；确认会向本地已有 `tickets` 表真实创建一条售后**演示申请**，不执行资金退款。同一 action 重复提交不重复建票，新的申请仍可能建新票。不要为了日常检查反复确认。投诉的转人工、建工单也各自独立，转人工按钮本身不自动建票。

意图识别默认使用主力模型；`INTENT_SMALL_MODEL` 和 `INTENT_ESCALATION_THRESHOLD` 仅是惰性配置种子，填写后也不会启用小模型路线。低置信度归“其他”和最多一次 JSON 格式修复是当前行为，不能把它们当作低分升级模型。

## 4. 日志、知识与作业维护

### 4.1 查看状态与空间

```powershell
& $projectDocker compose @projectCompose ps -a
& $projectDocker compose @projectCompose logs --tail 100 standalone
& $projectDocker compose @projectCompose logs --tail 100 etcd
Get-ChildItem -LiteralPath $projectState -File | Select-Object Name,Length,LastWriteTime
Get-PSDrive -PSProvider FileSystem
```

应用异常看启动终端，节点日志在实际应用的 `log/ch06/nodes.log`；网页作业日志默认在 `log/acceptance/`，质量报告在 `reports/ch06/`。日志、SQLite、SQL 备份可能包含对话和工单，按业务资料保存，不公开上传。

### 4.2 知识维护

先运行 `Invoke-MewHelpKb status`、`Invoke-MewHelpKb verify-hybrid`。仅当已确认有 pending、停掉其他知识写入方并备份后，才执行补漏：

```powershell
Invoke-MewHelpKb vectorize
Invoke-MewHelpKb verify-hybrid
```

vectorize 会写 Milvus 并产生嵌入费用，不是启动必需步骤。录入返回“已入库 pending”但向量化失败时，保留原文、先修服务再补 pending，不重复粘贴录入，也不手工改成 done。新入库知识或源字节变化后需检查身份和校准，不能把旧阈值永久沿用。

日常不运行 build、rebuild、migrate 或 reset；`kb-reset` 会清知识库。不要通过清库、删 Docker 卷或删除 SQLite WAL 处理启动错误。

### 4.3 后台作业

`/admin`、`/kb` 只能调用既有固定 Make 目标：预览、建库、演示会话、挖掘、向量化、重建、重置；网页不能接收任意 shell 命令。预览只读，其余应按实际写入/费用范围处理，重置具有破坏性。Make 路径由现有 `MAKE_EXE` 控制，本机常用 `C:/Program Files (x86)/GnuWin32/bin/make.exe`。

网页作业在单应用进程内互斥，不能阻止另一个终端或旧服务同时写知识。重启应用后作业内存状态会重置，显示 idle 不能证明原子进程已退出；维护和停机前还要核对原终端及实际任务，不要重复启动。

## 5. 校准、评估与代码验证

### 5.1 profile 缺失或身份变化

先保证 MySQL、Milvus 和既有清单可用。缺文件时优先找回与当前源码/数据匹配的备份；如果确需生成新校准，使用原固定 24 条 calibration，不使用 test 调阈值，保留旧 profile 和报告：

```powershell
$calibrationStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$previousCalibrationReport = Join-Path $projectApp 'reports/ch06/calibration'
$calibrationArchive = Join-Path $projectApp "reports/ch06/calibration-before-$calibrationStamp"
if (Test-Path -LiteralPath $previousCalibrationReport) {
    Copy-Item -LiteralPath $previousCalibrationReport -Destination $calibrationArchive -Recurse -ErrorAction Stop
}
$projectProfile = Join-Path $projectState "calibration-$calibrationStamp.json"
$env:CH06_CALIBRATION_PROFILE = $projectProfile
& $projectPython scripts/demo_ch06.py calibrate
if ($LASTEXITCODE -ne 0) { throw '校准失败，保留旧文件与失败报告，不启用新profile。' }
& $projectPython -c "from scripts.demo_ch06 import configure; configure(); print('ch06 profile identity OK')"
if ($LASTEXITCODE -ne 0) { throw '新profile严格身份验证失败。' }
```

校准会调用真实模型、嵌入和重排并计费，当前入口固定写 `reports/ch06/calibration`，所以先归档。新终端需重新设置选定的 `CH06_CALIBRATION_PROFILE`，否则启动器回到默认文件。不得强改 hash、把旧 identity 填进新文件，或删除新增知识迁就旧 profile。

profile 额外绑定 ch06 管线，并不表示已经对多查询独立校准；目前校准范围仍是原单查询链。发布质量需另看独立测试与抽查，不以校准成绩代替。

### 5.2 按需复验

```powershell
$evaluationStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
& $projectPython -m pytest -q
& $projectPython scripts/demo_ch06.py router-eval --split dev --output "reports/ch06/router-dev-$evaluationStamp.json"
& $projectPython scripts/demo_ch06.py router-eval --split acceptance --output "reports/ch06/router-acceptance-$evaluationStamp.json"
```

后两条使用仓库合成样例调用真实上游，有费用；没有使用客户对话。ch06 政策单查/扩写比较入口为 `scripts/eval_ch06_policy.py`，固定输出 `reports/ch06/policy-comparison.json`，复跑前另存既有报告。运行需要有效 profile，不能和知识写入同时做，以免快照变化。

ch06 交付曾得到 700 passed、真实意图/严格 JSON 75/75；独立问题字面 74/75、有限助词语义一致 75/75 分开记录。12 条政策题单/多查 Recall@10 均 83.33%，短指代可能低分安全拒答，不能承诺扩写一定提升召回。以上是历史验收结果，不是本轮文档更新重跑的成绩，后续以新日志为准。详见实际应用的 `reports/ch06/summary.md`。

## 6. 重启与停止

### 6.1 正常停止或只重启应用

停止新请求，等待聊天、申请确认、后台作业及手工评估结束。回到**启动 8016 的原终端**按 Ctrl+C，等待 shutdown 完成；在另一个终端确认端口已释放：

```powershell
Get-NetTCPConnection -LocalPort 8016 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

无输出才表示该端口停止。重启应用无需关闭 MySQL/容器，使用同一 SQLite 和已选 profile，再执行 `& $projectPython scripts/demo_ch06.py serve`。待选原生 interrupt 可保留；工具异常中断不会作为新请求自动重放。

### 6.2 原终端找不到时

先查询上面命令返回的**当前** PID，检查其实际程序和命令；不要给 PowerShell 自带 `$PID` 赋值：

```powershell
$serverProcessId = [int](Read-Host '输入刚查到的8016 OwningProcess编号')
Get-CimInstance Win32_Process -Filter "ProcessId = $serverProcessId" |
    Select-Object ProcessId,Name,ExecutablePath,CommandLine
```

核实为本项目 Python、`demo_ch06.py serve` 或已确认的本项目入口。相对命令还须结合启动记录确认所属工作树。确认没有处理中请求后，只有该 PID 仍监听 8016 才停止：

```powershell
$serverListeners = @(Get-NetTCPConnection -LocalPort 8016 -State Listen -ErrorAction SilentlyContinue)
if ($serverProcessId -notin $serverListeners.OwningProcess) { throw 'PID归属已变化，请重新查询。' }
Stop-Process -Id $serverProcessId -Confirm
Get-NetTCPConnection -LocalPort 8016 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

这是强制终止兜底，不等于 Ctrl+C 正常关闭，可能未执行 lifespan 清理。权限不足时在管理员终端重新查询归属；不要结束全部 Python。保留 SQLite 主文件及现有 WAL/SHM；若当时正在建票，应核对 MySQL 与 creating/uncertain 状态，不因页面报错就再次提交。

### 6.3 整套停止或关机

先确认所有使用同一 MySQL/Milvus 的应用和任务均已停止，包括仍在使用它们的历史 8015；不能只停 8016 就关闭共享基础设施。随后先停 Milvus，再停外部 etcd：

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

整套恢复点必须同时包括 MySQL、Milvus 与外部 etcd、ch06 SQLite、实际源码/未提交内容、根配置、迁移清单及**当前选定**的 profile。Git HEAD 不足以保存未提交代码；单独备份 SQLite 也不是整个项目备份。

先停所有应用/作业/评估/调试连接，并确保没有其他程序或数据库事件继续修改相关数据。历史 8015 若仍使用同一数据，也必须纳入停写核对。先保留 MySQL 和检索服务运行，以便最后检查一致性；`/api/jobs` 的 idle 不足以证明已停写。

```powershell
Invoke-MewHelpKb verify-hybrid
& $projectPython -c "from scripts.demo_ch06 import configure; configure(); print('backup identity OK')"
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
@{Application=$projectApp;Manifest=$projectManifest;Profile=$projectProfile;State=$projectState} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $metadataBackup 'locations.json') -Encoding utf8
```

MySQL 用 `--result-file`，避免 PowerShell 输出重定向的编码问题；密码只在交互提示输入。`--single-transaction` 不替代整套跨库停写，也不覆盖任意非事务表/并发 DDL 的一致性保证。

上述数据复制完成后，还必须保存以下材料才算完整备份：

- **实际应用工作树**：app、data、scripts、tests、docs、reports、dev-notes、Makefile、pyproject.toml、uv.lock、.python-version、.env.example、.gitignore、存在的 AGENTS.md 与 `.superpowers` 账本；包含未跟踪源码。
- **根目录**：未提交/未跟踪内容与根 `.git` 仓库元数据；根 `deploy/milvus/` 的两份 Compose、user.yaml、embedEtcd.yaml 以及其他现存配置；根 `.env` 受控保存。不要遗漏根目录尚未同步到工作树的独立改动。
- **当前会话与历史运行状态**：ch06 SQLite 文件集已单独保存；如果还需恢复旧服务/旧会话，还要在其连接全部关闭后保存其实际 SQLite，不把旧文件覆盖到 ch06 路径。可选保存相应日志和缓存。

把上述源码材料复制到本次备份目录后，记录源码分支/HEAD、实际未提交状态、文件清单与 SHA256，并核对 SQL/volumes/SQLite/metadata/源码/私有配置齐全。工作树 `.git` 是链接文件，不能靠它替代根仓库元数据；换位置恢复时还需核对 Git 工作树路径。虚拟环境可重建，不必当作唯一环境备份。

SQLite 最后连接正常关闭通常会合并 WAL 并移除侧文件；异常退出仍可能留下 WAL。WAL 是持久状态的一部分：冷备时保留存在的整套文件，不手动删除，也不在运行中逐个复制主文件与侧文件。本手册提供的是停写冷备，不是在线一致性备份。SQL、根 `.env` 和备份中的对话资料须限制访问；完成后另存独立磁盘，同 E 盘副本不能抵御整盘故障。

### 7.2 恢复顺序

恢复涉及覆盖数据，先明确恢复点、目标机器和覆盖范围，保全当前现场，再按同一备份包逐项执行；它不是每日启动步骤。

1. 停全部应用/任务及 SQLite 连接；停止 Milvus/etcd。不要混用不同时间的 SQL、volumes 或 WAL。
2. 恢复匹配的实际应用源码、根配置和基础设施配置，核对路径、锁定依赖和 Git 工作树元数据；不要仅恢复 HEAD 而丢掉未提交内容。
3. 由有权限的操作者确认目标数据库后导入 SQL；恢复整套已停止的 Milvus/外部 etcd 数据。原数据先整体保全，不递归删除未经核实的路径。本次文档更新未授权或执行建表/覆盖库。
4. 在无连接时整体恢复对应 SQLite 及其现有侧文件，旧主文件/侧文件先一并保全，不将旧 WAL 与另一份主文件混搭。恢复 metadata 记录的迁移清单位置与选定 profile；自定义 profile 需重新设置环境变量。
5. 按第 1–3 节启动并校验双写一致性、严格 profile 身份、同一 session_id 的待选/工单状态，再开放使用。换电脑若身份或依赖变化，先查原因，不能强改 hash。

MySQL 工单写入与 SQLite 状态保存不是跨库原子事务。恢复/强制终止后的 creating 或 uncertain 应核实实际工单，不能保证 exactly-once，也不能删除状态后盲目重试。本手册不提供清空现有数据的一键恢复脚本。

## 8. 常见故障的处理顺序

- **8016 占用**：按 6.2 查询实际 PID/入口，不重复启动或结束全部 Python；8015 的旧响应不能当 ch06 成功。
- **MySQL 登录失败**：核对 MySQL80、账号权限、本地 DB_URL 编码；端口能连不等于能访问表，不打印完整连接串。
- **Docker/Milvus 初始化失败**：先确认 Desktop 引擎，再查两份 Compose、外部 etcd 健康和已有数据目录；保留日志，不清卷或切回旧嵌入模式。
- **BM25 能用而 dense/hybrid 失败**：先查嵌入模型网络/密钥/余额；只有 hybrid_rerank 失败再查重排。401/403 查鉴权，402 查余额，429 查限流，timeout 查网络/代理，不能一律归为迁移错误。
- **ONLINE 但聊天失败**：`/health` 不覆盖真实依赖；按 MySQL→Milvus/profile→模型服务排查。修改本地配置后正常重启应用，刷新页面不能清掉进程缓存。
- **profile 不匹配/缺失**：先看是否语料、模型、Prompt、generic 或 ch06 代码变更；恢复正确文件或按第 5 节新校准。不把阈值单独拷进环境绕过校验。
- **缺订单号停住**：应有聊天卡片和 pending interrupt；点选后同会话恢复。选择 409 会刷新公开 state；检查实际 sessionStorage/session_id 和 SQLite，不发第二条退款问题假装续跑。
- **退款问题安全拒答**：查证据闸与重排分数；短指代和部分口语存在误拒边界。扩写回退会保留原问，不为演示正答直接跳过政策检索或降阈值。
- **申请 creating/uncertain 或提交报错**：保存发生时间与会话/action 关联，核对 MySQL 工单及 SQLite；不自动重试或删状态重建。
- **SQLite locked/打不开/磁盘满**：查第二进程、调试连接、绝对路径、权限及磁盘；不改 `:memory:`、不删 WAL/SHM、不加多 worker。
- **作业显示 idle 但似乎仍运行**：核对旧进程/手工终端，重启不代表取消。网页互斥不覆盖其他进程。
- **预算兜底**：核对节点与工具循环；当前默认单轮 6 次工具、10 次决策、12,000 tokens、90 秒，不无限加预算掩盖问题。

## 9. 当前边界与资料

系统仍为本机演示，无鉴权的 `/admin`、`/kb`、`/api/*` 不直接当公网服务。SQLite 文件及进程内会话锁按单应用进程运行，不支持多实例共享。退款只建立待审核演示申请；不训练分类模型、不新增跨会话记忆。本次文档维护不提交、推送、合并或部署。

开发事实与完整结果见[当前交接](CH06_CURRENT_PROGRESS.md)、[开发留痕](../dev-notes/ch06.md)。实际应用的[交付报告](</E:/MewHelp AI/.worktrees/ch05-workflow-agent/reports/ch06/summary.md>)和[演示步骤](</E:/MewHelp AI/.worktrees/ch05-workflow-agent/reports/ch06/demo.md>)有质量限制与验证证据；运行报告只保留在产品工作树，没有在根目录复制另一份。

本手册按现有源码/锁文件核对。外部接口依据已通过 Context7 获取的官方说明：[Compose 多文件合并](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)、[Compose up](https://docs.docker.com/reference/cli/docker/compose/up/)、[Compose stop](https://docs.docker.com/reference/cli/docker/compose/stop/)、[MySQL 8 mysqldump](https://dev.mysql.com/doc/refman/8.0/en/mysqldump.html)、[SQLite WAL](https://www.sqlite.org/wal.html)。启动/停止参数还应与实际安装的 CLI 帮助核对，不因文档更新升级基础设施。
