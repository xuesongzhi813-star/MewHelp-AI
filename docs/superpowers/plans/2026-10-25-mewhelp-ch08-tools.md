# ch08 Pluggable Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一内置与独立HTTP MCP工具的动态注册、校验、权限、执行与审计，并交付主力Agent的工单预览确认流。

**Architecture:** 进程内注册中心每轮合并MCP发现结果，模型使用轮内固定快照，引擎每次执行复查本地权限。LangGraph持久化调用身份和确认状态，一次性进程内capability保护写入；已建MySQL审计表独立best-effort事务，不与工单事务耦合。

**Tech Stack:** Python3.12、既有uv/.venv、FastAPI、LangChain、LangGraph1.2.15/AsyncSqliteSaver、SQLAlchemy/MySQL、静态HTML/JS；新增候选mcp==1.30.0、jsonschema==4.25.1，既有依赖版本保留。

**Spec:** [已批准设计](../specs/2026-10-25-mewhelp-ch08-tools-design.md)。用户2026-10-25回复「批准」；此前已选择独立HTTP方案，并明确SQL表已建立。

**Status:** 用户批准Native后因网络不稳定明确暂停。Task0–4完成（最新已跑全量848passed）；Task5定向11passed，尚无本任务后全量；Task6–12未开始。只有用户恢复后从Task5验证继续。旧executor切换延至Task6，生产入口未接入。恢复入口：docs/CH08_RESUME_HANDOFF.md。

## Global Constraints

2026-10-26状态：用户已恢复Native；Task0–5完成（13定向、全量861passed），Task6进行中。上方暂停Status为历史，后续以专属账本为准。

- 产品根E:/MewHelp AI/.worktrees/ch05-workflow-agent，核查branch ch07-context、HEAD 0de0db1；根E:/MewHelp AI仅镜像文档。下列相对路径全部相对产品根。
- 保留原6项kb/语料改动、所有已有文档/服务/SQLite/profile/语料；不提交、推送、合并、部署。不因技能默认commit步骤请求额外授权。
- 只映射已只读核对的tool_audit_logs（13列、4索引、无外键），不create_all/DDL/ALTER/迁移/清库，不修改或输出真实.env。
- MCP为两个独立Streamable HTTP进程，默认127.0.0.1:8021/mcp与8022/mcp；启动前查占用，不停止未知进程。客服测试入口建议8020，另有占用则选空闲端口并留痕。
- 内置仅query_order/query_product/query_faq/create_ticket；无内置query_logistics回退。respond_to_user仅图控制协议，不记业务工具审计。
- 权限仅本地配置；未知MCP默认拒绝，外部write本章拒绝；create_ticket必须真实用户诉求、资料完整、前端确认及服务器capability全部满足。
- 发现每来源3秒、最多1次额外尝试、20页/100工具；单定义64KiB、描述2000字符、总Schema256KiB；禁止远程$ref。超过限制明确失败不发半份列表。
- read最多1次暂时故障重试；write总尝试固定1；空结果/参数错误/业务或永久错误不重试。调用总时间上限包含重试并受本轮剩余预算限制。
- 审计五中文状态：成功/失败/超时/校验拦下/权限拒绝；等待仅checkpoint。审计独立资源、有界1秒、GET_LOCK(name,0)、同连接显式释放，失败不阻断工具。
- 工单description为非空1–2000字符，类型售后/投诉/咨询；确认API只收session_id、preview_id、decision。旧/ticket和/refund的外部协议/按钮行为不变。
- ch08决策输出候选2600 tokens与P峰值一同计量；旧ch07 profile/预算不覆写。工具目录正常变化不要求重跑知识校准。
- 保持query_faq文本/artifact/证据语义、八意图JSON、历史/摘要及MySQL消息归档契约；所有真正业务入口走同一引擎。
- 框架/SDK/CLI具体用法实施前Context7核对锁版本；可判别后端行为TDD，Prompt语义用冻结样例真实评估；不把mock评估宣称为模型质量。
- 每任务完成即更新dev-notes/ch08.md和进度；任一有效额度窗口剩余<10%时先写恢复交接与留痕再暂停，不启动下个开发任务。

## Review Focus

