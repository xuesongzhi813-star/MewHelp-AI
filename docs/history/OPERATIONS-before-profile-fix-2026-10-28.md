# 历史运维归档：不可作为当前启动入口

此文件原样保存2026-10-28 profile故障修复前的维护正文，包含已失效的ch08步骤，仅供沿革查阅。当前命令见docs/OPERATIONS.md。

---

# 当前入口：ch09交付（2026-10-28复核）

产品在E:/MewHelp AI/.worktrees/ch05-workflow-agent，根目录仍为共享.venv、私有.env和基础设施。下文旧阶段快照保留，本节为当前运行入口。2026-10-28完整后端回归1111 passed in108.92s；真实Cloud/飞轮/UI和两轮72题沿用2026-10-27已完成的验收证据，本次未额外调用模型。自有验收服务已停止，当前无在线owner。

```powershell
Set-Location 'E:/MewHelp AI/.worktrees/ch05-workflow-agent'
$env:LANGFUSE_ENABLED='true'
$env:CH09_EVAL_ENABLED='false'
& 'E:/MewHelp AI/.venv/Scripts/python.exe' scripts/demo_ch09.py check
& 'E:/MewHelp AI/.venv/Scripts/python.exe' scripts/demo_ch09.py serve --port 8023
```

MySQL/Milvus及MCP依赖仍按下文既有步骤启动；本章不改基础设施。bootstrap已完成，勿删除active重复初始化。正式serve会自动消费全部旧未归并问题；本次验收仅限定自己的合成身份、原37条保留。需每日上海02:00评估时，将进程CH09_EVAL_ENABLED设为true再正常启动；默认调度会补最近槽并真实调用模型。

页面：/聊天、/review审核、/quality趋势与成本、/admin总览。手动评估命令scripts/eval_ch09.py --trigger 手动 --no-cache。两轮#12/#13有效完成，旧失败/中断保留。修改检索模型/地址/维度/集合须正常停止后demo_ch09.py calibrate，仅24题；有unfinished发布先按原release_id续跑，禁止重校准绕过。

正常停止：在本章终端Ctrl+C，等worker短事务及SDK shutdown结束、进程退出；出现shutdown unconfirmed须核对尾部Trace。勿杀其他端口/用户进程。暂停/恢复时保留MySQL、Milvus、log/ch09/checkpoints.sqlite及data/local/ch09整套同一时点备份，不删预算/标签/profile/journal。

日常Langfuse完整流程：① check与/api/observability/status确认日本区ready（不打印密钥）；②在聊天提知识问题，记turn的trace_id/系统session及消息ID；③到日本区对应项目打开Trace，按意图→检索/DB/精排→闸/自评→Agent/LLM展开，核对真实Prompt、输入输出、耗时、异常及usage；④在Session核对多轮同一系统ID；⑤旧答案👎后到审核详情对照原话/Top5，判断知识缺口或检索/生成问题；⑥按意图看Token/用途/窗口/as_of，unknown与partial不当0，强停历史残片可导致未归因；⑦按同标签/评分协议查看三指标、分母与失败，新标注版本另起趋势；⑧核准答案→96题影响逐题及其余不变确认→发布生效→同问复验，失败沿原发布续跑；⑨正常停机后检查Cloud尾部已提交。当前模型Cloud未匹配价格，金额未知，不猜价。

详细交付见[演示](../reports/ch09/demo.md)、[评估](../reports/ch09/evaluation.md)、[验收](../reports/ch09/summary.md)、[UI](../reports/ch09/browser.md)、[Trace](../reports/ch09/trace-audit.md)。2026-10-28用户已恢复额度；本次只读检查确认active存在、日本区配置就位且无在线owner，不自动启动消费者或额外评估。测试须与在线owner串行。

## 历史运维章节的使用范围

以下ch08启动器、8020端口、关闭Langfuse及ch09“尚未实现/待验收”的内容是历史记录，不能整段照抄作为当前启动命令。基础设施路径、MySQL/Docker/Milvus/MCP启动与精确识别进程的方法仍可参考；应用入口、开关、端口、checkpoint和备份范围以本页上方ch09步骤为准。保留log/ch09/checkpoints.sqlite与整个data/local/ch09，不用ch08的profile或启动器替换它们。

---

# MewHelp AI 整体启动、暂停、恢复与维护手册

适用版本：ch08 已交付功能，兼顾 ch09 部分开发的状态保全；Windows 本机，更新于 2026-10-26。本文是整个项目的统一操作入口，覆盖准备环境、启动、日常暂停、恢复、维护与整套停机。

**当前运行边界：仍使用 ch08 启动器。** 工作树含 ch09 Task1 的可选观测运行时及 Task2 的追踪辅助代码；主图、网关、续跑、checkpoint 和 SSE 尚未接通完整追踪，问题池、发布与定时评估尚未实现。日常运行保持 `LANGFUSE_ENABLED=false`，不以启动应用代替 Cloud 验收；新增能力上线后再更新操作步骤。

当前实际应用在 `E:\MewHelp AI\.worktrees\ch05-workflow-agent`。根目录提供共享 `.venv`、私有 `.env`、Docker 配置和数据；根目录的旧应用不用于启动 ch08。分支和工作树名称仍含 ch05，不代表应运行旧启动器。当前未提交修改也属于待保全的项目内容。

维护源为仓库docs/OPERATIONS.md，根目录《MelHelp AI日常运行维护手册.md》和产品工作树中的同页同步更新。ch08已进行自有服务启停、付费冻结校准和合成会话验收；未执行备份恢复、DDL或清库。下列备份恢复命令仅供需要时使用。

日常使用：执行第1节准备区；全部关闭时按第2节顺序启动，基础设施已运行时从2.3检查、2.4启动，再做第3节检查。**暂时离开、保留状态看6.4，回来恢复看6.5；暂停开发留痕看6.6。** 只重启应用看6.1，整套关机看6.3；知识/日志维护看第4节；profile失效看第5节；整套备份恢复看第7节。服务状态和PID随时会变化，不照搬交接快照。本文的命令是操作说明，本次文档更新没有执行启停、备份或恢复。

## 1. 环境、路径与唯一应用入口

常驻组件是 MySQL80（3306）、Docker Desktop 内的外部 etcd 与 Milvus（19530/9091），两个独立mock MCP进程（物流8021、售后8022），以及一个 ch08 FastAPI 进程（8020）。静态聊天页、`/kb` 和 `/admin` 都由该进程提供；SQLite 是应用读写的本地文件，不需要另起服务，也不需要前端 Node 服务或额外 worker。

