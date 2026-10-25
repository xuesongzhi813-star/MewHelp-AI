# ch07：当前会话三层上下文管理设计（已批准）

日期：2026-10-23；状态更新2026-10-25：设计/计划均批准，Native执行，Task0–11已finish；独立review2项Important经RED→GREEN修复，最终全量758 passed。真实证据及裁决见reports/ch07/summary.md、review.md、rulings.md和dev-notes/ch07.md。

## 1. 目标、裁决与现状

把 ch01 的固定滑窗升级为仅当前会话的三层上下文。用户最重视：装得下就保留原文；降级、摘要都可追踪；摘要不得阻塞当前回复；压缩后的早期订单和诉求还能用于理解及回答。没有跨会话记忆、用户画像、历史语义检索或主题打分。

用户原话包括「最近的留原文，一个字都不压」「旧梗概只作背景给模型看、不参与合并」「State带DB id锚点」。指定 LangGraph State/add_messages/checkpoint、LangChain trim_messages、现有 MySQL，不替换组件、不升级依赖。Prompt/数据工作用固定标注集评估，其余可单测后端行为用 TDD。

已获裁决：
- 多会话按同一浏览器归组；localStorage 保存匿名 user_id，各 session_id/conversation_id 独立。
- 5650 的参考预算没有额外公式；由实现方公开分项、校准后验收，不在代码中硬编码 5650。
- 用户已手工建表，只映射，不执行 DDL。
- 额度剩余不足 10% 且无法完成时优先交接及留痕，不冒称完成。

实际产品目录：E:/MewHelp AI/.worktrees/ch05-workflow-agent，分支 ch06-router；根目录仍为 ch04-retrieval-quality。根与产品原有未提交内容全部保留。现有实现已经有 Annotated[list[BaseMessage], add_messages]、AsyncSqliteSaver、每会话锁、原生 interrupt/Command 及确认工单，不重建这些能力。当前固定 history_token_budget=4000，消息的 LangChain id 尚未关联 MySQL 行 id，session_id 目前复用为 user_id。

2026-10-23 只读实测依赖：LangGraph 1.2.15、checkpoint 4.3.0、checkpoint-sqlite 3.1.2、langchain-core 1.6.10、SQLAlchemy 2.1.4。旧有 700 项通过仅为 ch06 历史结果，不代表本章验证。

## 2. 路径与取舍

沿用现有图增加统一上下文构建器、会话仓储和进程内每会话单飞的后台摘要任务。这是既定栈内的自然扩展。
- 不使用自动删除/替换 State 消息的摘要中间件：会破坏完整 checkpoint 与原文回载。
- 不另加 Redis/Celery、另一套记忆存储或向量历史检索：超出指定栈和本章范围。
- 不让后台摘要直接 aupdate_state：它可能与正在执行的图相互覆盖。后台仅原子写 MySQL 摘要和覆盖锚点，下一次会话锁内读取时同步进 State。

后台模型调用不持有聊天会话锁；MySQL 同步 I/O 沿用 asyncio.to_thread。现有单进程演示边界继续生效。后台任务重启后不自动重放业务工具；未完成的摘要下一轮按覆盖锚点重新检查。

## 3. 数据与身份

已只读确认的现有表：
- conversations：id、user_id VARCHAR(64)、status、summary TEXT 可空、summary_upto_msg_id BIGINT 可空、layer1_from_msg_id BIGINT 可空、created_at、updated_at。
- messages：id、conversation_id、role、content、tool_calls、tool_call_id、created_at。
- conversation_summaries：id、conversation_id、seq、from_msg_id、upto_msg_id、content、created_at；唯一索引 (conversation_id, seq)，索引 (conversation_id, upto_msg_id)。

新摘要只进入 conversation_summaries，不读写 conversations.summary；旧字段保留。两锚点持久化在 conversations，并在 State 同步：
- summary_upto_msg_id：已成功提交摘要覆盖的最后一条真实 messages.id，闭区间上界；无摘要为 null。
- layer1_from_msg_id：层1第一轮的用户消息的真实 messages.id，含该行；无历史为 null。
- 两者之间的原始行和关联 checkpoint 消息属于层2；不因降级修改、搬动或删除原文。
- DB id 不连续，不用 from_id-1 推算上一条。按同会话实际消息顺序找边界。

State 保留 messages reducer，新增锚点、摘要段快照、稳定消息 id 到 DB id/所属轮起点 DB id 的映射及当前轮起点。各节点只返回新增消息；改变模型视图不得返回裁剪列表给 reducer。

