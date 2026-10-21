# MewHelp AI 启动、维护与关闭手册

适用环境：当前 Windows 电脑，项目目录 `E:\MewHelp AI`，ch04 混合检索版本。核对日期：2026-10-21。

本文中的启动、停止、备份和恢复命令供你操作，编写文档时没有替你执行启停、备份或恢复。下面的路径、服务名已经过本机只读核查；换电脑后应重新确认。

## 1. 项目需要哪些服务

- **MySQL 8**：本机 Windows 服务名为 `MySQL80`，数据库 `melhelp`，默认端口 `3306`。保存知识原文、会话、消息、工单、挖掘暂存和低置信度问题。
- **Docker Desktop**：提供 Linux 容器运行环境。先打开 Docker Desktop，等引擎就绪。
- **etcd 3.5.23**：Compose 服务名 `etcd`，容器名 `mewhelp-etcd`，保存 Milvus 元数据。容器网络内端点为 `etcd:2379`。
- **Milvus 3.0.0**：Compose 服务名 `standalone`，容器名 `milvus-standalone`。客户端端口 `19530`；健康检查端口 `9091`。
- **FastAPI / Uvicorn**：一个 Python 进程同时提供聊天页、后台页、知识库页、静态资源和 API。本文统一使用 `127.0.0.1:8010`。
- **远程模型 API**：DeepSeek 提供路由、改写、生成和评审；SiliconFlow 提供 `BAAI/bge-m3` 嵌入及 `BAAI/bge-reranker-v2-m3` 重排。无需在电脑上另外启动模型进程，但需要网络、有效密钥与供应商额度。

**不需要另外启动前端开发服务器、Redis、Celery、Langfuse 或 MinIO。** 当前前端是 FastAPI 直接提供的静态页面，Milvus 使用本地存储。评估、建库、向量化和挖掘是按需命令，不是每次开机必须启动的守护服务。

启动顺序：**MySQL → Docker Desktop → etcd/Milvus → FastAPI**。

关闭顺序：**等待作业结束 → FastAPI/手工后台任务 → Milvus → etcd → MySQL（按需）→ Docker Desktop（按需）**。

## 2. 终端和工具准备

普通运行使用 PowerShell。启动/停止 Windows 服务时，另开“以管理员身份运行”的 PowerShell。下面每个新终端都应先进入项目目录。

```powershell
Set-Location -LiteralPath 'E:\MewHelp AI'
$taskDocker = 'C:/Users/20968/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
$taskCompose = @('-f', 'deploy/milvus/docker-compose.yml', '-f', 'deploy/milvus/docker-compose.external-etcd.yml')
```

本机核查到的工具：

- Python 版本由 `.python-version` 固定为 `3.12`。
- uv：`C:\Users\20968\.local\bin\uv.exe`，本次核查版本 `0.13.0`。
- MySQL 客户端：`E:\MySQL\bin\mysql.exe`。
- 备份工具：`E:\MySQL\bin\mysqldump.exe`，本次核查版本 `8.0.45`。
- GNU Make：`C:\Program Files (x86)\GnuWin32\bin\make.exe`。
- Docker Compose：本次核查版本 `v5.5.0`，通过上述 Docker 客户端调用。

首次准备环境，或确实变更了锁文件后执行：

```powershell
uv sync --locked
```

现有 `.venv` 正常时不需要每天同步。后续命令使用 `uv run --no-sync`，不会自动重新同步依赖；不要用它代替首次安装。不要为了启动随意升级 Python、依赖、Milvus 或 etcd。无需手动激活 `.venv`。

### 配置文件

真实配置只在本地 `.env`，变量清单见仓库根目录 `.env.example`。已有 `.env` 时**不要用示例覆盖**。新电脑可以在确认没有 `.env` 后复制示例，填入自己的连接信息和密钥；表结构由数据库管理员准备，应用不会自动建表。

至少确认以下配置已经正确填写，但不要把真实值粘贴到日志、聊天或文档：

