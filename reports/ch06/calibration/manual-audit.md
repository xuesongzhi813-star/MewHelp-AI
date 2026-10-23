# 主执行者抽查（2026-10-23）

这是一轮AI主执行者对固定calibration输出与既有材料的逐项核对，不是用户签字或独立人工验收。策略hybrid_rerank，未改变标签/语料；依据details.jsonl中的原问/回答/引用及data/kb/ch04权威材料。

- specific_model-01/02：MH-DJ101胸围110，MH-DJ102棉98%/氨纶2%，与product-specs对应型号一致，没有串款。
- colloquial-01/02：手机申请售后步骤与“签收五天、未穿未洗”条件均引用实际政策；7天从签收起、不承诺审核结果，符合returns-policy/after-sales-manual。01重排分.0557低于.25，部署会误拒；02为.2895可通过，不能用答出内容掩盖01的误拒。
- colloquial-03额外低分核查：保留吊牌/包装、不得洗涤及寄回照片符合原文，但分.0614会被误拒，保留该质量问题。
- synonym-01/02：普通地区实付满99包邮、否则10元，偏远另算；个人原因退回运费消费者承担，原订单包邮时核对实际发出运费。与billing-shipping/returns-policy一致，未扩大“全部地区免费”。
- metadata-01/02：限定牛仔夹克的MH-DJ102衣长66、限定衬衫MH-SH203袖长61，品类和SKU均保持，符合product-specs。
- multi_evidence-01/02：DJ101/102胸围110/120、差10；SH201粘胶100%、SH202棉70%/亚麻30%，两侧证据齐全，未混同型号。
- out_of_scope-01：未知SKU MH-SH299，最高分虽.8739仍can_answer=false，没有以相似商品尺寸代答。
- out_of_scope-02：问“退款实际到账的精确日期”，最高分.6029且can_answer=true，与冻结不可答标签不符；回答正文确实说明“无法给出精确日期”并解释原支付渠道/一般审核验收，未捏造日期。保留这项can_answer漏拒，不能因正文较安全而修改冻结指标。
- out_of_scope-03/04：can_answer=false，低分；没有通过抬阈值掩盖高分out_of_scope-02。

结论：抽查的具体事实与材料一致，但阈值.25仍有2/20误拒及1/4冻结标签漏拒，拒答F1=.6667；自动Faithfulness=1.0不等于整体正确率100%。该文件只覆盖原24条单查询calibration，不覆盖ch06多查询/浏览器Agent回答的全量人工审核。
