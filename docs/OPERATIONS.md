# MewHelp AI 整体启动、停止与运行维护手册

适用状态：ch05已完成后的整个项目，Windows本机。更新日期：2026-10-23。本文只提供一套当前运行流程，涵盖MySQL、Docker/etcd/Milvus、FastAPI、静态前端、LangGraph/SQLite及后台作业；不需要再拼接各章命令。

当前代码尚未合回根目录，因此基础设施和应用目录不同，这是本机现状，不是两套系统。不要移动工作树来省略路径，也不要启动根目录旧应用。本文档改写未执行启停、备份、恢复或数据写入。

## 1. 每个终端先准备这些路径与配置

普通操作使用PowerShell；启动/停止Windows服务另开管理员PowerShell。每个新操作终端先执行本节。

```powershell
$projectRoot = 'E:\MewHelp AI'
$projectApp = Join-Path $projectRoot '.worktrees/ch05-workflow-agent'
$projectPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
$projectDocker = 'C:/Users/20968/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
$projectCompose = @('-f', (Join-Path $projectRoot 'deploy/milvus/docker-compose.yml'), '-f', (Join-Path $projectRoot 'deploy/milvus/docker-compose.external-etcd.yml'))
$projectState = Join-Path $projectApp 'log/ch05'
$projectManifest = Join-Path $projectRoot 'log/kb/ch04-v2-final-migration.json'
$projectProfile = if ($env:CH05_CALIBRATION_PROFILE) { $env:CH05_CALIBRATION_PROFILE } else { Join-Path $projectState 'calibration-20261023-final.json' }
foreach ($requiredPath in @($projectApp,$projectPython,$projectDocker,$projectManifest,$projectProfile)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) { throw "缺少必要路径：$requiredPath，请先核对，不创建空库或重建知识。" }
}
Set-Location -LiteralPath $projectApp

# 使后续知识检查/维护命令使用与应用一致的现有知识库。
$env:KB_CORPUS_VERSION = 'ch04'
$env:KNOWLEDGE_COLLECTION = 'knowledge'
$env:HYBRID_COLLECTION = 'knowledge_hybrid_ch04_v2_final'
$env:KNOWLEDGE_STRATEGY = 'hybrid_rerank'
$env:KB_MIGRATION_MANIFEST = $projectManifest
$env:CH05_CALIBRATION_PROFILE = $projectProfile
$projectCalibration = Get-Content -LiteralPath $projectProfile -Raw | ConvertFrom-Json
$env:RERANK_MIN_SCORE = [string]$projectCalibration.threshold

# 只定义函数，不立即执行；加载根.env到Python进程，不打印凭据。
function Invoke-MewHelpKb {
    param([Parameter(Mandatory=$true)][string]$Command)
    & $projectPython -c "from dotenv import load_dotenv; load_dotenv('E:/MewHelp AI/.env'); from app.kb.ingest import main; raise SystemExit(main())" $Command
    if ($LASTEXITCODE -ne 0) { throw "知识命令失败：$Command，请查看错误后处理。" }
}
```

本机现有`.venv`已可运行，日常不必激活、不必重新安装。不要在工作树随手执行`uv sync`或升级依赖。Python为3.12，依赖版本以工作树的`pyproject.toml/uv.lock`为准；换电脑时另行按锁文件准备环境和配置。真实凭据仍在根`.env`，不要覆盖或输出。

整个项目只需以下常驻组件：MySQL80（3306）、Docker Desktop里的etcd和Milvus（19530/9091）、一个FastAPI进程（8015）。SQLite由应用直接读写本地文件，不是额外服务；前端也是FastAPI提供，不需要Node前端服务、Redis、Celery、Langfuse或额外worker。DeepSeek/SiliconFlow等模型通过远程API调用，需要网络、有效密钥和供应商额度。

## 2. 从全部关闭到可以使用

### 先启动MySQL

管理员PowerShell中执行；已是Running则跳过Start-Service：

```powershell
Get-Service -Name MySQL80
Start-Service -Name MySQL80
Get-Service -Name MySQL80
```

普通终端验证登录（密码按提示输入，不写在命令里）：

```powershell
& 'E:/MySQL/bin/mysql.exe' -h 127.0.0.1 -P 3306 -u root -p melhelp -e 'SELECT 1 AS ok;'
if ($LASTEXITCODE -ne 0) { throw 'MySQL登录失败，先修复配置或权限。' }
```