- `DB_URL`：MySQL 地址、账号、密码、`melhelp` 数据库。
- `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_EXTRA_BODY`：现有 DeepSeek 配置。
- `EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY`、`EMBEDDING_MODEL=BAAI/bge-m3`、`EMBEDDING_DIM=1024`。
- `RERANK_BASE_URL`、`RERANK_MODEL=BAAI/bge-reranker-v2-m3`；`RERANK_API_KEY` 为空时复用嵌入密钥。
- `MILVUS_URI=http://127.0.0.1:19530`；本机无认证服务的 `MILVUS_TOKEN` 留空。
- 网页后台作业需要 Make；找不到时设置 `MAKE_EXE` 为上述绝对路径。

## 3. 每次正常启动：已有数据库和知识库

### 3.1 启动 MySQL

在管理员 PowerShell 中：

```powershell
Get-Service -Name MySQL80
# 已是 Running 则不用重复启动。
Start-Service -Name MySQL80
Get-Service -Name MySQL80
```

也可在 Windows 的“服务”应用中启动 `MySQL80`。若服务名不存在，不要新建另一套服务或重新初始化数据目录，先核对安装方式。

端口可达与账号可用是两件事，分别检查：

```powershell
Test-NetConnection 127.0.0.1 -Port 3306
& 'E:/MySQL/bin/mysql.exe' -h 127.0.0.1 -P 3306 -u root -p melhelp -e 'SELECT 1 AS ok;'
```

`-p` 后不写密码，按提示输入。本机若使用其他数据库账号，替换 `root`。这里不从 `.env` 输出或拼接密码。

### 3.2 启动 Docker Desktop 与两个容器

从 Windows 开始菜单打开 Docker Desktop，等待引擎运行。回到已执行第 2 节准备命令的普通 PowerShell：

```powershell
& $taskDocker info
& $taskDocker compose @taskCompose up -d --wait --wait-timeout 120
if ($LASTEXITCODE -ne 0) { throw '容器未按时就绪，请先检查状态和日志。' }
& $taskDocker compose @taskCompose ps
Invoke-RestMethod 'http://127.0.0.1:9091/healthz'
```

正常情况下 etcd 健康后才启动 Milvus。健康检查超过 120 秒不等于数据丢失，先看日志。

**任何 Compose 启停、状态或日志命令都保留两份 `-f`。** 本文用 `$taskCompose` 统一传入。只使用原 `docker-compose.yml` 会退回旧的嵌入式 etcd 配置，该模式有已知启动竞态；也不要只执行 override 文件。

`deploy/milvus/volumes/etcd-external` 是已有元数据工作目录，配置故意禁止缺失时自动创建空目录。若它不存在，应先恢复匹配的备份，而不是新建空目录后继续。

### 3.3 显式启用 ch04 并核对阈值

在运行应用的同一个普通 PowerShell 中：

```powershell
$env:KB_CORPUS_VERSION = 'ch04'
$env:KNOWLEDGE_COLLECTION = 'knowledge'
$env:HYBRID_COLLECTION = 'knowledge_hybrid_ch04_v2_final'
$env:KNOWLEDGE_STRATEGY = 'hybrid_rerank'
$env:KB_MIGRATION_MANIFEST = 'log/kb/ch04-v2-final-migration.json'

if (-not (Test-Path -LiteralPath 'log/kb/ch04-v2-final-calibration.json')) {
    throw '缺少校准profile，请先按第8节核对备份或重新校准，不直接用开发阈值。'
}
$profile = Get-Content -LiteralPath 'log/kb/ch04-v2-final-calibration.json' -Raw | ConvertFrom-Json
$env:RERANK_MIN_SCORE = [string]$profile.threshold

# 检查profile与当前语料、ID/元数据、模型、Prompt及代码是否匹配。
uv run --no-sync python -c "from pathlib import Path; from scripts.eval_retrieval import _identity, load_profile; load_profile(Path('log/kb/ch04-v2-final-calibration.json'), _identity()); print('profile OK')"
if ($LASTEXITCODE -ne 0) { throw 'profile校验失败，请先处理，不启动为已校准服务。' }
```

当前v2冻结阈值为 `0.25`。从 profile 读取，避免维护时把旧数字当永久配置。`_identity/load_profile` 是本仓库现有校验函数，内部会只读连接数据库核查语料。

环境变量只影响**当前 PowerShell 及其子进程**。新开终端执行维护或评估时，也要设置本节变量。项目默认仍是 `ch03/dense`，直接运行 Uvicorn 而不设置变量会走旧版本。

