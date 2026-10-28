# ch09唯一独立后端整章评审

2026-10-27按已批准Native流程完成一次fresh只读review。范围为ch09后端/Prompt/数据处理，不包含前端实现或既有ch08变更；没有第二轮独立复评。评审未修改仓库/索引/HEAD、未运行测试或接触真实配置。

评审结论：无Critical，5项Important，无单列Minor；修复前不能finish。优点为旧轮身份/快照回捞、事务/命名锁归并、限定双写与journal、隔离只读评估、版本化96题及24/72边界。

## 发现与作者处理

1. profile未绑定真实检索模型/端点配置。隔离配置漂移测试初RED（未拒绝），现保存retrieval_runtime_hash，覆盖模型、服务地址、维度、集合/策略和Milvus目标安全摘要，不包含凭据；切换配置拒绝旧profile，24题前后身份一致才可安装。6种配置变动均有覆盖。
2. 离线calibrate绕过未完成发布。公共bootstrap/recalibrate_current在开始及安装前检查journal，CLI与在线发布共用保护；生产入口传owner/publish双租约检查，长模型工作后安装active前再检查。初RED进入旧标签读取，修复后拒绝；另测长工作后锁丢失保持原active。
3. 并发确认覆盖不可变文件。两线程barrier初RED：两写者均saved。改同卷os.link原子create-if-absent，冲突不覆盖，JSON语义一致可幂等；两标签相同确认返回同确认时间，不同审核人409冲突，已有版本仍可load。NTFS真实隔离文件验证通过，不执行删除目标的原子性降级。
4. 官方图callback上传完整State。真实SDK+LangGraph sentinel测试初RED：旧历史泄漏到节点输入；SDK官方mask配置投影持久State字段，省略不相关消息/历史上下文/引用映射，LLM实际Prompt保留。定向38PASS覆盖会话传播/取消/故障/并发确认。
5. Faithfulness JSON模式缺输出契约。真实同题HTTP400与默认适配器RED确认，ch09适配器补JSON schema指令；ch04旧Prompt未改。judge_prompt_hash包含新指令身份，旧无效judge轮不混画新趋势。定向11PASS，真实同份答案8事实、1.0且无judge错误。

作者修复后最终全量：1110 passed in109.28s，脱敏证据.superpowers/sdd/2026-10-26-mewhelp-ch09-observability-flywheel/task13-author-green.log。没有将作者GREEN称作第二次独立评审通过。真实两轮72题及修复后Cloud路径仍待重新验证，浏览器工具策略阻塞的点击验收未完成；整章尚未finish。

## Declined to judge裁决

前端实现免review按用户Vibe要求接受，但UI验收仍必须完成。旧ch08改动按基线排除，不能隐性改动。浏览器/Dashboard视觉受工具策略阻塞，保持待验收。Cloud美元价格无映射，依批准设计保持null，不猜价。公网鉴权、基础设施升级、DDL均明确不做。评审时进行中或待重跑的测试不算通过；现仅采用上列作者实际结果。


作者额外并发自查：按SQLAlchemy官方Connection非线程安全契约，四线程共享lease检查峰值4的1RED，RLock串行检查/释放/关闭后22PASS；最终全量1111 passed in106.36s（final-tests-green.log），无额外独立review。此前1110为该额外修复前快照。
