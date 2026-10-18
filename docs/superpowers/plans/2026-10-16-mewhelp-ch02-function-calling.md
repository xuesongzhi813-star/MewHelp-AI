# MewHelp AI ch02 实施计划:Function Calling 工具链

日期:2026-10-16
设计文档:`docs/superpowers/specs/2026-10-16-mewhelp-ch02-function-calling-design.md`(已批准)
执行方式:Subagent-Driven Development(实现者+评审者,每任务过 ledger/Rulings/fix loop)

## 任务分解

### Task 1:分支 + 数据层基建
- 从 `ch01-pure-chat` 切 `ch02-function-calling`
- 依赖:`uv add sqlalchemy pymysql`
- `app/config.py`:+`db_url`(默认 `mysql+pymysql://root:...@127.0.0.1:3306/melhelp`,密码可 env 覆盖)、`tool_timeout=5`、`faq_max_results=3`
- `app/db.py`:engine + SessionLocal(模块级)
- `app/models.py`:Conversation/Faq/Message/Ticket 映射既有四表(不 create_all)
- 测试:config 新字段解析、models 与真实表结构对齐(连库 introspect 核对列名/类型)
- 交付:`uv run pytest` 全过

### Task 2:faq 种子数据
- `app/seed.py`(含 `__main__` CLI 入口):幂等灌入 ~12 条 faq(退货政策/流程、换货、发票、尺码、会员、优惠券、发货时效、修改地址……),**故意不含「邮费」**;分类覆盖若干 category
- 测试:事务回滚打真实库,断言插入条数、幂等重跑不重复
- 交付:`uv run python -m app.seed` 后 faq 表有数据

### Task 3:五个业务工具 + 注册表
- `app/tools.py`:
  - `query_order(order_id)`/`query_product(product_name)`/`query_logistics(order_id)`:random 生成,不碰库;返回 JSON 字符串
  - `query_faq(keyword)`:SessionLocal 查 LIKE %(question/answer)%,前 N 条;未命中返回明确「无匹配」文本
  - `create_ticket(description, ticket_type)`:生成 ticket_no(T+YYYYMMDD+4位序号,查当日最大递增),写 tickets 表
  - 全部 `@tool(args_schema=Pydantic)` 中文 description;`TOOL_REGISTRY: dict[str, BaseTool]`
- 测试:随机三工具纯内存;faq/ticket 事务回滚;args_schema 生成的 JSON schema 正确
- 关键约束:工具函数**同步**签名(设计定死同步驱动)

### Task 4:工具执行器
- `app/tools.py`(同文件或 executor 拆分实现者定):`execute_tool(name, args) -> str`
  - 未注册工具 → 中文错误文本
  - `ThreadPoolExecutor` + `tool_timeout` 超时;超时/异常**重试 1 次**
  - 全部失败 → 统一中文错误文本(含工具名),绝不向上抛
- 测试:正常路径、错参、超时(sleep 工具+timeout 0.1)、重试后成功(首次抛二次成)、二次全败

### Task 5:MySQLSessionStore
- `app/session_store.py`:`MySQLSessionStore` 实现 ch01 的 `SessionStore` Protocol
  - `get_or_create_conversation(session_id) -> int`(user_id=session_id,取最新「进行中」,无则建)
  - `get_history(session_id)`:载 messages → LangChain 消息(user→Human;assistant→AIMessage(content, tool_calls JSON 还原);tool→ToolMessage(content, tool_call_id))
  - `append_message(session_id, message)`:三类消息分别落列(tool_calls 序列化 JSON)
- 测试:事务回滚——轮转写入 user/assistant(带 tool_calls)/tool 三条消息,读回重建比对(类型/content/tool_calls/tool_call_id 逐一断言);新 session 自动建会话;未知 session 空
- `InMemorySessionStore` 保留不删(ch01 测试继续用)

### Task 6:chat_service 编排(核心)
- `app/chat_service.py`:
  - `stream_chat(session_id, message) -> AsyncIterator[str]`:SSE 帧生成器
  - 调用①:`model.bind_tools(tools, parallel_tool_calls=False)` astream;content chunk→token 帧;chunk 累加拼 tool_calls(官方 `gathered + chunk` 姿势)
  - 有 tool_calls:发 `tool` start 帧 → execute_tool → assistant(tool_calls)+ToolMessage 落库 → `tool` end 帧 → 调用②模型**不绑工具** astream → token 帧
  - 无 tool_calls:直接收敛(与 ch01 一致)
  - user 消息落库在调用前;最终 assistant 落库;错误→error 帧不落 assistant(ch01 遗留行为先保持,终态一致性 ch02 不展开)
- `app/prompts.py`:SYSTEM_TEMPLATE 升级工具版(删约束1,加工具使用规范;保留 2/3/4)
- 测试:FakeChatModel 双场景——①返回 tool_calls 再返回文本(断言帧序列 token…/tool start/tool end/token…/done,bind 二次调用不带工具);②纯文本(与 ch01 帧序一致);store 落库调用次数与消息类型断言

### Task 7:main.py 接线 + 既有测试适配
- `/chat` 改走 chat_service;store 依赖注入换 `MySQLSessionStore`
- test_chat_api 适配:FakeChatModel 注入路径改 chat_service;数据库依赖用事务回滚 fixture 或 fake store(实现者按 chat_service 的注入缝选择,评审裁决)
- 全量 pytest 通过(ch01 27 个 + ch02 新增)

### Task 8:Prompt 样例评估(替代 TDD)
- `docs/samples/ch02-samples.md`:8 组标注样例(订单/物流/商品/FAQ 正例、建工单强烈诉求、纯闲聊不该调工具、「邮费」漏召回预期、能力边界外转人工)
- deepseek-flash 实测每组:选没选对工具/参数/回答质量,记录;Prompt 迭代至全过(预期 1-2 轮)

### Task 9:聊天页工具徽章(Vibe Coding,主会话直接做)
- SSE `tool` 帧处理:气泡顶部工具徽章(start 呼吸/转圈 → end 打勾定格),复古琥珀风
- 与用户来回调效果,不走评审流程

### Task 10:真实链路验收 + 最终评审
- deepseek-flash 跑验收 3 条留证(订单物流徽章+作答 / 退货政策 FAQ / 邮费漏召回)
- 整分支 code review(实现者-评审者模式),fix loop
- dev-notes 补齐各阶段条目,交付总结(演示命令/测试结果/文档路径)

## 依赖与顺序

Task 1 → 2/3(可并行)→ 4 → 5 → 6 → 7 → 8/9(可并行)→ 10
Task 9 依赖 Task 6 的帧协议落地,徽章样式与后端解耦可先行联调。

## 风险预案

- DeepSeek 工具调用 + thinking disabled 若实测冲突 → 停下问用户(Task 8 前置探测)
- 本机 MySQL 必须 3306 常开;测试全走事务回滚,不污染业务数据
- 端口/进程管理沿用 ch01 教训:uvicorn 用会话后台任务,验收留证用脚本不用交互式
