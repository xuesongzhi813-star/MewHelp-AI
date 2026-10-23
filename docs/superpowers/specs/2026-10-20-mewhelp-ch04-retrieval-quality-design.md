# MewHelp AI ch04：混合检索、重排与质量评估设计

日期：2026-10-20。状态：用户已回复「批准」，设计已批准，进入实施计划编写；实施计划及执行方式尚待审阅。

## 1. 意图、成功标准与边界

在现有客服聊天上，提高具体型号、口语问法、同义词及带条件问题的检索质量；让事实性知识回答可核查、证据不足可拒答，且能用同一评估集量化四种策略的表现。

用户指定技术保持不变：Milvus 原生 BM25，text 字段挂 BM25 函数，内置 chinese analyzer；dense 和 BM25 各 Top-50，经 hybrid_search + RRF；SiliconFlow `BAAI/bge-reranker-v2-m3` 取 Top-10。嵌入仍为既有 BGE-M3，LLM、依赖版本及基础设施沿用当前项目。

验收：四策略真实报告有数字；具体古着 SKU 可由 BM25 命中；聊天引用可点击看到 chunk 原文、章节路径并定位原文；库外问题明确拒答，并在低置信度表中查到原话和会话关联。

不做指代消解、多轮改写、Agent Loop、后台鉴权、反馈后端、自动学习或在线调参。用户反馈只在前端；不因为数据库已有 faith_cases 表而扩展人工审核平台。

## 2. 已批准裁决与事实

Ruling 1：用户回复「批准进行」，批准新建 `knowledge_hybrid` 集合、保留旧 `knowledge` 集合供回退；Milvus 可保存检索副本，MySQL 继续是原文权威源。不删除旧集合、不清空原文。

Ruling 2：用户选择「继续古着平台定位」。参考材料实际位于 data/kb 的六份英文名 Markdown；只借鉴文档类型及难度，不照搬宠物硬件、品牌、App 或权益。用户已有暂存文档不覆盖，新文档写入独立 ch04 子目录。

Ruling 3：用户选择「以中文知识文档为准」。以原三份中文文档作业务基准，统一新文档与 ground truth；旧库保留，冲突条目不进入新集合。现货通常付款后 24 小时发货；普通地区实付满 99 元包邮，否则 10 元；积分 100 抵 1，最多抵订单金额 20%，有效期 12 个月。通常时效不得写成保证。禁止承诺到账时间优先于任何旧文案。

本地已核实 pymilvus 3.0.2、SQLAlchemy 2.1.4、langchain-core 1.6.10、FastAPI 0.143.0；Python 3.12。MySQL 已有 low_confidence_questions，零 DDL。2026-10-20 两次只读检查时 19530/9091 不通，尚未核实运行中的 Milvus 版本及 BM25 能力；部署文件声明 v3.0.0。恢复后必须实测，失败不得换技术方案。

Ruling 4（2026-10-20修复阶段）：用户明确批准原Milvus3.0.0加独立同版本etcd3.5.23双容器方案，替代旧单容器embedded etcd。原因是初次选举竞态导致leader changed panic。原元数据复制并逐文件校验，原目录保留；外部etcd健康后再启动Milvus。运行说明见deploy/milvus/OPERATIONS.md。修复后实际服务版本3.0.0，真实chinese/BM25/相近SKU/RRF过滤测试已通过；未执行ch04生产集合迁移。

## 3. 方案选择

采用现有薄封装的渐进扩展：MySQL 仓储、Milvus 客户端、query 理解、rerank、证据判定及评估各自分离，通过结构化检索结果连接聊天层；现有 query_faq 文本调用仍可用。

对比方案：删除重建原集合实现少一些，但回退风险较高，已不采用；就地改 schema 易受服务版本和既有字段限制，且难隔离业务口径，已不采用。三种方案均不改变原生 BM25 和指定 reranker。

## 4. 数据与非破坏性迁移

### 4.1 文档及收录范围

新建 `data/kb/ch04/`，收录退货政策、商品 FAQ、售后手册、古着商品规格、会员权益、开票与配送六类文档。均使用 MewHelp 定位与一致口径。商品规格采用演示 SKU，明确是合成演示目录，覆盖牛仔夹克、衬衫、裤装、卫衣、运动外套等；SKU、平铺尺寸、材质、成色、已披露瑕疵与养护形成易混淆型号对。不得把模拟目录写成实时库存或真伪鉴定结论。

