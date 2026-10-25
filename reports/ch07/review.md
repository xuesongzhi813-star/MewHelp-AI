# ch07 独立整章代码评审

2026-10-25，Superpowers Native唯一整章review，gpt-6-astra全新上下文。只读工作树、209KB tracked diff+30个新源码评审包、Spec/Plan/ledger及报告；BASE=HEAD c4453dc，本章未提交，未以空HEAD范围代替工作区检查。主执行者按receiving-code-review核实后处理。

## 结论

初始With fixes：0 Critical、2 Important、0 Minor。追加摘要CAS/短事务、完整checkpoint与模型视图分离、artifact排除、来源归属/range验证、失败attempt公开均符合要求。

1. Important：app/static/index.html restoreChatActions仅构造ChatRefundUI，后者过滤普通create_ticket/handoff，刷新/切回后按钮消失。chat-actions.js setActions同时强制available，恢复时会错误重置completed/uncertain。要求同时恢复普通视图、绑定该会话并尊重服务端状态，浏览器验证同action确认及completed/uncertain禁用。
2. Important：app/workflow/runtime.py只在status=completed补归档；append_final提交前失败后finally改interrupted，下一轮先插用户，此后唯一槽位不允许补旧答。评审用InMemorySaver/Store复现：轮1有final_text但DB仅user，轮2后旧assistant永久缺失。要求依据实际稳定终答而非状态补写，恢复仍失败时阻止新用户越过槽位。提交后丢映射路径已有恢复能力。

## Review Focus

- 并发摘要：CAS不回退layer1，不写checkpoint，未提交范围仍在，检查通过。
- 容量/巨大artifact：估算实际模型视图、排除artifact、明确异常、不静默改L1，检查通过；仍为公开估算口径。
- 同文/间隙/归档恢复：同文和真实ID通过，提交前失败见第2项。
- 最早订单/歧义/跨会话：检查通过。
- SSE迟到/侧栏故障/旧操作：流隔离/原生interrupt/服务端幂等通过，普通动作恢复见第1项。

## Declined to judge及主执行者Ruling

- 分布式锁、跨进程摘要持久化：本章明确单进程本机，维持；若部署多进程会有跨进程竞态，需另立需求。
- 匿名归组的认证/公网控制：按批准边界维持，不宣称鉴权；若错误用于公网存在身份冒用风险。
- checkpoint丢失后的完整工具轨迹：工具未落MySQL是明确约束，原文可恢复，完整工具轨迹依checkpoint备份；若漏备会丢内部轨迹。
- 任意输入绝对零误判、估算与provider完全一致：不作无限保证，按冻结质量集、真实usage校准及显式容量guard交付；异常模型/分布仍可能误判或偏差。
- 原6项kb/语料换行差异：基线hash核实未变，不扩散修改；若基线认错会漏审，因此保留manifest可复查。

## 主执行者修复验证（不复评）

- 第2项先加test_final_archive_failure_recovers_before_next_user_commit：RED明确出现故障期间提前写入第二条user；修为新轮入库前只要有稳定final_text/current_turn就补归档。持续失败阻止越过槽位，恢复后旧答补写一次并恢复DB-id映射。相关22 passed in6.08s。
- 第1项真实Chrome新增普通动作建议→切换→切回/刷新→取消不写→同一action/会话确认一次、completed/uncertain禁用；RED恢复后按钮数0，修复restoreChatActions同时构造普通视图及setActions尊重服务端status后完整GREEN。
- 修后全量758 passed in35.19s（final-suite.log）；没有第二轮评审，不把作者验证冒称独立复评。当前两项Important均有失败先行的覆盖与通过证据；Minor无。