1. 确认后、业务写入前/后、checkpoint前/后任一点崩溃，恢复不能多一张票；独立审计失败不能诱发业务重试（Tasks2/3/8/9）。
2. 旧轮在途时策略撤销、URL改变或定义替换，不能借旧快照执行新目标；仅其他Server故障不取消可用工具（Tasks1/5/6）。
3. 用户只引用“帮我建工单”或否定/撤销，来源字面匹配也不应当成明确请求；短回复补资料不能跨话题/会话继承（Task7及Task11冻结语义样例）。
4. 审计提交已成功但客户端超时/节点未保存，补审计不重复插入；释放具名锁失败必须丢弃连接，不能把锁带回池（Task3）。
5. 预览后切会话、刷新、旧SSE迟到，卡片仍绑定原会话；恢复使用原Schema快照而新轮使用新目录，tool_call_id始终配对（Tasks6/8/10）。

## 文件职责、共用类型与执行约定

采用一个计划：客户端/引擎/图共同依赖调用身份、权限与快照，拆成独立项目会留下不可验收的中间权限路径；各任务可单独测试和审查。

- 新建app/tooling/contracts.py、registry.py、policy.py、validation.py：纯契约、注册、配置和Schema；不放DB或模型调用。
- 新建app/tooling/engine.py、capabilities.py、builtin.py、formatting.py：准入/执行、一次性写凭据、内置适配及结果投影。
- 新建app/tooling/audit.py、mcp_client.py、service.py：独立审计事务、单owner连接管理、应用生命周期与同步兼容桥。
- 新建app/mcp_servers/common.py、logistics.py、aftersales.py及包__init__.py：只依赖SDK/纯mock数据，不加载数据库配置。
- 新建app/workflow/tool_calls.py、ticket_draft.py、ticket_confirmation.py：调用准备/节点、草稿来源、确认恢复协调。
- 新建app/static/chat-tool-confirmation.js；index.html接帧，chat-conversations.js恢复；旧chat-actions.js只作必要接线，不重写旧交互。
- app/executor.py仅兼容包装；app/tools.py仅业务实现/注册适配；app/models.py映射审计；既有workflow/context/main/API按任务修改。

统一类型在Task1定义，后续不得自行更名：
- ToolCall(name:str,args:object,tool_call_id:str,invocation_key:str)；ExecutionContext(session_id:str,conversation_id:int|None,request_id:str,deadline:float,trusted:dict)。deadline统一monotonic；和既有epoch预算的转换仅在调用边界。
- ToolDefinition(name,description,input_schema,source,remote_name,definition_hash,handler)，source为builtin或server_id；handler实现ToolHandler(args:dict,ctx:ExecutionContext)->Awaitable[RawToolResult]，不持久化。
- RawToolResult(content:object,artifact:object|None=None,outcome:str='success')；ToolExecutionResult(content:str,artifact:object|None,status:'success'|'error',outcome:str,error_code:str|None,retry_count:int,elapsed_ms:int)。旧三字段兼容，新增字段有默认值。
- ToolSnapshot(version:str,policy_hash:str,definitions:tuple[dict,...],schemas:tuple[dict,...])仅序列化元数据；固定排序，含来源/端点/hash，不含handler、连接或grant。
- PreparedCall(call:ToolCall,definition_hash:str,args_hash:str,requires_confirmation:bool)纯数据可存State；RejectedCall(call:ToolCall,result:ToolExecutionResult)同样可存。
- PolicySnapshot(version:str,valid:bool,servers:dict,rules:dict)；规则包含effect/enabled/alias/routing_intents/timeout/output_fields/enum_map。builtin规则由可信默认注册+本地覆盖构成，配置损坏时builtin read仍用安全默认，write和MCP拒绝。

测试命令从产品根运行：优先uv run pytest；现有环境若uv/launcher受沙箱限制，按权限流程使用E:/MewHelp AI/.venv/Scripts/python.exe -m pytest，不重建venv。命令输出保存到.superpowers/sdd/2026-10-25-mewhelp-ch08-tools/对应task日志。步骤勾选以真实证据为准，每任务以diff检查、留痕代替未授权commit。

### Task 0：保全与可复现基线

**Files:** 新建专属账本progress.md、baseline.json、baseline-tests.log（位于上述.superpowers目录）；更新dev-notes/ch08.md。
**Interfaces:** 产出锁版本/文件hash/分支/测试基线供Task1和Task12比较。
- [ ] 读using-git-worktrees，确认已在linked worktree中，复用当前产品工作树；检查AGENTS/设计/当前Git，保存原6文件hash、依赖锁版本、文档改动清单。不创建第二个产品现场。
- [ ] 只读核对本地服务及已建审计表，不输出凭据；保存13字段、4索引、0FK元数据快照。读取现有测试fixture确认事务隔离。
- [ ] 运行uv run pytest -q并记录实际passed/failed/skipped及服务故障；未执行不能填758。基线非本章故障先归因，不扩散修改。
- [ ] 建账本勾选Task0，留痕记录已有改动/测试结果及额度；Task1前先读取此基线。

