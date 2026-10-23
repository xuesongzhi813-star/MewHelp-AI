# ch06 独立后端终审（修复前）

> 主执行者修后补记（2026-10-23）：下文独立评审原文保留。三个Important已分别复现RED并一轮修复，相关52passed，修后694passed；续接增加启动身份绑定和评估口径回归后最终700passed。无第二次独立review；前端Vibe、启动绑定补充及有限语义计分由主执行者验证。原报告的“暂不建议验收完成”是修复前结论，最新功能/质量边界见summary.md。

结论：暂不建议宣称本章验收完成。Critical 0；Important 3；Minor 0。下列问题均针对当前 ch06 新增行为；没有把 ch05 未提交改动或 HEAD 空提交范围当作无变化证据。该报告是一次独立后端审阅，不包含前端代码评审。

## Important 1 / P1：来源存在不等于订单关联唯一，允许猜最近订单或覆盖本轮明确订单

位置：`app/workflow/understanding.py:35-41`、`app/workflow/orders.py:23-26`。

触发一：历史消息 a 为“订单1001物流”，b 为“订单1002物流”，当前“它能退吗？”。模型返回 resolved、order_id=1002、source_message_id=b 时，校验只查看 b，未发现 a 中还有另一个候选，直接接受1002。触发二：历史 a 谈1001，当前明确“订单1002能退吗？”，模型返回 unchanged、order_id=1001、source_message_id=a，standalone_question仍正确保留1002；所有现有校验通过，verified_order_id返回1001。

影响：跳过订单选择，读取错误订单，后续政策判断及退款申请action绑定错误订单。原话透传不能防止此错误，因为订单关联来自独立字段。审核器短命令直接调用verified_order_id，两个场景实际输出分别为1002、1001；完整understanding路径已由新增RED测试复现，见下方证据。紧凑列表“订单1001和1002”还会被提取为单候选，应一并覆盖。

建议：当前明确订单优先且必须与关联字段一致；历史来源需要可验证的唯一关联，不能仅验证所选消息内出现该数字。跨消息多个候选且当前无明确消歧时保留歧义，进入现有选择器。仅修Prompt不足以保证边界。

## Important 2 / P2：JSON mode只验证宽松解析产物，严格JSON契约及首次解析统计不成立

位置：`app/workflow/models.py:40-48,65-72`；统计使用点 `app/workflow/understanding.py` 的RouterEvaluationGateway.evaluate_case。

触发：供应商raw.content是Markdown围栏、合法JSON后附解释、缺末尾右括号，或重复intent键，但结构化适配器返回合法parsed对象。_parse仅检查parsing_error及parsed，不验证raw正文，随后记录parsed=True。现有新增四个RED用例均显示DID NOT RAISE ValueError。

影响：非法原始输出绕过一次格式修复；分类/理解/扩写被计作首次严格JSON成功。现有“首次100%”只能说明适配器产物通过schema，不能证明用户要求的原始JSON成功率。此处旧_parse基础存在于ch05，但ch06新增json_mode边界、严格JSON及真实评估统计将其变成本章必须处理的交互问题。

建议：仅在JSON mode路径对完整raw正文执行严格JSON解析、重复键检查及schema验证，结算usage后再记录真实解析结果；保持旧function_calling适配器契约。保留attempt1，修复后重跑真实验收，不能回填旧首次成功率。

## Important 3 / P2：常见订单号自然写法无法作为有效来源，真实指代退化为重新选单

位置：`app/workflow/understanding.py:58-59`。

触发：历史“当前讨论的订单是1002”，或当前“订单号： 1002能退吗？”。order_ids只接受紧邻“订单”的数字或ORD前缀，因此明确信息被提取为空。正确模型关联会被fallback清空。主执行者真实冻结验收的chain-shirt-3已遇到自然来源问题，并新增natural_order_number_source及explicit_order_number_with_label_and_spaces两个RED回归。

影响：唯一有效历史无法完成指代消解；用户明明已给订单号仍收到选择卡，关键多轮验收失败。与Important 1联动修复时不能简单扩大成提取所有数字，否则时间/金额可能被误认订单。

## 审阅范围及验证依据

已读批准spec、plan、补充Ruling、review package、AI_HANDOFF和ch06阶段留痕；检查了understanding/orders/routing、graph/nodes/runtime、models/agent/budget、actions/refund_actions/API/schema、固定demo订单、multi_query/filters/query/contracts/retriever/knowledge及相关测试，另静态查看demo_ch06、eval_ch06和新增eval_ch06_policy。baseline hashes用于判定范围；未声称持有ch05原始文件快照。

已核查的结构：await_order实际调用interrupt；续跑使用同thread的Command(resume,update)，新预算和continuation分段保存；会话锁及最新检查点复核防止同进程重复续跑；新消息重置selection；确认写入沿用creating/completed/uncertain且客户端不能指定订单及conversation_id；政策各检索调用的异常会传播，不被其他成功命中吞掉；固定订单、卡片和物流读取同源数据。未发现需在以上结构处另报的问题，但这不代替实际故障注入和服务验收。

审核器自己的隔离pytest在沙箱异步初始化挂起，仅输出部分进度，已Ctrl-C结束，**没有本轮独立全量通过结论**。审核器短同步纯函数probe完成并确认Important 1的verified_order_id错误结果。主执行者应审阅反馈加入的完整RED记录为`log/ch06/review-reference-json-red.log`，本次已读取：9 failed, 22 passed in 3.46s，覆盖跨消息歧义、当前订单冲突、自然来源/空格/紧凑列表及四类非严格JSON。该记录属于主执行者复现，不冒称审核器独立运行结果。

未执行或不判断：用户明确豁免的前端代码；真实工单写入、真实断线/多标签浏览器验收、手机视口、服务部署/鉴权边界、最终新校准与多查询实际质量。未读或输出.env；无DDL、依赖变更、真实业务写入、提交/推送/合并/部署。

建议在以上三项修复并保留RED/GREEN后，再完成固定验收、新校准、12条真实政策对比和最终后端回归。当前真实75/75意图准确率不消除错单保护及严格JSON边界问题；两个多轮输出未达标签也应在最终报告保留沿革。
