# ch04 v2 交付报告（2026-10-21）

当前交付为124块古着知识与误拒答修复版本。原65块版本的源、实现、标签和报告见[v1归档](v1/summary.md)。本目录除summary.md、demo.md和v2/外的旧数值文件是v1历史，不能作为当前成绩。未提交、推送、合并、DDL或清库。

## 实际修复与知识发布

退货流程有召回却因无引用引导语整体拒答：生成最多一次格式修复，共用45秒预算，正文事实不可变，仍验证全部引用和禁止承诺。知识尺寸问法误选商品工具：在执行前按单轮知识意图固定FAQ，保留业务工具路径和单轮上限。

六份data/kb/ch04古着文档从65扩至124块，覆盖购物支付、售后流程、寄回验收、物流开票、成色测量护理与会员积分；根目录六份宠物参考按用户指令删除，三份中文基准保留。价保草稿无依据期限已删除。新集合knowledge_hybrid_ch04_v2_final；MySQL181总/124当前/57历史排除/pending0。原172行及48/65/初轮124三个旧集合完整哈希保留，见[迁移](v2/migration.json)。

## 四策略真实结果

正式合成集ch04-v2共96题，保留v1原问题/ground-truth/24校准72测试分割，仅重绑同章节来源指纹。72测试为60可答/12不可答、六桶各12。比较关闭专属分数门控，同题四策略共享QueryPlan/embedding。

- dense：Recall@10=1.0000，MRR@10=0.9299；Faithfulness=0.9973（62个回答）；误拒答1.67%，正确拒答75.00%。
- bm25：Recall@10=0.9500，MRR@10=0.8578；Faithfulness=1.0000（62个回答）；误拒答1.67%，正确拒答75.00%。
- hybrid：Recall@10=0.9750，MRR@10=0.9233；Faithfulness=1.0000（62个回答）；误拒答1.67%，正确拒答75.00%。
- hybrid_rerank：Recall@10=1.0000，MRR@10=0.9440；Faithfulness=1.0000（63个回答）；误拒答0.00%，正确拒答75.00%。

Recall/MRR分母60，误拒答分母60，正确拒答分母12，覆盖率分母72，标注事实分母115。全部服务/judge/事实评审失败0。重排MRR比dense提高约1.42个百分点，最终Recall@10同为1；124块小语料不能推出大库全面胜出。完整按桶/难度数字及分母见[比较报告](v2/test-compare-recovered/summary.md)和[288行原始结果](v2/test-compare-recovered/details.jsonl)。

[恢复清单](v2/compare-recovery.json)记录286条指纹匹配缓存、2条补跑，按策略覆盖选择计划、同数取早，不按分数挑选。test-compare保存网络故障现场，attempt-402保存模型额度故障，不充作有效质量成绩。

## 冻结部署与日常验收

仅24校准题按既定17候选规则选择0.25，[冻结profile副本](v2/calibration-profile.json)。72部署：59回答/13拒答，Faithfulness=0.99758（59回答分母）；误拒答2/60=3.33%，正确拒答11/12=91.67%，标注事实108/115，无服务/评审失败。见[校准](v2/calibration-compare/summary.md)、[部署](v2/test-deployment/summary.md)。没有测试集调参；两份报告独立生成，不能当作仅阈值不同的严格配对实验。

额外[15条日常开发回归](v2/general-validation.json)使用实际hybrid_rerank/.25，12可答/3不可答全部符合终态标签；12回答的标注概念已逐条核对。不是额外保留测试，也不混入正式96题。截图退货原句、下单与DJ101胸围110的[真实聊天](v2/chat-acceptance.json)均带引用；未知SH299/新疆精确费用进入self_check、月球许可进入retrieval_low_conf。BM25 Top3内命中DJ101/DJ102各自规格，DJ101并非Top1，原始返回可追溯。

真实6个引用与MySQL原文/章节逐项一致。前端引用弹层→原文定位与一次👍/👎沿用已验收实现，当前后端未改变前端契约；本轮没有重新截图，旧[screenshots](v1/screenshots/)只证明当时前端交互，不冒称v2截图。反馈仅sessionStorage，不发后端POST。

## 验证、人工核查和局限

Native集中后端复核和本轮路由增量独立复核已完成；引用格式修复无Critical/Important，语料价保Important已纠正，路由增量无Critical/Important。

[最终回归](v2/final-tests.json)：559 passed in39.23s，无skip，五张持久化表完整快照不变。一轮Windows临时manifest原子替换权限故障已保留报告，原样重跑全绿。[最终核查](v2/final-verification.json)确认24/288/72报告/profile与当前语料/模型/Prompt/离线代码identity一致，124块MySQL/Milvus一致，历史哈希不变，8010健康200；交付文本已知凭据模式无发现，未读取.env，不代表完整安全审计。

[人工核查](v2/manual-audit.md)覆盖每桶05/06四策略48个输出、部署逐桶样例、全部冲突/高分库外及Faithfulness扣分。两道口语部署题分数低于.25而误拒答；否定到账/必达/专票等正文虽有支持，can_answer=true仍按固定不可答标签计错误放行。metadata-13部署中的卫衣规则外推被judge扣分；证书“没有任何机构记录”的否定措辞也存在过绝对风险。原分数保留。

同模型judge有相关偏差，Faithfulness高分不等于全部引用完美或100%可靠。只做单轮改写，无指代消解/多轮改写；business混合知识题在单工具已用且缺证据时仍保守拒答。/admin、/kb、/api维持演示无鉴权边界。

## 启动维护

当前演示 http://127.0.0.1:8010 。完整环境、profile校验、功能演示与评估复跑见[demo.md](demo.md)；全服务启动、关闭、日志、备份恢复与回退见[运行维护手册](../../docs/OPERATIONS.md)。必须显式设置ch04、最终集合、hybrid_rerank和最终迁移清单，再从profile读取阈值；不要改.env或混用初轮v2集合。

原文/ID/元数据、模型、Prompt或离线代码变更后须重新校准/评估，不可强改profile哈希绕过；保留迁移清单与profile备份。全过程见[dev-notes/ch04.md](../../dev-notes/ch04.md)。

用量说明：发生一次引用修复时，answer_usage只记录最后一次API响应的token，未合计两次调用，不据此估算总费用。