### Task 1：注册、权限快照与严格Schema准入

**Files:** 新建app/tooling/__init__.py、contracts.py、registry.py、policy.py、validation.py、config/tool_policy.json；修改pyproject.toml、uv.lock、app/config.py、.env.example；新建tests/test_tool_registry.py、test_tool_policy.py、test_tool_validation.py。
**Interfaces:** ToolRegistry.register(definition,*,replace=False)->None、unregister(source,name)->None、replace_source(source,definitions)->None、snapshot(policy)->ToolSnapshot；PolicyLoader.load()->PolicySnapshot；validate_arguments(schema,args)->tuple[dict,...]；validate_definition(definition)->None。
- [ ] Context7核对uv新增依赖但不升级既有锁版本的方法及已选SDK兼容；增加mcp==1.30.0/jsonschema==4.25.1并对比所有已有包版本。冲突保留诊断，不静默升级。新增TOOL_POLICY_PATH占位配置，不改真实.env。
- [ ] 写RED：test_registry_rejects_collision_and_preserves_snapshot断言旧snapshot不随register改变；test_unknown_mcp_denied_despite_read_only_hint断言Server声明无效；test_malformed_policy_revokes_mcp_and_writes断言builtin read仍可用。
- [ ] 写RED：type/required/enum/minimum/maximum/extra/local-ref、bool≠integer、NaN/Infinity、禁止remote-ref；校验错误只含字段路径/约束且≤5条。典型断言：
```python
errors = validate_arguments({"type":"object","required":["n"],"properties":{"n":{"type":"integer","minimum":1}}}, {"n":True})
assert errors and errors[0]["path"] == "$.n"
```
- [ ] 运行uv run pytest tests/test_tool_registry.py tests/test_tool_policy.py tests/test_tool_validation.py -q，记录预期缺实现/行为失败；再实现上述纯接口、名称别名/冲突/本地规则/Schema大小上限及hash。配置包含两个Server、3个MCP read、builtin规则与输出映射。
- [ ] 同命令GREEN；核对依赖既有版本未变、无远程Schema网络访问。diff检查与留痕。

### Task 2：统一执行器、输出投影与一次性写凭据

**Files:** 新建app/tooling/engine.py、capabilities.py、builtin.py、formatting.py；修改app/executor.py（兼容类型导出）、app/tools.py（Schema约束）；新建tests/tooling_fakes.py、test_tool_engine.py、test_tool_capabilities.py、test_tool_formatting.py。
**Interfaces:** ToolEngine(registry,policy_loader,audit_sink,capabilities)；prepare(call,ctx,snapshot)->PreparedCall|RejectedCall纯函数；await invoke(prepared,ctx,*,nonce=None)->ToolExecutionResult（复查权限、执行、终态审计）；await finish(call,ctx,result)->ToolExecutionResult（仅审计）；await dispatch(call,ctx,snapshot,*,nonce=None)组合上述。AuditSink为async write(call,ctx,result)->None协议，Task3实现；先用FakeAuditSink。
**Interfaces:** CapabilityStore.issue(session_id,invocation_key,args_hash,policy_hash)->str；consume(nonce,session_id,invocation_key,args_hash)->bool原子且一次；clear()->None用于重启测试。register_builtins(registry)->None；format_result(raw,definition,policy)->ToolExecutionResult。tests/tooling_fakes.py定义RecordingHandler、FakeAuditSink与make_engine()，所有计时/重试用可控事件/时钟，不靠sleep竞态。
- [ ] 写RED：invalid args/无grant/MCP write/伪造context均handler0次；capability不匹配不消费合法票，匹配消费后再用拒绝；撤权在invoke复核，old snapshot不能绕过。
- [ ] 写RED：read暂时故障2次成功retry_count1；普通RuntimeError/not_found各1次；write超时1次且不能再消费grant；总timeout包含退避；审计抛错业务结果不变。
```python
result = await engine.dispatch(call, ctx, snapshot)
assert result.outcome == "permission_denied"
assert handler.calls == 0
assert audit.rows[-1].result.outcome == "permission_denied"
```
- [ ] 运行uv run pytest tests/test_tool_engine.py tests/test_tool_capabilities.py tests/test_tool_formatting.py -q为RED；实现有界同步线程适配、异步invoke、暂时错误白名单/最多1次重试、系统注入与错误脱敏。不要把GraphInterrupt/CancelledError当业务异常吞掉；取消已开始写入时外层保留uncertain。
- [ ] 实现本地字段投影/枚举中文/未知状态/structuredContent优先，保留FAQ文本+artifact。过大的原结果先限大小，模型输出再按ch07观察预算裁剪；artifact不含被剔除的内部字段。
- [ ] 同命令GREEN；旧executor测试的“任意异常重试”“模型注入覆盖”按新明确契约改为永久错误不重试/保留字段拒绝，并记录为何改变，不降低安全断言。此时不接生产入口、不激活写授权。

