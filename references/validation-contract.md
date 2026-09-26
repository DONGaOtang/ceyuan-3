# 数字检查数据契约

把原文中可核验数据提取为JSON，再执行：

```text
python scripts/check_plan.py input.json --output validation.json
```

脚本不读取任意PDF/Word，也不自动核真实报价/安全许可。必须人工/模型先对照原文核提取，source写原文页码/章节/表行；原值有矛盾则写审查问题，不悄悄选择。缺失或null输出unknown，不当0。数值允许JSON数字或十进制字符串；不接受负值/非有限数/布尔值。数量为整数。金额单位统一，税费预先按注明口径列项。

```json
{
  "budget": {
    "currency": "CNY", "tax_basis": "all_inclusive",
    "items": [{"name":"场地","quantity":1,"unit_price":4000,"source":"预算表第1行"}],
    "contingency":400,"declared_total":4400,"ceiling":5000
  },
  "capacity": {"attendees":80,"staff":10,"approved_capacity":100,"source":"场地确认函及名单"},
  "throughput": {"stations":2,"service_minutes":5,"window_minutes":30,"demand":12,"source":"签到章节"},
  "inventory": [{"name":"胸卡","needed":90,"available":100,"source":"物料表"}],
  "tasks": [
    {"id":"design","start":"2026-09-24T09:00:00+08:00","end":"2026-09-24T12:00:00+08:00","depends_on":[],"resources":["designer"],"source":"排期表"},
    {"id":"print","start":"2026-09-24T13:00:00+08:00","end":"2026-09-24T18:00:00+08:00","depends_on":["design"],"resources":["printer"],"source":"排期表"}
  ]
}
```

tax_basis只接受all_inclusive（所有税费已计入）或tax_exempt（有依据的免税口径）作为已知；未知用null。未含税须先把有依据的税额列入items再标all_inclusive，不能把tax_exclusive或“待确认”字符串当已核。币种用三位大写代码，脚本只查形式，不验证真实币种/报价。税基未知时算术仍可pass，但口径与未超限判断为unknown；已给定金额超限仍报fail。

budget全项目费用只列一次，contingency独立；小计=sum(quantity*unit_price)，总额=小计+应急金。不同币种先按有日期的汇率单独转换，不能混币运行。
capacity为同一时点在场参与者+工作人员与对应布局的批准容量；不是全天累计人次。
throughput理论上限=floor(window_minutes/service_minutes)*stations；建议工位数为理想下限，实际还需到达分布/换班/异常缓冲，不能据此保证排队时间。
tasks要求带时区ISO时间，依赖按finish-to-start；resources为该任务独占的人/场地/设备ID列表，空列表表示明确无独占资源，缺失返回unknown；兼容单个resource字段。相同ID在半开区间[start,end)重叠会报fail，首尾衔接和零时长不算重叠。多人资源池先拆为具体ID；转场/搭建缓冲要计入占用时段。其它依赖类型、共享资源池容量、现金流/统计归因/法律和授权仍人工审查。本脚本不声称覆盖这些检查。
输出inputs保留本次完整提取值及source，checks的code定位到对应输入段/任务ID；结合原文提取台账复核，不能把输入快照当作来源真实性证明。每项状态pass/fail/unknown。退出码0：无fail/unknown；1：有fail；2：仅unknown或输入格式错误。空输入不通过。overall仅表示此输入的算术/约束状态，不等于活动可执行。

## 计算结果与交付文件绑定

check_plan 的 pass 仅覆盖本次提取输入。将原文证据位置、计算输入版本、检查结果位置写入 requirements 事实台账；修订预算文件后，重新提取数字、运行计算并核对正文，再用 project_state.py verify 记录实际文件哈希及审查方法。不得只复用旧 validation.json 或将其 pass 当成全案通过。

完整交付需分别记录：独立需求覆盖复查 coverage_review、每项事实的 evidence_locations / artifact_ids / verification_result，以及产物 verification。字段结构见两个 schema；操作与未知处理见 [project-state.md](project-state.md)。脚本核版本和已登记结论；原件漏提、正文含义、证据真实性、授权范围仍须明确人工/模型复核。没有真实样本时，只能将回归结果称为合成案例验证。
