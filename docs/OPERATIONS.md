# MewHelp AI 日常启动、停止与维护手册

适用版本：**ch09，2026-10-28修订**。唯一产品目录 `E:/MewHelp AI/.worktrees/ch05-workflow-agent`；根目录仅提供共享 `.venv`、私有 `.env`、基础设施和文档阅读副本。应用统一使用 **demo_ch09.py、8023端口、log/ch09/checkpoints.sqlite和data/local/ch09/active.json**。

正文全部按当前入口执行。旧全文移至[历史归档](history/OPERATIONS-before-profile-fix-2026-10-28.md)，不再混入日常步骤。项目是本机演示，后台和API无鉴权，不作为公网部署。

## 1. 每个新终端的准备区

沿用已安装Python 3.12及锁定依赖，不隐式sync或升级依赖。密钥只在根私有.env，不在命令、前端或Git里填写。

```powershell
$projectRoot = 'E:/MewHelp AI'
$projectApp = Join-Path $projectRoot '.worktrees/ch05-workflow-agent'
$projectPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
$projectDocker = 'C:/Users/20968/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
$projectMysql = 'E:/MySQL/bin/mysql.exe'
$projectMysqlDump = 'E:/MySQL/bin/mysqldump.exe'
$projectComposeBase = Join-Path $projectRoot 'deploy/milvus/docker-compose.yml'
$projectComposeEtcd = Join-Path $projectRoot 'deploy/milvus/docker-compose.external-etcd.yml'
$projectCompose = @('-f', $projectComposeBase, '-f', $projectComposeEtcd)
$projectManifest = Join-Path $projectRoot 'log/kb/ch04-v2-final-migration.json'
$projectState = Join-Path $projectApp 'log/ch09'
$projectVersions = Join-Path $projectApp 'data/local/ch09'
$projectOrigin = 'http://127.0.0.1:8023'
foreach ($requiredPath in @($projectApp, $projectPython, $projectComposeBase,
    $projectComposeEtcd, $projectManifest, (Join-Path $projectRoot '.env'))) {
    if (-not (Test-Path -LiteralPath $requiredPath)) { throw "缺少必要路径：$requiredPath。先核对或恢复。" }
}
Set-Location -LiteralPath $projectApp
$env:PYTHONIOENCODING = 'utf-8'
$env:LANGFUSE_ENABLED = 'true'
# 受控启动不自动补跑评估；日常需每日02:00评估时按5.3改true。
$env:CH09_EVAL_ENABLED = 'false'

function Invoke-MewHelpKb {
    param([Parameter(Mandatory=$true)]
          [ValidateSet('status', 'verify-hybrid', 'preview')][string]$Command)
    & $projectPython -c "from scripts.demo_ch09 import configure; configure(require_active=False); from app.kb.ingest import main; import sys; raise SystemExit(main(sys.argv[1:]))" $Command
    if ($LASTEXITCODE -ne 0) { throw "只读知识检查失败：$Command。先处理实际错误。" }
}

function Test-MewHelpProfile {
    $profileCheckCode = @'
from scripts.demo_ch09 import configure
try:
    configure()
    from app.flywheel.publishing import version_store, load_active
    from app.evaluation.release import validate_release, require_no_unfinished_release
    versions = version_store()
    require_no_unfinished_release(versions)
    validate_release(versions, load_active(versions))
except Exception as exc:
    known = {
        'unfinished publication must be resumed before calibration',
        'retrieval configuration changed; recalibrate required',
        'retrieval code changed; recalibrate required',
        'corpus drift; recalibrate required',
        'corpus/vector consistency check failed',
        'profile hash mismatch', 'profile labels mismatch', 'active identity changed',
    }
    message = str(exc)
    print('ch09 profile FAIL: ' + (message if message in known else type(exc).__name__))
    raise SystemExit(1) from None
print('ch09 profile identity OK')
'@
    & $projectPython -c $profileCheckCode
    if ($LASTEXITCODE -ne 0) { throw 'ch09 profile验证失败，按第5节处理，不启动旧章入口。' }
}
```

区分两种profile：旧章节 `default/demo` 是上下文预算预设；ch09沿用default预算，当前CLI不接受 `--profile`。**检索profile由active.json引用不可变profiles文件**，不使用CH08_CALIBRATION_PROFILE，也不是单独一个重排阈值。启动器在子进程固定当前集合、工作流、检查点及版本目录，不改.env。bootstrap已完成，日常不重复初始化。

## 2. 从全部关闭到应用可用