### Task 3：既有审计表映射与非阻塞去重

**Files:** 修改app/models.py；新建app/tooling/audit.py、tests/test_tool_audit.py、tests/test_tool_audit_mysql.py。
**Interfaces:** MySQLAuditSink(session_factory,*,timeout=1.0) implements AuditSink；build_audit_row(call,ctx,result)->dict；独立同步_write_terminal在独立有界线程/连接内执行。连接只用于审计，驱动connect/read/write timeout有界。
- [ ] RED精确映射13列、中文ENUM、无FK/无新增列；success+not_found写成功；取消写权限拒绝；重试数1合并一行；未注册标builtin+source_resolution；ID>64映射无碰撞、error≤512、duration unsigned、JSON中文可读且脱敏。
- [ ] RED故障注入：GET_LOCK返回0/NULL、SELECT/INSERT/COMMIT失败、commit成功但客户端报超时、RELEASE_LOCK失败。业务计数不增加；释放失败connection.invalidate；同key后续补写一行；审计资源耗尽不占业务池。
```python
await sink.write(call, ctx, success)
await sink.write(call, ctx, success)
assert rows_for(call.invocation_key) == 1
assert recorded_status(call.invocation_key) == "成功"
```
- [ ] 运行uv run pytest tests/test_tool_audit.py -q为RED；实现参数绑定、稳定key摘要、具名锁0等待、锁内候选查重/事务插入、finally同连接释放。没有upsert唯一索引假设，没有待确认行。
- [ ] 同命令GREEN；真实MySQL测试用注入连接+外层事务/SAVEPOINT或现有同等隔离fixture，禁止create_all/全表删除；运行uv run pytest tests/test_tool_audit_mysql.py -q。记录真实验证/不可用原因，不用mock冒充真实。
- [ ] 将MySQLAuditSink接入引擎构造但保持可注入；检查任务2“审计失败业务成功”再次GREEN并留痕。

### Task 4：两个独立HTTP mock MCP服务

**Files:** 新建app/mcp_servers/__init__.py、common.py、logistics.py、aftersales.py；调整app/demo_orders.py为纯共享fixture（不破坏旧订单）；新建tests/test_mcp_mock_data.py、tests/test_mcp_servers.py。
**Interfaces:** common.MockBusinessData(seed:int|None=None,max_cache:int=1024)；logistics.build_server(host='127.0.0.1',port=8021)->FastMCP；aftersales.build_server(host='127.0.0.1',port=8022)->FastMCP。模块CLI支持--host/--port，默认streamable-http/stateless/json_response。
- [ ] RED：同参同进程相同、缓存有界；物流events按时序、1001/1002/1003与固定订单状态一致；在保/退货进度枚举和日期自洽；所有结果is_demo=true；seed可覆盖found=false，不查询MySQL。
- [ ] RED真实协议：独立进程initialize/list_tools/call_tool能取得1个物流+2个售后工具；Schema要求order_id非空≤64；错误结构不冒充not_found。
- [ ] 运行uv run pytest tests/test_mcp_mock_data.py tests/test_mcp_servers.py -q记录RED；按1.30.0文档实现三个工具及CLI，返回spec§10字段；不添加业务管理HTTP/任意脚本入口。
- [ ] 同命令GREEN，测试只启动自己所有的空闲端口进程并保证清理；此任务不删除现有内置物流，待Task6统一切换避免半接入破坏现有入口。

### Task 5：MCP动态发现与连接生命周期

