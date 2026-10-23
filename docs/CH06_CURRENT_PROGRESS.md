# 最新状态覆盖：ch06功能与验收完成（2026-10-23）

恢复后先只读核实上次694项回归终态，再续Task8。现在Tasks1–8已完成，最终700 passed in37.25s。意图75/75、严格raw JSON75/75、订单9/9；字面问题74/75，有限领属助词等价75/75、三组语义链全对。原始失败attempt与统计口径都保留，不改冻结标签。

真实浏览器已完成原生interrupt暂停/重启/同session点选续跑、取消0/确认1/重复1、刷新/过期409与前端恢复、390px检查。新增唯一合成工单T202610230001，无资金退款。短历史“它能退吗”会完成订单/政策检索但可能因证据分低安全拒答；12题政策单/多查Recall均83.33%，未观察到提升。24题单查询校准阈值.25，额外ch06管线hash已实施并先测后改，不再是待办建议。

产品位于 E:/MewHelp AI/.worktrees/ch05-workflow-agent；2026-10-23经用户明确授权后切分支ch06-router，5个提交已推送origin/ch06-router，PR #6（base ch05-workflow-agent）待评审合并，尚未合并/部署。专属账本保留。8016为演示端口；共享8015未动。

最终恢复入口：产品reports/ch06/summary.md与demo.md；根docs/OPERATIONS.md为当前统一命令；dev-notes/ch06.md阶段14起为恢复过程、阶段17为finish。下文均为中断时历史记录，不能用来重做已完成任务或声称仍未开发。

---

# ch06 当前进度与恢复入口

更新时间：2026-10-23。本文件为进行中交接，不是finish声明。

## 工作位置与授权

产品仅在 `E:/MewHelp AI/.worktrees/ch05-workflow-agent`，分支ch05-workflow-agent，继承大量未提交ch05内容；根E:/MewHelp AI仍ch04旧产品，仅同步本章spec/plan/dev-notes。BASE=HEAD43516c8。阶段21起经用户明确授权提交/推送/建PR（分支ch06-router，PR #6），但仍不合并/部署，禁止DDL/清库/依赖升级/真实退款，不修改或输出真实.env。根.venv解释器用于产品工作树执行。

用户批准设计及8任务计划，采用推荐Native；前端Vibe Coding，不做前端TDD/review。两项新裁决必须保留：订单选择原生LangGraph interrupt()+Command(resume,update)，不能退回应用层手工as_node续跑；意图默认主力大模型，降级只留intent_small_model/intent_escalation_threshold两个惰性配置种子，不能启用小模型路径。默认低分归其他不等于升级。

## 已完成与证据

Tasks1–7已记录完成，Task8进行中：基线/75固定验收+16dev、历史理解/八类intent+confidence、固定三订单、政策多查询/统一去重重排、原生选择中断与HTTP续跑/恢复、确认后售后工单形式演示退款、聊天卡片及原因表单。原问题不覆盖归档。源码、测试和留痕均未提交。

基线637pass；Task2全量655；Task4初版1fail667pass（裸Agent重复改写计量被误改）已修复，之后671pass；Task5全量677pass；Task6相关27pass；评审前完整实现684pass in37.51s。所有实际pytest使用login=false与require_escalated正常本机环境，沙箱asyncio初始化可能无输出卡住，不能改产品绕过。

合成浏览器8017（模型/订单/工单全内存，无真实MySQL写入）已核验卡片→点选→继续→取消计数0→确认计数1/PREVIEW-0001，刷新恢复已提交/待选；截图reports/ch06/order-selector-preview.png，JS语法检查通过。不能当真实模型/检索/工单验收。临时脚本log/ch06/preview_ui.py，日志preview-ui.log，启动exec session53314；只清理本轮8017，不停用户8015。当前预览session0c44febb待选，5789c741已提交合成申请。

## Task8当前未通过事项（不要忽略RED）

独立后端review已派单次fresh reviewer /root/ch06_backend_review；范围见reports/ch06/review-package.md，输出reports/ch06/review.md（若尚不存在，review仍运行）。前端排除。没有第二次review授权需求，但不主动另派评审；收到结论后主执行者一轮RED→GREEN修复并全量。

真实固定验收reports/ch06/router-acceptance-attempt1.json：75/75意图正确、宽松结构解析首次100%，但question73/75，jacket/shirt两条关键链失败，整体passed=false。jacket-3“到哪了”未规范成“到哪里了”；shirt-3指代回退，疑为来源“订单是1002”的提取器不支持。不要改冻结标签，需修实现/Prompt再重跑。此前JSON指标仅LangChain parsed，不足以声称严格原始JSON100%，review指出宽松解析可能接受围栏/截断/尾随文字。