`KNOWLEDGE_COLLECTION` 必须仍为旧 `knowledge`；新目标由 `HYBRID_COLLECTION` 指定，不能把两者都写成 `knowledge_hybrid`。

### 3.4 检查知识库，启动应用

```powershell
uv run --no-sync python -m app.kb.ingest status
uv run --no-sync python -m app.kb.ingest verify-hybrid
if ($LASTEXITCODE -ne 0) { throw '知识库校验未通过，请先处理。' }
uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Uvicorn 在前台运行，这个终端留着看日志；需要检查时另开 PowerShell。不要把每日启动写成“建库→重建索引→启动”，已有知识库不需要每天重灌。

页面入口：

- 聊天：<http://127.0.0.1:8010/>
- 后台总览：<http://127.0.0.1:8010/admin>
- 知识库与检索自测：<http://127.0.0.1:8010/kb>
- API 文档：<http://127.0.0.1:8010/docs>
- 应用存活检查：<http://127.0.0.1:8010/health>

### 3.5 启动后的检查

```powershell
Invoke-RestMethod 'http://127.0.0.1:8010/health'
Invoke-RestMethod 'http://127.0.0.1:8010/api/kb/overview'
```

`/health` 只说明应用进程能响应，**不验证 MySQL、Milvus 或远程模型**。还应查看 `/kb` 库存及一致性，并进行一条实际问答。

2026-10-21 的基准快照是 MySQL 总 181 块、当前语料 124 块、排除历史 57 块、Milvus 最终集合 124 块、pending 为 0。后续合法新增知识会改变数量，应以一致性和自己的变更记录为准，不为凑回基准数量而删数据。

演示检查可以问“MH-DJ101的胸围是多少”，点引用查看原文与章节，再点满意度反馈。未知 SKU 的拒答可能产生低置信度记录；实际聊天也会保存会话，模型调用会计费。反馈按钮目前仅前端保存，不会写后端表。

## 4. 哪些任务需要单独启动

默认无需任何额外 worker。网页作业由 FastAPI 启动固定 Make 子进程；不要为了运行后台再起一个 Celery 或第二个 Uvicorn。

查看作业状态：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8010/api/jobs'
# 按需查看某个已注册作业：
Invoke-RestMethod 'http://127.0.0.1:8010/api/jobs/kb-vectorize'
```

应用内同一时间只允许一个作业，但这个互斥**不覆盖另一个应用进程或你手工启动的 CLI**。保持一个 Uvicorn 进程、一个 worker；不要同时从网页和终端做录入、向量化、迁移、重建或挖掘。评估期间保持知识库不变。

ch04 下网页“建库”对应的 `kb-build` 会跳过旧 `faq` 种子，按匹配的已有迁移清单录入文档并向量化；它不负责建立首次迁移基线，因此不能替代第 8 节的首次迁移命令。`kb-reset` 在 ch04 被禁止。

确实需要从历史客服会话挖知识时，在单独终端设置第 3.3 节变量后，按需运行一次：

```powershell
uv run --no-sync python -m app.kb.mining --once --limit 5
```

它会读取业务会话、调用模型、产生知识变更，不是健康检查。不要把真实客户对话拿来随意演示。当前没有必须常驻的挖掘进程；如果自己明确启用 `--interval` 循环，关闭时必须回到其终端按 Ctrl+C。

## 5. 正常关闭与重启

### 5.1 只停止/重启应用

1. 不再提交聊天或新作业，在 `/admin` 或 `/api/jobs` 确认没有 `running` 作业；另行确认手工 CLI/评估终端也已结束。
2. 在 Uvicorn 终端按一次 **Ctrl+C**，等待正常关闭日志。
3. MySQL、etcd、Milvus 可以继续运行。要重启应用，在同一终端再次执行第 3.4 节 Uvicorn 命令；新终端需重新设置第 3.3 节环境。

当前网页没有统一取消作业接口，应用重启也会丢失内存中的作业状态，不能假定退出应用会自动取消所有子进程。正常情况下等作业结束再关闭。

终端遗失或端口占用时先定位，不要结束所有 Python 进程：

```powershell
Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
# 得到实际PID后，用Get-Process -Id 该PID核实所属程序。
```

只有确认 PID 是本项目且正常关闭已不可行时，才在任务管理器结束该具体进程；之后重新检查 pending、一致性和作业日志。不要照抄历史文档里的 PID。