**Files:** 新建app/tooling/mcp_client.py、service.py；新建tests/test_mcp_client.py、test_tool_hotplug.py、tests/mcp_dynamic_server.py（仅测试服务器）。
**Interfaces:** MCPManager.start()->Awaitable[None]、discover(policy)->Awaitable[dict[str,list[ToolDefinition]]]、call(source,remote_name,args,*,deadline)->Awaitable[RawToolResult]、aclose()->Awaitable[None]。ToolService.refresh()->Awaitable[ToolSnapshot]原子发布可用来源并返回本轮快照；service持有registry/engine/policy/manager/capabilities，连接owner任务和调用队列均有限。
- [ ] RED分页直到无cursor、重复cursor失败、20页/100条及64KiB/2000字/256KiB边界；某来源分页失败不发布半份、不沿用该来源旧列表，其他来源正常。
- [ ] RED连接中断/Server重启/取消/应用关闭可回收owner及transport，下一轮重连；同时两会话refresh不会让来源定义相互覆盖或丢失；本地URL更改后旧call拒绝。
- [ ] RED热插拔真实进程：测试Server同PID增删工具；本地许可更新后客服下一轮能调用，旧snapshot不变；无许可不暴露；同名不覆盖内置；MCP异常isError为故障而非成功文本。
- [ ] 运行uv run pytest tests/test_mcp_client.py tests/test_tool_hotplug.py -q为RED；实现SDK会话单owner启动/关闭、发现锁/发布版本、异步调用与错误归类。工具调用不读Server权限声明。
- [ ] 同命令GREEN；依spec的超时/有限重试做边界测试，完整归还测试进程资源，留痕。

### Task 6：接入所有业务入口与动态上下文快照

**Files:** 修改app/main.py、tools.py、executor.py、chat_service.py、workflow/agent.py、nodes.py、graph.py、runtime.py、models.py、knowledge.py、context/assembly.py、context/budget.py、context/logging.py；补全tooling/service.py、builtin.py；新建tests/test_tool_integration.py、test_dynamic_tool_context.py；修改test_executor.py、test_tools.py、test_agent_loop.py及context相关测试。
**Interfaces:** app.state.tool_service由lifespan启动/关闭；WorkflowDeps/Runtime注入ToolService。State新增tool_snapshot:dict；Gateway.decide/stream_answer与understand接受同一snapshot可选关键字，旧测试适配可注入。ToolService.run_sync(call,ctx,snapshot)->ToolExecutionResult仅从非owner线程run_coroutine_threadsafe；无app生命周期时构建短期builtin-only服务，相同引擎/审计，不复用异步MCP连接；owner事件线程同步调用返回明确错误并要求async入口。
- [ ] RED所有真实业务入口使用引擎：前置query_order、FAQ/多查询政策检索的单次逻辑工具调用、主力read工具、旧聊天业务工具；检测禁止nodes预检索再伪造成功，FAQ计量适配仍只调用需要的模型次数且保留RetrievalResult。
- [ ] RED同轮decide/answer/计量/log schema_hash一致；不同会话各持snapshot；新目录增长使预算真实减少或拒绝，不能暗删工具；max description的完整JSON在2600 cap与P峰值中计量。
- [ ] 补充Unicode/转义密集description边界：字符数≤2000但完整参数估算超输出额度时，在请求模型前明确预算不足/要求精简，不能把字符数当token数或截断成非法JSON；普通2000汉字样例用实际provider usage校准。
- [ ] RED同步入口不会阻塞应用事件循环/跨loop复用session；旧无确认能力入口调用write返回权限拒绝；工具消息原tool_call_id完整配对。
- [ ] 运行uv run pytest tests/test_tool_integration.py tests/test_dynamic_tool_context.py tests/test_executor.py tests/test_agent_loop.py -q为RED；接入lifespan，refresh先于本轮上下文预算构建；迁移主路径为await引擎，删除READ_TOOLS固定拒绝分支，以snapshot+policy判断。
- [ ] 将旧内置query_logistics实现及注册移除，统一由logistics MCP供给；失败明确暂不可用。更新旧物流测试为MCP契约，保持订单fixture。legacy get_tools委托registry可见快照，不再保留平行固定dict为事实源。
- [ ] FAQ adapter在引擎invoke内运行retrieve_knowledge所需受计量逻辑，可信ExecutionContext注入query计划/adapter，不从模型args接受；不把证据闸拒答记服务成功事实答案。
- [ ] 定向测试GREEN并运行uv run pytest tests/test_workflow_knowledge.py tests/test_context_assembly.py tests/test_context_budget.py tests/test_context_logging.py -q；检查原源码保护hash，只改必要文件。

### Task 7：明确工单诉求、可核验草稿与动态工具路由