消息协议：
- 每条用户原话先落 MySQL，拿到 id，再构造稳定 LangChain id 和进入图的锚点。
- 图的内部 AI/tool 消息保持稳定 id，关联本轮用户 DB id；工具结果只进入完整 State/checkpoint，不新写 messages。
- 新轮只把用户原话和用户可见最终客服回复写入 messages；不写纯工具调用 AI 消息，避免留下无结果的调用。旧消息表中的工具记录按旧格式兼容读取，不清理历史数据。
- 用户轮起点使用真实DB id；最终客服消息使用由该轮起点派生的稳定id（例如 assistant:<user_db_id>），内部工具消息另有稳定id。客服回复落库后保存 DB id 映射；只更新同 id 的元数据，不重新追加同内容消息。归档中断恢复先按该用户行至下一用户行的同会话DB区间定位唯一客服终答槽，再核对角色/原文及checkpoint；不能只靠文本相等去重。若出现多个终答或内容冲突，保留现场并报错，不自动覆盖。
- checkpoint 是完整图历史（包括工具结果）的持久化来源；MySQL 是用户可见原文、锚点和摘要的权威。checkpoint 丢失时可从 MySQL 恢复用户/客服原文，但不能恢复本章从未落 MySQL 的完整工具轨迹，必须说明此恢复边界。

匿名 user_id 是演示归组键，不是认证。新 ChatRequest 可带 user_id 和 conversation_id；session_id 继续作为公开会话/图线程标识。创建后canonical session_id使用 u:<user_id>:c:<conversation_id> 的确定性格式（新user_id使用32位hex，长度适配既有动作接口），不新增映射表。新会话首问使用临时客户端key；进程内首问单飞与映射缓存只处理同进程重试。若创建后尚未收到元数据就重启，可能保留一个空/中断会话，可从列表恢复，不承诺这段跨重启首问恰好一次。没有新字段的旧请求保留原 session_id→旧 user_id 的兼容路径；新前端统一走多会话协议。所有动作/恢复 API 共用解析器，核对canonical内user_id/id及数据库归属；POST显式字段与canonical不一致拒绝，GET绝不建会话。

## 4. 三层视图与边界移动

按完整轮划界，用户消息、客服工具调用/结果、最终回复一起归属用户 DB 起点；当前轮不参与历史降级或摘要。

层1：
- 从最新已完成轮往前，用 LangChain trim_messages 配合统一 token_counter 选择预算内后缀，再对齐完整轮和工具配对。
- 保留的 BaseMessage 内容与工具调用参数不变；不截断用户、客服或既有 ToolMessage。当前轮单独追加。
- 不按条数切边界，不因为达到某个轮数强制压缩。

层2：
- 用户内容原样；客服可见回复只保留前60个 Unicode 字符及明确截短标记。
- AI 工具调用与 ToolMessage 成对保留；大块工具正文替换为「工具名/调用id/结果已归档」一行，不复述未经核实的业务事实。
- 只操作副本，完整 State 和 MySQL 原文不变。
- 触发计量使用实际层2模型视图的序列化 token 估算（包括工具标识与消息封装），不是数据库条数。

层1超预算时，移动 layer1_from_msg_id 至保留下来的完整轮起点，记录「层1 降级 X→Y」及数量/tokens。已经降级的轮不因短问题或窗口变大自动升回层1。层2超预算只调度摘要，不同步生成摘要。

后台完成前仍保留尚未摘要的层2，不提前移动 summary_upto_msg_id。每次模型调用另做全请求窗口校验；在摘要尚未追上且无法容纳时，输出明确「上下文预算不足」，保留数据并重试后台任务，禁止静默丢弃用户原话或伪造摘要。

## 5. 追加式后台摘要与一致性

任务捕获不可变快照：conversation_id、原 summary_upto、当时 layer1_from、对应真实最后覆盖 DB id、完整待摘要消息、旧摘要背景和配置版本。不吸收任务启动之后的新轮。

