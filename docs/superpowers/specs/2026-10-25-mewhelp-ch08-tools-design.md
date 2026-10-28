# ch08：即插即用工具系统设计（已批准）

日期：2026-10-25。状态：用户已选择方案1——两个独立HTTP MCP服务，并在完整设计交付后回复「批准」。设计已批准，实施计划另行审阅；尚未实现。文中候选工程默认值按实施阶段兼容核查和实际校准处理，不代表已经验证。

2026-10-25最新执行状态：计划/Native均已批准；Task0–4完成，Task5进行中，整章未finish。用户因网络不稳定明确暂停，只有明确恢复后继续；审批不重做。当前未接聊天生产链，证据及恢复命令见docs/CH08_RESUME_HANDOFF.md。

## 1. 目标、事实与范围

统一内置/MCP工具的发现、Schema、权限、执行、格式化与审计，让获本地许可的新工具下一轮即可使用，不改主力Agent核心、不重启客服服务。新增物流与售后两个独立mock MCP服务，物流工具迁出内置清单；主力Agent支持用户明确请求后的工单追问、原生interrupt预览、确认resume与取消。

当前产品：E:/MewHelp AI/.worktrees/ch05-workflow-agent，ch07-context，核查HEAD 0de0db1；根E:/MewHelp AI为旧ch04源码及文档镜像。保留现有6项未提交改动、既有服务、checkpoint、profile、语料和真实.env。未经另行授权不提交、推送、合并、部署。

现状：tools.py固定dict、agent.py固定READ_TOOLS、models.py和context/assembly.py过滤create_ticket；executor.py对非校验异常普遍重试；ch05按钮写票绕过执行器并以creating/completed/uncertain防重复；ch06已有原生interrupt/Command与会话锁；ch07动态预算当前依赖固定工具Schema。ch05以后已有受预算限制的Agent循环，以当前源码为准，不退回ch02旧单轮编排。

本章不做真实物流/售后集成、真实退款、MCP写业务、任意代码上传、插件市场、鉴权改造、公网部署、多worker分布式协调或依赖全面升级。沿用MySQL、SQLite checkpoint、LangGraph、LangChain和静态前端。仅新增已建审计表的ORM映射，MCP服务不建表、不读取真实业务库。用户补充“SQL表我已经提前建立”，已于本轮只读核对tool_audit_logs结构、索引及无外键；不新增/修改数据库结构。

## 2. 已选路径与取舍

1. 独立Streamable HTTP服务（用户2026-10-25选择）：物流、售后分别运行，客服通过固定本地配置中的地址连接；可以独立重启，模拟真实外部接入。代价是多两个进程及连接/断线管理。
2. stdio子进程：启动简单，但进程生命周期耦合客服进程；本章不采用。
3. 仅把函数改成装饰器注册而不做真实MCP：不能满足独立服务与动态发现，不采用。

客户端与两个服务使用官方Python MCP SDK。候选锁定mcp==1.30.0、jsonschema==4.25.1；已查询这两个版本的官方文档。批准后做锁文件兼容核查，仅增加所需依赖，保留既有包版本；若必须改变既有依赖版本，先报告具体冲突并裁决，不静默升级。现有LangGraph==1.2.15不变。

## 3. 总体结构与注册契约

新增工具基础模块，职责分为registry、policy、validation、engine、audit、mcp_client、formatting；物理文件划分可在实施计划中微调。app/executor.py作为兼容入口委托统一引擎，app/tools.py保留内置业务实现，移除内置物流的注册和调用实现/入口。

统一ToolDefinition至少含：
- name：模型可见的唯一名称；description：用途描述；input_schema：模型参数JSON Schema。
- source：builtin或稳定server_id；remote_name：MCP原始名称；schema_hash、definition_hash。
- handler/adapter：仅进程内，不写入State；系统注入字段声明。
- effect、enabled、timeout、retry和输出投影：从本地权限/执行规则取得，绝不从Server annotations推导。

内置启动注册query_order、query_product、query_faq、create_ticket。注册/注销提供进程内公开接口，调用后下一轮生效；注册同名不同定义必须显式replace，否则拒绝。新内置Python实现可由宿主可信集成代码调用注册接口，不提供网页上传执行代码，也不承诺编辑任意Python文件会自动热加载。无需要升级Agent分支判断的工具名称白名单。

主力Agent的respond_to_user是图控制协议，不是业务工具；不伪装成审计业务调用。所有真正业务调用，包括非法、未授权、超额的模型工具请求，都进入引擎的准入/拒绝路径并记录审计。

