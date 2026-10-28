# ch09真实评估结果

固定原96题，24题校准、72题保留；原文件哈希保持不变。下列两轮使用相同标签、同一profile与评分协议，均重新调用真实生成及judge，不复用回答/评分缓存。

## 轮次 #12

状态：success；完成72题；核心服务失败0，judge失败0。
Recall@5 0.741667；Recall@10 0.750000；MRR@10 0.716667；检索有效分母60。
Faithfulness 0.972701；有效分母33；回答覆盖45.83%；误拒答率46.67%。
标注 labels-d34f5273eddea9bf2f11；profile b844750af71588a5a6cfe7cb2df4aed628b51618ea317ca3b6921956ffca107d；judge 055057ebd5dd2e627088b5c04e07dd19fbee35f6f8458c10862416c0adf51ad8。

## 轮次 #13

状态：success；完成72题；核心服务失败0，judge失败0。
Recall@5 0.791667；Recall@10 0.800000；MRR@10 0.766667；检索有效分母60。
Faithfulness 0.991111；有效分母35；回答覆盖48.61%；误拒答率43.33%。
标注 labels-d34f5273eddea9bf2f11；profile b844750af71588a5a6cfe7cb2df4aed628b51618ea317ca3b6921956ffca107d；judge 055057ebd5dd2e627088b5c04e07dd19fbee35f6f8458c10862416c0adf51ad8。

两轮分数差异包含模型随机性，不能把同一语料上的波动解释成知识新增的因果收益。保留题没有参与阈值选择。

旧#7保留原记录（32个judge失败、Faithfulness=N/A，不计完整验收）；#8保留interrupted。新judge身份与旧轮分开比较。详细安全投影见eval-runs.json，脱敏逐题结果在log/ch09/eval-round1-v2.log、eval-round2-v2.log。