复用 build_chunks，不在入库侧复制同义问法或拆存多份。SOURCES 保持材料清单唯一入口，增加显式 ch03/ch04 材料版本选择；前端材料清单与 CLI 必须读取同一选择。旧文件与旧 FAQ 不自动成为新语料源。

新文档中已定义基准之外的商业权益不凭空增加；无现有依据的具体折扣、升级金额、专票能力、保修、补偿及服务时限，写成需人工或以页面公示为准。知识内容出现冲突时，不允许生成模型自行择一承诺。

### 4.2 集合结构

新集合主键 id 仍为 VARCHAR(64)，值为 MySQL chunk.id 字符串；vector 为 1024 维 FLOAT_VECTOR，AUTOINDEX/COSINE。新增 text VARCHAR（max_length=65535，UTF-8 字节长度预检查），enable_analyzer=true，analyzer_params={"type":"chinese"}；sparse 为 SPARSE_FLOAT_VECTOR，由 FunctionType.BM25 从 text 生成；SPARSE_INVERTED_INDEX/BM25 索引。

text 使用唯一标准化的 category/questions/answer 文本，不注入查询同义词。另有 category、product_category、content_type 三个标量过滤字段。category 沿用售后/物流等业务分类；product_category 为商品品类，二者不混用。商品品类从明确的源章节/SKU 目录按确定规则生成，不新增 MySQL 列；规则同时用于索引及回表校验，通用政策标为「通用」。

中文 analyzer 对 SKU 连字符的分词效果必须在服务恢复后 run_analyzer 加真实 BM25 检索验证；查询保留完整 SKU，不在改写中丢失型号差异。验证失败需停下来说明证据并请用户裁决，不替换 analyzer。

### 4.3 迁移与状态

迁移命令先输出目标集合、MySQL 影响范围、待收录材料及旧数据排除数。用户已批准新集合路线；实际写入前仍须核对这些具体目标，不执行旧 kb-reset/kb-rebuild 来代替迁移。

初始迁移先保存旧 MySQL chunk ID 快照，再按 ch04 文档切块及既有指纹去重录入。旧 ID 中与新版标准块完全同指纹的可以复用，其余作为 legacy_excluded_ids，既不复制到新集合，也不参与新集合补漏和一致性计数。旧数据仍可由旧集合读取。

生成原子写入的本机迁移清单 `log/kb/ch04-migration.json`，记录源文件哈希、集合名、原始 ID 快照、复用/排除 ID、进度及完成状态；不含原文、对话或凭据。清单属于演示环境的本地运行状态，随数据库及集合一同备份，不是另一份原文权威源。已有清单不可被重跑覆盖成新基线；丢失或目标不匹配则中止并明确提示恢复，不能误将旧库全量纳入新语料。

初始清单之后的新 chunk 默认属于当前语料；既有后台手工录入和挖掘路径继续可用。仅按排除列表排除历史行，不要求每次录入额外注册到文件。切换前逐 ID 核对新集合与预期收录集合、hash、关键 SKU、中文分词及失败恢复；任何缺失保留未完成状态，不报告迁移成功。

正常新增仍为 MySQL pending → Milvus upsert/flush → MySQL done。迁移必须对所选 ID 全量校验/回填，不能因旧 MySQL 为 done 跳过新集合。补漏只处理目标语料中的 pending；新集合恢复只操作新集合所选语料，不重置旧行。overview/status 的计数明确区分 MySQL 总量、当前语料、排除历史行及目标集合量，不能继续把全库 done 数直接与新集合对比。

回退切换配置到旧集合及旧 dense 策略；不自动删除新知识或新集合。旧集合只保证迁移前快照，不声称含切换后的新增知识。

## 5. Query 理解与检索

QueryPlan 只接收本轮原话，不使用历史：raw_query、normalized_query、lexical_query、intent、显式过滤条件及改写说明。先规范空白/全半角，保留 SKU、金额、数量、否定词及时间起点；LLM 将口语归一，检索侧有限同义词表扩展 lexical_query。dense 使用 normalized_query；BM25 使用保留原型号的 lexical_query。入库原文不扩写别名多份。