流程：
1. layer2估算超过预算，记录 summary trigger；每会话最多一个任务。已有任务记 skip，不阻塞回复。
2. create_task 后立即返回聊天流程；管理器强引用任务并记录 start。
3. 只从捕获批次提炼新摘要。旧摘要可作背景，不能作为待合并正文；不得重写旧行或把旧事实再写进本段。
4. 摘要输出几十至200个中文字符。保留出现过的商品/SKU、订单号/手机号、明确诉求和未解决问题；不留寒暄；没有出现的事实、结果或承诺不得补造。
5. 双向核验：新实体必须有本批原文来源；本批明确订单号/手机号/SKU及重要诉求、未解决问题不得遗漏。实体清单作确定性校验，诉求覆盖使用独立的原文—摘要核验步骤，并用冻结标注集评估，不能只靠生成器自称覆盖。失败可有界重试或细分批次；仍失败记 fail，不推进覆盖边界。
6. 在短事务中锁定 conversations 行，比较预期 summary_upto；核对无重叠、seq 连续，插入新段，并原子更新 summary_upto_msg_id。layer1_from 已被新轮推进时不得回退。
7. 记录 summary done 第N段、from/upto、耗时、输入/输出估算。旧锚点已变化则 skip 并重新核查，不产生重复段。

追到的是任务启动时的层1起点之前的最后真实消息，不覆盖层1首条。无论是输入超过后台模型窗口，还是200字内无法保留本批必要事实，都按完整轮细分为连续子批，每子批独立新增一段；旧摘要只作背景，不递归压缩新生成段；成功后最终追到快照边界。每个子批提交后可单独恢复。单轮原文也放不进摘要窗口，或单轮必要事实本身超过200字时，记录input_capacity/fact_capacity失败，保留未覆盖层2，不截断用户话或有损推进锚点。这是显式异常边界，不能在报告中冒称已全部摘要。

关停取消任务并记录 skip/cancel；已提交段保留，未提交段下次重试。启动时不启动全库扫描任务。

## 6. 统一上下文装配与前缀

Agent 的消息顺序固定为：
1. 唯一固定 SystemMessage：人设、红线、工具规范、证据数据使用规则。
2. 层2截短对话。
3. 层1原文。
4. 当前用户原话，逐字保留；Agent不再重复注入完整standalone_question。
5. 一条 HumanMessage，合并「早期摘要段 + 已核验指代实体及source_id + 本轮订单数据 + 检索证据」，标为非指令数据；除摘要归A外，所有本轮动态附注共同受E限制。
6. 本轮 ReAct 新增的 AI/tool 消息，按执行次序追加。

没有背景/证据时可以省略第5条。完整standalone_question只用于内部理解/分类/检索，不在Agent请求中重复计入长问题。指代附注只含经来源核验的实体/关联，不截短或改写原用户消息。绝不把摘要、订单、检索结果、每轮时间或决策阶段指示写成新 system。摘要按 seq 正序，不二次合并；实际注入全文纳入预算。

确定性read_order产生的AI/ToolMessage在完整State中照常保留，并标记为本轮preloaded_order；Agent视图同时排除该调用与结果，只把订单数据放入第5条一次（占E）。理解/分类视图仍按历史规则处理，不能在归档或State中删除这对消息。普通Agent工具轨迹仍在第6条，受P计量。

Agent 决策和回答均绑定固定同一套只读工具 + respond_to_user schema；不再按是否检索/退款动态删除工具。执行权限仍由后端限制：退款禁止额外业务查询、query_faq不得重复执行、create_ticket不可直接调用；回答阶段用 tool_choice=none。固定工具可见性不意味着开放写操作。前缀稳定由消息与schema的确定性序列化验证，不承诺供应商实际命中缓存。

coref 与意图识别共用同一份 history_ctx：摘要行在前，层2与层1按时间顺序在后，当前问题独立传递；两者均注入，不再只有指代消解拿历史。各自固定system与结构化JSON契约继续沿用；修复提示放非system消息。每轮即使进入闲聊/投诉/其他固定回复也构建并记录 history_ctx。

## 7. 摘要中的指代来源

摘要段作为带稳定 source_id（如 summary:<id>）的背景来源进入理解。模型不能凭空生成 DB id。
- 验证 source_id 属于当前会话且是本次实际注入的段。
- 订单号必须存在于该段，并回查该段覆盖的当前会话原文，确认实体确实出现。回查是按明确 id 范围核验，不是语义历史检索。
- source_message_id 指向原消息时继续沿用 ch06 验证；摘要来源增加独立分支，不直接放宽原规则。
- 当前明确订单优先；多订单的「它」仍保守处理。
- 「最开始那个订单」可按覆盖范围内首次被用户明确提及的订单核验；「后来怎么说」只能依据已记录结果及未解决诉求，不凭空补充处理结果。
- 商品、手机号、否定和时间等保真也纳入固定评估集。不能仅用“包含订单号”代替整条诉求正确。

