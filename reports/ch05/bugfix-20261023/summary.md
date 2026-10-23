# 2026-10-23 检索与知识维护故障修复

用户四项问题：BM25外检索失败、录入失败、知识聊天失败、初始化profile不符。

定位证据：旧workflow日志为EmbeddingError；当前独立嵌入及四策略均成功。旧运行进程未能直接连接，恢复启动后API正常。无法从旧笼统日志还原当时HTTP状态，不声称已确认是鉴权/余额/网络中的某一项。诊断脚本最初受系统代理影响返回非JSON，loopback排除代理后明确无监听，不当成应用数据返回。

数据现场：182总块、125有效、57历史，pending0，Milvus125且ID/内容/元数据一致。交付时124有效；用户新增知识使旧profile仅runtime_corpus_hash不符，校验器正确拒绝。修复还改变code_hash，未强改hash/删除新增块/覆盖旧profile。

修复：
- EmbeddingError安全分类和提示；401/403/402等不盲目重试，连接/超时/限流仍有有限重试。
- 检索API和workflow SSE报告对应嵌入/重排依赖，不再统一误导成迁移或生成失败。
- 入库502说明MySQL已保存pending，先修依赖后只补pending，不重复提交。
- profile严格比较保留，提示具体差异字段而不输出值。
- CH05_CALIBRATION_PROFILE可选择新校准文件，避免覆盖历史冻结文件。统一维护手册第1节默认选择本次最终文件，含重新校准和常见故障处理。

TDD：4条初始RED、profile选择RED、pending提示RED、异常链日志RED均复现；最终637 passed in35.62s，0failed/0skip。录入成功/部分失败/补漏路径由隔离API回归覆盖；本次没有新增真实知识或工单，仅读取现有125块，未清库/DDL/重建。

独立评审：Critical无，Important一项为异常链泄露供应商原文，format_exception回归RED→GREEN，已切断包装异常链。延期Minor：启动器只取threshold，未内置完整identity/split核验；保留文档的独立前置严格核验，代价是绕过维护流程直接启动可能选错profile。未追加第二轮review。Reviewer未评判旧ch05整体、历史供应商具体原因或真实校准质量，由主执行者真实验证。

校准：原24条calibration、原语料与标签不变；新文件log/ch05/calibration-20261023-final.json，阈值0.25。首轮文件保留，评审后代码hash变化所以重新真实校准而非手改identity。最终严格校验及HTTP验收结果见verification.json。calibration-final/summary.json中的服务/评审错误单列；不把can_answer=False当执行失败，也不冒称72条独立test部署或人工质量审计已重跑。

本地服务8015最终启动PID35872仅为快照；后续启动请按维护手册准备配置和选择最终profile。原根目录只有同步文档，产品修复仍在ch05工作树。未修改真实.env、提交、推送或合并。
