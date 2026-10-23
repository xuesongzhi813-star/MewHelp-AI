# ch05 交付结果

2026-10-22。功能开发、集中评审修复和最终真实验收完成；保留未提交工作树，未提交/推送/合并/部署。

- 唯一实现：`E:/MewHelp AI/.worktrees/ch05-workflow-agent`，分支ch05-workflow-agent，基点b1e6906。原根目录只同步文档，不含本章产品实现。
- 手写裸循环先行，LangGraph确定性骨架随后；七类意图固定四出口，官方SQLite状态/checkpointer，前置知识闸，多步只读Agent及预算控制。
- 退款退货强制检索后可查订单；物流/订单/售后直接Agent；投诉固定文本/独立选项；闲聊固定回复（仍有一次分类模型）。弱证据兜底记问题池，不进Agent。
- 转人工仅前端两句模拟接待。工单必须独立确认后单次调用原create_ticket，真实会话ID由系统注入。不点/取消/单独转人工零票；两种操作顺序均实际验证，重复确认不加票。

最终全量 **630 passed，0 failed，0 skipped**（35.79s）；固定分类 **28/28**，Agent **8/8**。评审四项Important已用RED→GREEN修复，无Critical/延期Minor，详见[评审记录](review.md)。

最终真实HTTP8条均符合检查：政策前置检索/闸、物流工具、两步订单→物流、闲聊、投诉、弱证据、缺字段追问、仅转人工。policy最终5800tokens，首token 10.62s、末token 11.65s，多个token帧证明实时流式。见[acceptance.json](acceptance.json)。这不是SLA或大规模性能测试。

跨进程恢复原会话1→2条用户消息、ID无重复，无旧证据/预算复用，见[restart.json](restart.json)。裸循环FAQ实际4次模型/6359tokens，见[bare-policy.json](bare-policy.json)。各轮通过数不相加。

真实工单是明确确认的合成会话bc263f5a的T202610220001/0002；重复确认仍2票。本轮cddf458e转人工+取消确认仍0票，见[browser-db.json](browser-db.json)及截图。未删除这些或其他数据。已有知识语料源字节保留，未重建、校准或改表。

失败证据保留：acceptance-attempt1的policy预算兜底；agent-loop-attempt1/2；intents/agent-resume-attempt1分别27/28与7/8。修复Prompt后按原标签重跑，不更改样例来凑通过。最终报告为intents-final.json、agent-final.json、verification.json。

演示命令与关闭方法：[demo.md](demo.md)。留痕：[dev-notes/ch05.md](../../dev-notes/ch05.md)。当前维护入口：[CH05_CURRENT_PROGRESS.md](../../docs/CH05_CURRENT_PROGRESS.md)。

限制：官方SQLite单进程、进程内会话锁、无鉴权本地演示；A方案不提供ch04全文硬校验保证；跨库非原子，创建不确定须核实，不声称exactly-once。未接真人客服、正式意图/指代、上下文升级、MCP、Langfuse或飞轮入库。正式生产部署仍需后续章节，不能将本章演示直接上公网。
