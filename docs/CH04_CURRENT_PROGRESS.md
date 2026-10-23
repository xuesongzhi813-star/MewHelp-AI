# ch04 当前状态与维护入口

更新时间：2026-10-21。原Task1–14已交付；随后用户批准的v2误拒答修复/古着知识扩写已实现，四策略、部署、日常回归、真实聊天和文档收尾均已完成，本章v2 finish。当前分支ch02-function-calling，HEAD仍513c7b9d09f9e697e3ba7e11ba4c49bca140e8e2。没有提交、推送、合并、DDL或清库，用户暂存及ch03/admin改动保留。

## 当前入口

先读AGENTS.md、AI_HANDOFF.md顶端、dev-notes/ch04.md阶段30起及已批准spec/plan的v2 Ruling。旧阶段26–28及reports/ch04/v1是历史成绩。当前报告入口reports/ch04/summary.md，运行维护见docs/OPERATIONS.md和reports/ch04/demo.md。

## 已完成

- 退货流程证据存在但无引用引导语导致拒答：最多一次引用格式修复，同45秒预算，事实文字不可变，仍复验引用和禁止承诺。
- 知识尺寸被路由到商品工具导致无证据拒答：知识意图在执行前选FAQ，business意图保持原单个业务工具；不增加工具轮次。
- ch04六份古着文档扩展至124块，根目录六份宠物参考已按指令删除，原中文三份保留。价保草稿误带7天规则已纠正，初轮v2不启用。
- 最终集合knowledge_hybrid_ch04_v2_final，清单log/kb/ch04-v2-final-migration.json；MySQL181总/124当前/57排除/pending0。原172行及旧48/65/初轮124三个集合完整哈希未改，禁止重建基线或清库。
- 最新完整559 passed in39.23s，五表完整快照不变。Windows临时文件拒绝访问失败报告保留，原样重跑全绿。引用修复独立评审无Critical/Important，语料价保Important已纠正；新增路由增量独立复核无Critical/Important。
- 96题标签及24校准/72测试分割保持，只按同章节重绑来源为ch04-v2；最终profile log/kb/ch04-v2-final-calibration.json冻结0.25。旧v1阈值0.65不再使用。
- 72部署无服务/评审失败：59回答，Faithfulness约.99758（59分母），误拒答2/60、正确拒答11/12、事实108/115。没有拿测试集调参。
- 15条日常hybrid_rerank开发回归全部符合标签，12回答标注概念已逐条核对。真实8010退货原句/下单/DJ101均有引用；BM25两相似SKU正确；未知SH299/新疆费用self_check，月球许可retrieval_low_conf入池，仅合成会话。

## 当前服务与已完成验收

当前8010服务已显式hybrid_rerank恢复（当时PID43380/工具session54213，状态需实际复核）。同一PowerShell设置KB_CORPUS_VERSION=ch04、KNOWLEDGE_COLLECTION=knowledge、HYBRID_COLLECTION=knowledge_hybrid_ch04_v2_final、KNOWLEDGE_STRATEGY=hybrid_rerank、KB_MIGRATION_MANIFEST=log/kb/ch04-v2-final-migration.json，再从最终profile读取RERANK_MIN_SCORE。不要改.env，也不能只设集合漏策略。完整命令见demo.md。

四策略有效目标v2/test-compare-recovered；test-compare为网络故障现场、attempt-402为模型额度故障。用户已恢复额度，真实探针parsed=True；成功缓存按完整identity复用，不按成绩择优。补跑已完成：286缓存+2补跑共288行，无服务/评审失败。24/288/72报告与冻结profile一致；每桶2题及全部冲突、真实6个引用、历史哈希、凭据模式和diff已核查。见v2/compare-recovery.json、manual-audit.md、final-verification.json。

Docker需合并base与external-etcd两个Compose文件，不重做修复/reset/prune/drop。禁止DDL；默认ch03+dense回退仍保留，切配置而不删数据。前端沿用已验收的引用弹层与sessionStorage一次反馈，无需重做前端TDD/review；不做指代消解、多轮改写。

## 最终结论

v2已完成，无待续跑评估或待实现任务；误拒答2/60、正确拒答11/12与生成外推/终态标签冲突仍是公开限制，并非零风险。后续优化需新需求，不自动开启下一阶段。当前分支保持未提交，执行账本及数据备份保留。