### 2.1 MySQL

管理员PowerShell，已运行跳过启动：

```powershell
Get-Service -Name MySQL80
if ((Get-Service -Name MySQL80).Status -ne 'Running') { Start-Service -Name MySQL80 }
```

普通终端验证既有库，密码只在交互提示输入：

```powershell
& $projectMysql -h 127.0.0.1 -P 3306 -u root -p melhelp -e 'SELECT 1 AS ok;'
if ($LASTEXITCODE -ne 0) { throw 'MySQL登录失败，核对服务与授权。' }
```

### 2.2 Docker、外部etcd与Milvus

先打开Docker Desktop等引擎就绪，始终按次序合并两份Compose：

```powershell
& $projectDocker info
if ($LASTEXITCODE -ne 0) { throw 'Docker引擎未就绪。' }
& $projectDocker compose @projectCompose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose配置校验失败。' }
& $projectDocker compose @projectCompose up -d --wait --wait-timeout 120
if ($LASTEXITCODE -ne 0) { throw '检索基础设施未就绪，先看日志。' }
& $projectDocker compose @projectCompose ps
Invoke-RestMethod 'http://127.0.0.1:9091/healthz' -TimeoutSec 5
```

根 `deploy/milvus/volumes/etcd-external` 与 `volumes/milvus` 是已有数据。缺卷找回匹配备份，不创建空卷、prune或改用单文件嵌入etcd。

### 2.3 启动MCP，核对知识、profile和预算

各开一个执行了第1节的终端；端口已监听先按6.2核对归属，已有本项目健康进程可复用。

```powershell
# 物流终端，保持运行
& $projectPython -m app.mcp_servers.logistics --host 127.0.0.1 --port 8021
```

```powershell
# 售后终端，保持运行
& $projectPython -m app.mcp_servers.aftersales --host 127.0.0.1 --port 8022
```

第三个已准备终端执行以下只读检查：

```powershell
Invoke-MewHelpKb status
Invoke-MewHelpKb verify-hybrid
& $projectPython scripts/demo_ch09.py check
if ($LASTEXITCODE -ne 0) { throw '配置/数据库检查失败，不继续启动。' }
Test-MewHelpProfile
& $projectPython -c "import asyncio,json; from scripts.demo_ch09 import configure; configure(); from scripts.demo_ch08 import budget; print(json.dumps(asyncio.run(budget()),ensure_ascii=True))"
if ($LASTEXITCODE -ne 0) { throw '工具预算检查失败，先修MCP发现/策略或预算。' }
```

`check`仅检查配置布尔、owner和active是否存在，**不证明profile身份有效**；必须看到 `ch09 profile identity OK`。严格检查覆盖当前代码、检索配置、MySQL/Milvus语料、标注及profile摘要，并拦截未完成发布。预算命令只复用内部budget函数，配置已经走ch09，未调用旧profile校验。原工具目录为7个且diagnostics为空；合法配置变化以实际清单为准。

### 2.4 启动当前应用

```powershell
$chapterListeners = @(Get-NetTCPConnection -LocalPort 8023 -State Listen -ErrorAction SilentlyContinue)
if ($chapterListeners.Count) { throw '8023已占用，按6.2核对进程，不重复启动。' }
& $projectPython scripts/demo_ch09.py serve --port 8023
```

保持终端打开，等待Application startup complete。打开 `http://127.0.0.1:8023/` 聊天，`/review`审核，`/quality`看趋势/成本，`/admin`总览，`/kb`查看知识，`/docs`看接口。单进程运行，不加reload、多worker或另起旧章节应用。

**正式serve自动消费全部旧未归并问题**，会调用模型并更新队列，不局限于验收样本。评估开关只控制定时评估，不关闭消费者；启动前确认接受这一既有行为。

## 3. 启动检查与日常使用

另一个已准备终端执行：

```powershell
Invoke-RestMethod "$projectOrigin/health" -TimeoutSec 5
Invoke-RestMethod "$projectOrigin/api/kb/overview" -TimeoutSec 15
Invoke-RestMethod "$projectOrigin/api/observability/status" -TimeoutSec 10
Invoke-RestMethod "$projectOrigin/api/review/releases" -TimeoutSec 10
```

health只是存活检查，不能替代profile验证。观测应为ready、region=jp；degraded仅说明观测故障，不应阻断问答。有未完成发布按5.2恢复，不以HTTP200掩盖知识维护状态。真实问答会调用模型、写会话，弱证据可能落池，不属于无副作用检查。