MCP已知别名：logistics/query_logistics→query_logistics；aftersales/query_warranty→query_warranty；aftersales/query_return_progress→query_return_progress。内置query_logistics必须不存在，不保留同名降级。其他工具默认名为mcp__{server_id}__{remote_name}，本地配置可指定稳定别名。长度/字符非法或碰撞时拒绝该条注册并报告诊断，不静默覆盖，不随机改名；内置名称保留。

## 4. 动态发现、配置与故障边界

配置为本地JSON文件（建议config/tool_policy.json），仅管理工具接入，默认绑定127.0.0.1。内容包含server_id、URL、启用状态、逐工具许可/读写类型/别名/输出投影与超时，以及routing_intents（允许该read工具从哪些既有意图进入business）。启动时校验；每个新对话轮和确认恢复前重新读取并验证文件内容，使用内容hash标识版本。原子写文件为推荐编辑方式；语法或规则非法时停止该配置控制的MCP调用，内置只读不受影响，不继续沿用可能已撤销的许可。

每轮开始：
1. 加载本地有效策略。并发访问各个已配置Server，initialize后list_tools，遍历所有分页（循环cursor视为故障）。
2. 每个来源完整分页成功后原子替换其定义快照；失败不发布半份列表。本轮隐藏不可用来源，保留诊断，禁止回退内置物流。
3. 按本地许可合成有序的模型可见快照，包含Schema、来源、端点身份、policy_hash、definition_hash。未知工具可被发现并登记为disabled，不发给模型；本地显式授权后下一轮可见。
4. 决策、回答、计量与日志使用同一轮快照，不在每次模型调用中漂移工具列表。新工具在下一轮可见，正在执行的轮次不换定义。

推荐由应用生命周期拥有MCP连接管理器，每Server独立的异步owner task管理transport/session及关闭，避免跨任务退出SDK上下文；连接失败时下一轮重连，不重启客服。发现并发采用有限上限，不因某个Server阻塞其他来源。初始工程默认：发现每来源3秒、最多1次额外重试、最多20页/100条工具、单定义64KiB、单描述2000字符、全部模型工具Schema总256KiB；超过上限明确标记不可接入，仍由实际token预算作最终检查。具体限值集中配置并测试边界。

执行前刷新本地策略并比较来源绑定；旧轮快照中的工具若已撤权、移除Server、改URL或发生已知定义变化则拒绝，不能只因模型先前见过就调用。确认恢复更要重新核验。下一轮发现最新Schema；如果远端在两次发现之间变化并拒绝旧参数，作为工具失败回灌，不声称本地能冻结外部Server。

断线错误/超时为真故障，不表示查无记录。对工具目录新增无关项不要求重启或重新做检索校准。

## 5. 本地权限与参数校验

本地effect仅read/write，enabled明确允许；未知来源/名称默认拒绝，Server的readOnlyHint、destructiveHint、description、返回文本均不能授予权限。外部工具本章只允许明确白名单中的read；即使配置把外部工具标为write，也拒绝执行并提示此写能力尚未开放。内置write只有create_ticket。

引擎不接收模型可伪造的confirmed=true作为授权。可信执行上下文独立携带conversation_id、request_id、tool_call_id、调用来源和服务器生成的确认凭据；这些字段不在模型Schema中，不从模型args复制。旧按钮授权也只能来自服务器验证的现有action状态。

处理次序：建立调用身份→解析工具/权限（未知或禁用即拒绝）→验证模型参数→系统参数注入→执行或等待确认。缺少真实会话上下文的写调用拒绝；审计conversation_id可空以覆盖异常入口。伪造conversation_id/confirmed等非Schema字段会被校验拦截，不允许覆盖系统值。

采用Draft 2020-12，工具顶层输入必须object。支持required/type/enum/const/minimum/maximum/minLength/maxLength/items/组合Schema及文档内$defs/$ref；禁止网络引用解析。注册时check_schema，拒绝无法安全支持的方言/引用与过深结构。对内置严格additionalProperties=false；外部按其公开Schema执行，但系统保留字段即使远端允许附加属性也禁止进入授权通道。不做“123”→123这类类型强转，不把bool当integer，不接受NaN/Infinity。

参数错误返回稳定错误码invalid_arguments、中文说明及至多5个字段路径/预期约束，不把含敏感原值的验证器异常原样交给模型。业务执行次数和重试数均为0。模型可用剩余步骤重新调用或追问，不无上限重试；资料缺失时优先追问，不凭空补参数。