### 再启动Docker和检索服务

从开始菜单打开Docker Desktop，等引擎就绪。在已执行第1节的普通终端：

```powershell
& $projectDocker info
if ($LASTEXITCODE -ne 0) { throw 'Docker引擎未就绪。' }
& $projectDocker compose @projectCompose up -d --wait --wait-timeout 120
if ($LASTEXITCODE -ne 0) { throw '容器未按时就绪，先看日志。' }
& $projectDocker compose @projectCompose ps
Invoke-RestMethod 'http://127.0.0.1:9091/healthz' -TimeoutSec 5
```

Compose始终使用根目录这两份配置，不从worktree另起一套数据。etcd健康后才启动Milvus。`deploy/milvus/volumes/etcd-external`不存在时应恢复匹配备份，不能建空目录绕过保护；不切回单Compose旧启动方式。

### 检查已有知识与校准文件

```powershell
Invoke-MewHelpKb status
Invoke-MewHelpKb verify-hybrid
& $projectPython -c "from dotenv import load_dotenv; load_dotenv('E:/MewHelp AI/.env'); from pathlib import Path; import sys; from scripts.eval_retrieval import _identity, load_profile; load_profile(Path(sys.argv[1]), _identity()); print('profile OK')" $projectProfile
if ($LASTEXITCODE -ne 0) { throw '校准profile不匹配，先排查，不启动为已校准服务。' }
```

这些是已有知识的检查，不是建库。当前冻结阈值0.25从profile读取；124当前块/181总块是此前快照，合法知识变更后数量可不同，不删数据凑数。材料、模型、Prompt或代码改变可能使profile失效，不能删JSON或强改hash跳过校验。

### 最后启动应用，前端一起就绪

```powershell
Set-Location -LiteralPath $projectApp
& $projectPython scripts/demo_ch05.py
```

这个终端保持打开以查看日志。启动器在Python进程中加载根`.env`，显式启用工作流、既有最终知识集合/profile，并将检查点路径设为工作树内绝对路径；不改写`.env`。启动器读取阈值不等于上一步完整指纹核查。

统一访问：聊天 <http://127.0.0.1:8015/>，后台 <http://127.0.0.1:8015/admin>，知识库 <http://127.0.0.1:8015/kb>，API说明 <http://127.0.0.1:8015/docs>。只运行一个应用进程，不加`--reload`或多worker；8010/8000旧端口不再作为本文操作入口。

## 3. 启动后的检查与日常使用