历史共享8015、8016、8018可能仍在运行，它们不是ch08当前入口。不要因启动8020就结束它们；但整套基础设施关停或一致性备份前，必须核对所有服务是否仍使用同一MySQL/Milvus，以及它们各自的SQLite路径。

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
$projectState = Join-Path $projectApp 'log/ch08'
$projectContextProfile = 'default' # 日常大窗口；小窗口级联演示改为'demo'
$projectManifest = Join-Path $projectRoot 'log/kb/ch04-v2-final-migration.json'
$projectProfile = if ($env:CH08_CALIBRATION_PROFILE) {
    if ([IO.Path]::IsPathRooted($env:CH08_CALIBRATION_PROFILE)) {
        $env:CH08_CALIBRATION_PROFILE
    } else {
        Join-Path $projectApp $env:CH08_CALIBRATION_PROFILE
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

# 供手工知识检查使用，与 ch08 启动器所选的现有知识库保持一致。
$env:KB_CORPUS_VERSION = 'ch04'
$env:KNOWLEDGE_COLLECTION = 'knowledge'
$env:HYBRID_COLLECTION = 'knowledge_hybrid_ch04_v2_final'
$env:KNOWLEDGE_STRATEGY = 'hybrid_rerank'
$env:KB_MIGRATION_MANIFEST = $projectManifest
$env:CH08_CALIBRATION_PROFILE = $projectProfile
# ch09尚未完成全链验收；本终端的日常启动关闭云观测，不改真实.env。
$env:LANGFUSE_ENABLED = 'false'

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

`--profile default|demo`选择上下文预算预设，`CH08_CALIBRATION_PROFILE`选择检索校准JSON，两者用途不同。启动器会在子进程覆盖七个窗口/峰值相关环境变量（含decision容量）；切配置用这个参数，不只改MODEL_CONTEXT_WINDOW。语料版本仍是ch04，章节到ch08不代表需要迁移知识库。MySQL的conversations/messages/conversation_summaries等表由项目外维护，应用不自动建表。

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

先在两个独立终端设置第1节变量并启动MCP，已占用端口先核对所属进程，不重复启动。Server不连数据库、不建表，重启会重置进程内随机mock缓存。

```powershell
# 终端A，保持运行
& $projectPython -m app.mcp_servers.logistics --host 127.0.0.1 --port 8021
```

```powershell
# 终端B，保持运行
& $projectPython -m app.mcp_servers.aftersales --host 127.0.0.1 --port 8022
```

在第三个已准备终端继续下列检查。budget的tool_count正常为7、discovery_diagnostics为{}；少于7先核对MCP进程、地址和本地策略，不把/health当发现成功。源码未注册内置物流回退。

```powershell
Invoke-MewHelpKb status
Invoke-MewHelpKb verify-hybrid
& $projectPython -c "import sys; from scripts.demo_ch08 import configure; configure(profile=sys.argv[1]); print('ch08 profile identity OK')" $projectContextProfile
if ($LASTEXITCODE -ne 0) { throw 'profile缺失或身份不匹配，请按第5节处理后再启动。' }
& $projectPython scripts/demo_ch08.py budget --profile $projectContextProfile
if ($LASTEXITCODE -ne 0) { throw '上下文预算自检失败，先核对所有分项，不继续启动。' }
```

这些命令只检查现有数据，不是初始化建库。启动器逐项核对原有语料/模型/Prompt/代码身份与ch08检索代码身份，再读取阈值；不能只从旧文件取一个 0.25 就启动。125 有效块、阈值 0.25 是 ch08 验收快照，后续合法变更后应重新核对，不能删数据凑旧数量。

### 2.4 启动整个应用

```powershell
Set-Location -LiteralPath $projectApp
$chapterListeners = @(Get-NetTCPConnection -LocalPort 8020 -State Listen -ErrorAction SilentlyContinue)
if ($chapterListeners.Count) { throw '8020已占用，按6.2核对现有进程，不重复启动。' }
& $projectPython scripts/demo_ch08.py serve --profile $projectContextProfile --port 8020
```

保持此终端打开。启动器加载根 `.env`，显式启用工作流及既有知识集合，绑定 `log/ch08/checkpoints.sqlite` 和已选择 profile；不会改写 `.env`。只运行一个 8020 进程，不使用 `--reload` 或多 worker，不从根目录直接运行旧应用。

统一入口：[聊天](http://localhost:8020/)、[知识库](http://localhost:8020/kb)、[后台](http://localhost:8020/admin)、[API 文档](http://localhost:8020/docs)。若MySQL、Docker/检索服务原本已运行，日常可直接做2.3、2.4。启动器提供整个项目的后端和静态前端，不再另起旧章应用。

## 3. 启动检查与日常使用

另一个已准备的终端执行只读检查：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8020/health' -TimeoutSec 5
Invoke-RestMethod 'http://127.0.0.1:8020/api/kb/overview' -TimeoutSec 5
Invoke-RestMethod 'http://127.0.0.1:8020/api/jobs' -TimeoutSec 5
```

`/health` 只说明应用响应，不证明数据库、检索和上游模型全部可用。需要端到端检查时，可在聊天页问订单 1001 物流，或新会话问“这个能退吗”：后者应出现三张演示订单卡，点击后自动继续订单→扩写→政策→判断。聊天会调用付费模型并保存合成消息，弱证据问题还可能写低置信度池，不属于完全无副作用的健康检查。

建票新流只有用户明确要求且资料足够才展示预览卡；确认提交才落tickets，取消按权限拒绝落审计。POST /api/chat/actions/tool-confirmation只接受session_id/preview_id/decision。旧投诉和退款按钮协议保持不变，点按钮本身就是确认。

订单选择使用LangGraph原生interrupt，暂停期间网络响应结束不等于退款判断已完成；点卡由相同会话恢复。ch08刷新同标签或点击侧栏旧会话会回载MySQL用户/客服历史原文，并恢复待选、退款申请、建工单/转人工建议；已完成或待核实的工单按钮不能当作可重复提交。调试启动器会展示理解后的本轮问题，不代表新增跨会话记忆。

退款表单原因从固定类目选择，取消不建票；确认会向本地已有 `tickets` 表真实创建一条售后**演示申请**，不执行资金退款。同一 action 重复提交不重复建票，新的申请仍可能建新票。不要为了日常检查反复确认。投诉的转人工、建工单也各自独立，转人工按钮本身不自动建票。

意图识别默认使用主力模型；`INTENT_SMALL_MODEL` 和 `INTENT_ESCALATION_THRESHOLD` 仅是惰性配置种子，填写后也不会启用小模型路线。低置信度归“其他”和最多一次 JSON 格式修复是当前行为，不能把它们当作低分升级模型。

### 3.1 ch08上下文与多会话

普通启动默认W128000，验收的代表性21轮无需压缩；并不保证任意长度的21轮都装得下。切换小窗口前按6.1正常停掉当前8020，在同一准备好的终端执行：

```powershell
$projectContextProfile = 'demo' # 切回日常时改'default'，停掉旧进程后再启动
& $projectPython scripts/demo_ch08.py budget --profile $projectContextProfile
if ($LASTEXITCODE -ne 0) { throw '预算不足，先核对输入/输出/工具/步骤等开销。' }
& $projectPython scripts/demo_ch08.py serve --profile $projectContextProfile --port 8020
```

另一个终端查看上下文与级联日志：

```powershell
Select-String -Path log/app.log -Pattern 'model_ctx|history_ctx|层1 降级|summary trigger|summary start|summary done|summary skip|summary fail'
```

ch08 demo最终实测：W24000、O2000、max_user2000、steps3、tool_result1200、rerank_top_k5、decision2600。七工具目录下system2852、evidence1500、summary1000、safety1000、peak9600，history6048、layer1=4232、layer2=1814。旧18k即使只有四内置，layer1仅442，不能容纳一轮，故仅ch08调为24k；旧profile未改。default为128000/4000/4000/8/2400/10、decision2600，七工具实测history74548、layer1=52182、layer2=22364。目录、Prompt或摘要变化须以budget实际输出为准，不固定沿用这些快照。

聊天页“新对话”保留旧会话，发送第一条消息后新会话出现在侧栏。摘要仅当前会话使用，checkpoint完整历史不裁。切回大窗口不会回写或撤销已提交摘要、回退锚点；验证默认不压缩时开全新合成会话，不复用已经摘要的会话。

model_ctx含实际Agent payload、全部摘要、逐条窗口；history_ctx每轮都有，也记录理解/分类实际输入。日志仅本地保管。后台摘要追加完成后下一轮同步，关闭取消未完成任务，未提交边界的下一轮仍可重试。只读接口为GET /api/conversations?user_id=...、GET /api/conversations/{id}/messages?user_id=...。

### 3.2 浏览器会话归组与地址

localStorage的mewhelp-user-id是同一浏览器归组键，sessionStorage的mewhelp-chat-session保存该标签页当前会话。刷新、同源新标签和侧栏可继续旧会话；无痕窗口、换浏览器或清站点数据会失去原归组键，数据库原文不会因此删除。

固定使用同一个地址：localhost:8020与127.0.0.1:8020、不同端口属于不同origin，localStorage不共享。侧栏突然为空时先回到原地址核对，不清MySQL或新建数据库。同一浏览器备份/迁移需私下保全这两个键及原origin；恢复到原origin后再刷新。匿名标识不是登录认证。

### 3.3 动态工具、本地权限和审计

config/tool_policy.json是本地授权事实来源，可在轮间热加载。每Server的url/enabled及每工具的enabled/effect/alias/routing_intents/output_fields/enum_map均由本地控制。未知MCP工具默认不可用，外部write本章禁用，不信Server的read/write声明。轮内Schema固定；新轮拉目录，执行前仍复核当前策略/定义/端点。无效JSON会关闭MCP和写工具，保留已知内置读取。不要在有待确认写操作时随意变更策略，旧预览可能被安全拒绝。

统一引擎校验JSON Schema，不做类型强转；错误作为ToolMessage回模型。只对短暂网络读故障重试一次，空结果不重试，写操作不自动重试。tool_audit_logs映射项目外已建表，无外键；校验/权限拒绝也记录，审计失败不阻塞业务。日志只记录终态，等待确认不伪装成功。

每次读取重试都会重新复查权限/定义/端点，MCP请求绑定核验URL。工单描述可通过whole_message引用本会话真实user全文，模型用服务端draft_id发起建票，避免长文本复写；引用与完整参数共同绑定一次性capability并进入审计，前端仍展示完整描述。旧literal参数严格比较，旧投诉/退款按钮保留完整服务器拼接文本。明确校验/权限拒绝为rejected并说明未执行，结果不明才uncertain。

预览可跨正常重启恢复；confirmed/creating之后若结果不明，显示待核实，不自动重放。签发凭据只在原进程内有效。先核对同一合成/实际conversation_id的tickets、tool_audit_logs及SQLite状态，不通过新建预览或反复点击试探结果。不要删除SQLite/WAL或审计记录来解除状态。

## 4. 日志、知识与作业维护

### 4.1 查看状态与空间

```powershell
& $projectDocker compose @projectCompose ps -a
& $projectDocker compose @projectCompose logs --tail 100 standalone
& $projectDocker compose @projectCompose logs --tail 100 etcd
Get-ChildItem -LiteralPath $projectState -File | Select-Object Name,Length,LastWriteTime
Get-PSDrive -PSProvider FileSystem
```

应用异常看启动终端，节点日志在实际应用的 `log/app.log`；网页作业日志默认在 `log/acceptance/`，质量报告在 `reports/ch08/`。日志、SQLite、SQL 备份可能包含对话和工单，按业务资料保存，不公开上传。

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
$previousCalibrationReport = Join-Path $projectApp 'reports/ch08/calibration'
$calibrationArchive = Join-Path $projectApp "reports/ch08/calibration-before-$calibrationStamp"
if (Test-Path -LiteralPath $previousCalibrationReport) {
    Copy-Item -LiteralPath $previousCalibrationReport -Destination $calibrationArchive -Recurse -ErrorAction Stop
}
$projectProfile = Join-Path $projectState "calibration-$calibrationStamp.json"
$env:CH08_CALIBRATION_PROFILE = $projectProfile
& $projectPython scripts/demo_ch08.py calibrate --profile $projectContextProfile
if ($LASTEXITCODE -ne 0) { throw '校准失败，保留旧文件与失败报告，不启用新profile。' }
& $projectPython -c "import sys; from scripts.demo_ch08 import configure; configure(profile=sys.argv[1]); print('ch08 profile identity OK')" $projectContextProfile
if ($LASTEXITCODE -ne 0) { throw '新profile严格身份验证失败。' }
```

校准会调用真实模型、嵌入和重排并计费，当前入口固定写 `reports/ch08/calibration`，所以先归档。新终端需重新设置选定的 `CH08_CALIBRATION_PROFILE`，否则启动器回到默认文件。不得强改 hash、把旧 identity 填进新文件，或删除新增知识迁就旧 profile。

profile额外绑定ch08检索代码，动态工具目录hash不触发付费重校准，只重计上下文预算，并不表示已经对多查询独立校准；目前校准范围仍是原单查询链。发布质量需另看独立测试与抽查，不以校准成绩代替。

### 5.2 按需复验

在实际应用目录执行。真实模型、检索、对话验收均会计费；对话和确认验收会新增独立合成记录，脚本保留记录，不清理真实数据。

```powershell
& $projectPython -m pytest -q
& $projectPython scripts/eval_ch08.py --split dev --mode real --output reports/ch08/dev-new.json
& $projectPython scripts/eval_ch08.py --split acceptance --mode real --output reports/ch08/acceptance-new.json
& $projectPython scripts/regress_ch08.py --task router --profile default --output reports/ch08/router-new.json
& $projectPython scripts/regress_ch08.py --task dialogue --profile demo --output reports/ch08/dialogue-new.json
```

冻结数据为docs/samples/ch08-tools-dev.jsonl和acceptance.jsonl及各自.sha256。ch08语义评估不执行业务写入；mock只验证评估器，不能证明真实质量。scripts/integrate_ch08.py会在8020真实创建合成会话/工单；scripts/hotplug_ch08.py临时修改本地策略并最终恢复、使用自有8023测试进程。scripts/browser_real_ch08.cjs分seed/resume，重启步骤只适用于自己记录的进程。固定报告名运行前先归档，不能覆盖原失败记录后声称从未失败。

本章最终全量947 passed in60.02s。最终开发集20/24、验收集30/36严格匹配，取消/否定/假设路由差异及一条商品指代不充分的工具选择差异保留，安全负例均无有效建票草稿。真实HTTP六场景、Chrome合成9项、实际重启恢复/取消0/确认及重复1通过；2000字中文和含转义描述用来源引用均完整预览/落库2000字且回复带工单号。旧冻结意图/订单/21轮结果、所有原失败与最终复验见reports/ch08/summary.md，不沿用旧758冒充本轮成绩。

## 6. 重启、暂停、恢复与停止

### 6.1 正常停止或只重启应用

停止新请求，等待聊天、申请确认、Make作业及手工评估结束。会话摘要无需手工改边界或另停worker；正常shutdown会关闭MCP Client、取消未完成后台任务，已提交的摘要段保留，未提交覆盖范围在以后轮次按预算重试。若已进入数据库提交，以实际持久化段和锚点为准，不回退边界。回到**启动8020的原终端**按Ctrl+C，等待shutdown完成；在另一个终端确认端口已释放：

```powershell
Get-NetTCPConnection -LocalPort 8020 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

无输出才表示该端口停止。重启应用无需关闭MySQL/容器，使用同一SQLite和已选检索profile，再执行 `& $projectPython scripts/demo_ch08.py serve --profile $projectContextProfile --port 8020`。新终端需先重做第1节并恢复原default/demo选择与自定义profile路径。原生interrupt待选可保留；工具异常中断不会作为新请求自动重放，已经生成但暂未落库的终答会在新用户入库前恢复。

### 6.2 原终端找不到时

先查询上面命令返回的**当前** PID，检查其实际程序和命令；不要给 PowerShell 自带 `$PID` 赋值：

```powershell
$serverProcessId = [int](Read-Host '输入刚查到的8020 OwningProcess编号')
Get-CimInstance Win32_Process -Filter "ProcessId = $serverProcessId" |
    Select-Object ProcessId,Name,ExecutablePath,CommandLine
```

核实为本项目 Python、`demo_ch08.py serve` 或已确认的本项目入口。相对命令还须结合启动记录确认所属工作树。确认没有处理中请求后，只有该 PID 仍监听 8020 才停止：

```powershell
$serverListeners = @(Get-NetTCPConnection -LocalPort 8020 -State Listen -ErrorAction SilentlyContinue)
if ($serverProcessId -notin $serverListeners.OwningProcess) { throw 'PID归属已变化，请重新查询。' }
Stop-Process -Id $serverProcessId -Confirm
Get-NetTCPConnection -LocalPort 8020 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

这是强制终止兜底，不等于 Ctrl+C 正常关闭，可能未执行 lifespan 清理。权限不足时在管理员终端重新查询归属；不要结束全部 Python。保留 SQLite 主文件及现有 WAL/SHM；若当时正在建票，应核对 MySQL 与 creating/uncertain 状态，不因页面报错就再次提交。

停止后端后，回到自己启动的两个MCP终端分别Ctrl+C；不得按端口历史快照批量终止Python。

### 6.3 整套停止或关机

先确认所有使用同一MySQL/Milvus的应用和任务均已停止，包括可能仍在使用它们的历史8015、8016、8018及手工CLI；不能只停8020就关闭共享基础设施。分别按实际PID/入口正常停止相关服务，不根据旧端口列表批量结束进程。随后先停Milvus，再停外部etcd：

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

### 6.4 日常暂停：暂时离开并保留状态

只暂停 AI 开发、仍要使用网页时，按6.6记录开发状态即可，应用可继续运行。若要暂停整个演示应用，按以下顺序操作；这会保留现有数据，但不等于已做备份。

1. 停止发送新消息或点击提交，不再发起后台作业、知识修改和付费评估。等待正在处理的聊天、工单提交和作业结束；若写操作结果不明，记录会话和调用标识，按3.3核实，不重试提交。待确认卡片可以保留到恢复后，不必为了停机点击确认或取消。
2. 私下记录实际应用路径、浏览器原地址、当前会话、`default/demo`选择、`CH08_CALIBRATION_PROFILE`绝对路径，以及已启动的终端/进程归属；不记录密钥、连接串或客户原文。查看待核实工单及运行作业，避免恢复时重复执行。
3. 在8020原终端按Ctrl+C并等待正常shutdown，按6.1确认端口释放；然后分别停止自己启动的物流8021和售后8022。丢失终端时按6.2逐个核对归属，强制停止后仍要核实未决写入。
4. 短暂停可保留MySQL、Docker/etcd/Milvus运行。准备关机或停共享基础设施时，继续6.3，先核对其他使用方。暂停过程中不删除文件、不重建知识库、不做依赖同步。
5. 保留MySQL数据、Milvus/外部etcd卷、`log/ch08/checkpoints.sqlite`及存在的WAL/SHM、检索profile、迁移清单、`config/tool_policy.json`、根私有`.env`、全部源码及未提交/未跟踪内容。浏览器保留原origin和3.2的归组键；新origin不会自动显示原列表。需要防磁盘故障时另按第7节做完整冷备。

关掉网页或浏览器不会停止后端；关闭启动终端可能意外结束服务，不能把它当作已核对的正常暂停。暂停后记录哪些组件实际停止、哪些仍运行；`/api/jobs`显示idle不代表所有后台/手工子进程已经结束。

### 6.5 从暂停恢复到可用

1. 先读暂停记录，确认恢复的是同一工作树、同一数据和同一会话状态。新终端执行第1节，并恢复原`$projectContextProfile`；若使用自定义检索profile，应在准备区前设置原`CH08_CALIBRATION_PROFILE`路径。不要为了恢复新建空SQLite或切换检查点目录。
2. 按第2节检查MySQL、Docker、etcd/Milvus；只启动尚未运行的依赖。若8020/8021/8022已监听，按6.2方式核对各自程序与启动记录，已属于本项目的健康进程可复用，不重复启动或按旧PID结束。
3. 缺少哪个MCP就只在相应终端启动哪个；完成2.3的知识一致性、profile身份和工具预算检查。原七工具配置下应发现7个工具且diagnostics为空；合法增删工具时以本地策略与实际清单为准。日常恢复不运行calibrate、vectorize、建库或reset；只有检查失败并查明原因后才进入对应维护流程。
4. 8020未运行时按2.4启动，再执行第3节只读检查。打开原浏览器地址和旧会话，核对历史原文、待选订单/工单预览及已完成/待核实状态。恢复interrupt需要在原会话点击卡片，不用新消息代替确认；新消息会使旧建票草稿失效，不能用于试探是否已经提交。
5. 发现creating/uncertain、重复提交风险或跨库状态不一致时，先核对tickets、tool_audit_logs和SQLite。确认已有工单则沿用原工单号；不靠重启、删状态或再次确认来补偿。MCP重启后的mock轨迹可能改变，这是随机演示数据的边界，不表示MySQL订单丢失。

本节恢复的是已有服务与持久状态，不自动恢复之前的手工评估进程，也不自动重发写工具或开始ch09开发。若发生文件/数据覆盖，按7.2恢复流程处理，而非按日常启动处理。

### 6.6 暂停或恢复开发时的交接维护

因网络、额度或主动中断需要暂停开发时，先更新`docs/AI_HANDOFF.md`、所涉章节的`docs/CHxx_CURRENT_PROGRESS.md`及`dev-notes/chxx.md`，并保存该计划的`.superpowers/sdd/`账本。写清已完成/进行中/未开始、最后落盘验证及日志、未决问题、下一步文件/任务、依赖与editable状态、服务是否改动；失败或未执行项如实记录，不能沿用旧通过数。

额度不足10%时按用户规则优先交接留痕并暂停开发。暂停开发不自动停止已有服务，也不授权清理未提交内容；把服务状态与开发状态分别记清。明确恢复后先核对Git工作区、落盘文件和相关测试，不重做已批准步骤，不把旧PID、测试数或额度快照当作当前事实。

当前ch09恢复点为Task2接线：Task1已完成，Task2只有辅助上下文/SDK内存导出定向验证；Task3–13未实施。Native已获准，无需重批；本次只更新运维文档，未恢复其功能开发。具体以[最新ch09交接](<E:/MewHelp AI/.worktrees/ch05-workflow-agent/docs/CH09_CURRENT_PROGRESS.md>)、章节留痕和专属账本为准。

## 7. 整套备份与恢复

### 7.1 同一停写窗口的冷备

整套恢复点必须同时包括 MySQL、Milvus 与外部 etcd、ch08 SQLite、config/tool_policy.json、实际源码/未提交内容、根配置、迁移清单及**当前选定**的 profile。Git HEAD 不足以保存未提交代码；单独备份 SQLite 也不是整个项目备份。

先停所有应用/作业/评估/调试连接，并确保没有其他程序或数据库事件继续修改相关数据。历史8015、8016、8018若仍使用同一数据，也必须纳入停写核对。先保留MySQL和检索服务运行，以便最后检查一致性；/api/jobs的idle不代表摘要、旧子进程或手工作业已结束。

MySQL整库备份包括既有知识/工单表和conversations、messages、conversation_summaries、tool_audit_logs及双锚点；SQLite保存完整LangGraph历史、工具轨迹、原生interrupt和操作状态。两边必须来自同一停写窗口。仅备份消息表或仅复制SQLite都不能完整恢复当前会话。

```powershell
Invoke-MewHelpKb verify-hybrid
& $projectPython -c "import sys; from scripts.demo_ch08 import configure; configure(profile=sys.argv[1]); print('backup identity OK')" $projectContextProfile
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
@{Application=$projectApp;Manifest=$projectManifest;Profile=$projectProfile;State=$projectState;ContextProfile=$projectContextProfile;BrowserOrigin='http://localhost:8020'} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $metadataBackup 'locations.json') -Encoding utf8
```

MySQL用--result-file，避免PowerShell输出重定向的编码问题；密码只在交互提示输入。--single-transaction仅为InnoDB提供事务快照，不替代整套跨库停写，不允许导出期间并发DDL，也不保证非事务表的一致性。账号需具备相应SELECT/视图/触发器/例程/事件导出权限；权限失败时修正授权与备份范围，不把缺表/空SQL标记为完整备份。

上述数据复制完成后，还必须保存以下材料才算完整备份：

- **实际应用工作树**：app、data、scripts、tests、docs、reports、dev-notes、Makefile、pyproject.toml、uv.lock、.python-version、.env.example、.gitignore、存在的 AGENTS.md 与 `.superpowers` 账本；包含未跟踪源码。
- **根目录**：未提交/未跟踪内容与根 `.git` 仓库元数据；根 `deploy/milvus/` 的两份 Compose、user.yaml、embedEtcd.yaml 以及其他现存配置；根 `.env` 受控保存。不要遗漏根目录尚未同步到工作树的独立改动。
- **当前会话与历史运行状态**：ch08 SQLite 文件集已单独保存；如果还需恢复旧服务/旧会话，还要在其连接全部关闭后保存其实际 SQLite，不把旧文件覆盖到 ch08 路径。可选保存相应日志和缓存。
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

- **8020 占用**：按6.2查询实际PID/入口，不重复启动或结束全部Python；8015/8016的旧响应不能当ch08成功。
- **MySQL 登录失败**：核对 MySQL80、账号权限、本地 DB_URL 编码；端口能连不等于能访问表，不打印完整连接串。
- **Docker/Milvus 初始化失败**：先确认 Desktop 引擎，再查两份 Compose、外部 etcd 健康和已有数据目录；保留日志，不清卷或切回旧嵌入模式。
- **BM25 能用而 dense/hybrid 失败**：先查嵌入模型网络/密钥/余额；只有 hybrid_rerank 失败再查重排。401/403 查鉴权，402 查余额，429 查限流，timeout 查网络/代理，不能一律归为迁移错误。
- **ONLINE 但聊天失败**：`/health` 不覆盖真实依赖；按 MySQL→Milvus/profile→模型服务排查。修改本地配置后正常重启应用，刷新页面不能清掉进程缓存。
- **profile 不匹配/缺失**：先看是否语料、模型、Prompt、generic 或 ch08 代码变更；恢复正确文件或按第 5 节新校准。不把阈值单独拷进环境绕过校验。
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

已交付功能与完整结果见[ch08交接](<E:/MewHelp AI/.worktrees/ch05-workflow-agent/docs/CH08_CURRENT_PROGRESS.md>)、[ch08开发留痕](<E:/MewHelp AI/.worktrees/ch05-workflow-agent/dev-notes/ch08.md>)；后续部分开发见[ch09交接](<E:/MewHelp AI/.worktrees/ch05-workflow-agent/docs/CH09_CURRENT_PROGRESS.md>)和[最新总交接](<E:/MewHelp AI/.worktrees/ch05-workflow-agent/docs/AI_HANDOFF.md>)。实际应用的[交付报告](<E:/MewHelp AI/.worktrees/ch05-workflow-agent/reports/ch08/summary.md>)和[演示步骤](<E:/MewHelp AI/.worktrees/ch05-workflow-agent/reports/ch08/demo.md>)有质量限制与验证证据；运行报告只保留在产品工作树，没有在根目录复制另一份。上述本地链接指向产品原件，在根阅读副本中也可使用。

本手册按现有源码/锁文件核对。外部接口依据已通过 Context7 获取的官方说明：[Compose 多文件合并](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)、[Compose up](https://docs.docker.com/reference/cli/docker/compose/up/)、[Compose stop](https://docs.docker.com/reference/cli/docker/compose/stop/)、[MySQL 8 mysqldump](https://dev.mysql.com/doc/refman/8.0/en/mysqldump.html)、[SQLite WAL](https://www.sqlite.org/wal.html)。启动/停止参数还应与实际安装的 CLI 帮助核对，不因文档更新升级基础设施。

2026-10-25本次文档核对：Docker Compose v5.5.0的--wait/--wait-timeout及stop --timeout、两份Compose config --quiet、本机MySQL客户端8.0.45、ch08 CLI help及两profile预算已只读验证。服务当时MySQL Running、etcd/Milvus healthy、8020监听；这只是当时快照，启动/停止/恢复的写操作未演练，本次未重跑758项或真实模型评估。


## 10. ch09 部分实施状态（2026-10-26，开发暂停）

完整设计和13任务计划已获准，用户已选Native；Task1完成、Task2部分实施后中断保存，Task3–13未开始。不存在已验证可用的ch09独立启动入口、新worker或定时评估任务，继续按本文ch08流程运行。三张MySQL表已有，ORM仅映射，不建表、不改表、不清库。这里的开发暂停不会主动停止当前服务。

以下维护要求已纳入获批设计，待相应任务实施验收后启用，目前不执行：人工核准新知识后先核对评估影响；待标注确认时旧知识库继续服务。确认后进入双写/24题校准维护窗口，知识类请求提示稍后重试，业务/闲聊继续；窗口时长取决于模型与存储服务。写入前预检失败不影响旧版；写入后失败需续跑发布，不能强改profile哈希绕过。正式发布原子切换知识/标注/profile描述，且不覆盖ch04原集或混画跨标注版本趋势。

实施时私有data/local/ch09目录将包含发布记录、标注版本、profile和active描述，须与MySQL/Milvus/checkpoint成套备份；本阶段尚未创建或启用该目录。拟每日02:00（Asia/Shanghai）在应用运行时评估，停机不运行，最多补最近一个调度槽，具体命令待实现验证后纳入本文。

Langfuse日常观察完整流程将在实际接入后补齐：日本项目连接检查、Session多轮定位、Trace节点/Prompt/工具/检索及异常、意图Token/成本统计、反馈与问题池关联、审核发布及评估趋势、云端限额/延迟排查、正常停止排空。当前不能用此设计说明冒充云端已可用。恢复入口见docs/CH09_CURRENT_PROGRESS.md及dev-notes/ch09.md。

实施计划预留独立ch09入口/SQLite、在线owner锁与发布协调：在线时脚本经应用管理入口，离线才可独占运行，避免脚本绕过在线知识读者；这只是计划，不能现在调用尚不存在的脚本。执行计划及测试/校准/验收命令见docs/superpowers/plans/2026-10-26-mewhelp-ch09-observability-flywheel.md。实际运行验证后再替换本手册中的ch08启动步骤。

### 10.1 ch09 当前代码与验证边界

Task1完成后的历史全量为961 passed；Task2辅助上下文/真实SDK内存导出组合定向17 passed，不能作为Task2主链接线或整章验收。官方SDK锁定5.0.4、原锁包未升级，共享环境的root editable已恢复；日常运行不用重新安装依赖，也不在此执行依赖同步命令。

`app/main.py`已挂接可选观测运行时的lifespan；`LANGFUSE_ENABLED`默认false，未启用或配置缺失时不连接Cloud。本文准备区显式关闭，真实`.env`不改。日后启用须先完成日本区后端私有配置与真实验收，密钥只在后端。正常shutdown会尝试排空；`LANGFUSE_SHUTDOWN_TIMEOUT`默认10秒，超时只记未确认，强杀不保证缓冲提交。同进程同项目的SDK关闭后重开会明确降级，正常重启应使用新应用进程。当前没有Cloud鉴权/Trace树/成本/发布/定时评估验收。

### 10.2 本次文档维护的验证范围

2026-10-26本次仅核对本地源码、启动参数、配置样例与交接沿革，新增6.4–6.6暂停/恢复步骤，更新ch09实施边界并同步阅读副本。文档检查包括PowerShell代码块静态解析、必需路径与本地链接、四份手册内容一致及变更空白检查；未启停服务、未读取真实`.env`值、未运行付费模型/Cloud请求、未执行备份恢复或数据库操作，也未以历史947/961通过数冒充本次产品测试。

### ch09 Task2接入状态（2026-10-27）

Native已恢复，Task1–2完成，973项全量通过。主图/续跑/旧入口及自定义步骤已能采集Trace，新增turn元数据帧包含系统会话、用户/回答消息ID与本次request/trace ID；识别该帧不影响token/tool/citations/done。默认LANGFUSE_ENABLED=false，SDK内存测试不是Cloud上报验证。仍沿用ch08启动入口；Task3反馈与后续飞轮/评估尚未完成。详情见CH09_CURRENT_PROGRESS与dev-notes/ch09阶段10–11。

### ch09 Task3后端反馈维护（2026-10-27）

Task1–3源码完成，987项全量通过；聊天页按钮尚未接POST。后端/api/feedback只接收turn帧中的系统session_id、user_message_id、assistant_message_id及rating=down，原话/证据由服务端取得，不可自行传原文。重复反馈返回同event_id，缺历史快照仍可收集并标不可用，保存失败503可重试；自动拒答保存失败仍给兜底并报告collection_status.saved=false。后台归并/审核、正式校准和评估尚待后续任务。旧运行进程不会因文件修改自动生效（除非显式reload），不要重启用户旧服务验证；当前仍按ch08维护入口，后续另启ch09自有实例。

### ch09开发维护补充（Task4，2026-10-27）

置信评分与固定校准网格已实现，全量1002通过；尚未产生可启动的真实ch09 profile。当前仍按ch08步骤启停和备份，不使用测试合成阈值。恢复开发读CH09_CURRENT_PROGRESS与专属task-5-brief；生成前自评与后续发布仍待实现。

### ch09 Task5维护状态（2026-10-27）

Task1–5完成，Task6进行中。全量1014 passed in87.78s，生成前自评真实12/12符合标注；预算/服务错误不入知识缺口。当前仍按ch08启动、暂停、恢复；正式profile未校准激活，Cloud未验收。新ch09入口与审核发布未完成，不把模块级通过当整章完成。

### ch09 Task6运行维护（2026-10-27）

Task1–6完成，全量1027通过；归并Prompt真实16/16符合标注。日常整项目仍按ch08启停、暂停与恢复，独立ch09入口待Task8，默认FLYWHEEL_ENABLED=false。启动ch09消费者后，问题提交唤醒，漏通知由30秒扫尾补齐；退出会取消模型等待并等短事务结束、释放命名锁。

处理失败退避30/120秒，第3次needs_retry不再自动调用模型。排查processing.error_code（不输出原话或配置）；核实原因后，在产品目录用既有Python运行scripts/flywheel_ch09.py --retry EVENT_ID重置调度，累计attempts/error_history保留。--once会使用真实模型处理最多20条已有未归并事件，勿当健康检查；与在线消费者共用数据库锁，不重复累加。此阶段未运行真实队列补跑。备份继续包含MySQL与既有checkpoint；发布私有版本目录将在Task7纳入。

### ch09 Task7版本备份维护（2026-10-27）

Task1–7完成，全量1043通过，影响Prompt v3真实8/8。当前整项目仍按ch08入口启停；ch09独立启动/发布尚待Task8，不运行bootstrap写真实版本。版本私有目录为data/local/ch09，已gitignore；正式启用后必须纳入整套冷备，包含impacts草案/人工确认、eval_versions、releases、profiles和active.json，与同一时间点MySQL/Milvus/checkpoint对应。

版本目录丢失先停对应发布并恢复匹配备份，不从review_status=通过猜已生效、不补造人工确认。坏hash/缺来源不跳过；若旧active与当前语料仍可核验，知识服务可继续旧版，否则显示知识降级待恢复。原data/eval/ch04的cases/split/ANNOTATION_NOTES保留原件；标签改回旧内容也会形成新版本历史，跨标签版本另开趋势，不跨版本计算下降。

### ch09 Task8独立入口与发布恢复（2026-10-27）

Task1–8完成，全量1068通过；以下命令已具备，但首次真实bootstrap/发布在Task12核对目标后执行。当前已验收整项目仍按ch08步骤启动、暂停及恢复；不要把本模块测试当整章真实验收。产品目录为E:/MewHelp AI/.worktrees/ch05-workflow-agent，解释器沿用E:/MewHelp AI/.venv/Scripts/python.exe。

在产品目录，运行`& 'E:/MewHelp AI/.venv/Scripts/python.exe' scripts/demo_ch09.py check`只读查看开关/owner/active/密钥是否已配置，不打印密钥；`bootstrap`会读取固定96题并对24题真实检索校准、写私有版本/profile/active，失败不建active；`calibrate`对当前确认版本重校准。离线命令先取owner再取publish锁，在线calibrate通过默认http://127.0.0.1:8023的管理API执行（可用--url明确同机端口）。`serve --port 8023`使用独立log/ch09/checkpoints.sqlite和data/local/ch09；端口被占用须选择明确空闲端口，不停止旧进程。共享MySQL/Milvus与MCP前置步骤沿用现有整项目手册，ch08的profile/SQLite/启动器不覆盖。

审核API：GET /api/review及/{id}看队列和原话/Top5快照，POST /{id}/approve保存人工答案，POST /impact查看全96题建议，POST /impact/confirm明确逐题修改和其余不变后发布；状态见/releases/{release_id}，失败经同路径/retry续跑。前端Task11尚未接。先journal后pending→限定向量flush→done→24校准→active；写后失败知识维护，业务继续，不清理pending、不将旧profile冒充匹配。重启会根据未完成journal保持维护，恢复备份须MySQL/Milvus/checkpoint/整个data/local/ch09配套。

旧/kb手工写入、vectorize/build/rebuild/reset在ch09 owner在线时被阻止，使用新审核入口；只读健康检查继续可用。脚本不能越过owner直接写。日常暂停仍按主应用→MCP、必要时共享基础设施的既有顺序；应用关停会先停止消费者/请求、释放owner，观测最后flush。不要删除私有journal、checkpoint或真实.env。日常Langfuse Dashboard完整流程及Cloud读回证据在Task12–13补齐，当前默认开关关闭。

### ch09 Task9评估维护（2026-10-27）

Task1–9完成，全量1082通过；真实两轮72题模型评估尚未执行，不能把合成回归当指标验收。ch09应用内默认上海02:00调度，CH09_EVAL_ENABLED可关闭，CH09_EVAL_HOUR/MINUTE可配置；只在应用在线时运行，启动最多补最近一个槽，不补历史多日。首次serve可能立即补槽并真实调用模型，受控演示时可在启动终端设置CH09_EVAL_ENABLED=false再手动两轮。不要把评估当免费健康检查。

产品目录运行`& 'E:/MewHelp AI/.venv/Scripts/python.exe' scripts/eval_ch09.py --trigger 手动`或固定Make ch09-eval；在线走owner应用API，默认8023，改端口用--url或由serve传递CH09_API_URL；离线先owner，禁止与在线知识读者直接并行写。POST /api/evaluations/run等待真实一轮结束；GET /runs与/trends查看结果。记录先running，结束更新同一行；正常取消为interrupted，进程失联由取得eval锁的下一次运行恢复，不能手动把旧running改成功。

趋势只连接同标注/保留题/评分协议/judge身份，跨版本并列不算差值。N/A/失败不是0；观察Recall@5/@10、MRR@10、Faithfulness及覆盖率、拒答率、分母、judge失败数。核心服务未完整跑完或语料漂移不进成功趋势。评估会持有固定release读租约到整轮结束，发布等待它退出；停止应用先取消调度并等隔离状态/短事务收尾，再释放owner和flush观测。日志/checkpoint在log/ch09/evaluation，业务会话/工单/问题池不写入。

### ch09 Task10观测统计与日常查看（2026-10-27）

Task1–10完成，全量1092通过；以下Cloud查看流程待Task12实际读回核验，本阶段只有SDK内存/合成统计验证。配置只放本地后端.env，参考.env.example占位符，不放前端或Git；Base URL必须日本区https://jp.cloud.langfuse.com。LANGFUSE_ENABLED=false时业务可运行；启用后看启动日志及GET /api/observability/status的ready/degraded，密钥仅显示是否配置。degraded不等于问答不可用，排查配置/网络后正常重启对应应用，勿强杀期待缓冲必达。

日常观察完整顺序：1）按项目手册确认共享基础设施/MCP、有效ch09 active及profile，启动独立服务；2）问一条真实知识问题，记录该回答turn帧的trace_id、session_id及用户/助手消息ID；3）登录日本区对应Langfuse项目，在Traces/Observations查该trace_id，展开请求根→意图→检索→精排/DB→闸→自评→Agent/工具→回答，检查节点关系、输入输出、Prompt、耗时/异常及Generation usage/cost；4）用同session再问一轮，在Session查看多轮并核对系统session_id；5）在聊天页👎后，于审核详情按消息/Trace ID核对原话和Top5当时证据，禁止拿最新一轮代替；6）打开成本页或GET /api/observability/costs，选purpose=chat，看最终意图排序、未知数、完整性和as_of，飞轮/评估/摘要用独立purpose；7）查看评估趋势，同标签/协议比较，核对分母和失败原因；8）日常暂停按手册正常停止主应用，等后台任务/SDK shutdown后再停必要依赖，保留日志/checkpoint/版本目录。前端入口和真实演示仍待Task11–12。

成本读数使用官方Observations v2游标后端归因，不读取Prompt/答案；Cloud仍可能有写入延迟，本地有trace_id不等于已上传。缓存15分钟，失败保留旧as_of并标stale；complete=false不能当全量账单，unknown不能当0。只有叶子Generation真实usage计数，缓存子类不重复加总；Cloud未返回价格则仅显示Token，USD观测成本的单价日期未返回时不捏造。需要补充模型定价应由后端/Cloud项目设置按明确来源和日期核实，前端不得填猜价。预算/429冷却跨重启保留，Metrics预算96/日（当前归因路径消耗0），观察分页20请求/分钟。

### ch09额度暂停与恢复位置（2026-10-27）

额度剩9%按用户规则暂停开发；Task1–10完成，Task11仅普通回答反馈接线，尚未浏览器验收。最新后端1092通过早于这两处前端改动，不是整章最终结果。旧服务未动，日常仍按既有ch08已验收入口；不在暂停期间执行bootstrap、队列消费、发布或付费评估。额度恢复且用户明确后读docs/CH09_RESUME_HANDOFF.md继续Task11，不重批Native/设计。保留所有日志/账本/私有状态，Task12真实验收与Task13整章评审未完成。


### ch09恢复维护更新（2026-10-27）

审核入口/review、质量成本入口/quality已实现，浏览器验收待完成。私有data/local/ch09已有真实24题校准的active/profile，勿删除或拿72题重新选阈值。日本区三项私有配置已获用户授权就位，LANGFUSE_ENABLED未写.env，受控验收用进程环境开启；CH09_EVAL_ENABLED=false避免启动即补跑。验收仅用本次合成身份处理队列，不清理旧37条池记录。UI工具被策略终止，当前不将页面实现视为验收通过。首次启动/停止仍使用下述ch09独立端口与原进程精确识别流程；只停止本章自己启动的进程。


### ch09测试与在线实例必须串行

真实ch09实例持有全局MySQL owner锁，旧知识写测试会被正确拒绝；不要边运行全量pytest边运行8023实例。先正常停止自己启动的ch09实例、确认owner_online=false，再跑全量，最后重启并核对active/profile。1093项串行通过为本阶段快照，后续owner修复22项通过，最终全量另记。验收脚本分probe/merge/preview/publish/costs/trace，run-id必须ch09-前缀，仅使用合成身份；publish需要人工先核对96题及显式确认标志，不能据模型建议自动确认真实标注。


### ch09真实闭环维护（阶段23）

已完成首次真实bootstrap与一次仅新增1条合成验收知识的发布；启动步骤、审核/影响确认/续跑及实际命令见reports/ch09/demo.md。根私有配置三项已授权就位，示例环境文件仍只有占位符。不要以旧历史段落“未bootstrap”覆盖最新状态；完整UI/Dashboard点击仍待验收，不能称整章完成。两轮72题正在真实执行，等待完成再正常停止本章实例，不与全量pytest并行。Langfuse金额未知保留null，现可按意图看真实Token消耗。


### ch09唯一review后维护更新（阶段24）

全量1110项通过。旧profile不含新retrieval_runtime_hash时会明确拒绝知识问答；停止本章实例后运行demo_ch09.py calibrate对固定24题重新校准，再启动8023。若有unfinished发布journal，必须按原release_id续跑，禁止靠calibrate重新设active。更换embedding/rerank模型/服务地址/维度/检索集合或Milvus目标后也需重新校准，凭据轮换本身不改阈值身份。不要删版本/profile/预算/journal来绕过保护。

首轮#7 Faithfulness全部judge失败保留为历史，#8中断保留；新judge适配身份会单独起趋势，旧N/A不当0也不算完整验收。最终两轮结果另见reports/ch09/evaluation.md。详细review见reports/ch09/review.md；仅一次独立review，作者全量GREEN不等于额外review。


### ch09修复后实跑状态（阶段25）

新配置身份已真实24题重新校准，正常停机SDK shutdown_confirmed=true已实测；8023目前停止等待最终回归。启动命令仍见reports/ch09/demo.md；停机须先等归并短事务结束/关闭worker，再等SDK排空，强杀无法保证尾部Trace。修复后Cloud所有子观察session_id与系统一致，正式自动回调消费者在限定合成run实跑通过；日常正常入口仍全队列消费，不使用验收限定器。共享owner连接检查现在线程串行，最终全量另记。


### ch09最终代码运行核对（阶段26）

最终串行全量1111PASS；24题新身份profile已激活，真实观测故障不阻断问答、正常停机排空及重启恢复合成积压均通过。原37条问题池未动。当前8023为本章受控验证进程，两轮72题真实评估运行中，暂勿与全量pytest并行或在中途切active。日常正式serve启动会启用原配置的全量自动消费者及默认02:00调度，说明见demo.md及10.4日常Langfuse流程。新脚本CLI在线等待上限3600秒，评估记录不会因客户端提前离开而伪报完成；以eval_runs终态/分母为准。页面实点/Dashboard视觉仍待验证。