## 6. 统一执行、错误与结果

一个异步引擎接管业务调用，内置同步函数通过有界线程池，MCP走异步SDK。旧同步execute_tool/execute_tool_result契约保留为适配层，但不能另建权限、重试或审计逻辑；异步调用方不得阻塞主事件循环或跨loop复用MCP session。具体同步桥接由实施计划固定并通过测试，业务主路径优先迁为await引擎。

引擎分为纯prepare（解析/权限/参数，不产生业务副作用）、invoke及record_outcome；图可以先prepare决定是否interrupt，但最终invoke仍重新校验授权。调用身份在准备节点持久化。所有返回ToolExecutionResult保持content、artifact及现有success/error兼容，新增outcome/error_code/retry_count/elapsed_ms等结构化信息；图使用结构化状态，不靠匹配中文判断成功。

重试最多1次，仅对read工具的明确连接暂断、读超时、HTTP 429/502/503/504等暂时故障；小幅退避计入该调用总预算，Retry-After需有上限。普通ValueError、鉴权错误、Schema错误、MCP isError、业务空记录、不支持工具不自动重试。只有可信适配器明确归类为暂时故障的MCP错误才可重试。write总尝试次数固定1，超时标明结果待核实，不自动补发。

timeout是整次逻辑调用（含重试）上限，并取本轮剩余时限的较小值；沿用现有tool_timeout/knowledge_tool_timeout，允许本地工具覆盖。审计耗时使用单调时钟累计活跃校验/执行/重试时间，不包含用户看卡片的等待时间；等待时长可由created_at/completed_at辅助判断。等待后resume建立新预算段，与原request关联，不能沿用已过期的轮时限。

Python同步线程超时不能强杀；线程池有界，写操作超时/断线保留creating或uncertain而非重新执行。不能承诺跨MySQL/SQLite exactly-once；宁可要求核实，也不自动生成第二张票。

三类业务分诊：invalid_arguments、not_found、execution_error；另有permission_denied与timeout执行状态。空查询为not_found且不重试，审计status=success（查询已正常完成）、outcome=not_found；不能伪装为服务故障。服务异常为failure/timeout且如实告诉模型暂时不可用，不泄漏堆栈、连接串或远端原始异常正文。

输出由可信本地投影定义：只留回答必要字段，按enum_map译成人话，json.dumps(ensure_ascii=False)；缺失/未知枚举明确“未知状态”，不能误报成功。新MCP工具授权时配置字段投影与必要枚举映射，通用适配器执行，无需改核心代码。structuredContent优先；文本JSON仅解析受限大小，非JSON文本按许可和长度保留；不把图片/嵌入资源当作文本JSON，也不跟随资源URL。

query_faq(keyword)保持现有文本外部契约、RetrievalResult artifact、证据闸和引用；不要为了统一JSON强行包裹FAQ破坏旧契约。其工具调用也在统一引擎内执行，预算已计量的query_adapter/检索上下文作为可信注入；禁止先在节点检索再伪造一次引擎成功。确定性前置query_order以及与query_faq对应的前置知识检索同样经引擎；多查询扩写/重排仍是检索内部步骤，不伪装多条模型工具调用。

完整允许保留的工具结果在checkpoint artifact中，发给模型的结果仍遵守ch07有界观察；内部字段先裁除再持久化，不把外部不必要数据或凭据保存进artifact。审计仅存摘要，不复制完整对话。

## 7. 明确工单诉求与路由

不能仅移除create_ticket过滤：当前售后→refund，且refund分支禁止额外工具，会把建票/在保/退货进度都引去政策判断。

保持分类器对外八意图JSON不变，在已存在的问题理解步骤增加内部requested_operation（none/create_ticket/read_tool）、candidate_tool（可空）及用户原文来源信息。理解器只看到本轮获本地许可的工具用途数据；read_tool候选必须精确匹配本轮快照中的名称，否则不做工具路由。read_tool表示语义匹配建议，不产生执行权限；权限仍由本地规则决定。自然语言识别由受计量的理解模型完成；服务端验证来源是本会话真实user消息、引用片段确实存在，不能使用工具结果、模型回答或摘要作为建票授权。主力模型不能通过tool args自行设置requested_operation。

