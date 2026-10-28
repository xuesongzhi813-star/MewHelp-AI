# 运维故障补充交接（2026-10-28）

旧demo_ch08 profile启动误用已解决，当前ch09无需校准。OPERATIONS完整正文现为ch09，含Test-MewHelpProfile和第5节同类故障分流；旧手册仅在docs/history归档。文档原检查/预算及临时8项HTTP启动通过，SDK正常排空，自己启动的8021/8022/8023已停。profile与active哈希不变，无旧问题消费或代码/.env/知识改动。按新手册第1→2.3→2.4启动；不要再复制历史ch08命令。详见dev-notes阶段31–33。

---

# ch09恢复复核（2026-10-28）

用户已恢复额度并要求完成最后任务。Task1–13已在上次交付完成；当前没有待续写的功能任务。只读启动检查通过，日本区配置及active存在，owner_online=false。历史8%暂停由本次恢复覆盖，详细复核与文档收尾见dev-notes/ch09阶段29起。启动使用docs/OPERATIONS.md开头的ch09入口。

下文是历史交接快照；其中“尚未完成”“暂停”不代表当前待办。保留分支、工作树、所有未提交改动及运行状态，不自动提交、发布或清理。

---

# ch09最终交接（2026-10-27）

本章实现及六项核心验收完成，Native唯一后端review问题已修，保留工作树不做Git集成。后端1111 passed in106.36s；前端Vibe真实浏览器已验，历史重载反馈缺口已修。Cloud由SDK完整读回，用户人工确认Dashboard。真实后端+页面两次合成飞轮通过；eval#12/#13各72题有效，Faithfulness .972701/.991111且judge0失败。原96/24-72不改，原37池未消费，仅两条合成知识。

产品E:/MewHelp AI/.worktrees/ch05-workflow-agent，分支ch07-context/HEAD0de0db1不变；根只共享配置/运行时/阅读副本。用户明确授权仅写本地.env三项Langfuse配置，值未写日志/文档。自有8023/8024已停止，SDK正常shutdown确认；无DDL/清库/提交/推送/合并/部署。

下一步：依docs/OPERATIONS.md当前入口启动正式服务；不会自动运行下一章。额度primary剩8%，按规则停止继续开发，剩余模型质量差异/金额未知见reports/ch09/failures.md，不称模型100%。所有日志/版本/失败记录保留。阶段过程见dev-notes/ch09阶段21–28；交付见reports/ch09/summary.md及demo.md。

---



## 当前实施状态（阶段25，覆盖历史暂停）

Task1–10完成；Task11全部页面/接线已实现，UI工具策略阻塞故未实点验收。真实合成闭环、三个入口、自动回调、修复后Cloud全树/系统Session/Token与正常停机flush均已验证。唯一后端review0Critical/5Important均作者修复，1110项全量通过后另补共享lease线程串行22项PASS，最终全量待跑。新身份24题真实校准成功；失败#7 judge32/中断#8保留，需再跑两轮完整指标。产品与Native不变，无DDL/清库/提交/部署。


## 最新：恢复后Task11实现、Task12首次校准成功（2026-10-27）

Task1–10完成；Task11页面/反馈/后端视图已写，后端review API 5PASS，浏览器工具无法确定当前网址已终止UI操作，尚无真实UI验收。Task12本机125条一致语料真实24题bootstrap成功，active/profile已生成；Cloud知识全链读回/飞轮/两轮72题未验收。用户明确授权仅写本地.env三项Langfuse配置，已完成且其余配置不变。历史额度暂停已被恢复请求覆盖。继续当前Native，不重做审批。
# ch09 恢复交接（2026-10-27额度暂停）

用户要求低于10%额度暂停。最新primary剩9%、secondary剩58%，功能开发已停止；额度恢复且用户明确恢复后才续跑。已批准设计、计划、A自动后台归并、版本化人工标签和Native；不要再次审批。

## 位置与事实来源

唯一产品：E:/MewHelp AI/.worktrees/ch05-workflow-agent，分支ch07-context，HEAD仍0de0db18235d26a492dc36d3115a48c59daccb6b。根E:/MewHelp AI只镜像文档/共享.venv/.env/基础设施，不改根旧业务代码。保留全部ch08未提交内容。

先读docs/AI_HANDOFF.md、CH09_CURRENT_PROGRESS、dev-notes/ch09阶段13–20、已批准spec/plan及专属.superpowers/sdd/2026-10-26-mewhelp-ch09-observability-flywheel/progress.md和task-11-brief.md。账本Ruling优先于早期草案；没有Git提交，不靠HEAD判断任务是否完成。

## 已完成Task1–10