在另一个已准备好的终端执行：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8015/health' -TimeoutSec 5
Invoke-RestMethod 'http://127.0.0.1:8015/api/kb/overview' -TimeoutSec 5
Invoke-RestMethod 'http://127.0.0.1:8015/api/jobs' -TimeoutSec 5
```

`/health`只验证应用响应，不证明MySQL/Milvus/模型全部可用。还应看`/kb`一致性并试一条问答，例如退货流程或订单1001物流。聊天会保存消息并消耗模型额度，未知问题可能写低置信度池。

投诉会显示两个独立按钮；不点可继续聊天。转人工仅前端模拟接待，不创建工单；建工单必须另点并确认，**确认会真实写本地tickets**。满意度目前只在前端保存。不要为了健康检查随意确认建票。

网页作业是固定Make子进程，通常无需额外常驻任务。后台与手工CLI不要同时修改知识；应用内作业互斥不覆盖其他进程。找不到Make时核对`C:/Program Files (x86)/GnuWin32/bin/make.exe`及本地MAKE_EXE配置。

## 4. 运行中的维护

### 日志、空间与服务状态

```powershell
& $projectDocker compose @projectCompose ps -a
& $projectDocker compose @projectCompose logs --tail 100 standalone
& $projectDocker compose @projectCompose logs --tail 100 etcd
Get-ChildItem -LiteralPath $projectState -File | Select-Object Name,Length,LastWriteTime
Get-PSDrive -PSProvider FileSystem
```

应用异常看启动终端；工作流节点/路由/工具与token日志在工作树`log/ch05/nodes.log`，网页作业日志在该工作树`log/acceptance/`。定期检查磁盘与供应商额度；日志/SQLite可能含业务对话，不公开上传。不要用清Docker卷、删除WAL或数据库文件腾空间。

### 知识维护

先运行`Invoke-MewHelpKb status`、`Invoke-MewHelpKb verify-hybrid`。有明确pending向量化需求时，先备份并暂停其他知识写入，再执行：

```powershell
Invoke-MewHelpKb vectorize
Invoke-MewHelpKb verify-hybrid
```

向量化会写Milvus并产生嵌入费用，不是每日必跑项。失败保留pending续查，不手工改done。修改冻结文档、原文/元数据、检索模型或Prompt，需要核对语料指纹和校准结果；正式更新先制定新的材料/清单/评估方案。日常启动不运行build、rebuild、migrate或reset，不自动挖掘历史对话。

### 评估和代码验证

固定样例评估按需运行，输出使用新文件名以保留旧结果：

```powershell
& $projectPython scripts/demo_ch05.py eval --suite intents --output reports/ch05/intents-maintenance.json
& $projectPython scripts/demo_ch05.py eval --suite agent --output reports/ch05/agent-maintenance.json
```

它们会调用模型、产生费用；不要同时跑多份同目标任务。业务代码变更后运行相关测试，必要时完整回归（真实依赖需要已就绪）：

```powershell
& $projectPython -c "from dotenv import load_dotenv; load_dotenv('E:/MewHelp AI/.env'); import pytest; raise SystemExit(pytest.main(['-q']))"
```

ch05交付时630 passed、分类28/28、Agent8/8是历史验证快照，不代替本次结果。本轮文档改写未重跑模型评估或全量测试。

### SQLite状态保存

实际位置：`E:\MewHelp AI\.worktrees\ch05-workflow-agent\log\ch05\checkpoints.sqlite`。这是本机E盘文件，保存LangGraph消息、节点状态和可选操作状态；业务归档/工单仍在MySQL，知识向量仍在Milvus。

运行时可能同时存在`checkpoints.sqlite-wal`、`checkpoints.sqlite-shm`。WAL可能含已提交但尚未合并回主文件的内容，不能单独删除或漏备。Git忽略这些文件，不等于不需要备份。当前没有自动清理期限/压缩入口，不手工删内部表。

相同文件和相同后端session_id可继续会话；当前前端刷新/新会话会生成新ID，不能自动重绘历史。中断的工具操作不会在下一轮自动重放。工单creating/uncertain要核实SQLite/MySQL实际结果，不反复点击或删状态后再试。

## 5. 应用重启与整个项目停止

### 仅重启应用

1. 停新请求，等当前回答、确认工单请求和作业结束。看`/api/jobs`，并检查手工评估/CLI终端；内存状态idle不证明旧子进程已退出。
2. 在应用终端按一次Ctrl+C，等待关闭日志和进程退出，SQLite连接随lifespan关闭。
3. 用下列命令确认8015已不再监听；无结果即该端口已停止。端口仍在时先核实实际PID，不结束全部Python。

```powershell
Get-NetTCPConnection -LocalPort 8015 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

4. 保持MySQL与容器运行，在已准备好的终端再次执行`& $projectPython scripts/demo_ch05.py`。不要删除检查点；不要把旧PID当永久值。

### 找不到原启动终端：停止先前测试留下的8015服务

如果服务由先前测试或后台终端启动，当前终端按Ctrl+C不会影响那个进程。无需重新启动一份服务，先停止新请求，等聊天、工单确认和作业结束，再按下面步骤处理。本文没有替你执行结束进程。

1. 新开PowerShell，查询**当前**监听8015的PID，无输出表示已经停止：

```powershell
Get-NetTCPConnection -LocalPort 8015 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

2. 输入上一步实际看到的PID，检查可执行文件及启动命令。不要用历史文档中的PID，也不要给PowerShell自带的`$PID`变量赋值：

```powershell
$serverProcessId = [int](Read-Host '输入8015的OwningProcess编号')
Get-CimInstance Win32_Process -Filter "ProcessId = $serverProcessId" |
    Select-Object ProcessId,Name,ExecutablePath,CommandLine