明确“建工单”开启ticket_draft状态，绑定原诉求user消息；缺description时主力Agent先追问，缺订单并非所有工单必填，不强制弹订单选择器。type须为售后/投诉/咨询，歧义就追问。description限制为1–2000字符且非纯空白；超过上限先请用户提供精简问题描述，不静默截断关键诉求。草稿内容用同会话用户原文片段组合，不由模型创造订单号、金额、原因；模型传入description须与服务端已核验草稿一致，否则校验拒绝并要求澄清。来源引用验证并不等于语义判断绝对正确，因此自然语言门控以固定反例评估，真正写权限始终以按钮确认状态为准。

否定、撤销、讨论“什么是工单”、引用别人要求、只抱怨、仅要人工，都不能开启新建票流；既有草稿只允许在对应追问的回复中补资料，话题切换/撤销/完成清除。疑义默认追问，不先展示自编工单。前端“取消”结束本次草稿；之后明确再次请求才允许新建。

路由优先级：
- 已核验明确create_ticket诉求/有效补资料→business主力Agent（包括“建投诉工单”），可用create_ticket；不先强制查订单/政策。
- read_tool候选有效且当前八类intent在该工具本地routing_intents中→business主力Agent。物流/订单默认各允许本类；query_warranty与query_return_progress默认允许售后、退款退货、订单、其他；query_product默认允许商品咨询、其他。匹配的是具体查询操作，不能把退货资格/政策/新退款申请当作查询进度。无有效候选时保留原有路由。
- 普通正式投诉但未明确要求建票→原complaint_reply及按钮；原按钮点击就是其既定确认路径。
- 新MCP read工具的routing_intents由本地集成方配置，默认[]，即不新增对确定性分支的覆盖；已进入business时仍可被Agent使用。新增工具想承接原“其他”或商品等请求，只须将对应既有意图列入本地routing_intents，无需修改代码。理解器从动态工具目录提出候选，服务器结合八类intent和本地规则选business；不能仅信Server描述改变路由。普通投诉、闲聊保留固定出口，routing_intents不允许配置这两类；明确建票优先级已单列。新工具注册、授权、配置入口是可信集成方三项配置操作，不把未获许可的发现结果自动开放。

## 8. 工单原生interrupt与恢复

图流程为agent_decide→prepare_tool_call→agent_tools→agent_decide/agent_answer；prepare_tool_call持久化稳定invocation_key、tool_call_id、经校验的参数、draft来源、preview_id及绑定hash；该节点不做审计I/O。拒绝/校验错误也先持久化调用身份与待返回结果，再由后续节点统一写终态审计，保证首次审计前调用身份已经checkpoint提交。旧按钮以已持久化action_id确定性派生invocation_key；没有checkpoint的独立执行器入口在外层逻辑调用创建一次key并传给所有尝试。create_ticket有效且有明确诉求时准备preview；无诉求或参数非法直接经引擎生成拒绝/校验结果，不能弹卡或执行。

agent_tools最前部仅检查已存等待信息并调用interrupt(payload)。不能在此interrupt之前生成随机ID、调用LLM/业务工具、写tickets或插审计；不要用except Exception吞GraphInterrupt。纯prepare可重复执行；等待信息只存checkpoint，不向现有审计表插入虚假的成功/失败或空状态行。最终执行、取消、校验拒绝或失效时写一次终态，重复恢复按稳定invocation_key去重。

payload新增SSE类型ticket_confirmation，包含preview_id、tool_call_id、ticket_type、description、status；前端卡片“确认提交”“取消”，textContent渲染。页面刷新/切会话从state接口恢复当前pending卡片；等待的done只代表SSE流结束，不代表票已创建。

新增POST /api/chat/actions/tool-confirmation，输入仅session_id、preview_id、decision(confirm/cancel)，禁止客户端传description/ticket_type/conversation_id或任意tool args。返回SSE。API在会话锁内验证归属、真实pending interrupt、preview_id、调用及参数hash、最新权限。相同thread_id用Command(resume=服务器验证结果, update=新预算/continuation)恢复；节点再次校验resume与预览匹配。错误返回404/409/422，不偷偷重建草稿。