### 3.1 会话、上下文、工具与工单

固定同一origin；localhost与127.0.0.1、不同端口的存储不共享。localStorage的mewhelp-user-id归组列表，sessionStorage的mewhelp-chat-session保存当前会话。端口变化侧栏空白先核对原地址/归组键，不清库或删SQLite；旧章节检查点不会自动迁移到ch09。

default大窗口的代表性21轮未压缩不保证任意21轮都能装下。看实际budget及log/app.log的model_ctx/history_ctx与summary记录，超预算才级联。已提交摘要不回退，未提交边界可重试；不使用旧启动器demo参数切当前版本。

config/tool_policy.json是本地权限来源；目录轮间刷新、执行前复核，未知/外部写工具不自动放开。MCP重启可重置随机mock轨迹，不代表订单丢失。待确认写操作期间不要随意改策略。

订单/工单卡片用原会话interrupt恢复。确认建票才写tickets，取消不建票；退款仅为待审核演示申请，不执行资金退款。creating/uncertain时核对tickets、tool_audit_logs与checkpoint，不新建预览反复提交试探。

### 3.2 审核与知识发布

聊天👎提交稳定用户/助手消息ID，失败可重试、成功禁重复。审核详情对照原话、入口、当轮Top5全文及得分，区分真缺知识、检索遗漏或生成错误。未检索/快照不可得会说明原因，不能拿最新一轮代替旧轮。

在/review编辑核准答案→通过待发布→选择核准批→检查全96题影响→人工逐题修订并确认其余不变→填审核标识→发布。仅“已生效”表示进入当前知识库。驳回是终审，同义后续只累计次数；已通过行也参与查重。

发布顺序是MySQL pending→限定ID向量upsert/flush→done→24题校准→active原子切换。写后失败保留维护状态，沿原release_id续跑，不新建批次或删journal。模型示例答案不等于人工核准答案。

### 3.3 Langfuse完整日常观察流程

1. 完成第2节，启动后看/api/observability/status，核对enabled、keys_configured、日本区及ready，只看布尔不输出密钥。
2. 提知识问题，记该轮turn的trace_id、系统session_id和用户/助手消息ID。本地有ID不等于Cloud已摄取。
3. 登录https://jp.cloud.langfuse.com对应项目，按trace_id打开，展开意图→检索/DB/Milvus/精排→证据闸/自评→Agent/工具→生成，检查父子关系、真实Prompt、输入输出、耗时、异常与usage。
4. 同会话再问一轮，在Session核对系统ID与多轮。摘要/飞轮/评估按独立purpose查看。
5. 点旧回答👎，到审核详情核对该轮原话/Top5与消息身份，不用最新检索覆盖。
6. 在/quality按意图/用途/窗口看Token及成本，核对complete、partial、as_of、缓存。当前Cloud未映射模型价格时unknown/null不是0美元，不猜单价。
7. 三指标只连接同标注/profile/评分协议趋势，核对分母、覆盖率、误拒答和judge失败。N/A/中断/失败不当0，跨版本不直接算差值。
8. 按3.2核准发布后新会话复问，失败沿原发布恢复，不改旧hash。
9. 正常Ctrl+C等worker与SDK shutdown结束；强停后核对尾部Trace。统计缓存15分钟，分页/限流失败保留旧as_of并标stale/partial，不连续轮询Cloud。

## 4. 日志、知识与作业维护

```powershell
& $projectDocker compose @projectCompose ps -a
& $projectDocker compose @projectCompose logs --tail 100 standalone
& $projectDocker compose @projectCompose logs --tail 100 etcd
Get-ChildItem -LiteralPath $projectState -File | Select-Object Name,Length,LastWriteTime
Get-PSDrive -PSProvider FileSystem
```

应用日志log/app.log，检查点/评估诊断log/ch09，版本/人工确认/profile/journal/预算缓存data/local/ch09，交付reports/ch09。资料可含对话与工单，私有保管。日志归档先停写再复制，不删原文、摘要或WAL腾空间。

后台Make仅执行app/core/jobs.py注册的固定argv目标，shell=False。ch09 owner在线时旧建库/vectorize/rebuild/reset写入口受保护，使用审核发布流程。pending先查原发布journal，不全库补写。日常不reset、清库或删卷。/api/jobs idle不证明独立评估、摘要或手工作业已结束。

## 5. Profile失败的具体分流与恢复

### 5.1 本次故障：先排除用错入口

