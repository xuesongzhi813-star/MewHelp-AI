# ch09真实Cloud审计

以下来自官方SDK Observations v2真实读回。Dashboard视觉核对已由用户人工完成并回复“已看到完整调用树、Prompt 和 Token”；早期原生浏览器策略阻塞及专用浏览器超时保留为历史失败，不作为代理点击成功的证据。

## known

Trace：[33129354220b4850a2b1376500fbaf2f](https://jp.cloud.langfuse.com/project/cmvotjvji00fmad0gmp6tmapw/traces/33129354220b4850a2b1376500fbaf2f)。
系统session_id：u:fff436e6932d4212878df005f29cda07:c:8536；59个观察，5个Generation；所有子观察session匹配，无悬空子节点。
各真实Generation总Token：[1707, 3228, 1046, 660, 1577]。成本未获Cloud价格映射，保留null。

## gap

Trace：[bc9347e5fc6341d08227d8bfa7ef10c7](https://jp.cloud.langfuse.com/project/cmvotjvji00fmad0gmp6tmapw/traces/bc9347e5fc6341d08227d8bfa7ef10c7)。
系统session_id：u:fff436e6932d4212878df005f29cda07:c:8537；53个观察，3个Generation；所有子观察session匹配，无悬空子节点。
各真实Generation总Token：[1125, 668, 1591]。成本未获Cloud价格映射，保留null。

## later

Trace：[b75786986c3743b6a12be673fdb621be](https://jp.cloud.langfuse.com/project/cmvotjvji00fmad0gmp6tmapw/traces/b75786986c3743b6a12be673fdb621be)。
系统session_id：u:fff436e6932d4212878df005f29cda07:c:8537；27个观察，2个Generation；所有子观察session匹配，无悬空子节点。
各真实Generation总Token：[710, 1620]。成本未获Cloud价格映射，保留null。

## 树与字段核对

已知问答实际包含chat→LangGraph→resolve_reference/classify_intent→retrieve_knowledge→tool-execution/knowledge-retrieval→embedding/Milvus/MySQL回表/rerank→evidence_gate/knowledge.sufficiency→agent_decide/agent_tools/agent_answer→ChatOpenAI与log_result。各节点、Span、Generation输入/输出及start/end实际可读；LLM输入保留真实system/human Prompt，状态输入省略无关旧历史。同步/异步/流式及异常/取消SDK内存导出另有回归覆盖。

SDK按指定trace_id构造NonRecordingSpan虚拟物理父；is_root_observation=true的应用逻辑根不要求该虚拟父实际导出。其余每个parent_observation_id均在本Trace存在。

正常停止已实测closed/shutdown_confirmed=true；观测出口故障时真实问答/引用/反馈继续。本章未操作Langfuse Cloud服务的运行状态。


最终Dashboard人工核验：用户明确回复“已看到完整调用树、Prompt 和 Token”。专用浏览器超时历史保留；该验收已由用户实际完成。
