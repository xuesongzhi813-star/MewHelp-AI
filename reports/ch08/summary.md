# ch08 交付与验证报告

统一动态注册、严格JSON Schema、本地权限、执行/重试/错误投影、独立审计、两个HTTP mock MCP及原生建票确认均已接入。内置物流已下线，四内置加三MCP为默认七工具。Native实现，SQL仅映射已建表；未DDL、清库、改真实.env、提交、推送、合并或部署。

最终全量 **947 passed in60.02s**（专属账本final-review-suite2.log）。唯一独立评审0Critical/5Important/1Minor，审计来源问题升级Important一起修复；失败用例RED→GREEN，最长来源续补等67项定向结果已包含在全量中。原6项用户改动hash保留。作者修复后未进行第二轮独立复评，详见review.md。

最终真实HTTP/model/MySQL六场景全过（integration-real.json）；实际Chrome完成缺描述追问、补齐、同preview跨后端重启恢复、取消0票、确认及重复1票和刷新，与browser-real/database.json核对。Chrome合成HTTP9项覆盖安全文本、双击、网络失败、跨会话迟到流、390px、旧按钮确定拒绝提示；旧前端回归也通过，产物在old-ui-regression-after-review，旧ch07报告字节恢复。

最长合法范围已闭合：同样的2000字中文和含转义描述经真实模型、原生interrupt、确认及MySQL分别完整落库2000字，回复包含真实工单号（output-capacity-reference-real.json，2/2）。采用真实user整条消息引用和服务端draft_id，完整载荷一并绑定capability；原literal仍严格比较，不静默替换。原复制失败1976/1934及首次引用失败0/2均保留在原报告/attempt1中。

七工具最终预算：default128k，system2852/history74548/layer1=52182/layer2=22364；demo24k，system2852/history6048/layer1=4232/layer2=1814，decision均2600。18k实测不足一轮，旧ch07 profile未改。独立冻结calibration24题完成、无服务失败、threshold0.25，仍为单查询校准；工具目录变化只重计预算。启动与保全见demo.md/统一运维。

最终严格语义dev20/24、acceptance30/36（*-after-review.json），冻结标签不改。取消/否定/假设走其他或知识，与部分旧退款标签不同；另1条商品指代不充分时没有选择商品工具。这些保留为模型路由质量差异，安全负例均无有效建票草稿。热插拔原5/6：初次模型选已有订单工具；新增授权、撤权、断线、同端点重连生效且当时客服PID29212未变，策略最终恢复，不抹原失败。

最终协议下已复跑旧冻结回归：意图75/75、首次strictJSON75/75、订单9/9，字面74/75、有限助词语义75/75，三组多轮链均过，无服务失败（ch06-router-after-review.json）。两组各21轮在最终demo预算/动态引擎下全部完成，摘要/回忆事实通过（ch07-dialogue-after-review.json）。原先词序差异的失败报告仍保留，不以最新结果抹去旧结果。

额外兼容：真实既有low_confidence_questions在本轮间新增两项可空列，仅ORM映射retrieved_chunks JSON/matched_review_id unsigned BIGINT，4项真实回滚测试与最终全量通过，不接新审核业务。

运维风险：本地单进程演示、无公网鉴权；creating/uncertain不自动重放，先核对tickets/审计/SQLite。此前一次失败测试曾回显本地配置凭据，专属日志已清理、runner先脱敏再落盘，历史工具输出无法回收，密钥轮换仍需用户处理。当前改动/报告最终敏感值检查另见留痕，不在文档记录凭据。

代码与证据保留在现有工作树，交接入口docs/AI_HANDOFF.md、CH08_CURRENT_PROGRESS.md和dev-notes/ch08.md。全部裁决、代价与明确未扩项边界见rulings.md。
