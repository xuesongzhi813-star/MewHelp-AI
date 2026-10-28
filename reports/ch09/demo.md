# ch09验收与演示（已交付）

产品目录：E:/MewHelp AI/.worktrees/ch05-workflow-agent。Python为根共享.venv/Scripts/python.exe；根真实.env仅后端读取。本章无DDL、清库、提交或部署。

真实合成闭环已完成：ch09-native-v4问题→retrieval_low_conf事件516+反馈517→review808（出现2次）→人工核准验收口令→96题影响逐题核对无修订→限定双写/24校准/active切换complete→同问回答“墨绿猫眼”并有引用。随后真实浏览器完成review965核准、96题人工确认、发布及新会话同问正确回答“柚子罐头”。原125条在用语料保留，本章累计新增两条合成验收知识。另闲聊/已知答案反馈分别review809/810。self_check真实复合问句样例ch09-selfcheck-v2已落池并归并。全部记录保留便于复查，不把合成答案当店铺政策。

受控脚本只选择本次合成事件ID，未消费原37条池记录。正式demo_ch09入口默认会启动后台自动消费者并处理旧未归并记录，这是已批准行为；不要把正式启动当只读检查。

浏览器验收已完成：后续专用浏览器通道实际操作了审核发布、旧轮反馈、历史重载、分页、失败重试和质量图表，见[browser.md](browser.md)。早期loopback不可达及原生Chrome网址策略中止作为历史失败保留。Cloud Dashboard由用户人工确认“已看到完整调用树、Prompt 和 Token”，SDK审计见[trace-audit.md](trace-audit.md)。

## 实际运行命令

在产品目录PowerShell执行（本章真实24题bootstrap已完成，勿删除active反复初始化）：

```powershell
$env:LANGFUSE_ENABLED='true'
$env:CH09_EVAL_ENABLED='false'
& 'E:/MewHelp AI/.venv/Scripts/python.exe' scripts/demo_ch09.py check
& 'E:/MewHelp AI/.venv/Scripts/python.exe' scripts/demo_ch09.py serve --port 8023
```

打开http://127.0.0.1:8023/聊天，/review审核，/quality看趋势/成本。日常需要02:00定时评估时，移除CH09_EVAL_ENABLED的临时覆盖（默认true），下次启动会补最近时间槽并实际调用模型。

复验脚本分阶段，记录保存在log/ch09/acceptance/RUN_ID/evidence.json；probe/merge/preview会写本次合成数据，publish会实际写知识并重新24题校准：

```powershell
& 'E:/MewHelp AI/.venv/Scripts/python.exe' scripts/accept_ch09.py --run-id ch09-native-v4 --phase trace
& 'E:/MewHelp AI/.venv/Scripts/python.exe' scripts/accept_ch09.py --run-id ch09-native-v4 --phase costs
& 'E:/MewHelp AI/.venv/Scripts/python.exe' scripts/eval_ch09.py --trigger 手动 --no-cache
```

每次评估均真实生成/judge，不复用回答/评分缓存；仅固定72条保留题，2轮结果与全部失败尝试另见evaluation.md。不要用72条成绩反过来选证据阈值。

## 日常审核

详情对照用户原话、入池入口、当轮Top5全文及原分/重排分，先判断真实知识缺口还是检索/生成错误。编辑核准答案后通过（此时待发布），再选核准批→检查96题标注影响→必要时逐题修订事实/来源/禁止承诺→确认其余不变和审核标识→发布。只有“已生效”才可宣称进入知识库。驳回终审，后续同义只累计次数。已写入失败保持维护，按原release_id续跑，勿重建批次/删journal。
