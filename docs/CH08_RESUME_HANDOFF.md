# ch08 暂停与恢复交接

> **2026-10-26：ch08已完成。本文其余内容仅为早期暂停快照，不再按Task5恢复。** 最终947 passed、真实最长描述/确认流及旧回归均已验证；最新以AI_HANDOFF.md、CH08_CURRENT_PROGRESS.md、dev-notes/ch08阶段17–18及产品reports/ch08为准。所有原始失败、账本和工作树保留，未提交/推送/合并/部署/DDL/清库。后续不自动扩新章节。

> 2026-10-26用户已明确恢复；Task5补齐13定向及全量861 passed，现Task6进行中。本文以下为2026-10-25暂停时快照；最新以CH08_CURRENT_PROGRESS和专属账本为准，不重做Task0–5。

2026-10-25：用户明确要求「网络暂时不稳定，停止开发，提前完成交接文档和留痕文件」。**已暂停开发，只有用户明确恢复后才继续，不自动等网络或额度恢复续跑。**

## 当前进度和证据

设计、计划、独立HTTP方案、已建SQL表及Native执行均已确认，无需再问。

- Task0完成：758 passed in39.56s；保存原6文件hash、锁版本、审计表结构。
- Task1完成：注册/权限/严格Schema；36定向通过，随后全量794 passed in36.61s。
- Task2完成：引擎/capability/输出投影/内置适配；随后全量822 passed in32.97s。
- Task3完成：既有审计表映射、独立事务/具名锁/非阻塞失败；18项新增含真实MySQL外层回滚，随后全量840 passed in31.58s。
- Task4完成：两个独立HTTP mock MCP服务；真实SDK协议与嵌套输出投影通过，最新已完成全量**848 passed in37.91s**。
- Task5进行中：动态发现/单owner会话/组合服务已实现，**11定向passed in5.17s**。本任务后全量尚未执行；最后启动被自动权限审核超时拒绝，task-5-suite.log不存在，不能记作测试失败或通过。
- Task6–12未开始：聊天入口、动态上下文、工单草稿/路由、interrupt/API/前端、真实模型/浏览器、整章review仍待完成。

**新框架尚未接客服聊天生产链。旧执行器、固定READ_TOOLS和内置物流仍在使用；Task6统一接线时才切换。不能宣称现有客服已拥有完整MCP、审计和工单确认。**

## 产品现场与保全

唯一产品：E:/MewHelp AI/.worktrees/ch05-workflow-agent；branch ch07-context；BASE/HEAD 0de0db18235d26a492dc36d3115a48c59daccb6b。本轮无commit/push/merge/deploy。

根E:/MewHelp AI为旧ch04源码，只镜像文档，不从根源码继续实施。暂停时复核原6项kb/语料文件hash全部一致。真实.env未改/未输出；无DDL/ALTER/清库/向量重建；审计真实测试以外层事务回滚并断言无残留合成行。

新增mcp1.30.0/jsonschema4.25.1及必要依赖，所有已有锁版本未变。uv sync曾改变共享venv项目editable路径，已--no-deps重装恢复原根映射；未来sync加--no-install-project。

未执行旧服务8015/8016/8018启停。MCP测试仅用临时空闲端口且fixture finally清理自建进程，未部署常驻8021/8022。本次暂停没有再次启动测试或服务。

最后已知额度快照剩余17%/87%，随后有少量工作，未为本次暂停再次网络查询；它不是实时余额。本次暂停由用户要求触发。

## 文件与账本

新增app/tooling/：contracts、registry、policy、validation、engine、capabilities、builtin、formatting、audit、mcp_client、service。新增app/mcp_servers/：common、logistics、aftersales。新增config/tool_policy.json。新增对应tests/test_tool_*.py、test_mcp_*.py、tooling_fakes.py及仅测试用mcp_dynamic_server.py。

已有源码仅必要改动：app/config.py加tool_policy_path；app/tools.py工单描述1–2000字符；app/models.py映射既有13列审计表；pyproject.toml/uv.lock加依赖；.env.example加占位。没有改旧executor/workflow/main接线。

权威账本：.superpowers/sdd/2026-10-25-mewhelp-ch08-tools/progress.md，保留不删。该目录包含：
- baseline.json（原hash/锁版本）、audit-schema.json（13列/4索引/无FK）、task-N-brief.md。
- baseline-tests.log及task-1/2/3/4-suite.log：实际全量结果。
- task-2-adapter/schema/pool-red和green：artifact、2000字符、取消线程等待名额缺口，各自RED→GREEN。
- task-3-source-red/final-green：MCP来源误记builtin的RED→GREEN。
- task-4-protocol/projection-red及green：SDK需dict[str,Any]生成structuredContent；物流events逐字段白名单与中文枚举。
- task-5-boundary-red/final-green：关闭owner要唤醒在途future；文本JSON found=false要识别not_found，修后11定向通过。
- run-tests.py、baseline.py：现有环境测试与保全脚本；pause-manifest.json：暂停时代码/测试hash。

## 用户恢复后从哪里继续

先读本文件、CH08_CURRENT_PROGRESS、专属progress.md和task-5-brief.md，再核对Git/保护hash/环境。**从Task5验证门槛继续，不重做Task0–4，不重做审批。**

以下命令仅在用户明确恢复后运行：

```powershell
Set-Location 'E:/MewHelp AI/.worktrees/ch05-workflow-agent'
& 'E:/MewHelp AI/.venv/Scripts/python.exe' '.superpowers/sdd/2026-10-25-mewhelp-ch08-tools/run-tests.py' task-5-final-green.log tests/test_mcp_client.py tests/test_tool_hotplug.py -q
& 'E:/MewHelp AI/.venv/Scripts/python.exe' '.superpowers/sdd/2026-10-25-mewhelp-ch08-tools/run-tests.py' task-5-suite.log -q
```

现有venv在沙箱启动曾受限，此前按工具权限流程运行。最新一次是自动审核未在时限内完成，不代表被判不安全；恢复时可按工具规则重试，不修改产品绕过限制。

核对Task5计划每项证据：分页、上限、超时、单源故障、并发刷新已有定向；实际Server断线后同端点重连、URL变更/关闭取消等若缺直接证据需补测，不能仅据11项就complete。通过全量和剩余门槛后再Task6，统一main/Runtime、前置订单/FAQ/旧入口与Schema预算；不要留下两套权限/审计。

## 未验证与已知问题

- 整章未finish，无真实模型、前端浏览器或整章独立review。
- 暂停时git diff --check提示.env.example:98末尾新空行，恢复后清理；暂停阶段没有为修格式而改配置。
- 新snapshot/实际Schema计量、旧checkpoint/profile兼容未接入；保留ch07原文件，不改hash绕过验证。
- 写capability/审计池/MCP owner的完整应用生命周期需Task6/8集成。审计best-effort不等于跨库exactly-once；不能因缺审计行重放写。
- 单次审核超时前启动的Task5全量不存在，恢复时必须真正执行并记录。