聊天编排每轮只生成一份 QueryPlan，经系统注入的内部参数传到 query_faq；该参数从模型可见 schema 摘除，不允许模型覆盖原话或过滤条件。直接调用 query_faq 或检索 API 时可由检索入口生成 QueryPlan，评估则预先生成并复用。改写失败回到本轮原话及显式过滤，不丢弃 SKU 后继续搜索；记录回退原因，不伪称改写成功。改写、检索、rerank 和生成分别有有界超时，FAQ 专用总预算覆盖内部调用链，不盲目受原先 10 秒工具预算截断；具体值及最慢路径验证进入实施计划，其他工具预算沿用原配置。

禁止因推测品类而强过滤。仅用户明确提出条件或 API 显式传入时使用过滤；明确 SKU 可从目录确定品类。过滤模型输出和用户 API 参数都必须经允许值校验，字段名白名单、值参数化，不能接受原始 Milvus 表达式。两条 AnnSearchRequest 使用相同过滤，在召回前生效。显式品类过滤默认精确匹配；需要通用政策时调用者明确加 include_general，不偷偷放宽条件。

四种策略共用 QueryPlan、语料及过滤：dense Top-50；bm25 Top-50；hybrid 两路各 Top-50，经服务端 hybrid_search + RRFRanker(k=60) 保留最多 100 个去重候选；hybrid_rerank 在同一融合候选上经指定 reranker 取 Top-10。在线默认 hybrid_rerank。禁止在 Python 自行 RRF 冒充 hybrid_search。

本地 PyMilvus 3.0.2 的 MilvusClient.hybrid_search 参数为 ranker；不用 ORM Collection 示例的 rerank。保留单路 search 以供评估和原有向量去重使用；明确 anns_field，防加入第二向量字段后歧义。挖掘去重继续 dense/COSINE，不改用 RRF 分数。

回 MySQL 获取原文并保持候选排序，跳过不存在的 ID 且报告异常数量。记录 dense/BM25/fusion/rerank 分数的类型，不把 RRF 或 BM25 分数当余弦，也不称其为概率。reranker 出错是服务失败，不默默降级成其它策略。

SiliconFlow 请求 `/v1/rerank`，model=BAAI/bge-reranker-v2-m3，query=normalized_query，documents=MySQL 候选文本，top_n=10，return_documents=false；严格校验返回 index 唯一/越界/分数有限/条数。配置单独的 base URL/model/key，并允许 key 缺省复用已有 embedding key，不读取或输出真实值。不升级依赖；若选用 httpx 作为运行时客户端，须从已有 dev 依赖提升为显式运行依赖并保持锁定版本。

## 6. 证据充足性、引用和聊天契约

### 6.1 结构化结果与兼容边界

内部 RetrievalResult 含 QueryPlan、strategy、按相关性排列的 hits、分数类型及拒答原因。Hit 含 chunk_id、原文、section_path、category/product_category、content_type。content 是给模型的文本，artifact 是给编排层的结构化副本，不经模型重新解析原文来拼引用。

query_faq 对模型可见参数仍仅 keyword；直接 invoke 普通参数字典仍返回原「N. 问题\n→ 答案」文本及原无匹配文案。内部采用 LangChain content_and_artifact，经 ToolCall 调用可取得 ToolMessage artifact。执行器增加结构化执行入口，并保留 execute_tool(...) -> str 兼容包装；所有工具仍共用校验、系统注入和超时重试，不绕过执行器。

重试时只采用成功且被等待的那次结果；超时后仍运行的线程不得通过全局变量覆盖本轮引用或写问题池。引用、低置信度写入与会话关联均由聊天编排消费最终结果后执行，检索函数无这些副作用。create_ticket.conversation_id 的现有系统注入契约保持。

### 6.2 低置信度与生成自评

正常无召回或在线 rerank 最高分低于配置阈值，确定性拒答，不请求模型编造；source=retrieval_low_conf。阈值初始开发起点为 0.5，不解释为 50% 正确率；用独立校准子集选择并记录冻结值，禁止用测试集调阈值。

有证据时，生成阶段返回结构化 AnswerDecision：can_answer、reason、answer、used_citation_numbers。要求模型判断现有证据是否覆盖本轮关键条件；缺字段、内容冲突、仅话题相似或仅支持问题一部分时拒答，source=self_check。除非用户明确拆分提问，不用一小段证据伪装回答整个问题。