### 5.2 关闭项目的数据库容器

完成应用与所有手工任务的关闭后，在项目根目录、具有第 2 节变量的 PowerShell 中：

```powershell
& $taskDocker compose @taskCompose stop -t 120 standalone
if ($LASTEXITCODE -ne 0) { throw 'Milvus停止失败，先检查状态，不继续停元数据服务。' }
& $taskDocker compose @taskCompose stop -t 120 etcd
& $taskDocker compose @taskCompose ps -a
```

这里使用 `stop`，保留容器和数据，下一次仍用第 3.2 节 `up -d --wait` 启动。日常关闭不需要 `down`。`down` 默认会移除容器与项目网络，本项目 bind mount 数据目录仍在，但这不等于备份，更不是日常修复手段。

不要运行 `down -v`、`docker system prune`、删除 `volumes`、删除 WAL 或清空知识库来“关服务”。

### 5.3 关闭 MySQL 与 Docker Desktop

MySQL 若也服务于其他项目，可以保持运行。确认无其他使用方、确实需要关闭时，在管理员 PowerShell 中：

```powershell
Stop-Service -Name MySQL80
Get-Service -Name MySQL80
```

确认项目容器已停止后，可从系统托盘退出 Docker Desktop。不要直接结束 Docker 后台进程代替有序关闭。正常关机前可按这一顺序操作。

## 6. 日常维护

### 状态与日志

```powershell
& $taskDocker compose @taskCompose ps -a
& $taskDocker compose @taskCompose logs --tail 100 standalone
& $taskDocker compose @taskCompose logs --tail 100 etcd
& $taskDocker inspect milvus-standalone --format '{{.State.Status}} | {{.State.Health.Status}} | {{.RestartCount}}'
& $taskDocker inspect mewhelp-etcd --format '{{.State.Status}} | {{.State.Health.Status}} | {{.RestartCount}}'
```

应用日志直接看 Uvicorn 终端；网页作业日志在 `log/acceptance/`，页面也可查看日志尾部。`log/`、`.env` 和实际数据目录不进入 Git，但不表示可以随便删除。向外发日志前检查凭据、连接信息和客户消息。

### 向量化补漏

在已设置 ch04 环境的维护终端中，先查看再补跑：

```powershell
uv run --no-sync python -m app.kb.ingest status
uv run --no-sync python -m app.kb.ingest vectorize
uv run --no-sync python -m app.kb.ingest verify-hybrid
```

这会写新集合并调用嵌入 API。失败时保留状态继续排查，不手工把 MySQL 的 pending 改为 done。若 pending 为 0，不必每天运行向量化。

ch04 下 `rebuild` 会重新嵌入当前 eligible 范围，保留集合而不是 drop，但仍有模型费用和数据写入；只在查明需要后备份并执行，不当作通用修复第一步。旧 ch03 路径的重建行为不同，运行前务必确认环境变量。

### 知识修改与 profile 失效

- `/kb` 手工新增、挖掘知识或修改原文/元数据，都可能改变实际语料指纹；新增后原冻结 profile 不能自动视为仍有效。
- `data/kb/ch04/` 六份文件与正式评估标签、迁移清单的 source hash 绑定，不能直接改文件后强行覆盖旧清单继续用原报告。
- 需要更新冻结源时，先制定新语料版本、清单和评估标签方案，备份后迁移，再重新校准/评估；不是删除 JSON 后重新猜 ID 基线。
- 改模型、Prompt 或相关后端代码同样需要核对 profile/cache。正式评估期间暂停知识写入。

### 常规检查频率

- 每次启动：服务健康、profile 校验、知识库状态和一条实际问答。
- 每次数据/配置变更前：做配套备份，记录旧值与恢复点，勿把密钥写入记录。
- 每次代码修改后：运行相关测试；涉及完整链路时再跑全量 `uv run --no-sync pytest -q`。测试需要真实 MySQL/Milvus，维护窗口运行；不要同时修改业务数据。
- 定期检查磁盘剩余空间、Docker 占用、模型供应商额度；不要通过盲目清理卷腾空间。

## 7. 配套备份与恢复

