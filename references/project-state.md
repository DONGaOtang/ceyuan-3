# 项目状态与变更传播

使用 `schemas/project.schema.json`。多轮/全案/跨模块项目存用户项目目录；临时单项任务可不落JSON。CLI：

用户决策单独按user-decision-gates.md记录在项目目录user-decisions.md：节点、选择、条件/范围、输入与方案版本、用户原话位置、关联事实/模块/产物和确认状态。state的ready、verify或audit结果不表示用户批准。当前脚本不自动解析确认台账或真实授权，须对照用户消息复核，不宣称自动审批校验。

```text
python scripts/project_state.py init <project.json> --project-id demo
python scripts/project_state.py bootstrap <project.json> --modules budget delivery media registration
python scripts/project_state.py set <project.json> --key attendees --value 500 --source "用户本轮确认"
python scripts/project_state.py validate <project.json>
python scripts/project_state.py verify <project.json> --artifact proposal --requirements <requirements.json> --reviewer "复核人或模型标识" --method "逐项对照原需求、预算表与方案正文，记录证据位置" --result pass
python scripts/project_state.py audit <project.json> --requirements <requirements.json>
```

init拒绝覆盖文件。set记录历史、递增revision，默认把依赖该fact key的模块/产物标stale，再沿模块依赖传递。同值同来源不重复写；同值新来源也更新版本供复验。脚本只标过期，不自动改预算/方案，也不会自动把stale变ready。

bootstrap仅在未注册模块/产物时使用，按任务选择budget/delivery/media/registration中的必要项，自动建立proposal下游依赖。模板未知事实填null，不编造来源；产物路径只是待生成位置，不代表文件存在。媒体默认依赖地点和人数，报名默认依赖目标；旧项目不会自动迁移，按实际产物补齐这些依赖并标stale再复验。模板是起点，实际涉及目标、供应商、媒体权限等自定义事实时追加依赖。已有模块不被模板覆盖。

validate拒绝ready模块引用非ready上游/产物或null事实，也拒绝非ready模块持有ready产物。同一产物被多个模块共同维护时，变更会传播到所有生产模块。复验后需要同时更新模块和产物状态；格式通过不证明文件已经生成或经过真实复验。

facts记录value、source、updated_at；允许null表示未知。assumptions不能并入facts当事实。modules每项有status、depends_on_facts、depends_on_modules、artifact_ids。artifacts由模块引用；引用ID必须存在。
交付前检查不存在未复验stale项；只在对应计算和证据重核后设置ready，保存复验记录。不使用用户确认替代事实核验。

例：attendees从300变500 → registration、budget、delivery等依赖模块失效 → proposal依赖它们也失效。品牌背景不自动失效。date变更检查档期/审批/媒介排期；budget变更检查资源/机制；goal变更检查全部策略依赖。
同步检查用户确认基线：超出确认条件的决定标需重新确认，保留旧决定与来源，用影响分析交用户选新增取舍。用户已直接指定的新值立即登记，不重复确认该值；不能从改预算推定用户已同意删减权益。仅文字修订或授权范围内变化留版本记录，不重启所有节点。用户确认规划假设不将其转成已核外部事实。
脚本使用expected revision支持乐观检查；当前模式单写入者顺序操作，不支持多进程并发事务。需要并发时先扩展锁与合并机制，不假定现成支持。
不同project_id文件隔离；用户偏好与组织知识另外维护来源日期/适用范围。记录中不保存不必要个人资料、密钥或账号凭据。

## 交付事实审计：结构合法之外的一步

核对单格式见schemas/delivery-requirements.schema.json。完整/跨模块方案从原需求、原案和本轮变更中独立提取requirements.json，不从已有state反向复制一份。只列影响本次结论的字段；例如客户数、工作人员数、预算、日期、地点、目标、容量、报名截止、账号权限。名称与fact key一致；每项写值、来源位置、实际受影响模块。未知填null；案例ID不是人数等约束的替代。

```json
{
  "facts": {
    "attendees": {"value":60,"source":"用户需求：60位客户","modules":["budget","delivery","proposal"]},
    "location": {"value":"A城会场","source":"需求第2段","modules":["delivery","media","proposal"]}
  },
  "artifact_ids": ["budget", "delivery", "media", "proposal"],
  "coverage_review": {
    "reviewer": "待填实际复核者或模型标识",
    "method": "示例：尚未完成原始需求与全部附件的逐项覆盖复查",
    "source_inventory": ["原始需求第1—2段；实际使用时替换为真实版本和位置"],
    "result": "unknown"
  }
}
```