为避免先泄出猜测再改口，知识问答在 can_answer 和引用校验通过前缓冲文本；通过后按文本片段发 token SSE，保留 token/tool/done/error 类型及字段。代价是知识回答首个正文 token 更晚出现，工具状态照常显示。首轮工具路由的 content 同样先缓冲，不能在检索前发未经验证的事实性回答。QueryPlan 判断需要知识但模型未调 query_faq 时，由编排层执行一次 query_faq；不追加第二个业务工具、不做 Agent Loop。闲聊和现有订单/物流/工单路径仍沿用各自契约。

自评非法结构、未知引用、无引用的事实性知识回答、禁止承诺或生成失败时，不发布草稿。内容质量不合格可进入 self_check；模型/检索服务不可达明确返回服务异常，不算普通无召回，不用其污染低置信度知识缺口统计。问题池写失败返回明确错误，不能显示「已记录」或把失败当已达验收。

### 6.3 引用与首尾排布

引用编号先按相关性排名固定为 [1]...[10]，再改变 prompt 中的证据排布，绝不因排布重新编号。若十条证据按排名 1..10，展示给模型顺序为 1,3,5,7,9,10,8,6,4,2；最相关两条在首尾。不足十条遵循同一交替规则。保留相应前后文但不把未召回的相邻块自动当已引用证据。

给模型的每条证据明确写编号、chunk ID 和章节路径；引文标记为 [N]。生成后校验引用必须属于本轮证据且至少一条；段落级事实要求引用。自评 can_answer=false 不生成引用正文。System Prompt 将检索文本视作数据，不遵从文档里的指令。

新增 citations SSE 帧：items 中为实际被答案使用的编号、chunk_id、section_path、原文快照和同源 original_url；在正文 token 前发送。URL 由服务端生成 `/kb?chunk_id=<id>`，不采信模型 URL。GET `/api/kb/chunks/{id}` 从 MySQL 取原文，不存在 404，数据库不可达按服务错误处理；/kb 深链接加载并突出展示该块及章节路径。来源文件可唯一匹配 SOURCES 时再提供白名单源文档链接；手工/挖掘来源不伪造文件路径。chunk 本身即最小可定位原文，不用完整路径猜测磁盘文件。

本章不新增消息 metadata 列；当前聊天页无历史恢复接口，前端持有本轮引用快照。消息正文及工具文本已有编号信息；未来历史恢复需单独设计持久化引用映射，不宣称本章已支持。

### 6.4 问题池与负面知识

只映射现有 low_confidence_questions：raw_question 存当前用户原话，conversation_id 使用真实会话 ID，source 为 retrieval_low_conf 或 self_check，reason 为简短可审计原因，created_at 交数据库默认值。user_feedback 枚举本章不用。一次请求只在编排层终态写一次；用户重问是独立样本，断线重试不承诺跨请求 exactly-once。

System Prompt 明列：不保证退款/支付异常款项具体到账日期或天数；不保证物流必达日；不承诺赔付/补偿/免费维修；不保证古着真伪、全新成色、必然合身或实时库存；不把一般处理时效变成承诺。可以引用证据中的申请条件或一般发货说明，但涉及到账承诺必须拒绝作保证。规则适用于任何工具分支；涉及这些承诺的其他业务回答同样须先缓冲校验，不能先发 token 再拦截。订单/库存工具返回的当前模拟查询值可转述，但不扩写成未来保证。

## 7. 前端配套（Vibe Coding）

沿用现有聊天页风格。收到 citations 后保存到当前回答对象，只有已知编号渲染为可点按钮；未知编号不能生成任意链接。点击打开抽屉/弹层，显示编号、章节路径、MySQL 原文快照及「定位原文」按钮；原文按文本安全呈现，保留换行，支持关闭及键盘操作。浏览器实测流式分片跨越 [N] 的情况、关闭/重开、新问题之间引用不串、错误回答不残留来源。

每条正常完成的 assistant 回答左下角显示 👍/👎；点击立即点亮所选、显示「已反馈」、两个控件锁定。状态挂在消息对象，局部重绘后保留；用 sessionStorage 保存本页会话内的 message key/选择，避免重绘解锁。不发送后端、不写 user_feedback、不声称已入库；当前页面不恢复历史消息，因此不承诺刷新后能重新展示旧对话。error/未完成回答不显示可反馈状态。