**Git 不会保存实际数据库、Milvus 卷、本地密钥或 `log/` 中的迁移清单。** 完整备份必须包含同一维护窗口下的 MySQL、Milvus 数据、外部 etcd、对应源码/配置和清单。仅有报告或旧嵌入式 etcd 目录不够。

### 7.1 推荐的本机冷备步骤

先停止接收请求，按第 5.1 节停应用和所有作业，并禁止其他工具直接写这两个数据库。MySQL 此时仍保持运行，便于导出。以下备份目录放在仓库外；默认示例仍在 E 盘，完成后另复制到独立磁盘/受控备份位置，防止同盘故障。

```powershell
$backupRoot = 'E:\MewHelp-Backups'
$backupStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupPath = Join-Path $backupRoot $backupStamp
New-Item -ItemType Directory -Path $backupPath -ErrorAction Stop | Out-Null

$dumpFile = Join-Path $backupPath 'melhelp.sql'
& 'E:/MySQL/bin/mysqldump.exe' -h 127.0.0.1 -P 3306 -u root -p --single-transaction --routines --events --triggers --hex-blob --no-tablespaces --set-gtid-purged=OFF "--result-file=$dumpFile" melhelp
if ($LASTEXITCODE -ne 0) { throw 'MySQL导出失败，不能把这个目录标为完整备份。' }

& $taskDocker compose @taskCompose stop -t 120 standalone
if ($LASTEXITCODE -ne 0) { throw 'Milvus未正常停止，不能继续冷拷贝。' }
& $taskDocker compose @taskCompose stop -t 120 etcd
if ($LASTEXITCODE -ne 0) { throw 'etcd未正常停止，不能继续冷拷贝。' }

Copy-Item -LiteralPath 'deploy/milvus/volumes' -Destination (Join-Path $backupPath 'volumes') -Recurse -ErrorAction Stop
Copy-Item -LiteralPath 'deploy/milvus/docker-compose.yml','deploy/milvus/docker-compose.external-etcd.yml','deploy/milvus/user.yaml','deploy/milvus/embedEtcd.yaml' -Destination $backupPath -ErrorAction Stop
Copy-Item -LiteralPath 'log/kb/ch04-v2-final-migration.json','log/kb/ch04-v2-final-calibration.json' -Destination $backupPath -ErrorAction Stop
```

`--result-file` 避免 PowerShell 输出重定向造成备份编码问题；不要改成 `mysqldump > dump.sql`。`--single-transaction` 主要保证 InnoDB 的一致性，备份期间不进行 DDL；这里还先停止所有项目写入以保持 MySQL 与 Milvus 对应。

另保存当前完整工作区：`app/`、`data/`、`scripts/`、`tests/`、`docs/`、`reports/`、`dev-notes/`、`deploy/` 配置、`Makefile`、`pyproject.toml`、`uv.lock`、`.python-version`。本项目仍有未提交改动，仅记录 Git HEAD 不够。备份时不要遗漏未跟踪源码；`.venv` 可按锁文件重建，不必备份。

`.env` 应放入受控、加密或访问受限的备份中，不能混入公开压缩包。SQL 备份也包含会话等业务数据，应按相同标准保护。

记录备份时间、源码版本/工作区快照、MySQL 与 Milvus 版本、集合名及文件校验和。例如：

```powershell
Get-FileHash -LiteralPath $dumpFile -Algorithm SHA256
Get-ChildItem -LiteralPath (Join-Path $backupPath 'volumes') -Recurse -File |
    Get-FileHash -Algorithm SHA256 |
    Export-Csv -LiteralPath (Join-Path $backupPath 'volumes-sha256.csv') -NoTypeInformation -Encoding UTF8
```

确认备份完整后，按第 3 节恢复服务。冷拷贝命令只适用于两个容器已正常停止；不能复制正在写入的 etcd 数据文件并声称是完整备份。在线备份需要另行设计应用一致性快照，本手册不混用在线与冷备步骤。

### 7.2 恢复原则

恢复会覆盖当前业务状态，应在明确选择恢复点、确认影响后进行；先备份当前事故现场，不能把恢复当每日启动步骤。

1. 停应用、全部作业及 Milvus/etcd，禁止其它写入。
2. 选择同一备份包中的 SQL、整个 `volumes`、Compose/存储配置、源码版本、迁移清单和 profile。不要混合不同时刻的 etcd 与向量数据。
3. 由数据库管理员将 SQL 恢复到确认的数据库；示例通过 MySQL 客户端的 `SOURCE` 导入，避免 PowerShell 的 `<` 重定向问题。导入可能覆盖现有表和数据，执行前检查备份内容及目标：