确认恢复采用以下具体协议，避免“首次执行也看到creating”与重放无法区分：
1. await确认节点验证resume后只提交confirmed/cancelled状态，不写业务。prepare-write节点仅对confirmed且未进入creating的调用在进程内CapabilityStore生成一次性dispatch capability，绑定session_id、invocation_key、args_hash、policy身份与nonce；checkpoint只存nonce和creating状态，不存可复用的执行凭据。
2. prepare-write成功提交checkpoint后才进入execute-write。引擎从CapabilityStore原子取出并消费同一nonce的capability，复核当前权限及参数，然后调用create_ticket一次。缺少capability一律不执行，不能仅凭checkpoint中的confirmed/creating重新签发。
3. 节点重入时capability已消费；进程重启时capability丢失：两者均转uncertain并提示核实，不自动重放。即使实际上还没来得及写库也保守处理。若prepare-write在checkpoint提交前失败，工具尚未执行，原confirmed节点可以重新准备；未持久化的孤立capability有界清理，不能凭其发起执行。
4. pending interrupt跨重启仍可由用户新确认正常恢复；只有已经creating的调用失去自动续写能力。会话锁和preview状态阻止同一确认重复进入prepare-write。旧按钮也先持久化creating，再签发/消费只用于当次调用的capability；重试请求看到creating不重新签发。
5. 成功后将ticket_no和结果保存到completed checkpoint，才向用户宣称成功；终答失败不撤销已完成票，下次状态查询仍返回工单号。写后checkpoint失败沿用uncertain；不从审计表自动推断/重放业务。该方案提供本应用内至多一次尝试的保守行为，不提供跨库exactly-once。

取消通过同一中断resume产生permission_denied ToolMessage，业务写入0次，审计同一行终结permission_denied；模型回复“已取消，未提交”。确认成功ToolMessage携带ticket_no，最终回复必须包含该编号；失败/超时不能编造成功。取消后不因模型继续调用create_ticket而再次弹卡，旧grant不能复用。

重复/并发确认返回已有结果或处理中状态，不再次resume写节点；已取消/过期卡返回明确已处理。新用户消息使未确认卡过期，原调用以permission_denied/expired终结并补齐工具消息配对后开始新轮；无确认的断线等待可刷新恢复。等待时撤销权限→恢复时拒绝。写入中SSE断线/超时→creating/uncertain保持，不自动重放。

旧ch05 /ticket与ch06 /refund请求/响应、按钮数量、表单语义保持不变；内部create_ticket_once改为携带经旧action验证的可信grant调用同一引擎，并纳入审计；保留completed重复返回与uncertain规则，不另弹一张ch08确认卡。非写handoff动作不伪装工具。旧非workflow聊天路径不能直接建票，无确认能力时明确拒绝并提示使用可确认流程。

## 9. 审计模型：映射用户已建表

用户补充“注意SQL表我已经提前建立”。2026-10-25已通过information_schema只读核对tool_audit_logs；没有读取客户记录或执行DDL。实际结构如下，ORM必须精确映射，禁止为满足初稿另增列/唯一索引：
- id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY。
- conversation_id BIGINT UNSIGNED NULL；tool_call_id VARCHAR(64) NULL。
- tool_name VARCHAR(128) NOT NULL；tool_source ENUM('builtin','mcp') NOT NULL；mcp_server VARCHAR(64) NULL。
- arguments JSON NULL；result_summary TEXT NULL。
- status ENUM('成功','失败','超时','校验拦下','权限拒绝') NOT NULL。
- error_message VARCHAR(512) NULL；retry_count TINYINT UNSIGNED NOT NULL DEFAULT 0；duration_ms INT UNSIGNED NULL。
- created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP。
- 索引：idx_conversation_id、idx_status、idx_tool_name及PRIMARY；无外键，无tool_call_id唯一约束。

一条逻辑调用的自动重试合并成一个终态审计。等待确认、creating、uncertain等过程状态保存在checkpoint，不插尚无结论的审计；真实执行成功/失败/超时、校验拦下、权限拒绝或取消时写对应中文状态。业务not_found写“成功”，摘要outcome=not_found；记录已完成查询但没有匹配，绝不重试。纯等待中进程退出不会伪造审计失败，恢复后待最终结果；进程在结果写审计前崩溃可能缺记录，是best-effort审计边界。

arguments保存模型实际参数（按敏感字段规则脱敏）；系统注入会话另列，不保存凭据；非法非object入参保存可序列化摘要，超限使用truncated标记。result_summary保存有界JSON文本，至少含invocation_key、request_id、outcome、摘要文字，必要时附原始remote_name/error_code/source_resolution；中文不转义，保持易读。它是结果摘要，不是完整对话或工具全文。error_message提供脱敏中文并限512字符。

稳定invocation_key存于checkpoint，在首次审计前已独立提交；审计行以result_summary.invocation_key标记该身份，不占用原tool_call_id。正常tool_call_id原样保存；异常超长ID使用“前缀+摘要”映射为64字符，完整ID只在有界摘要元数据内保留，不截断后碰撞。未注册名称由内置调度器拒绝，tool_source=builtin、mcp_server=NULL，并明确source_resolution=unregistered；不冒认某外部Server。已解析MCP工具被拒绝仍记录真实mcp来源。