```

应能核对为本项目共享venv的Python，命令为`demo_ch05.py`或已确认的本项目Uvicorn入口；若命令使用相对路径，应结合原测试记录/启动方式核对，单看python.exe名字不够。不确定就不要结束。

3. 确认没有正在处理的请求，且该PID仍在监听8015，才执行指定进程的停止命令；它会再次要求确认：

```powershell
$serverListeners = @(Get-NetTCPConnection -LocalPort 8015 -State Listen -ErrorAction SilentlyContinue)
if ($serverProcessId -notin $serverListeners.OwningProcess) {
    throw '该PID已不再监听8015，请重新查询，不结束其他进程。'
}
Stop-Process -Id $serverProcessId -Confirm
```

这是无法回到原终端时的**强制终止兜底**，不等同于Ctrl+C的正常关闭，不能保证执行应用shutdown/lifespan清理。不要用`Stop-Process -Name python`或结束所有Python。若提示拒绝访问，使用管理员PowerShell从查询PID开始重做，不直接照抄旧编号。

4. 再查询端口，确认无监听后才重启：

```powershell
Get-NetTCPConnection -LocalPort 8015 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

若仍有结果，核对是否自动重启或另一个进程占用，不能反复盲杀。MySQL和Docker容器不需要随应用一起停止。强制终止后保留SQLite主文件与wal/shm，让SQLite下次打开时处理恢复；不要删WAL。若终止时有工单确认在执行，先核实tickets与操作状态，不因页面报错就再次建票；后台子任务也要单独核对。重启仍按本文第1–2节使用同一检查点文件和选定profile。

### 全部停止或准备关机

先完成上述应用与所有手工任务的关闭，再执行：

```powershell
& $projectDocker compose @projectCompose stop -t 120 standalone
if ($LASTEXITCODE -ne 0) { throw 'Milvus未正常停止，先检查，不继续停止etcd。' }
& $projectDocker compose @projectCompose stop -t 120 etcd
if ($LASTEXITCODE -ne 0) { throw 'etcd停止失败，先检查。' }
& $projectDocker compose @projectCompose ps -a
```

只有确认MySQL无其他使用方、确实需要关闭时，管理员PowerShell执行：

```powershell
Stop-Service -Name MySQL80
Get-Service -Name MySQL80
```

最后可退出Docker Desktop。日常使用stop保留容器/数据，不用down -v、system prune、删volumes/WAL或清库来关闭项目。下次从第2节按顺序启动即可。

## 6. 整个项目的备份与恢复

完整恢复点需要**同一次停写窗口**中的MySQL、Milvus与外部etcd、SQLite、源码/配置、迁移清单和profile。Git只存代码，且当前工作树未提交，仅记录HEAD不够。备份完成后另存独立磁盘/受控存储；同E盘副本不能抵御整盘故障。

### 冷备操作顺序

先按第5节停应用、作业、评估与所有SQLite连接，禁止其他工具写数据；此时先保持MySQL和容器运行。确认目标后手工执行，本文编写时未执行这些命令：

```powershell
$projectBackup = Join-Path 'E:\MewHelp-Backups' (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Path $projectBackup -ErrorAction Stop | Out-Null
$projectDump = Join-Path $projectBackup 'melhelp.sql'
& 'E:/MySQL/bin/mysqldump.exe' -h 127.0.0.1 -P 3306 -u root -p --single-transaction --routines --events --triggers --hex-blob --no-tablespaces --set-gtid-purged=OFF "--result-file=$projectDump" melhelp
if ($LASTEXITCODE -ne 0) { throw 'MySQL导出失败，不能标记完整备份。' }

& $projectDocker compose @projectCompose stop -t 120 standalone
if ($LASTEXITCODE -ne 0) { throw 'Milvus未停止，不能冷拷贝。' }
& $projectDocker compose @projectCompose stop -t 120 etcd
if ($LASTEXITCODE -ne 0) { throw 'etcd未停止，不能冷拷贝。' }
Copy-Item -LiteralPath (Join-Path $projectRoot 'deploy/milvus/volumes') -Destination (Join-Path $projectBackup 'volumes') -Recurse -ErrorAction Stop
Copy-Item -LiteralPath $projectManifest,$projectProfile -Destination $projectBackup -ErrorAction Stop

$projectSqliteBackup = Join-Path $projectBackup 'sqlite'
New-Item -ItemType Directory -Path $projectSqliteBackup -ErrorAction Stop | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $projectState 'checkpoints.sqlite'))) {
    throw '检查点缺失，核对路径，不能创建空文件冒充备份。'
}
foreach ($stateName in @('checkpoints.sqlite','checkpoints.sqlite-wal','checkpoints.sqlite-shm')) {
    $stateFile = Join-Path $projectState $stateName
    if (Test-Path -LiteralPath $stateFile) {
        Copy-Item -LiteralPath $stateFile -Destination $projectSqliteBackup -ErrorAction Stop
    }
}
```