```powershell
& 'E:/MySQL/bin/mysql.exe' -h 127.0.0.1 -P 3306 -u root -p melhelp
```

在 MySQL 客户端内执行，将路径替换为实际选中的备份目录：

```sql
SOURCE E:/MewHelp-Backups/20261021-120000/melhelp.sql;
```

4. 将当前目录另存为事故现场，恢复备份的 `volumes` 与配置，不向运行中的目录覆盖文件，也不对未经核实的路径递归删除。
5. 恢复匹配的源码、`log/kb/ch04-v2-final-migration.json`、profile 和受保护的配置，按第 3 节启动。
6. 检查 MySQL 登录、Milvus 健康、`verify-hybrid`、profile 指纹及引用/拒答入池，再恢复业务访问。新电脑上存储权限、路径与模型配置不相同时，profile 可能需重新校准，不能跳过错误。

迁移前的 `deploy/milvus/volumes/milvus/etcd` 仅是历史保留目录，不能代替当前 `etcd-external` 备份。旧嵌入式启动竞态尚在，基础设施回退不等于“只换一个 -f”。详情见[Milvus 运行说明](../deploy/milvus/OPERATIONS.md)。

## 8. 初始化、迁移、评估与版本回退

### 已有数据的这台电脑

当前已迁移，不需要再初始化。日常执行 `status` 和 `verify-hybrid` 即可。必须保留 `log/kb/ch04-v2-final-migration.json`；目标集合存在但清单丢失时应恢复原清单，不能删除集合或重新猜基线。

### 确认需要首次迁移或按清单续跑

仅在备份、检查目标环境后，于已配置 ch04 的终端执行：

```powershell
uv run --no-sync python -m app.kb.ingest preview --corpus-version ch04
uv run --no-sync python -m app.kb.ingest migrate-hybrid --dry-run
# 确认dry-run目标/排除范围正确后，才执行真实写入：
uv run --no-sync python -m app.kb.ingest migrate-hybrid
uv run --no-sync python -m app.kb.ingest verify-hybrid
```

迁移保留旧集合，但会录入并重新嵌入当前语料；重跑仍有调用费用，不是只读命令。迁移/schema/hash 检查失败时按错误恢复材料或备份，不清库绕过检查。项目不负责创建 MySQL 表。

### 真实评估与重新校准

不是启动前每天都跑。它们会调用远程模型、产生费用和报告；评估 CLI 不写业务会话或低置信度池。运行前备份已有报告和 profile，尤其不要用新结果覆盖旧报告后继续引用旧成绩。

```powershell
uv run --no-sync python scripts/eval_retrieval.py --validate-only
uv run --no-sync python scripts/eval_retrieval.py --split calibration --calibrate --profile log/kb/ch04-v2-final-calibration.json --output-dir reports/ch04/v2/calibration-compare --cache-dir log/kb/ch04-v2-final-eval-cache
uv run --no-sync python scripts/eval_retrieval.py --split test --mode compare --output-dir reports/ch04/v2/test-compare-recovered --cache-dir log/kb/ch04-v2-final-eval-cache
uv run --no-sync python scripts/eval_retrieval.py --split test --mode deployment --profile log/kb/ch04-v2-final-calibration.json --output-dir reports/ch04/v2/test-deployment --cache-dir log/kb/ch04-v2-final-eval-cache
```

24 条校准用于选择阈值，72 条测试只用于报告，不能拿测试集调参。默认目录为 `reports/ch04/calibration-compare`、`test-compare`、`test-deployment`，每份都有汇总、逐题明细和配置；缓存是 `log/kb/ch04-eval-cache`。

命令结束后检查退出码、报告行数和服务/评审失败数，不能把“进程退出”当成成功。中断时先保留缓存，确认没有旧进程仍在运行后再恢复；不要同时启动两份同目标评估。缓存键包含 QueryPlan，重跑不保证全部命中；不要任取缓存文件拼报告。

### 回退到 ch03 的应用路径

先停 FastAPI 与作业，在准备启动的终端设置：