无需DDL的应用内审计去重：
- 用invocation_key构造长度小于64的具名前缀锁，在独立审计连接执行GET_LOCK(name,0)；拿不到锁则本次跳过并告警，不阻塞业务。
- 锁内按conversation_id、tool_call_id、tool_name、tool_source/mcp_server定位候选，解析有界result_summary中的key；同key已存在则不再插入，否则插入终态并提交。使用参数绑定；不依赖不存在的唯一索引。
- finally在同一连接RELEASE_LOCK；释放失败必须失效关闭该连接，不能把持锁连接放回池。锁仅覆盖短审计事务，不覆盖工具执行/用户等待；commit/rollback本身不释放MySQL具名锁。
- 插入成功但checkpoint未标审计完成时，可用同key重复写审计而不会重复插入；只允许补审计，不重放工具。尚未持久化的结果不能从缺失审计推断为执行失败。
- 保证范围是遵守该协议的本应用写入方；无DB唯一约束不能约束绕过协议的外部写入者。单进程演示边界不变，不把此设计宣传成强一致审计系统。

审计独立短事务，与tickets事务隔离。任何审计错误（含权限不足/连接不可达）记录脱敏告警、不中断业务、不吞原工具结果，不重试业务。审计独立有界I/O等待（初始1秒）与连接/线程资源，驱动连接/读写超时同样有界，不能只取消Python等待就让连接无限挂住。终结更新失败最多一次独立best-effort补写，不启动永久重放队列；告警只带调用key/异常类型，不输出参数/DB URL。duration_ms为活跃校验/执行/重试累计，排除用户确认等待；created_at为终态审计插入时间。

现有状态enum、VARCHAR宽度和unsigned数值均做映射/上限测试；没有request_id/phase等物理列，不在ORM虚构。真实落库验收以隔离fixture和合成数据核对，禁止清库。若后续发现表差异，仅报告，不自动ALTER。

## 10. 两个mock MCP服务

两个独立模块/启动命令，默认建议127.0.0.1:8021/mcp（物流）与8022/mcp（售后），端口正式启动前查占用，不停止未知服务。FastMCP(stateless_http=True,json_response=True)，真实传输使用streamable-http，不能用普通REST冒充MCP。

物流query_logistics(order_id:string非空≤64)：
返回order_id、carrier、current_status、eta、events[{time,status,location}]、is_demo。由Server内部Random和有界缓存生成，时间顺序一致，同参同进程稳定。保留1001/1002/1003演示订单的物流状态一致性：Server复用无DB的纯演示fixture数据，迁移对应物流生成逻辑，不调用客服内置物流函数；一般订单随机mock，所有结果明确演示。随机mock不与真实订单挂钩。

售后query_warranty(order_id:string非空≤64)：
返回order_id、warranty_status、coverage_note、expires_at、is_demo。“在保”明确为演示售后保障状态，不承诺真实二手商品保修条款；政策答复仍依知识证据。
售后query_return_progress(order_id:string非空≤64)：
返回order_id、return_no、status、updated_at、next_step、is_demo。进度仅mock，不创建退货申请/退款。空结果用结构化found=false及中文原因，不抛网络错误；日期/状态逻辑自洽。

本地映射物流/保障/退货枚举为中文并仅投影所需字段；用固定的测试seed/注入随机源覆盖not_found、未知枚举等，不借助真实订单数据。不为验收修改冻结知识/评估标签。

热插拔验收用独立测试MCP服务在同一服务进程动态增删工具，更新本地许可，证明客服PID不变、下一轮能发现/调用；两个业务Server实现不必附带生产管理API或任意代码加载入口。

## 11. ch07上下文、恢复与兼容

“全局固定工具Schema”升级为“本轮快照稳定”；当前轮和对应确认恢复沿用同一公开Schema快照，执行权限使用最新本地规则。snapshot内容/哈希为可序列化State，handler/session不进入checkpoint；恢复按可信当前注册表重建适配器并验证身份，不可直接反序列化执行代码。

预算构建、Gateway bind_tools、reserve_call、ensure_fits和model_ctx日志引用同一个snapshot。Schema增长按实际token扣减历史容量；不足时明确拒绝本轮请求，不暗删部分工具、不截断JSON Schema、不沿用旧固定值。可见性按本地许可确定而非模型判断。模型回答仍tool_choice=none，维持稳定前缀排序和中文序列化。

