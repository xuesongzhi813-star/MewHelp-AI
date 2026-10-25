# ch07 计划自审记录

日期：2026-10-23。这是writing-plans要求的主执行者自审，不是独立计划/代码评审。设计已由用户回复「批准」；本计划尚待用户审阅及选择执行方式。

计划：docs/superpowers/plans/2026-10-23-mewhelp-ch07-context.md，Task0–11共12项，全部未开始。

检查：
1. Spec覆盖：逐节映射到任务，预算/双锚点/后台追加/三处注入/前缀/日志/API/页面/默认与演示21轮/留痕/额度交接均有归属。
2. 步骤粒度：后端步骤区分RED、实现、GREEN与记录；Prompt/数据用冻结dev/acceptance及真实评估；不添加未授权commit步骤。
3. 接口一致：统一ContextBudget、ConversationIdentity、StoredMessage、SummaryRow、ContextSnapshot、MessageRef、HistoryView、LayeredView、SummaryBatch及明确方法签名；依赖方向单向，后台不依赖checkpointer。
4. Review Focus：竞态、容量、崩溃恢复、指代来源、前端迟到帧五项分别落入测试或浏览器/标注集步骤。
5. 比例：保留签名、固定值、测试断言/命令和评估门槛，不在计划里抄写产品函数体。

自审发现并修订：
- 预算扣除分项应为6项，并补H=0/1时非负分层测试。
- 工具增长测试区分messages表行数与checkpoint工具对数量；层2不重复计被替换正文/完整artifact。
- 先前中断轮的用户原话不能被“完整轮”筛选丢弃，只在模型视图过滤孤悬工具对。
- Task5不提前依赖Task8才创建的日志模块，生命周期先接已有预算/管理器，日志后接。
- API描述明确匿名归组过滤不等于认证；明确字段类型而非无值JSON占位。
- 页面引用编号与实际注入的Top-K证据一致，不展示模型没有收到的额外引用。
- 单轮累计cap与窗口分离：新workflow显式context_turn_token_budget严格生效，未设时按有限调用数推导；旧agent_token_budget保留旧loop用途，启动日志公开实际规则。

尚未验证：没有产品代码、测试、模型校准、21轮或浏览器成绩；12项任务的checkbox全部未勾选。预算5650是批准设计的演示算术目标，真实固定前缀及usage必须后续实测。

推荐Native执行：接口关联较紧，单执行者逐任务实现可减少交接与额度开销，最后独立整章review；用户也可选择SDD。writing-plans要求计划交付后由用户审阅并选择方式，再实施。
