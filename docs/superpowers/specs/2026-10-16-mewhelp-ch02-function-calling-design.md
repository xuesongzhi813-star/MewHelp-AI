# MewHelp AI ch02 设计:Function Calling 工具链

日期:2026-10-16
状态:待批准
前置:ch01-pure-chat(对话/SSE/结构化输出/trim_messages 均已交付)

## 1. 目标

给现有客服聊天装上查数据的能力:LangChain `@tool` 定义五个业务工具,模型自行决定调用;工具执行结果回灌给模型组织最终回答;全程仍走 SSE 流式;聊天记录(含工具调用与结果)落 MySQL。

**单轮调用约束**:一轮最多一次工具调用,执行完必须收敛作答,不做 Agent Loop。

## 2. 已确认决策(用户裁决)

| 决策点 | 结论 |
|---|---|
| session_id ↔ conversations 映射 | 页面 session_id 直接当 `user_id` 用:取该 user_id 最新「进行中」会话,没有则建。API 形状零改动 |
| DB 驱动 | 同步 SQLAlchemy + pymysql(纯 Python,Windows 无装机坑;本机 MySQL 毫秒级,阻塞可忽略) |
| 分支 | 从 `ch01-pure-chat` 顶端切 `ch02-function-calling`(master 尚无 ch01,PR 未合并) |

## 3. 数据库事实(2026-10-16 实测)

本机 MySQL `melhelp` 库,root@127.0.0.1:3306,四张表已建好、全空:

- `conversations`(id bigint 自增 PK, user_id, status enum('进行中','已转人工','已结束')默认'进行中', created_at, updated_at)
- `messages`(id PK, conversation_id FK, role enum('user','assistant','tool'), content text 可空, tool_calls json 可空, tool_call_id varchar(64) 可空, created_at)
- `faq`(id PK, question varchar(512), answer text, category varchar(64), created_at, updated_at)
- `tickets`(ticket_no varchar(32) PK 格式如 T20260701008, conversation_id FK, description text, ticket_type enum('售后','投诉','咨询'), status enum('待处理','已处理')默认'待处理', created_at)

ORM 映射既有表,**不 create_all、不建表、不改表**;灌数据仅 faq 表(其余三表由运行时写入)。

faq 种子数据要点:含「退货政策/流程」条目;**故意不含「邮费」相关条目**——验收标准 3 要演示关键词查不到的漏召回。

## 4. 分层架构

```
app/
  config.py        # + db_url(默认拼本机 melhelp)、tool_timeout、faq 条数上限
  db.py            # engine + SessionLocal 工厂(模块级单例)
  models.py        # SQLAlchemy 2.0 declarative:Conversation/Faq/Message/Ticket
  seed.py          # 灌 faq 测试数据(幂等:已存在同 question 则跳过;CLI:uv run python -m app.seed)
  tools.py         # 五个 @tool + TOOL_REGISTRY
  history.py       # 不动(trim_history/estimate_tokens 原样复用)
  session_store.py # MySQLSessionStore 替换 InMemorySessionStore
                   #   Protocol 不变:get_history(session_id)/append_message(...)
                   #   + get_or_create_conversation(session_id)
  chat_service.py  # 编排:模型定工具 → 执行 → 回灌收敛(从 main.py 抽出)
  prompts.py       # SYSTEM_TEMPLATE 升级为工具版
  main.py          # /chat 改走 chat_service;依赖注入换 MySQL store
  static/index.html# Vibe Coding:工具徽章 + 状态帧
scripts/
  eval_samples.py  # 样例评估驱动(Task 8 交付物,POST /chat 逐组评估)
```
(seed CLI 实际位于 app/seed.py,入口 `uv run python -m app.seed`,见上方目录注;此为 Task 8 实施时的目录修订)

## 5. 五个工具规格

| 工具 | 参数 | 数据来源 | 返回 |
|---|---|---|---|
| `query_order` | order_id: str | 随机生成 | 订单状态/金额/商品等 dict 转 JSON 字符串 |
| `query_product` | product_name: str | 随机生成 | 价格/库存/规格 |
| `query_logistics` | order_id: str | 随机生成 | 物流公司/当前节点/预计送达 |
| `query_faq` | keyword: str | **SQL LIKE 查 faq 表** | question LIKE %kw% OR answer LIKE %kw%,按 id 取前 N(默认 3)条,命中返回"问题→答案"文本;未命中返回"无匹配"明确文本 |
| `create_ticket` | description: str, ticket_type: 售后/投诉/咨询 | **写 tickets 表** | 生成 ticket_no(T + 日期 + 4 位序号,查当日最大序号递增),插入后返回工单号 |

每个工具配 pydantic args_schema(Field 中文 description 帮模型填参)。随机生成工具内部用 `random` 即可,不接真实 API、不建订单/物流表。

## 6. 工具基础设施

- **注册**:`TOOL_REGISTRY: dict[str, BaseTool]`,新增工具一处注册;`bind_tools(list(TOOL_REGISTRY.values()), parallel_tool_calls=False)` 从参数上抑制并行多调
- **参数校验**:`@tool(args_schema=...)` 由 LangChain 完成——本地实测:错参抛 `ValidationError`(langchain-core 1.6.10)
- **错误处理**:统一执行器 `execute_tool(name, args)` 包一层 try/except——校验错/内部异常/超时统统**转成中文错误文本作为工具结果回灌**(模型据此致歉或转人工),绝不打断 SSE 流
- **超时重试**:工具在线程池执行,`tool_timeout`(默认 5s)超时;超时或异常**重试 1 次**;再失败回灌错误文本
- **回灌**:结果包成 `ToolMessage(tool_call_id=...)` 追加进消息列表,二次调用模型