对新会话/新轮发现工具；确认恢复不把新增无关工具混进原轮schema，但须查最新许可、来源和已注册定义是否有效。老checkpoint没有新字段时提供旧只读兼容快照；不能从旧checkpoint凭空恢复新的写授权。不改原历史/摘要，不重建旧SQLite。

为完整工单参数设置独立decision输出容量：ch08候选默认agent_decision_max_tokens=2600，覆盖至多2000字符的description及工具参数封装；它必须纳入P峰值和实际ensure_fits，不能继续沿用300而让模型输出JSON被截断。具体校准后可收紧，但必须覆盖最大合法Schema参数，预算不足时提前明确提示，不能偷偷放大输出而不扣历史。ch07原profile不修改；ch08小窗口demo需重新计算，不能承诺保留原5650历史容量。

新增ch08专属启动器、profile、checkpoint和测试合成会话，保留ch07原文件。检索校准身份与动态工具目录身份分开：代码/检索管线改变按旧严格规则生成新profile；工具目录正常热变只重计Schema预算并记hash，不要求为每次新增工具跑付费知识校准。模型/预算估算变化需重做代表性token校准。

## 12. 验收与实施边界

可判别的后端行为先RED后GREEN，实施计划拆任务并逐阶段留痕。最低验收：
1. 注册同构、启动内置、重名拒绝、完整分页、新增/注销/Schema更新下一轮生效、断服不回退、目录半失败与配置损坏默认拒绝；实际独立HTTP进程互通。
2. JSON Schema类型/必填/枚举/边界/嵌套/附加字段/本地引用；远程$ref不发网络；拦截后handler调用0次、错误回灌、审计1行。
3. 未授权工具/伪造Server只读声明/模型confirmed/跨会话/过期卡/策略撤销均无法执行写；本地动态许可生效，MCP写拒绝。
4. read暂时错误才重试、业务空结果/永久错误不重试、write超时一次；并发确认一次；超时线程后续完成也不自动再建票；审计故障不影响工具结果。
5. 纯投诉保持旧按钮；明确建票缺描述追问→补资料→卡片；取消0票、确认1票带工单号、双击仍1票；多轮草稿、否定/引用/撤销/话题切换、任意工具输出伪装请求、原文来源核验。
6. 同thread原生interrupt跨应用重启恢复；resume前不重跑业务/生成新ID/新增审计行；新消息过期、断线、成功后终答失败、creating/uncertain重启均不重放；旧/ticket和/refund完整回归。
7. 所有业务入口统一引擎（包括前置订单/FAQ/旧按钮），工具消息ID配对、无无计量模型调用、FAQ artifact/引用与错误语义保留。
8. Schema热变导致实际预算/日志hash变化；本轮稳定、会话隔离、恢复不混新Schema；ch07摘要/历史/多会话/旧checkpoint回归。
9. 已建表ORM/五中文终态/空结果/重试次数/耗时/参数脱敏/来源/无FK；同key重复审计/锁未获/释放失败/插入后checkpoint失败均可判别；真实库用隔离fixture核验，无DDL/清库。
10. 浏览器真实检查卡片、确认/取消、刷新、切会话、重复点击、网络失败、390px布局、安全文本渲染；同时跑冻结路由回归和新增工单/在保/退货进度正反例真实模型评估，报告失败样本不改标签凑通过。

不沿用ch07的758项作为本章结果。完成时记录实际测试/跳过/服务依赖/模型成本、变更文件、剩余问题并审查diff和敏感信息。旧服务不随意停止。额度任何有效窗口剩余<10%时停止新开发任务，先更新CH08_CURRENT_PROGRESS、AI_HANDOFF入口与dev-notes/ch08，再交接并暂停；unknown额度不当作0或无限。

## 13. 由实施者决定的细节与审批内容

实施者可决定模块拆分、测试fixture、注入接口、有限退避具体值、CSS卡片样式与错误码字面值；不能自行改变独立HTTP、本地权限、明确诉求+确认、无写重试、审计非阻塞和旧按钮兼容原则。

本次设计审批特别包含：未知MCP工具默认拒绝且本地许可热加载；外部write本章禁用；轮内Schema固定/轮间刷新；旧按钮内部纳入同一引擎但前端不加确认；只映射已核对的审计表；工单草稿采用可核验用户原文避免自编描述。依赖兼容与真实业务写入验收结果尚未产生，不冒称已通过。