2026-10-28截图在旧demo_ch08.configure报code_hash、runtime_corpus_hash不匹配。已复现：旧profile与ch09新增追踪代码及两条合成知识不一致，但**当前ch09 active/profile严格验证通过**。根因是文档旧命令，不需要真实校准。

同类问题依次处理：

1. 看堆栈入口。若为旧demo_ch08或log/ch08的profile，不再沿旧步骤校准；保留原文件，重新执行第1节。
2. 执行Test-MewHelpProfile，看到ch09 profile identity OK后直接按2.4启动。旧profile不需要与当前版本强行匹配。
3. `问题ID hybrid_rerank: False`来自评估的can_answer=False，表示拒答，库外题拒答可能符合预期；不是进程失败。看紧随命令的$LASTEXITCODE、异常和汇总判断是否成功。
4. active_exists=true只说明存在，不替代严格校验。不能删active、手改hash/阈值或重跑旧calibrate绕过。

适用于章节升级/合法发布后误用旧章冻结profile。旧章复现实验应使用匹配的完整源码/语料/检查点，不混用当前共享数据硬凑旧成绩。

### 5.2 当前ch09严格检查真的失败时

保留失败类型、阶段与时间，不外贴密钥、完整连接串或客户原文，再分流：

- FileNotFoundError、profile hash mismatch、profile labels mismatch、active identity changed：查产品目录及active引用的版本/标注/profile/来源文件，恢复同一备份。缺文件或摘要坏了不能盲目校准，更不删active重新bootstrap。
- unfinished publication must be resumed before calibration或发布失败/维护中：在/review或GET /api/review/releases找到原release_id，修正该条错误后点续跑。API为POST /api/review/releases/{原release_id}/retry，会实际写入/调用模型，不用于健康检查。不删journal、不新建批次、不靠calibrate绕过。
- corpus/vector consistency check failed：先查MySQL/Milvus及双写状态，有未完成发布走上条；其他外部写入查明来源。不清库、手改done、删卷或立即校准掩盖不一致。
- retrieval configuration changed：核对嵌入/重排模型与地址、维度、策略、集合及Milvus目标。误改恢复原配置再正常重启；有意变更才校准。凭据轮换本身不改变此身份。
- retrieval code changed或corpus drift：确认是预期代码/知识变化、一致性正常、无未完成发布，保留全部旧版本后再新校准。外部未审核知识变化先核实内容/来源，不删除新增行凑hash。
- OperationalError、连接/鉴权/超时：先修服务、权限或模型网络；端口监听不等于数据库授权正常。沙盒不可达但同机普通终端可用时，不修改profile应付沙盒。

确需校准时先按第6节正常停应用、确认owner离线。在已准备终端执行以下命令（**本次故障未执行，当前不需要**）：

```powershell
& $projectPython scripts/demo_ch09.py check
if ($LASTEXITCODE -ne 0) { throw '无法核对owner，不校准。' }
# 确认输出owner_online=false；有未完成发布应先按原记录恢复。
$calibrationStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$calibrationBackup = Join-Path $projectApp "log/ch09-state-before-calibration-$calibrationStamp"
Copy-Item -LiteralPath $projectVersions -Destination $calibrationBackup -Recurse -ErrorAction Stop
& $projectPython scripts/demo_ch09.py calibrate
if ($LASTEXITCODE -ne 0) { throw '校准失败，保留旧active/profile和失败资料，不改hash。' }
Test-MewHelpProfile
```

只对当前人工确认版本的固定24题做检索/精排校准，会产生费用并创建新profile、原子切换active；72保留题不调阈值。失败不会伪报生效，不需设置CH08_CALIBRATION_PROFILE。owner_online=true时找回原终端正常停机，不强抢锁或结束不明进程。

### 5.3 评估与回归

真实72题评估会调用生成/judge并计费，一轮一条eval_runs：

```powershell
& $projectPython scripts/eval_ch09.py --trigger 手动 --no-cache
if ($LASTEXITCODE -ne 0) { throw '评估未成功，查eval_runs终态与失败原因。' }
```

需每日上海02:00评估时正常停应用，启动终端设$env:CH09_EVAL_ENABLED='true'再按2.4启动。应用需在线，启动最多补最近槽，可能立即付费；不是系统开机计划任务。受控启动维持false再手动触发。

查看/quality的Recall@K、MRR、Faithfulness及分母/失败，新标注或judge身份另起趋势。既有#12/#13各72题有效，旧#7不完整/#8中断保留，不手改成功。

回归与在线owner必须串行；正常停本章应用，check确认owner离线后运行脱敏runner，每次换唯一basetemp后缀：