## 8. token预算与演示校准

独立区分模型单次窗口和本轮累计调用预算；旧 agent_token_budget=12000 是费用/调用约束，不能当上下文窗口。新预算字段与旧设置兼容关系在计划中列全，不因扩大窗口自动无上限调用。

统一估算器包括中文、标点、ASCII、消息封装、tool_calls/args 与稳定schema，不能只数content。先以当前中文1字≈1token为基线，用真实 provider input_tokens 的固定混合样例校准；若调整中文系数，同步重新校准分项预算，保留旧/新误差结果。

定义：
H = max(0, min(希望保留轮数 × 每轮稳态token, W - S - E - A - O - F - P))
- W：MODEL_CONTEXT_WINDOW，配置的模型窗口，默认128000；不是自动探测能力或声称模型官方上限。
- S：固定system+工具schema+封装的估算，取实际值与配置保底值中较大者。
- E：本轮所有动态附注（已核验指代实体/来源、订单、检索证据及标签）的共同硬上限，受RERANK_TOP_K及单份证据上限约束。摘要另占A，不能把原问题/完整改写藏在E外。
- A：摘要预留，取配置保底与实际注入全部摘要的较大者。摘要增长会减少历史预算，不重写旧段。
- O：MAX_OUTPUT_TOKENS，最终回答预留。
- F：协议封装保底 + 窗口比例安全余量。
- P：当前用户上限 + 当前ReAct峰值。需要包含决策文本/工具参数和工具结果，不把它们算作已经完成的历史。

设计方提出以下公开的演示profile初值，实施阶段必须以固定前缀实际计量和真实usage校准，未验证前不得标为实测：
- W=18000、O=2000、用户上限2000、Agent步骤3、工具观察上限1200、RERANK_TOP_K=5，沿用用户给定。
- S保底2000；E=5×300=1500（指代实体/来源、订单、证据和数据标签共用，不额外隐藏注入）；A保底1000。
- F=ceil(W×2.5%)+400=850；决策输出硬上限300。
- 将 MAX_AGENT_STEPS 定义为包含最终收敛回答的Agent模型调用上限：3即最多2次决策及1次回答；P=2000+(3-1)×(1200+300)=5000。每次决策至多一个工具结果，结果content包含工具协议标签并限1200；决策正文与参数共同限300。确定性前置订单只进入E，不把其AI/ToolMessage重复注入当前轨迹。达到决策上限直接收敛回答，仍保持既有工具/动作权限和显式追问约束。
- H=min(目标历史容量,18000-2000-1500-1000-2000-850-5000)=5650，目标历史容量需大于等于5650。
- 层1=(7×H)//10-1=3954，层2=(3×H)//10=1695；明确留下1token分层拼接余量，使用整数运算，不依赖浮点误差。

上述系数已随本设计获得用户批准，仍不是实际usage校准结果；真实固定前缀若超过S，必须扣除实际值，不倒改计数器凑5650。E超限优先在检索证据生成阶段提供明确有界摘录，保留原文引用，不在层1历史上剪内容。全部摘要超A时扣实际值，日志明确H变化。每次调用再计算实际payload+schema+输出上限，必须小于窗口；reserve_call的累计预算也必须覆盖获准的完整轮，具体默认值由计划固定并经测试验证。

工具边界：完整工具原始返回存入checkpoint的artifact；用于模型的本轮ToolMessage.content在产生时按TOOL_RESULT_MAX_TOKENS生成有界观察，不能把丢失字段伪装为完整结果。已经形成的层1消息之后不修改。此边界与源工具API的完整返回分开测试。

启动自检以最大输入和至少一次最小业务往返算能否放进窗口；失败输出「上下文预算不足」的分项警报并拒绝无效配置。默认profile初值：W128000、希望40轮、每轮稳态2000、O4000、用户4000、步骤8、工具2400、Top-K沿用现有10；通过代表性20+轮固定样例验证不降级不摘要。不能声称任意20轮最大长度都保证不压缩。

## 9. 可观测性

应用启动时为app命名空间配置UTF-8 log/app.log，不依赖演示启动器才写日志，避免重复handler。
- history_ctx：每轮必打摘要全文、共享历史视图逐条id/role/content、窗口条数、token估算、会话/请求id；理解/分类真正调用前的输入也可对应查出。
- model_ctx：每次Agent调用前记录实际最终messages（含全部摘要、滑窗、当前轮轨迹）、工具schema身份、窗口条数、估算分项与输出上限；不能记录构造前的近似视图。
- summary trigger/start/done/skip/fail：捕获/实际覆盖边界、seq、估算、耗时与原因，异常不输出连接串或密钥。
- 预算日志同时列H及层1/层2、触发前后用量、provider usage与估算差异。