**Files:** 新建app/workflow/ticket_draft.py；修改workflow/understanding.py、router_prompts.py、routing.py、agent.py、contracts.py、nodes.py；新建tests/test_ticket_draft.py、test_tool_routing.py；更新test_workflow_understanding.py；新增docs/samples/ch08-tools-dev.jsonl、ch08-tools-acceptance.jsonl。
**Interfaces:** UnderstandingResult新增requested_operation:'none'|'create_ticket'|'read_tool'、candidate_tool:str|None、request_source_id:str|None、request_quote:str|None、description_sources:list[dict]；validate_ticket_draft(understanding,*,user_rows,prior_draft,current_turn)->TicketDraft|None；route_with_tools(intent,understanding,snapshot,policy)->Route。TicketDraft保存真实用户来源、description、type、状态及所属conversation，JSON可序列化。
- [ ] RED服务端来源验证：只user且本会话、原文片段存在；拒绝摘要/工具/assistant来源、跨会话、伪造订单/金额；args.description与核验草稿不一致拒绝。空/纯空白/>2000字符不能准备卡片。
- [ ] RED明确工单缺描述保持clarify；只有有效补资料继承草稿，撤销/话题切换清除；candidate精确匹配本轮快照且routing_intents允许才改路由；普通投诉/闲聊不可被read配置覆盖，八意图JSON仍严格两字段。
```python
assert route_with_tools("售后", warranty_request, snapshot, policy) == "business"
assert route_with_tools("退款退货", policy_question, snapshot, policy) == "refund"
assert route_with_tools("投诉", ordinary_complaint, snapshot, policy) == "complaint"
```
- [ ] 运行uv run pytest tests/test_ticket_draft.py tests/test_tool_routing.py tests/test_workflow_understanding.py -q为RED；实现接口与Prompt，不给query_warranty等增加硬编码核心分支；当前授权目录作为不可信用途数据参与已受计量理解调用。
- [ ] 同命令GREEN；冻结dev24与acceptance36条互不重合样例，覆盖明确/否定/引用/取消/短回复/跨话题/在保/退货进度/新read热路由；记录SHA。此时只验评估器和标签，模型效果留Task11，不能以单测证明自然语言可靠性。

### Task 8：原生interrupt与不可重放写节点

**Files:** 新建app/workflow/tool_calls.py、ticket_confirmation.py；修改workflow/graph.py、nodes.py、runtime.py、contracts.py；新建tests/test_ticket_interrupt.py、test_ticket_recovery.py；更新tests/test_langgraph_interrupt_contract.py。
**Interfaces:** prepare_tool_call(state,service)->dict纯准备调用身份/错误/preview；agent_tools节点首先interrupt；prepare_ticket_write(state,capabilities)->dict签发nonce并返回creating；execute_ticket_write(state,service)->Awaitable[dict]消费后invoke。TicketConfirmationCoordinator.validate(snapshot,request)->dict、stream(request,model)->AsyncIterator[dict]，依赖Runtime会话锁和真实StateSnapshot。
- [ ] RED真实临时SQLite：prepare身份checkpoint后才审计；等待agent_tools前无handler/随机ID/审计I/O；同thread关闭重开仍pending；Command(update=旧action)不消费或重复写。
- [ ] RED取消0次写/权限拒绝/配对ToolMessage；确认1次+ticket_no；并发确认一次；校验/无明确诉求不弹卡；多工具调用只允许一个实际业务执行，其余经引擎拒绝且配对/审计。
- [ ] RED断点矩阵：confirmed前/creating提交前/creating后invoke前/业务commit后result checkpoint前/完成后终答失败；同进程重入与重启均不重复写。重点断言：
```python
capabilities.clear()  # 模拟进程重启，checkpoint已有creating
result = await execute_ticket_write(state, service)
assert handler.calls == 0
assert result["ticket_confirmation"]["status"] == "uncertain"
```
- [ ] 运行uv run pytest tests/test_ticket_interrupt.py tests/test_ticket_recovery.py tests/test_langgraph_interrupt_contract.py -q为RED；按spec§8分节点checkpoint边界实现，不能在execute-write入口重新签发capability。
- [ ] 实现新消息使pending过期：先用旧调用身份补拒绝ToolMessage/审计，再开启新轮；取消终结草稿，不让Agent再次弹卡。确认恢复新预算段但原tool snapshot不变，执行前复查最新策略。
- [ ] 同命令GREEN；补tool pairs/消息归档/等待done不归档虚假终答/多会话隔离测试，更新留痕。

### Task 9：确认API、状态恢复与旧按钮统一写入