随后保存根目录`deploy/milvus/`配置文件，以及**实际应用工作树**的app、data、scripts、tests、docs、reports、dev-notes、Makefile、pyproject.toml、uv.lock、.python-version和`.superpowers`账本；包含未跟踪源码。工作树的`.git`只是指向根仓库的链接文件，需另保存根Git仓库元数据或完整版本记录，不能以仅拷worktree就保住Git历史。无需拷虚拟环境。

根`.env`和数据库备份应加密或访问受限，不能进入公开压缩包。记录备份时间、源码快照、集合/配置版本并保存文件SHA256；确认SQL、完整volumes、SQLite文件集和源码全部齐全后才标记完整备份。最后一个SQLite连接正常关闭通常会合并WAL并清理侧文件，异常退出后可能残留；**冷备期间将存在的整套文件保留，不手删侧文件，也不在运行中逐文件复制**。本流程不是在线一致性备份。

### 恢复操作顺序

1. 明确恢复点和覆盖范围，停全部应用/任务，保全当前事故现场；恢复不是每日启动步骤。
2. 使用同一备份包的SQL、整个volumes、SQLite文件集、源码/配置及清单/profile，不混用不同时刻数据。
3. 管理员确认目标数据库后恢复SQL；替换已停止的Milvus/etcd数据与配置。原目录先保全，不对未经核实路径递归删除。
4. 在无SQLite连接时恢复其文件集，目标旧主文件与wal/shm整体先另存；不能混搭旧侧文件，不向运行中的数据库覆盖文件。
5. 恢复匹配源码、根配置与路径，按第1–3节启动并验证知识一致性、相同session_id历史和工单状态，再开放使用。

SQL导入、数据目录覆盖和清理均有实际破坏性，本手册不提供一键恢复脚本。换电脑时重新核对磁盘、服务名、共享venv和启动器寻找根配置的布局；不只拷一个启动脚本。MySQL/SQLite不跨库原子，创建结果不明须核实，不能宣称exactly-once。

## 7. 常见故障处理

- 8015占用：先查询实际PID及所属程序；不要同时启动第二个实例或连旧端口误判。缺工作流按钮时确认从实际应用目录启动且SSE以done结束；错误轮按钮禁用是保护。
- MySQL登录失败：核对本地账号、DB_URL编码和服务；3306可达不等于有权限。不打印连接串。
- Docker不可达/Milvus退出：确认Desktop就绪、两份Compose、etcd健康和已有etcd-external目录；保留日志，不删WAL、不反复切回旧嵌入模式。
- ONLINE但问答失败：依次核对MySQL、Milvus/profile、模型网络/密钥/供应商额度；health不能代替端到端检查。
- SQLite locked：查第二个进程或调试连接；当前仅单进程锁，不通过删除wal/shm或多worker规避。
- SQLite打不开/磁盘满：核对绝对路径、目录权限和空间；不改`:memory:`绕过持久化，不自动清检查点。
- 预算兜底：查节点与工具循环，默认6次工具、10次决策、12,000 tokens、90秒；不无限放宽额度掩盖问题。
- 知识/profile不一致：核对源字节、清单、实际数据和配置变更，必要时在单独批准的维护方案中重新校准；不改hash或删库凑数。
- 工单creating/uncertain：保留关联与发生时间，核对MySQL和SQLite状态；不自动重试或删除状态再建票。
- 作业仍在运行：查看实际子进程与手工终端，应用重启不会证明任务已取消。


### 检索、录入和初始化失败的处理顺序（2026-10-23实测）

**BM25成功，dense/hybrid/hybrid_rerank失败**：BM25不调用嵌入模型，其余三条路径需要嵌入；先看应用提示/终端的embedding错误，核对根.env的EMBEDDING_API_KEY、EMBEDDING_BASE_URL、EMBEDDING_MODEL与供应商余额、限流、网络/代理。401/403核对鉴权，402核对额度，429稍后重试，timeout/transport核对网络。不要将所有错误都归因于Milvus迁移，更不要reset。仅hybrid_rerank失败时再查重排模型/密钥/服务。修改根.env后须正常重启应用（配置与客户端在进程中缓存），仅刷新页面不会生效；不要输出密钥或连接串。