精确上下文日志是用户明确要求，仅本地演示使用；质量验收使用合成订单/手机号，仓库报告不复制真实客户对话或真实凭据。log保持gitignored。

## 10. 多会话API与页面

两个新增只读接口：
- GET /api/conversations?user_id=...：只列该匿名用户所有会话，新在前（created_at/id降序），含id、session_id、首条用户原话预览、时间、has_summary。
- GET /api/conversations/{id}/messages?user_id=...：先核归属，按id升序返回用户/客服历史原文；旧tool行不展示但不删库。读取未知会话404，不能创建记录。

不新增创建接口；「新对话」只生成新的客户端会话身份，首条POST /chat原子建立conversations行，SSE增加兼容元数据帧返回conversation_id/稳定session_id。客户端在收到元数据后更新当前会话和侧栏。并发首问需要进程内按客户端会话身份锁保护及缓存检查；canonical后按真实conversation_id定位，不要求新增唯一索引。临时key跨重启的首问去重边界见第3节，不伪造现有表不存在的映射列。

浏览器localStorage归组键和会话列表缓存只保存演示所需身份，不保存用户画像。每个标签页可有自己的当前会话。点击旧会话先加载完整原文，再恢复该会话的订单等待/动作状态；原生Command续跑、工单确认和幂等规则保持。渲染用textContent。流期间切换需取消旧请求并使用视图版本隔离迟到帧，不能把旧会话结果追加到新窗口。

侧栏API失败静默保留基本聊天，不把会话列表故障当聊天故障；重载失败不能清空已有聊天。旧页面sessionStorage记录尽可能保留为一个兼容旧会话，无法证明归属的历史不批量收编。390px及桌面均做浏览器检查。

## 11. 验证、评审及交付

后端TDD覆盖：预算分项与边界整数、中文估算、三层原文不可变、完整轮/工具配对、State/DB锚点、无tool写库、后台单飞/竞争/失败/关停、跨重启续聊、摘要段追加和CAS、来源核验、只读接口不写库与归属、SSE兼容及旧动作回归。

Prompt/数据用固定合成标注集：早期订单/手机号/商品/否定/未解决诉求、寒暄不留、多个订单与最初订单、摘要诱导编造和无证据后续结果；分别测摘要事实、coref/意图、Agent最终回答，保留真实失败样例，不改标签抬分。

真实验收：
1. 默认profile连续至少21轮，无降级/摘要，完整原文回载。
2. 演示profile复跑同类21+轮，日志出现层1降级→层2超预算trigger→后台done第N段；后续最初订单/诉求回答与标注一致。
3. 用受控阻塞摘要替身断言用户done先于summary done；再真实记录时间线。不以“使用create_task”代替非阻塞证据。
4. grep model_ctx/history_ctx能看到实际摘要及窗口；闲聊轮也有history_ctx。
5. 浏览器创建两会话、互相切换、刷新/新标签归组、原文回载、续聊及摘要标记；侧栏失败聊天仍可用；原生订单选择跨重启和确认幂等回归。
6. 相关测试先绿后跑必要全量；MySQL回滚fixture，不清空知识或旧数据；真实验收会话加独立合成前缀，不删除用户数据。

设计独立builder check后由用户审批，再写计划并评审；按任务实现+规格/质量review+修复，最终验证与finish。每阶段即刻追记dev-notes/ch07.md，不统一补写。最终提供演示命令、真实测试结果、报告/留痕路径与限制；不提交、推送、合并或部署。

## 12. 文档依据与未验范围

已通过Context7读取并核对本地签名：
- LangGraph add_messages/checkpoint：https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint/README.md
- LangGraph/LangChain消息裁剪：https://docs.langchain.com/oss/python/langgraph/add-memory
- SQLAlchemy只读反射：https://docs.sqlalchemy.org/en/20/core/reflection.html

本章尚未实跑测试、调用摘要模型或浏览器验收；5650预算系数仍是设计选择。MySQL只读反射成功，未读客户对话、未执行数据或结构修改。实施使用FastAPI及其他具体新接口前继续按用户要求查Context7并对照锁定版本。