**Files:** 修改app/schemas.py、api/chat_actions.py、workflow/actions.py、refund_actions.py、runtime.py；新建tests/test_tool_confirmation_api.py；更新tests/test_chat_actions.py、test_refund_actions.py、test_order_selection_api.py、test_workflow_recovery.py。
**Interfaces:** ToolConfirmationRequest(session_id,preview_id,decision:Literal['confirm','cancel']) extra=forbid；POST /api/chat/actions/tool-confirmation返回SSE；state接口新增ticket_confirmation，不改变selection/actions旧字段。旧confirm_ticket/create_ticket_once改为注入service异步引擎，保留API响应结构与completed/uncertain状态。
- [ ] RED预检404/409/422、跨会话/参数篡改/不存在pending/过期preview/撤权；锁内第二次校验防预检与resume竞态；确认和取消stream均结束且消息配对。
- [ ] RED旧/ticket和/refund仍点击确认后1票，重复返回原结果，不弹ch08卡；旧动作creating保存失败handler0次，写后状态失败uncertain；旧action_id稳定导出invocation_key，重复请求不增审计。
- [ ] 运行uv run pytest tests/test_tool_confirmation_api.py tests/test_chat_actions.py tests/test_refund_actions.py tests/test_order_selection_api.py -q为RED；实现Coordinator接线及旧按钮内部同一引擎，任何客户端confirmed字段都不能直接生成新流grant。
- [ ] 同命令GREEN，附带uv run pytest tests/test_workflow_recovery.py tests/test_workflow_chat.py -q；核对旧订单pending期间确认旧按钮不消耗该interrupt。

### Task 10：前端工单预览卡片与刷新恢复

**Files:** 新建app/static/chat-tool-confirmation.js；修改app/static/index.html、chat-conversations.js；新建tests/test_tool_confirmation_page.py、scripts/browser_ch08.cjs（沿用既有浏览器验收设施）。
**Interfaces:** window.createToolConfirmationView({container,sessionId,submit,onEvent,isCurrent})->{setPreview,complete,fail,dispose}；submit固定POST新API；状态available/submitting/completed/cancelled/expired/uncertain。
- [ ] 先写后端页面契约RED：加载新模块/正确API/固定按钮文案“确认提交”“取消”，旧模块及API仍在；不得用此浅测代替真实浏览器。
- [ ] 实现textContent卡片、一次点击禁用两按钮、SSE成功显示ticket_no、取消状态/故障待核实；回载state显示当前pending；session固定捕获+视图版本阻挡旧流，dispose终止旧监听。
- [ ] 运行uv run pytest tests/test_tool_confirmation_page.py tests/test_chat_actions_page.py -q和JS语法检查；只测试用户行为/协议，不写镜像实现的DOM细节测试。
- [ ] 在自己启动的合成API页面实际浏览器验证确认/取消/双击/网络错误/刷新/切会话/迟到流/390px/HTML不执行，截图与结果保存reports/ch08/browser-synthetic/；旧投诉和退款按钮操作不变。真实后端验收留Task11。
- [ ] 留痕实际浏览器工具/环境、通过场景和未验证项；不声称仅页面冒烟就是完整前端验收。

### Task 11：真实集成、模型质量、预算与运行手册

**Files:** 新建scripts/demo_ch08.py、eval_ch08.py、tests/test_eval_ch08.py、test_ch08_demo_profile.py；新建reports/ch08/summary.md、demo.md、rulings.md、review.md；更新docs/OPERATIONS.md及既有根阅读副本、AI_HANDOFF/CH08_CURRENT_PROGRESS/dev-notes。
**Interfaces:** demo_ch08支持budget/calibrate/serve，--profile default|demo、--port（serve）；独立log/ch08/calibration-profile.json及checkpoints.sqlite。eval_ch08支持--split dev|acceptance --mode mock|real --output；支持控制确认/取消的合成会话，输出逐例调用/票数/审计/usage/失败原因及数据SHA，不记录凭据。
- [ ] RED评估器拒绝错标签/服务故障冒充通过；mock标记不等于真实质量。新profile区分代码/检索身份与动态snapshot hash；目录变化只重计预算，不强制付费校准；2000字符描述完整参数不截断。
- [ ] 运行uv run pytest tests/test_eval_ch08.py tests/test_ch08_demo_profile.py -q记录RED→实现→GREEN。默认W128000/O4000/max_user4000/steps8；demo沿用W18000/O2000/max_user2000/steps3并决策cap2600，实际计算预算，放不下一轮则明确失败并记录合法配置调整，不能修改旧profile或计数凑5650。
- [ ] 运行新budget/按冻结calibration生成ch08独立profile，再启动两个自有MCP与客服8020（先核对端口/本地DB/合成session）。真实HTTP热增工具/授权/撤权/Server断线重连证明客服PID不变，无内置物流回退。
- [ ] 先dev24后acceptance36真实模型评估；所有安全负例必须0票/无未经确认副作用，确认正例票数与编号正确，read调用路由正确；记录语义错误并修Prompt后重跑受影响集，不改冻结标签凑分。
- [ ] 运行旧冻结ch06 75意图+9订单回归与ch07代表性上下文/摘要/21轮回忆场景，记录允许变化（售后子路由）和不允许回归（八意图格式、回忆事实、原文、消息配对、预算）。沿用已有评估器可复用路径，不重复编造新质量标准。
- [ ] 真实浏览器完成“缺描述追问→补齐→预览→取消0票/确认1票/重复仍1票”、刷新重启待确认、在保/退货进度/物流、旧投诉/退款按钮；实际对照合成会话tickets和审计，演示写入不清理真实数据，报告合成记录标识。
- [ ] 运行uv run pytest -q取得实际全量结果；核对日志schema_hash/工具calls/五状态/重试/耗时/敏感信息；如外部依赖不可用，写明确未验证范围，不标整章finish。
- [ ] 更新统一手册的启动/检查/停止/备份：两个MCP独立进程、策略热加载、audit表及checkpoint保全、写结果待核实处理、所有自有进程PID/端口。根阅读副本与产品文档同步，不执行无关备份/恢复。