**录入返回502且显示已入库pending**：不是MySQL完全失败。先查status/verify-hybrid，可能是MySQL成功而向量化失败，库存一致性闸显示mismatch属于正确保护。先修复上面的依赖，再运行Invoke-MewHelpKb vectorize和verify-hybrid；只补pending，不重复粘贴录入、不手改done、不重建所有向量。若仍失败，保留pending和日志继续排查。当前实际库125有效块、pending0、双写一致是2026-10-23快照，不是固定库存目标。

**知识客服报错、订单查询正常**：知识路径多了改写/嵌入/重排，按上述依赖逐层检查。修复后先四策略自测，再问政策题确认citations与done。错误轮不开放按钮、不会写工单是正常保护。/health只代表应用活着，不能证明远程服务可用。HTTP诊断脚本访问127.0.0.1时也可能被系统代理接管，须只对本机请求排除代理；不要因此禁用所有远程API代理。

**profile不符**：load_profile会列差异字段。仅runtime_corpus_hash变化通常表示真实知识新增/修改；code_hash表示相关检索/评估代码或源字节变化；model_parameters_hash/prompt_hash需核对配置/Prompt。2026-10-23定位时根/worktree代码字节相同，仅runtime_corpus_hash变化：知识从124有效块增至125，原冻结profile正确失效。此次修复修改相关代码后code_hash也改变。不要强改hash、删新增知识、关闭校验或覆盖旧profile来凑通过。

本次已用原24条calibration标注生成独立文件，严格identity校验通过，阈值0.25；旧冻结文件不改动。使用本机修复版本时，**在执行本文第1节前**先选择它：

```powershell
$env:CH05_CALIBRATION_PROFILE = 'E:\MewHelp AI\.worktrees\ch05-workflow-agent\log\ch05\calibration-20261023-final.json'
```

第1节默认已选择本次修复文件；上面的变量可用于独立启动终端。随后从第1节重新准备路径和配置，profile核对命令会检查选中的文件。应用启动器也使用同一环境变量；新终端需重新设置。新文件位于Git忽略的log中，须与数据/源码一并备份。此结果仅是calibration，不冒称已重跑72条test部署评估。以后知识再变更，该文件也会失效，不能永久沿用。

后续需要重新校准时，先停知识写入、保证双写一致并完成第1节准备；用新的输出名，不覆盖既有报告/标签：

```powershell
$calibrationStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$newProfile = Join-Path $projectState ("calibration-$calibrationStamp.json")
$newReport = "reports/ch05/calibration-$calibrationStamp"
& $projectPython -c "from scripts.demo_ch05 import configure; configure(); from scripts.eval_retrieval import main; raise SystemExit(main())" --split calibration --calibrate --profile $newProfile --output-dir $newReport
if ($LASTEXITCODE -ne 0) { throw '校准失败，保留报告并排查，不启用未完成的新文件。' }
# 成功后明确选择新文件，再从第1节重新准备、核对并重启应用。
$env:CH05_CALIBRATION_PROFILE = $newProfile
```

真实重校准会调用模型并计费；报告若有服务/评审失败，不宣称校准完成。正式质量结论还需要独立test部署评估与人工抽查。新profile不是数据库修复工具，也不代替双写一致性检查。

## 8. 当前边界与资料

应用仅本地演示、没有鉴权；SQLite文件和进程内会话锁不支持多实例共享，不放网络盘供多个应用共用。前置证据闸后流式输出，不再具有全文生成后硬校验发布保证。日常维护不自动提交/推送/合并/部署，不改变数据库表或基础设施。

2026-10-22此前只读快照为MySQL Running、etcd/Milvus healthy、8015可用；以后以当前检查为准。ch05功能与验证报告在应用工作树reports/ch05/summary.md，演示在reports/ch05/demo.md，过程在dev-notes/ch05.md，恢复入口为docs/CH05_CURRENT_PROGRESS.md。旧版手册已另存历史，不作为另一套启动方案。

命令与接口依据现有源码/锁文件及已通过Context7核对的[LangGraph持久化](https://docs.langchain.com/oss/python/langgraph/persistence)、[SQLite WAL](https://www.sqlite.org/wal.html)、[Uvicorn生命周期](https://uvicorn.dev/concepts/lifespan)、[Docker多配置文件](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)等官方说明。
