# ch09失败与返工保留

- UI历史失败：早期内置浏览器无法访问本机loopback；原生Chrome工具无法可靠确定当前网址而停止该轮UI操作。后续通过专用浏览器通道完成实际页面验收，Dashboard由用户人工确认；详见browser.md。没有绕过原生工具限制。
- 并行验证安排：全量与在线owner并行导致58失败/1035通过；停止本章实例后串行1093通过，未放松安全闸。
- 真实probe v1-v3：owner上下文未yield租约，校验None.assert_owned，知识维护；失败记录保留。先RED后修，v4真实知识/池/合成发布整圈成功。
- selfcheck-v1：复合题先被正式证据闸拒绝，不能算模型自评验收；v2胸围+测量尺品牌先过正式闸再真实self_check入池。未调阈值凑入口。
- Cloud审计：SDK JSON/Python模式字段别名与整段字符串脱敏令首份审计JSON无效，改Python模式、递归字符串脱敏并真实重取；没有因误读而改正确的成本实现。子观察Session缺失修为整轮属性传播。
- eval#7：72题完成，检索指标可读，但32个judge因缺JSON提示全部HTTP400，Faithfulness=null，不计完整三指标验收；保留原记录和v1日志。#8停止本章实例中断，已恢复标记interrupted。旧ch04 Prompt不改，ch09新适配器身份单独起趋势，重新完整跑两轮。
- 唯一review：5Important均经作者RED/GREEN修复，详见review.md；没有二次独立review。额外租约线程自查1RED后串行化。
- 最终全量1111 passed in106.36s；改动216文件当前凭据扫描无匹配，原评估文件hash不变。