- SDK5.0.4 Japan Cloud可选/生命周期故障隔离，统一请求身份、Callback及自定义Span、SDK内存导出。
- 三入口池事件、Top5快照/旧轮checkpoint回捞、POST /api/feedback、同轮同source幂等。
- 正式证据评分/固定小网格仅24题、主图与后续FAQ共闸、生成前自评。真实profile未校准。
- 自动标准化与全状态全队列语义去重、原子计数/绑定、有限重试/唤醒/扫尾。
- 不可变标签版本、全96题模型影响建议与人工确认、来源绑定/历史回滚。
- 审核API/发布journal、限定pending→upsert/flush→done→24校准→active、维护恢复、owner及整轮读租约、独立demo_ch09入口。
- 只读当前graph评估/72题口径、EvalRun一行一轮、上海02点同槽去重/趋势API。
- 官方Observations v2游标按最终根意图归因，叶子Generation去重/真实重试、缓存/预算/429、unknown与安全统计API。

各任务最终全量：961/973/987/1002/1014/1027/1043/1068/1082/1092；这些按历史阶段记录，最新后端全量1092 passed in105.43s，task10-green.log。真实Prompt样例：自评12/12，查重v2 16/16（v1 15/16），影响v3 8/8（v1/v2 7/8），失败报告均保留log/ch09。不是Cloud或整章真实验收。

## 当前Task11部分

已改app/static/chat-citations.js和index.html：普通回答保存turn IDs并把down提交后端；成功禁重复、失败可重试，旧无ID提示。未浏览器验证，后端1092全量发生在这两处前端编辑之前。

下一步按Vibe直接完成review.html/review.js、quality.html/quality.js、GET /review和/quality及admin导航；审核详情原话/入口/快照/分数、核准/驳回、全题影响确认/其余不变、发布状态/失败重试；趋势按版本/协议筛选、N/A/失败/分母；成本未知与缓存时间。确认流ticketView仍只写纯文本，需接反馈或明确其消息身份展示。真实浏览器逐项验证旧轮down、多标签、跨会话、长/注入文本、维护/失败/空图，不做前端TDD或独立review。

Task12真实验收仍全部待做：只读核对目标/旧服务与共享库影响，首次bootstrap真实24题、Japan Cloud鉴权和完整Trace读回，合成未知问题→待审→人工标注确认→限定发布→再问正确，真实down，至少两意图usage及两轮同版本72题真实评估。真实.env未修改，Cloud密钥是否存在只查布尔，不输出/抄写；先保留所有已有数据，不运行reset/rebuild冒充验收。用户选择不可改技术栈，遇真实矛盾再问。

Task13唯一全后端独立review尚未派发，遵循Native整章一次review，排除前端；重要修复逐项RED→GREEN，最终全量/文档/finish。无用户授权不提交/推送/合并/部署，不删除计划账本。

## 环境与验证命令

沿用E:/MewHelp AI/.venv/Scripts/python.exe，product cwd。SDK依赖已安装，原版本未升级；root editable映射保留。若不得不sync，用--inexact --no-install-project，避免移除原editable。

后端测试用scripts/test_ch09.py脱敏runner，在native环境执行：

```powershell
& 'E:/MewHelp AI/.venv/Scripts/python.exe' scripts/test_ch09.py -q --tb=short --basetemp log/ch09/resume-next
```

普通沙盒可能asyncio挂起；不能把挂起当通过。每个任务门槛按Native；前端Vibe例外仍需浏览器。Windows原子替换会间歇Win5；新ArtifactStore及旧migration清单均已先故障复现后加有限10/20/40/80/160ms重试，不删除目标。最终Task9/10全量通过。

独立启动命令已经实现但真实运行未验收：scripts/demo_ch09.py check / bootstrap / calibrate / serve --port 8023。check只读脱敏；bootstrap真实24题模型/检索且写私有版本，Task12核对后做。data/local/ch09与log/ch09/checkpoints.sqlite独立，别覆盖ch08。应用内评估默认开且会补最近02点，受控演示先设CH09_EVAL_ENABLED=false再手动两轮；脚本eval_ch09.py --trigger 手动在线走owner本机API，离线先取owner。脚本flywheel_ch09.py --once会真实消费队列，别当健康检查。

所有新API仍沿用既有无鉴权演示边界；不发布公网。整个项目启停、备份与恢复见docs/OPERATIONS.md及根日常维护手册最新Task8–10补充，包含完整Langfuse日常观察步骤（待真实读回核验）。私有预算/缓存/人工确认/版本/journal/active、MySQL/Milvus/checkpoint成套保全，不清理旧服务数据。
