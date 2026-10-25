# ch07 验证报告

2026-10-25。**Task0–11已finish。** 独立评审2项Important经RED→GREEN修复，最终全量758项及真实浏览器通过。产品现场为E:/MewHelp AI/.worktrees/ch05-workflow-agent，ch06-router；本章未提交/推送/合并/部署，未改.env/依赖/表结构/知识库数据。

## 功能与边界

三层纯视图、双真实DB-id锚点、追加式异步摘要、完整LangGraph checkpoint与裁剪模型输入分离。L1原文、L2用户原文/答复前60字/工具标识、L3每段不超200字；旧段只作背景，不重写。摘要仅会话内使用，三处注入；固定Agent system+工具schema，动态资料放用户之后。会话匿名按同浏览器分组，侧栏只读回载MySQL原文，保留原生interrupt/确认幂等契约。

## Task10真实结果

- Task10全量757 passed in54.83s；独立评审修后全量 **758 passed in35.19s**，final-suite.log，无skip。
- 摘要dev12/12，冻结acceptance18/18，均真实模型+独立覆盖核验，实体双向检查。报告summary-dev-verified.json、summary-eval-verified.json。
- 旧ch06冻结75条：意图75/75、首次严格JSON75/75、订单9/9、字面74/75、既定有限助词语义75/75；无服务错误。router-regression-verified.json。
- 21轮dev两组42轮均完成并正确回忆最初订单，降级11/10次、摘要2/2段。原输出“没有收到”与标注“未收到”同义，保留原评分报告，修正通用别名后dialogue-dev-final.json通过。
- 冻结acceptance默认两组42轮：0降级、0摘要；末轮1001/未收到/签收人事实齐全，无编造处理结果、无服务错误。default-dialogue.json。
- 冻结acceptance演示两组42轮：11/10次降级、各2段摘要；末轮均正确。demo-dialogue.json。例：层2 1960>1695触发，第1段13223..13230落库；再2037>1695，第2段13231..13251追加。
- 真实时间线summary-timeline.json：其中两次user done先于summary done；另外两次后台先完成亦正常。受控Event回归证明即使后台阻塞，前台done仍发出且完整checkpoint不被摘要覆盖。
- token校准12/12取得真实usage；固定Agent前缀估算1682、实际1531、系统保底2000。CJK系数1.0保持；短文本偏保守，schema合成样例低估73–87token，由固定系统保底与安全余量覆盖，未改计数凑预算。token-calibration-final.json。
- 实际预算：默认H80000；演示W18000-S2000-E1500-A1000-O2000-F850-P5000=H5650，L13954/L21695/拼接余量1。预算入口与provider前缀校准均验证。
- 新独立检索profile为log/ch07/calibration-profile.json，使用原冻结24条calibration，严格绑定语料/模型/检索管线与本章代码；旧ch06 profile不改。仍是单查询校准范围，不把它当所有多查询质量证明。
- 真实Chrome/8018/真实模型通过逐条原文相等、续聊已摘要会话、双会话/切回/刷新、新标签归组、摘要标记、侧栏503聊天可用和390px无横向溢出。browser-e2e.json与browser-real-*.png；另有合成API竞争/HTML不执行/失败保留视图测试browser-synthetic.json。
- 最终log-audit.json逐条核对四个验收会话：每轮history_ctx、三处摘要、Agent只有一个且打头的system、工具schema hash固定、全部实际Agent payload满足估算guard。原6项hash不变、5份冻结样例SHA、8对文档镜像、23+份JSON解析及新增文件凭据模式扫描通过；两工作区diff --check/修后JS语法通过。

## 失败与返工公开

原始报告均保留：摘要dev首跑7/12（1次结构化输出失败），后续同义误报及攻击指令复述修复；acceptance初跑17/18（核验器误要求保留攻击指令），修核验范围后18/18，同一冻结标签不改，不声称是新的未使用holdout。旧75条首跑来源错误保守拒绝2条，修source提示后9/9。

21轮首跑一组从第9轮报错：同文内部决策误绑定终答DB槽，RED→GREEN修复；另一组摘要事实正确但回忆误走知识拒答，改分类/Agent提示后通过。另补DB提交/checkpoint前崩溃尾部、旧工具签名、证据装不下明确失败、UI元数据/历史竞争等回归。详见dev-notes/ch07.md阶段19–21。

## 限制

独立review初评With fixes，0 Critical/2 Important/0 Minor；普通动作恢复和终答提交前失败补归档均经RED→GREEN修复，完整758项通过。未派复评，具体归因/覆盖及Declined to judge裁决见review.md。

全部过程Ruling及成本见rulings.md；无Minor延后。本轮8018已重启到最终demo版本（当时PID44000），用户原8015/8016未动；后续以实际PID核验，不凭快照停进程。

本机单进程，无鉴权匿名演示；不提供跨会话画像/记忆。普通HTTP非安全域名受既有引用组件crypto限制，演示使用localhost。后台任务不跨进程持久化，关闭取消任务，未提交覆盖范围下一轮重试；MySQL/SQLite不是跨库原子事务。模型仍有随机误判/JSON失败可能，保守拒答或要求选单；原生interrupt轮不必产生assistant终答，原文API按实际消息回载。启动profile变化需重校准，不强改hash。真实客户原文日志仅本地保管。