```powershell
$env:KB_CORPUS_VERSION = 'ch03'
$env:KNOWLEDGE_STRATEGY = 'dense'
$env:KNOWLEDGE_COLLECTION = 'knowledge'
uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port 8010
```

这是选择旧检索/聊天路径，不是数据库时间点恢复；旧模型/规则冲突也可能重新出现。新集合与数据仍保留，不能为回退执行 reset/drop。回到 ch04 时重新执行第 3.3 节，不要只改一个变量。

## 9. 常见问题

- **Docker 客户端找不到**：执行第 2 节绝对路径；路径变了先确认安装位置。`docker info` 报引擎不可达时先等 Docker Desktop 就绪。
- **Milvus 启动就退出，日志 leader changed/134**：确认始终合并两份 Compose、`mewhelp-etcd` 健康且 `etcd-external` 正确存在；保留日志，勿删 WAL、原目录或反复切回嵌入模式。
- **3306 通但数据库 Access denied**：核对数据库账号/密码、DB_URL 的 URL 转义和本地权限。不要打印连接串；MySQL 端口打开不代表登录成功。
- **页面 ONLINE 但聊天失败**：`/health` 不检查依赖。依次看 MySQL 登录、Milvus 健康/一致性、模型密钥、供应商额度/限流/网络及后台错误日志。
- **网页后台报找不到 make**：确认 GNU Make 路径，给应用启动终端设置 `MAKE_EXE` 后重启；CLI `uv run ...` 不依赖 Make。
- **网页建库失败**：检查迁移清单是否缺失、源文件 hash 是否变化，以及嵌入是否失败；首次初始化按第 8 节迁移。ch04 的 `kb-build` 已跳过旧 FAQ，但直接运行 `faq` 子命令仍会被拒绝。
- **profile/source hash 不匹配**：核对是否改了语料、ID/元数据、模型、Prompt 或后端代码；恢复匹配备份或完成新版本评估，不能只改 JSON 里的 hash/阈值让检查通过。
- **知识库数不一致或 pending 不为 0**：先检查模型嵌入/数据库/存储日志，再按第 6 节补向量化及 verify；多余 ID 或 stale 需查明原因，禁止自动删行凑数。
- **8010 被占用**：按第 5.1 节定位具体 PID；可以明确改为 8012 做临时演示，浏览器/API地址也要一起换，不能误连旧实例。
- **停止 Uvicorn 后作业还在**：回到手工任务终端或核实具体子进程；作业状态仅内存，不以应用重启后的 idle 证明旧进程不存在。
- **知识题被拒答但库里有答案**：v2已修复无引用引导语和知识意图误选商品工具；仍可能因分数门槛或证据不充分而拒答，当前72部署误拒答2/60，先看对应报告与日志，不通过禁用质量关口“修好”。

## 10. 资料入口与核验依据

- [ch04 交付与真实指标](../reports/ch04/summary.md)
- [功能演示与评估命令](../reports/ch04/demo.md)
- [当前维护/交接入口](CH04_CURRENT_PROGRESS.md)
- [开发留痕](../dev-notes/ch04.md)
- [Milvus 专项运维](../deploy/milvus/OPERATIONS.md)

本手册按仓库源码、`.env.example`、Makefile、合并后的 Compose 配置及本机服务/CLI 帮助核对，具体工具语义通过 Context7 查询官方资料：

- [Docker Compose 多文件合并](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)
- [Compose stop](https://docs.docker.com/reference/cli/docker/compose/stop/) 与 [down](https://docs.docker.com/reference/cli/docker/compose/down/)
- [uv 环境同步与锁文件](https://docs.astral.sh/uv/concepts/projects/sync/)
- [FastAPI 手动运行](https://fastapi.tiangolo.com/deployment/manually/)
- [MySQL Windows 服务](https://dev.mysql.com/doc/refman/8.0/en/windows-start-service.html)
- [mysqldump 与 Windows 输出注意事项](https://dev.mysql.com/doc/refman/8.0/en/mysqldump.html)
- [SQL 备份恢复](https://dev.mysql.com/doc/refman/8.0/en/reloading-sql-format-dumps.html)

本机后台页和 API 仍无鉴权，仅按本地演示使用；本文没有把它配置成公网部署。备份 SQL、真实会话、`.env` 和数据库日志均需受控保存。