## 7. 聊天编排(单轮)

```
用户消息 → 落库(user) → 载历史(DB→LangChain 消息,含历史工具调用重建)
  → 拼 [System] + trim_history(历史) + [Human]
  → 调用① 模型(bind_tools) astream 流式
      ├─ content chunk → SSE token 帧(逐字照旧)
      └─ tool_call chunk → 累加拼装(本地实测 AIMessageChunk + 可拼出完整 tool_calls)
  → 若出现 tool_calls:
      SSE 帧 {type:"tool", name, phase:"start"}
      执行(校验/超时/重试) → assistant(tool_calls) 消息与 ToolMessage 依次落库
      SSE 帧 {type:"tool", name, phase:"end"}
      → 调用② 模型(**工具可见但 tool_choice="none"**,逼收敛) astream → token 帧 → done
      注记(2026-10-16,Task 8 样例评估修订/Ruling 3):原设计为「不绑工具」,
      实测 thinking-disabled DeepSeek 在无工具上下文中想再调工具时会把 DSML 调用
      标记当正文吐出(Prompt 压不住);tool_choice="none" 服务器侧拒绝调用,
      单轮硬保证不变且更贴合模型预期。
  → 若无 tool_calls:直接 done(与 ch01 行为一致)
  → 最终 assistant 消息落库
```

调用② tool_choice="none" 是单轮约束的硬保证:工具 schema 可见但由服务器侧拒绝调用,
模型只能作答(Ruling 3,见上方注记);编排层对调用②产出的任何 tool_calls 均不执行(专测覆盖)。

**SSE 帧协议扩展**(向后兼容,新增一种):
- `token` / `done` / `error`:不变
- `tool`: `{"type":"tool","name":"query_logistics","phase":"start"|"end"}`

历史重建规则:role=user→HumanMessage;assistant→AIMessage(content, tool_calls 从 JSON 列还原);tool→ToolMessage(content, tool_call_id)。这样下一轮模型能看到上轮用过什么工具。

## 8. Prompt 升级与样例评估

SYSTEM_TEMPLATE 改为工具版:角色/称呼/约束 1「查不到实时数据」删除(现在查得到了),替换为工具使用规范(优先用工具查证、调一次即答、工具失败如实说明并转人工);约束 2/3/4(不私自承诺退款、追问限 2 问、超范围转人工)保留。

产出不是可单测的 Prompt → 按约定**换样例评估**:`docs/samples/ch02-samples.md` 标注 6–8 组样例,覆盖:该调工具的(订单/物流/商品/FAQ)、该建工单的(强烈投诉诉求)、不该调工具的(纯闲聊/在能力边界外)、漏召回预期样例(「邮费」)。跑 deepseek-flash 实测,记录命中/偏差,迭代 Prompt 至全过。

## 9. 测试策略

- **单测(不动真实业务数据)**:config 新字段、models 映射、seed 幂等、五个工具(query_order/product/logistics 纯随机不碰库;query_faq/create_ticket 用**事务回滚** fixture 打真实 MySQL——每个测试开 savepoint,结束 rollback,不留脏数据)、execute_tool 错误/超时/重试路径(fake 工具)、MySQLSessionStore get_history/append 重建消息、chat_service 编排(FakeChatModel:一轮带 tool_calls、一轮纯文本,断言 SSE 帧序列与落库调用)
- **既有 27 测试**:受影响的是 test_chat_api(InMemory→MySQL)——保留 InMemorySessionStore 不删,ch02 测试继续可用;test_config 扩新字段
- **Prompt 样例评估**替代 TDD(见 §8)
- **真实链路验收**:deepseek-flash 跑验收标准 3 条,留证 dev-notes

## 10. 聊天页改造(Vibe Coding,不走评审流程)

AI 气泡内新增工具轨迹徽章:收到 `tool` 帧时在气泡顶部插一枚小徽章(工具名 + 运行状态),start→转圈/呼吸效果,end→定格打勾;徽章风格沿用复古琥珀+青。其余不动。

## 11. 风险与不做

- **不做**:Agent Loop 多轮循环、向量检索/RAG、真实电商/物流 API、表结构变更
- DeepSeek bind_tools 兼容性:OpenAI 协议标准能力,风险低;若实测发现 thinking disabled 与工具调用冲突,停下问用户
- 单测打真实 MySQL:依赖本机库常开;CI 不可移植——ch02 接受(演示项目),ch03 若上 CI 再引入 testcontainer/sqlite 双轨
- 随机生成工具每次结果不同:演示可接受,不缓存
  - 注记(2026-10-16,Task 3 实施时修订):实际实现加了同参 dict 缓存(同一 order_id 同进程结果稳定,重复提问答案自洽)——方向是改进,接受偏差;缓存无淘汰、ch03 长驻进程换 lru_cache(maxsize=...)

## 12. 验收标准(用户原话)

1. 聊天页问「订单 1001 的物流到哪了」→ 气泡带工具徽章(query_logistics),按返回结果作答
2. 问「退货政策是什么」→ query_faq 查到并作答
3. 问「邮费是多少」→ 关键词查表查不到,漏召回为预期,记录留给下一步升级
