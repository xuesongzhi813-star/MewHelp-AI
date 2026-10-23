# ch04 演示与复跑（PowerShell，仓库根目录）

本章使用古着平台六份材料、65块和12个合成SKU。MySQL是原文权威；Milvus保留旧knowledge48块，新knowledge_hybrid65块。无DDL，无清库。2026-10-21已完成24条校准、72条四策略对比和72条部署评估，指标及限制见[交付报告](summary.md)。本次恢复演示服务在8010，先前截图8012是2026-10-20验收端口。

## 服务与配置

Docker启动须合并两个文件，不能只启动base退回embedded etcd：

```powershell
& 'C:/Users/20968/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe' compose -f deploy/milvus/docker-compose.yml -f deploy/milvus/docker-compose.external-etcd.yml up -d
$env:KB_CORPUS_VERSION='ch04'
$env:KNOWLEDGE_COLLECTION='knowledge'
$env:HYBRID_COLLECTION='knowledge_hybrid'
$env:KNOWLEDGE_STRATEGY='hybrid_rerank'
```

模型密钥使用已有.env；不要输出.env或把KNOWLEDGE_COLLECTION也改成新集合。首次运行需确认目标和数据影响。以下迁移只写新集合及当前原文，重跑会重新嵌入当前块，不清空历史：

```powershell
uv run --no-sync python -m app.kb.ingest preview --corpus-version ch04
uv run --no-sync python -m app.kb.ingest migrate-hybrid --dry-run
uv run --no-sync python -m app.kb.ingest migrate-hybrid
uv run --no-sync python -m app.kb.ingest verify-hybrid
uv run --no-sync python -m app.kb.ingest status
```

log/kb/ch04-migration.json保留首次ID基线与来源hash，必须备份；已有新集合时缺清单会拒绝猜测恢复。正常运行无须每次迁移。不要运行kb-reset。

## 评估与冻结阈值

```powershell
uv run --no-sync python scripts/eval_retrieval.py --validate-only
uv run --no-sync python scripts/eval_retrieval.py --split calibration --calibrate
uv run --no-sync python scripts/eval_retrieval.py --split test --mode compare
uv run --no-sync python scripts/eval_retrieval.py --split test --mode deployment --profile log/kb/ch04-calibration.json
```

分别生成calibration-compare、test-compare、test-deployment下的summary.md/json、details.jsonl、config.json。24校准只用于选择阈值，72测试不重新调参。真实语料/ID/元数据、模型、Prompt和代码hash改变时cache/profile失效，需重新校准；评估途中不录入或向量化知识。拒答Faithfulness为N/A，服务失败单列。缓存位于log/kb/ch04-eval-cache；删除缓存意味着重新产生模型用量。

```powershell
$profile = Get-Content log/kb/ch04-calibration.json -Raw | ConvertFrom-Json
$env:RERANK_MIN_SCORE = [string]$profile.threshold
uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port 8010
```

浏览 http://127.0.0.1:8010 ：
- 问「MH-DJ101的胸围是多少」，点击答案[N]查看原文、章节，点定位原文进入/kb?chunk_id=…。
- 再问「MH-DJ102的胸围是多少」，确认相似SKU不会串来源；/kb的检索自测选BM25查询两个SKU。
- 正常回答左下角👍/👎，点一次点亮、已反馈并锁定；两条消息独立，纯前端sessionStorage，不发反馈POST。
- 新会话问「MH-SH299的胸围是多少」，明确拒答；再问「新疆地区实付99元的运费精确是多少」，缺失地区费用应拒答。只查询本次验收会话对应low_confidence_questions检查原话、入口、原因及时间，不浏览真实客户对话。

低分与生成自评是不同闸门，不能拿rerank高分当答题置信概率。没有保证到账、真伪或必然合身等承诺。

## 回归与回退

```powershell
uv run --no-sync pytest -q
# 停止当前演示进程后，独立进程选择旧路径：
$env:KB_CORPUS_VERSION='ch03'
$env:KNOWLEDGE_STRATEGY='dense'
$env:KNOWLEDGE_COLLECTION='knowledge'
uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port 8010
```

回退只切配置，不drop集合或删MySQL历史。/admin、/kb、/api/*沿用演示无鉴权边界。本章不做指代消解/多轮改写；单轮业务工具已占用时知识部分缺证据会拒答。开发过程见dev-notes/ch04.md。