前端按用户要求直接调整及浏览器验证，不套 brainstorm/TDD/code-review 子流程；后端引用接口及 SSE 契约仍需测试和评审。

## 8. 评估集、指标与报告

评估数据存 `data/eval/ch04/`，共 96 条人工可审阅的合成标注样例。六桶各 16 条：具体型号、口语模糊、同义词、元数据条件、多证据组合、库外/不可承诺。每桶固定 4 条校准、12 条保留测试，共 24/72。难度 easy/medium/hard 由是否需精确匹配、改写、过滤或组合证据标注。分割文件固定，不用测试结果反向修改阈值后仍称为测试成绩。

每条标注 id、raw_query、bucket、difficulty、filters、answerable、ground_truth_answer、required_facts、forbidden_claims、relevant_chunk_refs。chunk refs 用 source path + section path + 内容指纹绑定当前语料，运行时映射数据库 ID；找不到、映射歧义或源文件 hash 改变必须使评估失败，不能跳过样例或凑零分继续。不可回答题的相关集合为空，不计入检索 Recall/MRR 分母。

四策略复用同一 QueryPlan、embedding 和语料快照；保留预处理开关的诊断入口，但正式四策略报告预处理一致。记录 normalized/lexical query、过滤条件、候选/最终排序及分数、时延、模型名、参数、语料 hash、随机/温度设置、评估版本和成本用量（接口可得时）。缓存只复用相同配置、相同语料的结果，报告注明是否命中缓存。

检索指标：Recall@5、Recall@10、MRR@10；所有策略另报 candidate Recall@50。rerank 的 @50 明确是 rerank 前的融合候选，不将 Top-10 重排结果扩成 50；最终 @50 不适用。Recall 为相关 chunk 命中比例；多证据题另报是否命中全部 required refs，不能以第一条命中代替完整证据。按 query 桶、难度及全局汇总，给样本数和分母。

正式四策略生成对比统一最多给各策略最终前 10 条，使用同一首尾排布、AnswerDecision Prompt 和拒答规则；均只做非空硬闸及同一自评，关闭策略专属分数阈值，避免把阈值收益误算为检索提升。另报 hybrid_rerank 在线部署配置下的结果，使用其冻结阈值；分开展示正式公平对比及部署验收，不套用 RRF/BM25 的不可比分数。

Faithfulness 由固定评审 Prompt 拆分答案中的可验证事实，判断是否被提供的证据支持，并返回逐事实证据、支持数/事实总数和理由；不凭 judge 常识补证据。记录评审模型及原始结构化结果；不新增评估框架依赖。评审 LLM 可使用现有配置模型，但说明同模型评审局限，至少人工抽查每桶两条及所有高分矛盾例。空回答/纯拒答的 faithfulness 为 N/A，不记为 1，另报回答覆盖率、正确拒答率、误拒答率、required_facts 覆盖率，防止全拒答刷高分。judge 失败单列失败数，不能计为零或通过。

CLI 产出 JSON 明细和 Markdown 报告至 `reports/ch04/`，含四策略数字、分桶结果、失败题及案例来源。默认评估不写业务会话或问题池；独立聊天验收才验证真实入池。Faithfulness 报告需调用真实模型，fake 输出不作为验收数字。交付时展示实际结果，即使混合/重排未胜过基线也如实报告，不预设必胜。

## 9. 模块边界与验证

预期扩展现有 app/kb/milvus.py、store.py、dualwrite.py、retriever.py、ingest.py、sources.py、models.py、tools.py、executor.py、chat_service.py、prompts.py、api/kb.py、config.py；新增 query 理解、rerank、证据生成、低置信度仓储、评估模块和脚本。目录细分及任务顺序在实施计划中确定，不引入 LangGraph 或替换现有链路。

后端代码按 TDD：schema/analyzer/索引及 API 参数；两路 Top-50 和相同前置过滤；SKU 改写保真；rerank index 校验；首尾排列和编号；artifact 与旧字符串接口兼容；超时旧线程不能串引用；自评拒答/无工具旁路/禁止承诺；问题池真实会话绑定；引用 endpoint/SSE；迁移断点和旧数据隔离。保留 create_ticket 注入、单轮工具、错误语义和作业 argv 白名单回归。