```powershell
$testStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
& $projectPython scripts/test_ch09.py -q --tb=short --basetemp "log/ch09/maintenance-tests-$testStamp"
if ($LASTEXITCODE -ne 0) { throw '回归失败，保留脱敏日志。' }
```

普通沙盒异步/网络受限时在已授权本机环境执行，不放松业务锁或把超时当通过。

## 6. 正常停止、暂停与恢复

### 6.1 正常停止与重启

停止新请求，等问答、确认、发布、评估及短事务收尾。在**启动8023的原终端**Ctrl+C，等Application shutdown complete与worker/SDK结束，再确认端口释放：

```powershell
Get-NetTCPConnection -LocalPort 8023 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

无监听后check、Test-MewHelpProfile，再按2.4启动。只重启应用无需停MySQL/Milvus，不迁移SQLite、不删WAL/SHM或版本。shutdown unconfirmed需核对Cloud尾部；强杀不保证缓冲提交。

### 6.2 原终端找不到或端口占用

只查询当前PID，不能按旧PID或结束全部Python：

```powershell
Get-NetTCPConnection -LocalPort 8023 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
$serverProcessId = [int](Read-Host '输入刚查到的8023 OwningProcess编号')
Get-CimInstance Win32_Process -Filter "ProcessId = $serverProcessId" |
    Select-Object ProcessId,Name,ExecutablePath,CommandLine