已经新增真实复现tests，log/ch06/review-reference-json-red.log保留修复前9failed22passed；现已完成一轮修复，相关52passed in1.28s（log/ch06/review-fixes-green.log），修复后全量查看log/ch06/review-fixes-suite.log。下面为修复问题沿革，不应重新实现已完成修复：自然订单写法、订单号空格标签、紧凑双订单列表、跨两条历史多候选、当前明确订单被历史覆盖、四种原始非严格JSON。修复需完整json.loads校验JSON mode原始content/拒重复key（_parse函数调用模式adapter不能误改），计费在校验失败时仍结算；同步Provider测试fixture原始content为真实JSON。understanding来源需支持“订单是/订单号：”及列表，拒跨历史多候选无明确当前ID的猜测，禁止当前显式订单被历史替换；代价可能多一次用户选择，记录Ruling。

## 尚未完成的交付验证

1. 收review最终清单并一轮修复上述RED及其他Important，保持失败attempt；修后全量。
2. 重跑真实dev/75acceptance，关键多轮每轮指代正确。评估器目前只比较问题文本及意图，链expected_order_id尚未纳入判定，需一并校正统计逻辑并回归，不修改冻结数据。
3. 新启动器scripts/demo_ch06.py（serve/calibrate/router-eval）已创建/py_compile，8016+log/ch06/checkpoints.sqlite，默认要求独立log/ch06/calibration-profile.json严格identity。尚未校准，直接serve应失败而非复用旧hash。generic脚本eval_retrieval的identity模块清单不含新multi_query/workflow，需评估是否增加ch06管线绑定元数据，不能强改旧profile身份。
4. 原固定24calibration通过 `python scripts/demo_ch06.py calibrate` 创建新文件，保留旧ch05/.25配置；不修改语料/标签/冻结文件。校准后才启动真实8016。
5. scripts/eval_ch06_policy.py准备读取既有冻结test中前12条政策题，对比单政策查询与多查询，要求严格新profile，逐条写reports/ch06/policy-comparison.json；仅py_compile，未真实跑。统计扩写LLM usage，不把嵌入/重排算成免费或完整计费。一次真实source-prefix服务探针正在/已输出reports/ch06/policy-service-probe.json，仅只读查库，需查看结果。
6. 真实浏览器无ID退款/指代补全→原生暂停→点选→订单政策→Agent；合成session确认单新增1、取消/重复不增加，核实本地目标后才写；重启同session保留interrupt。当前无真实票写入，不随意删任何现有数据。
7. 最终reports/ch06/summary.md/demo.md、OPERATIONS/AI_HANDOFF/本进度文档、finish留痕，给实际命令/测试/限制；未完成不得标finish。

账本：`.superpowers/sdd/2026-10-23-mewhelp-ch06-router/progress.md`，因禁止commit不能清理，保留基线哈希、brief和失败日志。过程：dev-notes/ch06.md。会话中Superpowers插件缓存从6.4.2更新到7.0.1；已读新requesting-code-review技能，旧路径失效时查现有目录，不重复设计审批或重做已完成任务。


## 最新覆盖更新：额度交接（优先读，2026-10-23）

5小时额度已用99%，本章未finish。独立review最终报告已保存：Critical0/Important3/Minor0；已完成一轮修复，52项相关GREEN，包含来源自然写法/跨历史歧义/当前订单冲突、严格raw JSON及订单号链评估统计。源码已修，不要重做这些任务；修后全量pytest执行session见本轮日志尾部，文件log/ch06/review-fixes-suite.log（若未结束需确认实际进程/结果）。原先handoff上方“9RED尚未修”已被此更新覆盖。

接下来首先读取修后全量结果，失败定位而非直接宣称通过；重跑真实16dev/75acceptance，strict_raw_json已加入实际输出标记，关键链同时检查order_correct，冻结标签不改。若未通过保留attempt1/后续attempt，修Prompt/逻辑而非改预期。此轮首次75报告仍passed=false，不能冒称最终全链通过。

尚未启动新校准、12题政策对比、真实8016、真实票写入或最终交付。真实policy-service-probe.json已ok（只读服务探针）。scripts/demo_ch06.py默认profile不存在是正常，先calibrate。需要明确一个尚未实施的profile绑定加固：generic _identity未覆盖新multi_query/understanding等，可在新生成ch06 profile另加管线hash顶层元数据并在启动验证；不要伪造旧identity或覆盖旧冻结文件。这个加固尚未编码/验证，不能当既有能力。校准仍用原24calibration，12题对比用原冻结test政策题。

临时8017合成预览已发Ctrl-C停止（exec session53314），必要时只读核对8017归属；不触碰共享8015。截图保留reports/ch06/order-selector-preview.png，临时preview.sqlite仅合成状态。独立评审agent /root/ch06_backend_review已完成，无需重派第二轮；后续新启动器管线hash补充由主执行者测试/核验并注明review覆盖范围。

最终仍需reports/ch06/summary.md/demo.md、统一运维/AI_HANDOFF更新、finish留痕，以及实际命令、真实最终测试/评估/限制交付。新功能全量未提交，保留专属账本/全部未提交基线，不清理.superpowers。
