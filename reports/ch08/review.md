# ch08 唯一独立整章评审

Reviewer：fresh-context gpt-6-astra high，Native流程唯一席位；网络/额度中断后继续同一agent，未再派review。只读，未修改源码或执行真实业务写入。评审前918 passed in56.39s，保护6项hash一致。

原始结论：无Critical，5Important，1Minor，Ready to merge: With fixes。优点是confirmed→creating→execute与进程内一次性capability、独立审计/去重/锁释放、前端会话隔离与textContent已有实际证据。未发现写节点重放产生第二票的路径。

Important原始发现：

1. engine.py:155/service.py:94：暂时故障后的自动重试复用旧定义，未重查许可/URL；manager又按source取当前owner，可能把旧轮发向新端点。内存复现撤权后仍成功，retry1。
2. ticket_draft.py:89：已有描述但缺类型，用户仅回复“投诉”，ACK分支提前返回，仍缺ticket_type。
3. ticket_draft.py:34/81：假设“如果我要建工单”、ASCII单引号引用、长否定“我不希望你现在给我建工单”在受控模型误判下通过；collecting后换话题“这件衣服怎么清洗”，operation=none但当前文本被当描述，也可生成ready。按钮仍挡写入，但不满足明确诉求门控。
4. tools.py:96/actions.py:55/refund_actions.py:9：旧按钮合法500字note叠加1513字原问题和标题等可达2071，新共享maxLength2000拒绝，action已creating又误标uncertain；旧投诉长原话同受影响。
5. agent.py:30/models.py:124：2000字合法描述仅返回1976/1934。严格相等安全拒绝成立，但尚无完整合法范围E2E成功证据；建议调整来源引用协议或请求用户裁决收窄范围。

Minor原始发现：agent.py:79：预算/单步超额拒绝直接finish，已注册MCP工具审计成builtin/unregistered，丢真实server来源。

Reviewer未认定为缺陷：取消→other/概念咨询→knowledge比冻结refund标签更合理；hotplug首次误选工具是质量失败，不足以判注册生命周期错误；历史connection-refused有后续成功证据。

Declined to judge原文要点：公网鉴权/multi-worker/跨库exactly-once被设计排除；六项保护文件未变化不归责；未验证长描述多次纠错后最终是否成功，首步失败不能推断必然失败或已恢复。

作者复核与修复策略：五项Important均接受，并以新增RED测试验证。原Minor因违背明确审计来源要求、会误导安全追踪，升级Important加入同一修复 pass。长描述采用向后兼容的来源/草稿引用协议，仍接受原literal参数并严格比较，不收窄用户范围、不静默替换模型传入的错误literal文本。旧按钮使用可信confirmed_action兼容路径保留原完整文本，确定未执行的拒绝应明确标rejected。

修复结果：

- 重试撤权/端点/定义及实际owner绑定、类型短回复、假设/长否定/引用/none换话题、旧合法长退款及确定拒绝、审计来源均先复现RED后修复。初始8RED→完整16RED→23项（含引用防伪和SQLite重开）GREEN，日志final-review-first-red/final-review-complete-red/final-review-complete-green。
- 旧建票/退款前端错误显示在实际Chrome分别RED后修复，最终9场景GREEN（browser-synthetic/result.json）。旧成功协议及按钮确认次数保留。
- 最大描述以向后兼容引用协议修复；原失败报告完整保留。真实链路又发现改写兜底抹来源和续补缺选择器，两项RED后修复，67定向GREEN；同样2000字中文/转义描述真实模型→interrupt→确认→MySQL均完整2000字，回复含工单号，Important5闭合。
- 真实库额外列造成首次全量940通过/1失败；只读核对后仅ORM兼容两可空列，4真实回滚测试GREEN。最终全量947 passed in60.02s（final-review-suite2.log）。
- 最终浏览器同preview跨服务重启、取消0/确认及重复1与DB审计复核通过；六个真实HTTP场景复跑全过。
- 最终旧冻结75题意图/严格JSON75/75、订单9/9、有限语义75/75（字面74/75）；两组21轮摘要/回忆复验通过，保留所有之前失败。新冻结dev20/24、acceptance30/36的路由差异单独报告，不冒充满分。

作者确认上述Important均已修复、无遗留Critical/Important；Deferred minors：无（唯一原Minor已升级修复）。这是同一次修复pass的作者验证，不宣称第二轮独立复评。

Declined to judge作者裁决：公网鉴权/多worker/跨库exactly-once继续排除，代价是不能把本机演示直接扩展部署；6项原保护改动保持，仅做集成回归而不归责本章，代价是未对原改动做专项安全审查；长literal多轮纠错成功率不作推断，默认引用链路已有完整成功证据，代价是强制literal调用仍可能因复制不一致被拒绝。均保留在rulings.md。