## 14. 文档依据

- LangGraph interrupt官方说明：https://docs.langchain.com/oss/python/langgraph/interrupts 。恢复重入节点、相同thread_id、避免中断前副作用；不照搬新版本stream_events接口。
- MCP SDK 1.30.0：https://github.com/modelcontextprotocol/python-sdk/tree/v1.30.0/docs 。采用ClientSession、streamable_http_client、list_tools、call_tool、isError、structuredContent，不能混用v2迁移接口。
- jsonschema 4.25.1：https://github.com/python-jsonschema/jsonschema/tree/v4.25.1/docs 。Draft202012Validator/check_schema/iter_errors与受控引用Registry。
- MySQL 8具名锁：https://dev.mysql.com/doc/refman/8.0/en/locking-functions.html 。GET_LOCK(name,0)不等待，显式RELEASE_LOCK或连接断开释放，事务提交不会释放。
- 本地依据：ch06/ch07 spec/plan、dev-notes、runtime.py、agent.py、nodes.py、actions.py、tools.py、executor.py、context/assembly.py及tests/test_langgraph_interrupt_contract.py。

## 15. Builder check与作者修订（2026-10-25）

独立审阅提出3个重要设计缺口：写节点首次/重放判别、审计前调用身份持久化、动态read工具路由配置。均为实现设计问题，无需用户另补业务需求；作者已分别在§8一次性capability、§8/9已提交身份与终态去重、§4/7动态routing_intents中明确。期间用户补充已建SQL表，已只读核对13列/4索引/无FK，并整体替换原候选审计表方案。新增decision容量说明解决2000字符工单描述与旧300 token输出上限冲突。没有实施产品代码、没有独立复评；下次在批准后编写实施计划时按修订协议设计故障注入测试。

## 16. 最终评审后的实现裁决（2026-10-26）

本节覆盖早期候选接口中要求模型逐字重生成完整描述的做法；不收窄1–2000字符合法范围。用户已批准Native实施，以下为同一需求内的兼容修复，原literal参数仍保留。

- description_sources支持逐字quote或whole_message=true，二者互斥。whole_message由服务端从本会话真实user行取全文，模型不复写长文本。明确续补时requested_operation=create_ticket，引用原建票诉求；none/read_tool清理旧草稿。单独类型回复以真实用户枚举值补齐，普通ACK不能成为问题描述。
- 已核验collecting草稿正在收集description、且本轮明确为create_ticket续补时，缺少选择器可直接引用当前真实user回复；空话/纯请求/ACK/否定不能补成问题。问题改写或订单消解失败只兜底原问与订单字段；独立的工单来源仍须逐条通过真实用户校验，不因改写失败丢弃，也不把改写结果当授权。
- ready草稿产生绑定conversation、原始来源、完整描述/类型和轮次的draft_id。模型首选create_ticket({draft_id})，不复制全文；JSON Schema仍兼容description/ticket_type的严格literal调用。引用在引擎内解析为完整已核验参数，PreparedCall.resolved_args与原始引用一并哈希绑定一次性capability；前端展示解析后的全文。错误literal不得被静默替换，错误/跨会话/过期引用拒绝。审计保留引用及实际执行描述/类型。
- 每一次实际读取重试前复查当前本地权限、原始定义和端点；MCP handler捕获核验URL并把请求绑定到该owner，不能按source转向新URL。拒绝的重试不计作实际重试。超预算拒绝也解析已注册来源并审计。
- 旧投诉/退款按钮仍走原确认协议。trusted confirmed_action保留完整服务器拼接文本，兼容合法500字note叠加长原问题；新模型草稿的2000上限仍有效。已知入参/权限拒绝为rejected且明确未执行，只有实际结果不明才uncertain。兼容适配仍在统一引擎校验及一次性capability之后执行。
- ch08 demo窗口由候选18k改24k，decision2600；旧18k实测不能容纳一轮。最终七工具预算重新计算，旧ch07 profile不改。动态Schema变化只重计预算，检索校准身份不随工具目录变化。
- 真实库在本轮间新增low_confidence_questions.retrieved_chunks（nullable JSON）和matched_review_id（nullable unsigned BIGINT）；仅ORM兼容既有列，不DDL、不接新增审核业务、不回填数据。

实际验证与唯一独立评审见reports/ch08/review.md和summary.md；没有第二轮独立复评。原失败报告和专属账本保留，未获授权不提交/推送/合并/部署。

