# ch05 集中评审与修复记录

日期：2026-10-22。Native，独立上下文、只读检查实际 dirty/untracked 文件；BASE=HEAD，未将空提交范围当作评审。上次评审因额度中断未留下完整结论，本次恢复该集中评审，未逐任务派代理，也未派第二轮修复后 reviewer。

独立结论 **With fixes**；Critical 无，Minor 无。主执行者按使用效果保留 Important 等级，统一修复后用 TDD/全量验证收敛。

## 四项 Important

1. 中断时已 checkpoint 的 AI tool_calls 缺少结果，污染下轮模型请求。`nodes.paired_history` 仅为模型视图保留完整调用/结果组，不删除归档；`test_interrupted_tool_pair_not_sent_to_next_model` 前轮 RED，本轮 GREEN。
2. 知识出口已强制检索，却再次查 FAQ 并重复注入证据，真实 policy 触发预算兜底。预加载时从模型工具集合去掉 FAQ；提供商越界调用也复用既有结果、返回短观察；`test_preloaded_policy_evidence_is_not_retrieved_twice` 前轮 RED，本轮 GREEN。失败仍在 acceptance-attempt1.json，最终修复后真实政策回答正常。
3. 业务出口自行 FAQ 时，低分/NaN 结果虽在 ToolMessage 拒绝，仍注入系统证据。`model_messages` 只注入通过同一闸的结果；`test_business_faq_rejected_evidence_never_reaches_model` 本轮 RED→GREEN，同时覆盖低分和 NaN。
4. 裸循环 FAQ 经旧执行器触发未计量改写模型。改为同图使用 `retrieve_knowledge`/`query_adapter`，继续复用既有检索器；`test_bare_faq_rewrite_is_metered_without_legacy_execution`、`test_bare_faq_exhausted_rewrite_cannot_call_provider_or_retrieve` 本轮 RED→GREEN，真实裸 FAQ 4 次模型/6,359 tokens。

本轮新增3条复现先3 failed in1.79s，相关28 passed in2.38s；全量630 passed in36.91s。最初恢复时20定向/627全量是较早快照。最终 Task9 官方日志见专属账本，不能把不同运行数相加。

## 优点与五项重点

- 含订单号的政策问题由知识出口先检索；QueryPlan 不改图路由。
- 写工具只转建议；多调用逐一配对，单步执行一个只读工具；预算计量包含必要改写。
- 同会话锁贯穿聊天/确认，断线不发 done；状态跨重启、每轮证据/预算重置，异常工具历史按完整组投给模型。
- 工单先保存 creating 再单次调用原工具；MySQL 已写而 SQLite 未保存时保留 creating/uncertain，不自动重试。
- 前端按消息/action_id 分离；不点/取消无工单请求，handoff 不调用工单接口；双向操作顺序已真实验证。

## Rulings 与未评判范围

以下含账本裁决及每项 Declined to judge 的处置与代价：

- 原生 worktree API 报 Git unavailable，终端 Git 可用，改手工 worktree；代价：需保留该目录，不能当受管 worktree 自动归档。
- 用户禁止自动提交覆盖技能默认提交/清理；评审实际未提交文件，保留 ledger/scratch；代价：进度依赖工作树和文档，不能仅靠 Git 提交恢复。
- 复用根 `.venv`，根 `.env` 只加载进程内、不复制或输出；代价：命令必须选明解释器与配置来源。
- include_raw 结构化链丢弃调用阶段限额，构造时也绑定 max_tokens/timeout；代价若错误：模型账单上限失效；已有 MockTransport 请求体回归。
- 指令 Prompt 留 workflow/routing、agent 模块，不扩改旧 fallback Prompt；代价：以后正式分类需定位模块。生命周期接线属于 Task6，Task4 提供 checkpointer/锁；代价：需同时看两任务，已由回归覆盖。
- 简单决策次数测试给更高 token_limit，单独验证10次决策停止；默认12,000额度另测，不调大产品预算；代价：测试只证明各自边界，组合行为由预算回归/真实报告补充。
- 官方 SQLite 单机单进程、无鉴权演示沿用批准边界，不引入分布式锁/鉴权；代价：不能直接多实例或公网部署。
- A 裁决保留前置证据闸及 Prompt，取消全文硬校验发布保证；代价：流出文本不能撤回，引用和禁止承诺不再具有 ch04 同等硬校验。
- SQLite/MySQL 非原子、同步线程无法强杀不更换架构；代价：创建结果不明须人工核实，不能宣称 exactly-once。
- 提交/推送/合并/部署未授权，按保留原状完成；代价：用户仍需自行选择集成。
- reviewer 未检查新增 `scripts/demo_ch05.py` 或最新真实报告；主执行者核对官方Uvicorn接口、真实启动/评估/裸循环/重启结果和静态检查；代价：这部分没有独立评审，不声称有。

无延期 Minor。无需再次审查已由 RED→GREEN 和全量覆盖的修复。演示命令/限制见 demo.md；真实与隔离验证不能互相冒充。
