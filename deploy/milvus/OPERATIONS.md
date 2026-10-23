# Milvus 本机运行说明

2026-10-20：用户批准将嵌入式 etcd 拆为独立服务，保留 Milvus 3.0.0、etcd 3.5.23 与本地向量存储。原因是嵌入式 etcd 选举尚未完成时 Milvus 已访问元数据，触发 leader changed panic（退出码134），并非 Docker 标记的 OOM。

## 日常启动

在项目根目录 PowerShell 执行；本机 Docker 不在当前终端 PATH，使用实际安装位置：

```powershell
$taskDocker = 'C:/Users/20968/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
& $taskDocker compose -f deploy/milvus/docker-compose.yml -f deploy/milvus/docker-compose.external-etcd.yml up -d --wait --wait-timeout 120
& $taskDocker compose -f deploy/milvus/docker-compose.yml -f deploy/milvus/docker-compose.external-etcd.yml ps
Invoke-RestMethod http://127.0.0.1:9091/healthz
```

必须带两个 `-f`；只用原 `docker-compose.yml` 会切回旧的嵌入式 etcd。外部 etcd 健康后 Compose 才启动 Milvus。不要一边启动旧嵌入模式、一边运行外部模式；两份元数据会分叉。

## 数据位置与备份

- 当前外部 etcd 工作目录：`deploy/milvus/volumes/etcd-external`。
- 迁移前原 etcd 保留目录：`deploy/milvus/volumes/milvus/etcd`，启动外部模式时 Milvus 不再使用它。
- 现有向量/本地存储仍在 `deploy/milvus/volumes/milvus`。
- 初始三文件复制的长度及 SHA-256 清单：`log/diagnostics/etcd-external-copy.json`。外部服务启动后会正常改写自己的副本，清单代表迁移时刻，不是当前工作目录的实时校验结果。

原元数据目录保留用于事故回退，不要覆盖新目录来“重新同步”。当前备份应包含外部 etcd 与对应向量数据；运行中的 etcd 应用一致性快照，不直接复制活动数据库作为完整备份。禁止无理由执行 reset、prune、删除卷或删除 WAL。

## 查看状态和日志

```powershell
& $taskDocker inspect milvus-standalone --format '{{.State.Status}} | {{.State.Health.Status}} | {{.RestartCount}}'
& $taskDocker inspect mewhelp-etcd --format '{{.State.Status}} | {{.State.Health.Status}} | {{.RestartCount}}'
& $taskDocker logs --tail 100 milvus-standalone
```

不要公开未经检查的日志；日志可能含地址或配置。服务不健康时先保留错误证据，不以无限自动重启代替诊断。

## 回退边界

本次只复制元数据、重建容器配置并启动原版本服务，未删除任何数据。回退必须先停止使用外部 etcd 的 Milvus，并明确选择需要恢复的同一时刻元数据和向量数据；旧嵌入模式仍有已知启动竞态，不能承诺切回即恢复，也不能用迁移前元数据覆盖已产生新写入的当前状态。