Prompt/数据任务用标注样例验证并记录输入、预期、实际、失败归因及迭代，不写镜像实现的测试。MySQL 测试沿用事务/SAVEPOINT 隔离；Milvus 测试只用唯一测试集合并只清理自身集合，不清生产数据。真实迁移和评估前先核对服务、目标及数据范围。

流程：设计及 spec 批准 → writing-plans → 计划评审和执行方式选择 → 任务级实现/验证/留痕 → 后端 code review 与修复 → 真实四策略及聊天验收 → finish。未获用户明确授权不提交、推送、合并或部署。前端执行用户豁免。

## 10. 用户已批准的新增设计及环境门槛

本稿新增的具体约定包括迁移清单及排除历史数据范围、品类过滤语义、结构化 artifact 兼容路径、校验后输出知识正文、96 条样例的分割与指标分母。用户在审阅请求后回复「批准」，完整 spec 已获批准；Ruling 1–3 继续有效。

此前Milvus不可达门槛已由Ruling 4的授权修复解除，真实兼容性测试通过。后续正式迁移/评估仍须核对当前环境、目标和语料，不用一次服务恢复冒充整章验收。

## 11. 已核对的官方资料

- [Milvus chinese analyzer](https://github.com/milvus-io/milvus-docs/blob/v3.0.x/site/en/userGuide/schema/analyzer/analyzer/chinese-analyzer.md)：内置 type=chinese。
- [Milvus BM25 schema](https://github.com/milvus-io/milvus-docs/blob/v3.0.x/site/en/aiTools/schema_design.md)：text → BM25 Function → sparse 字段。
- [PyMilvus 请求与 ranker](https://github.com/milvus-io/pymilvus/blob/master/_autodocs/api-reference/rankers.md)：AnnSearchRequest、过滤和 RRFRanker；客户端关键字另以本地 3.0.2 签名核实。
- [SiliconFlow rerank](https://docs.siliconflow.cn/docs/api/rerank-post)：指定模型、query/documents/top_n、index/relevance_score。
- [LangChain tool response_format](https://reference.langchain.com/python/langchain-core/tools/convert/tool)：content_and_artifact。
- [SQLAlchemy reflection](https://docs.sqlalchemy.org/en/20/core/reflection.html)：只读核查既有表结构。

以上资料已通过 Context7 获取；设计依据不是未来版本接口保证，实施仍需锁定版本核验及真实探针。

## 12. 本次自审结论

已核对需求覆盖、旧接口兼容、源数据冲突、迁移重复运行、引用编号及评估分母。修订了三个设计隐患：QueryPlan 必须系统注入并在四策略间复用，避免重复改写改变比较对象；超时及重试不能让后台旧任务写引用/问题池；正式生成对比关闭策略专属阈值，在线阈值验收另报。LangChain 本地源码确认普通参数 invoke 的 content 返回与带 tool_call_id 的 ToolMessage 返回可区分，实际行为仍需实现阶段的兼容测试。

本次设计阶段无产品代码改动、无迁移执行、无真实评估成绩；本稿随后已获用户批准。环境不可达及未来阈值校准有明确验证步骤，不以文档自审冒充完成验收。

## Task14 review Ruling（2026-10-20）
自动SKU过滤保留通用政策；显式过滤不变。业务路由与requires_knowledge独立，订单号不能豁免知识证据关口。评估缓存及冻结阈值绑定实际eligible原文/ID/元数据及目标集合快照，并检查运行期间不变；embedding故障分策略记失败，BM25可继续。

## 用户批准的 v2 修复 Ruling（2026-10-21）
根目录六份宠物参考删除，ch04六份古着知识扩展至124块；新集合knowledge_hybrid_ch04_v2_final与独立清单发布，旧行/集合保留。引用格式最多修复一次，同45秒预算，去引用/Markdown后的事实文字必须相同，禁止承诺与全部引用仍复验。知识意图在执行任何工具之前固定选择FAQ，避免选错商品工具而误拒答；business意图仍执行原单个业务工具，业务混合政策题没有证据不得追加第二工具。正式96题标签和分割不改，只重绑章节来源指纹并重新校准/评估；额外15题为开发回归，不混入保留测试。v1成绩归档，不作为v2结果。
