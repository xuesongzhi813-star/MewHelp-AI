# ch04 交付报告（2026-10-21）

已完成本章混合检索、重排、引用与拒答入池、前端引用/一次反馈、真实评估闭环。保留未提交工作区；没有推送、合并、DDL或清库。

## 四策略真实对比

固定ch04-v1合成集96题：24校准、72测试；测试60可答/12不可答，六种query各12题。每题共享QueryPlan与embedding，compare关闭专属分数门控。288行中252条为指纹一致的真实缓存、36条本次续跑，来源[恢复清单](compare-recovery.json)。

|策略|Recall@5|Recall@10|MRR@10|Faithfulness (回答分母)|回答覆盖率|误拒答率|正确拒答率|标注事实覆盖率|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|dense|1.0000|1.0000|0.9528|1.0000 (54)|75.00%|16.67%|66.67%|78.26%|
|bm25|0.9583|0.9667|0.8908|1.0000 (56)|77.78%|10.00%|83.33%|88.70%|
|hybrid|0.9750|1.0000|0.9521|1.0000 (54)|75.00%|15.00%|75.00%|82.61%|
|hybrid_rerank|1.0000|1.0000|0.9700|1.0000 (55)|76.39%|13.33%|75.00%|85.22%|

Recall/MRR分母60；回答覆盖分母72；误拒答分母60；正确拒答分母12；标注事实覆盖分母115。全部服务/judge/required-facts失败0。candidate Recall@50：dense/hybrid/rerank为1，BM25为0.9917；rerank最终@50不适用。各桶与难度及分母见[完整比较](test-compare/summary.md)、[汇总JSON](test-compare/summary.json)、[288行明细](test-compare/details.jsonl)。

混合加重排将MRR@10从纯向量0.9528提升至0.9700（约1.72个百分点），Recall@5优于未重排混合0.975→1。这个65块的小语料已让纯向量Recall饱和，因此不能由本次结果断言大库全面胜出。BM25的回答覆盖与标注事实覆盖更高；所有策略生成质量仍受引用格式与自评波动影响。

## 冻结部署评估

仅24校准题在17个候选中选阈值0.65，[冻结profile](calibration-profile.json)。72条部署：Recall@10=1，MRR@10=.97；44回答/28拒答，Faithfulness=1仅44个回答分母；60可答中17误拒答（28.33%），12不可答中11正确拒答（91.67%），事实覆盖82/115。服务/judge失败0。较高阈值降低错误放行并增加误拒答，当前取值是固定校准结果，未用测试集调参。

原始[校准报告](calibration-compare/summary.md)与[部署报告](test-deployment/summary.md)分开保存，不能把compare与deployment当成仅阈值不同的确定性配对实验：生成模型仍会波动。

## 验收与数据保护

- [真实迁移](migration.json)：MySQL113总/65当前/48历史排除，新Milvus65，pending0；旧48行和48向量完整哈希前后相同；本轮verify仍逐ID/文本/元数据一致。
- [聊天验收](chat-acceptance.json)：BM25命中DJ101/DJ102；未知SH299与新疆费用缺失拒答入self_check，月球采矿许可拒答入retrieval_low_conf；仅合成唯一会话。
- 引用[真实弹层截图](screenshots/real-citation.png)、[原文定位](screenshots/real-original.png)、[一次反馈锁定](screenshots/real-feedback.png)及手机截图均保留。反馈只在前端sessionStorage，不写后端。
- [本轮完整回归](final-tests.json)：551 passed in37.23s，无skip；会话/消息/工单/低置信度/知识表测试前后完整快照哈希相同。首次527passed24errors为测试夹具漏了新子表的回滚内清理顺序，修复后通过，产品代码未变。

## 人工核查与已知限制

[人工记录](manual-audit.md)覆盖每桶05/06、四策略48个输出及全部13个标签冲突/高重排库外项，[异常原文包](test-compare/audit-packet.json)可追溯。保留固定标签：解释无法保证到账/物流/开票的正文虽然有支持，can_answer=True仍按不可答题计错。多余字面[N]与个别引用位置精度未被Faithfulness揭示；同一生成模型做judge存在相关偏差，Faithfulness=1不代表100%准确、全部引用完美或零拒答成本。

已知SKU可能被route选错工具而保守拒答；本章单轮工具上限保持。前端反馈不跨设备同步，没有后端数据飞轮。只做单轮改写，不做指代消解或多轮改写。/admin、/kb、/api仍按演示无鉴权边界。

## 运行与后续修改

见[完整演示命令](demo.md)，当前本机演示 http://127.0.0.1:8010 。保留KNOWLEDGE_COLLECTION=knowledge，另设HYBRID_COLLECTION=knowledge_hybrid、KB_CORPUS_VERSION=ch04、KNOWLEDGE_STRATEGY=hybrid_rerank；阈值从profile读取。默认配置保留ch03以便显式回退，不修改真实.env。

必须保留log/kb/ch04-migration.json与校准profile；原文、ID/元数据、模型、Prompt或后端代码变化使profile/cache失效，须重新评估，不能引用旧报告充新实现。后续可独立改进拒答终态语义、引用格式稳定性及阈值分桶，但本次没有拿保留测试调优。开发全过程见../../dev-notes/ch04.md。
