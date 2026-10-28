# ch08 当前状态与交接入口

2026-10-26最新：**ch08 Task0–12已完成；Native实现、唯一独立整章评审及重要问题修复完成。最终全量947 passed in60.02s。** 真实2000字中文/转义描述2/2完整预览并落库，真实HTTP6/6、Chrome9项、同preview跨服务重启/取消0/确认及重复1与审计核对通过。最终旧75题意图/JSON75/75、订单9/9、有限语义75/75（字面74/75），两组21轮摘要/回忆复跑通过。新语义dev20/24、acceptance30/36仍保留严格路由差异，无安全负例建票授权；原失败不抹去。

产品保留在E:/MewHelp AI/.worktrees/ch05-workflow-agent，分支ch07-context、HEAD0de0db1未变，6项原改动保全。默认七工具，MCP物流取代内置物流，严格引擎与前端确认不可绕过；来源/draft_id引用避免长文本复写，旧按钮保留完整文本和原确认协议。SQL只映射已建表，无DDL/清库/真实.env修改/提交/推送/合并/部署。当前8020/8021/8022自有进程保留，详情reports/ch08/demo.md。

交付报告reports/ch08/summary.md；原始评审与作者修复review.md；全部27项裁决及代价rulings.md；留痕dev-notes/ch08阶段17–18。专属账本保留，不从Task5/旧暂停重做，不再进行第二轮review。后续新章节/延后能力不视为已批准需求。

## 以下为历史阶段快照，均不作新的续跑指令

2026-10-26：**Native执行中；Task0–9功能接入并通过首轮全量906项，Task10前端已实现、待实际浏览器；未finish。** 最新另补4项SQLite故障测试、2项页面测试通过。草稿/动态路由、原生interrupt确认、一次性写凭据与严格确认API已接入，真实模型/浏览器/专属profile及整章评审仍待Task10–12。留痕阶段14和专属账本为当前入口；以下旧数字仅为历史。

统一引擎已接main/Runtime、Agent/前置查询、旧聊天/同步API及旧确认按钮；内置物流与READ_TOOLS已移除。新工单草稿/interrupt/预览前端尚待Task7–10，真实模型/浏览器/整章review待Task11–12。最新留痕阶段13及专属账本。

最新阶段见dev-notes/ch08.md阶段12及专属账本。恢复核对暂停hash一致；补齐同端点重启/端点更换，13定向通过。继续Task6，不重做Task0–5或审批。

最新已完成全量为Task4后848 passed in37.91s；Task5定向11 passed in5.17s，最后全量因自动权限审核超时未启动。新框架尚未接客服生产入口，旧执行器/内置物流仍在使用。

恢复先读[CH08_RESUME_HANDOFF](CH08_RESUME_HANDOFF.md)及专属账本，从Task5验证门槛继续，不重做Task0–4或已批准事项。原6项hash一致，真实.env未改，无DDL/清库/提交/部署。暂停检查.env.example末尾空行留恢复处理。

## 下文为暂停前历史快照，已被上文覆盖，不作续跑指令

## 已获裁决与当前产物

- 用户选择方案1：两个独立HTTP MCP服务（Streamable HTTP），物流与售后各自进程。
- 用户明确“SQL表我已经提前建立”；已只读确认tool_audit_logs的13列、4索引、无外键。按既有表映射，禁止新增/改表。
- 完整设计：[ch08工具系统设计](superpowers/specs/2026-10-25-mewhelp-ch08-tools-design.md)。
- 阶段留痕：[dev-notes/ch08.md](../dev-notes/ch08.md)。
- 设计已完成独立builder check；3项问题由作者修订，未独立复评；用户随后明确批准完整设计，不重复询问。
- 实施计划：[ch08计划](superpowers/plans/2026-10-25-mewhelp-ch08-tools.md)，用户明确Native执行；Task0–2完成，Task3–12待完成，不再询问执行方式。

## 现场与保全

产品目录E:/MewHelp AI/.worktrees/ch05-workflow-agent，当前ch07-context，核查HEAD 0de0db1。根E:/MewHelp AI仍为ch04旧源码，只镜像文档。保留产品原6项未提交改动、根已有文档和其他未提交内容。没有切分支、提交、推送、合并、部署；没有安装依赖、改uv.lock、DDL、清库、修改真实.env或停止既有服务。

本轮已实现注册/权限/Schema与引擎基础，新增依赖均未改变已有锁版本；修后实测822 passed。生产入口接线仍待Task6，MCP/审计/确认API/前端尚未完成，未跑真实模型或浏览器。专属账本记录所有RED/GREEN及Ruling。

## 下一步与恢复要点

1. 设计/计划/Native均已批准；继续Task3，不重做Task0–2及审批。
2. 先读专属账本和task-3-brief，完成审计映射/故障注入/真实MySQL隔离测试，再继续Task4–12。无提交、推送、DDL、清库授权。
3. 审计表五状态为中文且NOT NULL；只有id主键，无调用唯一索引。等待留checkpoint，终结审计用稳定key+短具名锁去重，不得按初稿虚构新增列。
4. 工单写入一次性capability原子消费，creating后重启不自动签发；审计身份在首次审计前已checkpoint提交。对这些协议做故障注入测试。
5. 路由候选改为动态read_tool+本地routing_intents，不给每个新工具新增核心分支；八类意图JSON保持。
6. 2000字符description与旧300决策token上限不兼容，ch08输出容量/峰值预算一起调整，保留ch07原profile及checkpoint。
7. 候选新增mcp==1.30.0/jsonschema==4.25.1，未实际解析安装；锁兼容须复核，不升级既有依赖解决冲突。
8. 任何有效额度窗口剩余<10%时先写恢复交接/留痕再暂停新开发，不以未知额度冒充充足。