先通过set登记事实，再登记模块depends_on_facts/depends_on_modules及产物；新项目可bootstrap起步。已有项目手工补依赖时保留旧版、递增revision并在history记原因，将新增依赖影响的模块及下游产物标stale再复验，不重跑bootstrap覆盖。临时单项任务可不落状态，但仍核输入与结论。

示例中的产物按实际范围选择，必须在state登记并生成；不要照抄不存在的文件或示例来源。覆盖复查完成后填写实际reviewer、所做方法和原件位置，result按观察填pass/fail/unknown；不能为获得pass删除未核项。然后逐个实际检查文件内容，执行verify记录相应结果，再audit。需求清单变化时先完成新的覆盖复查，再复核受其影响的文件。

交付运行audit：核对清单中的值是否登记且一致，指定模块是否直接或间接依赖该事实，必交文件是否注册、位于项目目录内、存在且非空，产物及生产模块是否仍过期/阻断。partial草案可通过登记子检查，但无内容复核记录或独立需求覆盖复核时总体返回unknown；null事实或过期产物同样返回unknown；缺事实/依赖/文件或值不一致返回fail。退出码0/1/2分别为pass/fail/unknown，输入格式错误也为2。validate只查结构，不代替audit。

audit不理解任意方案正文，不自动发现清单漏写、来源造假或文档中旧数字；交付时仍逐项比对原需求→清单→state→方案/附件。记录必查事实为何适用，遗漏任何影响结论的约束就补清单重验。预算、执行、媒介、报名表及外部已发布信息分别记录待更新/已复验，不能用JSON已变更代表文件或外部页面已同步。

## 文件版本与复核记录（D14）

旧版 schema_version 1.0 仍可读，不伪造历史复核。verify 必须在实际完成所述方法后调用；result 为本次审查的 pass / fail / unknown，不能因命令需要参数就填写 pass。命令不读懂正文，不替代专业审核，不自动将任何模块或产物设为 ready。

每次 verify 保存实际文件 sha256、input_revision、产物相关输入/依赖图摘要、requirements 摘要、reviewer、method、at、result，旧记录保留在 history。命令递增 revision 供写入冲突检查，但不递增 input_revision，因此逐个复核多个文件不会互相失效。set / bootstrap 修改输入时递增两个版本；旧项目 input_revision 缺失时从 revision 起步。输入摘要覆盖产物生产模块及传递上游的事实来源与日期、模块依赖及产物路径，可以发现未递增版本的手工修改。全局 input_revision 留作追溯；无关事实变更不使本产物复核失效，以相关输入摘要判定。assumptions 尚无依赖归属，因此假设变更保守地要求全案复核。

独立 coverage_review 放 requirements 中，必填 reviewer、method、source_inventory（原需求/原案/变更的可定位清单）、result。事实项可补 evidence_locations、artifact_ids、verification_result，逐项记录正文证据、受影响文件和人工结果。这些扩展字段是复核台账，脚本不会自动验证原文语义或链接真实性。先从原件提取需求，再另做一遍按原件逐段的覆盖复查；同一模型复查必须换审查顺序并明确“模型复查”，不能写成真人独立批准。扫描件读不清保留 unknown，不填猜测值。

同路径字节变化使 audit 返回 fail；输入或需求摘要变化使原复核记录过期并返回 unknown；缺内容复核记录、缺覆盖复查均为 unknown。人工记录的 fail / unknown 保留，不因哈希一致转为 pass。audit 本身只读，发现变化后先登记影响、修文档、实际复核，再 verify 留证；状态仍需按现有规则更新。文件内容与状态都同步并不等于外部已发布页面同步。

复核摘要用于版本一致性，不是防篡改签名；能编辑 JSON 的人也能改记录。不得将审核人的输入结论描述成工具自动证明。复核期间单写入、暂停修改文件；若随后发生变更，交付前再运行 audit 检出。

### 上游附件和逐事实复核的审计边界

requirements.artifact_ids 必须包括本次结论依赖的上游附件，例如审计 proposal 时同时列出所依赖的 budget、delivery 等实际产物。当前相关输入摘要包含依赖图、事实和路径，不含上游文件字节；只审计 proposal 不会自动发现未列入审计清单的 budget 正文被改。先沿模块依赖核全量产物清单，对上游与总案分别 verify，再执行同一份全量 audit；任一附件的同路径修改会由对应哈希检查报 fail。这项清单完整性须纳入 coverage_review 方法，不得把仅总案通过说成所有附件已同步。

事实项明确填写 verification_result 时，audit 将 pass / fail / unknown 纳入结果；非法枚举拒绝。该字段缺省不增加未知项，独立覆盖复核和产物复核仍必须完成。缺失或空文件时，文件检查 fail，哈希检查 unknown，绝不输出“哈希已匹配”。