### Task 12：整章评审、修复与交付留痕

**Files:** 完成reports/ch08/review.md、summary.md及专属账本、dev-notes/CH08_CURRENT_PROGRESS/AI_HANDOFF。
**Interfaces:** 产出需求→测试证据映射、真实残余风险和恢复入口；用户未授权Git交付，所有成果保留工作区。
- [ ] 按选定执行技能调用requesting-code-review。Native路径由一名全新上下文、最强可用模型审整章；逐任务subagent路径也需整章评审。评审输入含spec/plan、实际diff、故障矩阵、实际测试证据，不只提供作者摘要。
- [ ] 对确认的Critical/Important写有判别力RED再修复GREEN；不同意见按receiving-code-review技术核验；记录原始结论和作者修复，未复评不说“独立复评通过”。
- [ ] 修复后重跑相关验证；有整合/安全修复则重跑全量，除此不重复烧模型额度。verification-before-completion检查真实结果、diff、无秘密/DDL/用户改动误覆盖。
- [ ] 运行finishing-a-development-branch，但用户禁止提交/推送/合并优先；更新文档标明实际完成/未完成，报告仍待核实事项，不清账本、不删除已有工作树。
- [ ] 若额度<10%在任意任务提前触发，保存当前task、RED/GREEN、文件状态、服务端口/PID、尚未确认结果及下一命令，交接后暂停，不继续Task12冒称完成。

## 计划自检与执行交接

- Spec§1–5→Tasks0/1/5；§6→Tasks2/3/6；§7→Task7；§8→Tasks8/9/10；§9→Task3；§10→Tasks4/5；§11→Tasks6/11；§12→Tasks11/12。无遗漏需求。
- 共用类型与接口在Task1/2/5/8定义；后续直接使用。capability仅进程内，snapshot/PreparedCall/草稿可序列化；不要把handler/grant放State。审计只用既有字段。
- Review Focus五项均有任务内故障测试；步骤包含可判定断言/命令，不把测试期望计数当实测结果。
- 本章推荐Native：多数任务共享引擎/快照/图接口，串行实施能减少上下文交接开销；完成后保留独立整章评审。用户尚未选定本章执行方式，不能沿用ch07的选择冒作本章授权。
- 待用户审阅计划并选择Native或Subagent-driven后实施，不重问已批准设计/HTTP方案/已建SQL表。

## 执行后Ruling（2026-10-26）

用户已批准设计/计划并选择Native，以上待选择文字为历史。实现及唯一整章评审已执行，完整状态依专属progress.md和dev-notes/ch08，不按旧候选计数推断完成。

Task7/8接口兼容扩展：DescriptionSource可用whole_message引用真实user全文；ready草稿提供draft_id，create_ticket的Schema为引用或原description/ticket_type二选一。引擎将引用解析为PreparedCall.resolved_args，并将引用与完整参数共同绑定nonce；来源、否定/引用、none换话题和前端确认规则保持。长描述按证据输入预算预检，不再受字面复制输出容量误限。

Task2/5/6修复：每次实际重试重查权限/定义/URL，MCP请求绑定核验URL；预算拒绝保留注册来源。Task9旧确认按钮保留原完整长文本，明确校验/权限拒绝标rejected。Task11独立demo最终为24k，budget实际值随Schema计算；旧profile不改。真实现有低置信度表新增可空列只做ORM兼容。

Task12只进行一次fresh-context整章评审，Important及重分级审计问题均经作者RED→GREEN修复；不以作者验证冒充独立复评。所有原始失败、模型质量差异和裁决代价保留在reports/ch08及专属账本，不清理未提交工作树。