```

核实为本项目.venv、正确工作树的demo_ch09.py serve，相对命令结合启动记录确认。优先找回原终端Ctrl+C。仅在归属和无处理中请求均确认且必须强停时：

```powershell
$serverListeners = @(Get-NetTCPConnection -LocalPort 8023 -State Listen -ErrorAction SilentlyContinue)
if ($serverProcessId -notin $serverListeners.OwningProcess) { throw 'PID归属已变化，重新查询。' }
Stop-Process -Id $serverProcessId -Confirm
```

强停不等于正常关闭，保留WAL和journal，核对工单creating/uncertain与尾部Trace。8021/8022查归属同理，不给PowerShell自带$PID赋值。

### 6.3 日常暂停、整套停止与恢复

短暂停先停应用，再分别Ctrl+C自己启动的MCP，MySQL/Docker可保留。准备关机时先核对所有共用MySQL/Milvus的服务和CLI均已停，包括旧8015/8016/8018/8020；不擅停他人进程。之后先Milvus再etcd：

```powershell
& $projectDocker compose @projectCompose stop -t 120 standalone
if ($LASTEXITCODE -ne 0) { throw 'Milvus未停，不继续停止etcd。' }
& $projectDocker compose @projectCompose stop -t 120 etcd
if ($LASTEXITCODE -ne 0) { throw 'etcd未停，先排查。' }
& $projectDocker compose @projectCompose ps -a
```

MySQL确无其他使用方且需停机时管理员执行Stop-Service -Name MySQL80，再退出Docker。日常不使用down -v、prune、清库或删卷。

恢复先第1节，再按第2节只启缺失依赖，严格profile通过才启动。保留原origin/浏览器归组、ch09 SQLite、整套版本/人工确认/预算/journal及私有配置。不新建空SQLite或删active“恢复”。未完成发布按原release续跑，中断评估不自动变成功。

开发因网络/额度暂停时即时更新AI_HANDOFF、对应进度、dev-notes及账本，记实际文件、验证、未决问题和服务状态。额度不足10%优先交接暂停，不自动停服务或清理未提交内容。

## 7. 成套备份与恢复

不是日常启动步骤。停全部写入方/评估/调试连接，保持同一停写窗口，不能只备份HEAD或SQLite。必须保存：

- MySQL既有知识、工单、会话/摘要/锚点、审计、low_confidence_questions、review_queue、eval_runs。
- 根deploy/milvus/volumes内完整Milvus及外部etcd、两份Compose及相关配置。
- **整个data/local/ch09**：active、profiles、eval_versions、人工确认、绑定、releases、预算和缓存；加ch09 SQLite及存在的WAL/SHM。必须与数据库配套。
- 根迁移清单、config/tool_policy.json、私有.env（受控）、实际源码/未提交/未跟踪内容、锁文件、.superpowers、根Git元数据及工作树关联。虚拟环境可按锁恢复。
- 私下记录浏览器origin/两个归组键。需旧章节会话时额外保存其真实SQLite，不覆盖ch09。

全部应用停写、Milvus仍在线时先Invoke-MewHelpKb verify-hybrid和Test-MewHelpProfile。未完成发布作为故障现场连journal保存，不冒称可用发布点。导出/冷拷示例：

```powershell
$projectBackup = Join-Path 'E:/MewHelp-Backups' (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Path $projectBackup -ErrorAction Stop | Out-Null
$projectDump = Join-Path $projectBackup 'melhelp.sql'
& $projectMysqlDump -h 127.0.0.1 -P 3306 -u root -p --single-transaction --routines --events --triggers --hex-blob --no-tablespaces --set-gtid-purged=OFF "--result-file=$projectDump" melhelp
if ($LASTEXITCODE -ne 0) { throw 'MySQL导出失败，不标记完整备份。' }
# 先按6.3停Milvus/etcd，再执行下述冷拷。
Copy-Item -LiteralPath (Join-Path $projectRoot 'deploy/milvus/volumes') -Destination (Join-Path $projectBackup 'volumes') -Recurse -ErrorAction Stop
Copy-Item -LiteralPath $projectVersions -Destination (Join-Path $projectBackup 'ch09-versions') -Recurse -ErrorAction Stop
$sqliteBackup = Join-Path $projectBackup 'sqlite-ch09'
New-Item -ItemType Directory -Path $sqliteBackup -ErrorAction Stop | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $projectState 'checkpoints.sqlite'))) { throw '检查点缺失，先核对。' }
foreach ($stateName in @('checkpoints.sqlite','checkpoints.sqlite-wal','checkpoints.sqlite-shm')) {
    $stateFile = Join-Path $projectState $stateName
    if (Test-Path -LiteralPath $stateFile) { Copy-Item -LiteralPath $stateFile -Destination $sqliteBackup -ErrorAction Stop }
}
Copy-Item -LiteralPath $projectManifest -Destination (Join-Path $projectBackup 'migration-manifest.json') -ErrorAction Stop
```

再保存其他源码/私有配置，记录路径、origin、SHA256及Git工作区，核对材料完整。mysqldump用result-file避免PowerShell转码，密码只交互输入；single-transaction仅覆盖InnoDB，不替代跨库停写，不允许同时DDL。

恢复涉及覆盖，须先确认目标范围、保全现场，再在连接关闭时整体恢复同一包：源码/配置→MySQL→已停止的Milvus/etcd卷→ch09 SQLite及侧文件→整个版本目录与清单→第1–3节严格校验。不混搭不同时间SQL/WAL/active，不手改hash；SQL由有权限操作者交互导入，不提供自动清库/递归删除。备份/恢复命令本次未执行。

## 8. 常见故障速查与资料

- profile差异先看第5节与堆栈入口，当前ch09通过不修旧profile。
- 端口占用按6.2核对，不结束全部Python，不用旧端口响应冒充当前版。
- MySQL/检索查服务、权限、两份Compose和etcd；BM25可用而dense/hybrid失败先查嵌入，仅重排失败查重排；401/403鉴权、402余额、429限流、timeout网络。
- ONLINE但知识维护：health不证明profile/发布有效，查严格校验及原journal。Langfuse故障独立排查，不降阈值绕过证据闸。
- SQLite locked/磁盘满：查第二进程、连接、绝对路径和空间，不改memory、删WAL/SHM或加多worker。
- 侧栏空白查origin/归组，缺答查MySQL/checkpoint，不删数据；工单不明按同conversation/action查tickets/审计，不重放写操作。
- 成本unknown/N/A不当0；看价格映射/窗口/用途/缓存/完整性。同语料两轮波动不代表新知识的因果收益。

资料：[演示](../reports/ch09/demo.md)、[评估](../reports/ch09/evaluation.md)、[Trace](../reports/ch09/trace-audit.md)、[浏览器](../reports/ch09/browser.md)、[过程](../dev-notes/ch09.md)。未授权不提交/推送/合并/部署/DDL或清库。

本次按当前代码入口和真实身份验证修订，不改profile、知识、依赖或真实.env。18个PowerShell块语法通过，文档原检查代码实测成功，127条在用知识一致、7工具、当前profile有效；临时关闭消费者/定时评估的应用8项HTTP200，正常退出且SDK排空确认。没有执行真实校准或备份恢复。详见[本次故障验证](../reports/ch09/profile-startup-fix.md)及dev-notes/ch09阶段31–33。

Uvicorn受控启动/关停先查Context7官方Config/Server/should_exit及本地签名；基础设施沿用此前核验的Compose/MySQL接口，不因文档更新升级基础设施。
