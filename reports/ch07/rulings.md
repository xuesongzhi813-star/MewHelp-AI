# Native流程裁决与成本

按专属progress.md中Ruling出现顺序列全；没有Deferred minors。各裁决均不扩张用户授权。

1. Windows下Native PowerShell/Python等价记录task-start/task-done，保留brief/RED/GREEN/账本；若记录错，恢复者会误判任务，故保留原日志审计。
2. 基线唯一失败属于用户已建列尚未ORM映射，归Task2修映射而不改库/屏蔽；若诊断错，会漏掉真实不兼容，已用真实列映射测试复验。
3. canonical/legacy SessionStore兼容在API任务统一接线，保留旧签名；若错，会错建会话或影响旧确认，相关identity/API/actions回归覆盖。
4. 新聊天HTTP用例集中tests/test_conversations_api.py，不重复建test_conversation_chat.py；若漏覆盖会漏回归，已按实际用例/命令核对。
5. 浏览器合成origin用localhost，匹配既有引用组件安全上下文；普通非安全LAN域名未验证，若用于该环境，crypto接口可能不可用。
6. tool_choice=none时provider usage不计工具前缀，改用决策模式校准；若误用前者会低估固定开销，最终实际prefix1531≤保底2000。
7. 理解unchanged允许短JSON占位，程序恢复逐字原话；若误判unchanged会保守透传，State/Agent原话仍完整，不能据此发明来源。
8. 浏览器原文数量按实际DB消息校验，原生interrupt轮未必有assistant终答；若硬断言42会误报或诱导补写假答，实际逐条API/UI相等已验证。
9. 用户不允许commit，官方review-package拒绝空提交范围，改用tracked diff+全部新源码+baseline的等价包；若漏新文件会漏审，清单与工作区直接核对。未提交故不删账本/工作树。
10. 分布式锁/摘要跨进程持久化维持本章单进程边界；多进程使用会产生竞态，需另立需求。
11. 匿名user_id仅归组，不是认证；错误公网暴露会有冒用风险，维持本机演示。
12. checkpoint丢失后无法从MySQL恢复未落库工具轨迹；需保全checkpoint及侧文件，漏备会丢内部轨迹，用户/客服原文仍可回载。
13. 冻结有限评估和估算不能证明任意输入/模型绝对正确；保留实际质量/usage/guard证据，未来分布仍可能误判或估算偏差。
14. 原6项kb/语料换行差异按基线hash原样保留，不扩散；若基线错会漏审，因此manifest与最终hash均保留。

独立review初始两项Important均已修复并通过先RED再GREEN及完整758项；没有未处理的Critical/Important，没有Minor延后。没有第二次独立review，作者验证不冒称复评。最终保留ch06-router及现有工作树，无提交/推送/合并/部署。
