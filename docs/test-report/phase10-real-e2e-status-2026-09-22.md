# Agent Phase 10 真实链路验收记录（2026-09-22 / 实测 2026-09-23）

> **后续更新（2026-09-26）**：本文中的 2026-09-22/23 状态是历史记录。S2/S3 已补测，三个固定样本经人工复核通过预设 A1–A8 量表；评分脚本和算路探针的发现问题也已修复并有离线回归。最新证据见 [Phase 10 真实语义补测记录](phase10-semantic-sampling-supplement-2026-09-24.md)。

> **状态：真实链路已跑通，Phase 10 尚未全部达标。** 真实 DeepSeek + 真实百度经实际 `MVPToolGateway` 与 `run_agent` 已跑完固定案例矩阵：四类案例全部到达预期终态，其中含完整体检的规划案例耗时 528 秒、消费 1465 个算路目标并取得 Reviewer `approved`。但退出条件仍有 2 项未满足：真实语义质量只做了**一次**人工检查（样本不足以支撑结论），且模型侧出现了两次可复现的传输层失败。本文按第 6 节逐项交代。

## 2026-09-28 真实运行耗时口径更正与后续优化

此前 Phase 10 脚本把配额守卫的 `granted_routes` 总计标成“算路目标”。该计数当时同时包含实际路线目标，以及地理编码、POI 请求等各计 1 单位的调用；626.3 秒样本中的 1,641 应理解为**累计配额单位**，不能据此精确拆分各 Tool 的路线数或阶段耗时。3 单位/秒下，配额本身给出的理论下界约 547 秒；其余耗时构成没有被逐阶段计时。`evidence:sample-d2` 和 `tool_retry_count=1` 说明本次执行发生过一次补取证，但旧记录没有保留首次尝试的阶段细节。

后续代码已分开记录路线目标、非路线地图请求和总配额单位；盲区分析也改为近邻优先、按网格和设施类别逐批取证，已确认覆盖后停止同类多余路线，未确认覆盖则继续测候选或诚实标记 unknown。该算法变更目前只由离线测试验证；遵照额度限制，尚未用真实百度/模型再次运行，所以**实际能降到多少分钟还不能承诺**。已有 Agent Job 接口能立即返回任务号并轮询，但它改善的是 HTTP/页面等待体验，不会绕过百度配额或缩短真实计算时间。

## 1. 代码版本与范围

| 项目 | 值 |
| --- | --- |
| 仓库 | `/Users/liuyanhe/Desktop/15分钟生活圈智能体检与规划助手/` |
| 分支 | `feat/mvp-tool-gateway` |
| 本文基准 HEAD | `1cb7931`，`fix: ground the diagnostic proposal so Reviewer verdicts mean something` |
| 本轮起点 | `aaf9d0c`（Phase 9 交接文档） |
| 本轮新增提交 | 8 个：`a406e70` 配额层、`e8ec055` 验收入口、`2be62ba` 自检、`f41ce30` 文档、`c4b39e5` 拒绝诊断、`77165bd` 配额约束修正、`262a885` 三角色测量、`1cb7931` 夹具修正 |
| 改动规模 | 见 `git diff --stat aaf9d0c..HEAD` |

对齐的冻结基线：`docs/architecture/15分钟生活圈_Agent_App_Architecture_v1.0_交接文档.md` 第 4、11–16、20–22 节。本阶段未改动 Tool 1–6 的业务算法、任何 v1 Contract/Schema，也未改动百度单批 50 目标的限制。

## 2. 已完成的 Phase 10 基础设施（离线已验证）

### 2.1 按 AK 共享的百度配额保护

`docs/test-report/phase9-mvp-tool-gateway-2026-09-22.md` 记录：队友的正式验收之所以通过，靠的是**验收器自己的临时节奏**（每批最多 3 个目标、批间约 1.05 秒），而 `RoutingService` 与 API 都没有跨请求共享的限速。`app/providers/baidu/quota.py` 把这个保护放到真正执行限流的位置——HTTP 调用之前。

`BaiduQuotaGuard` 按 AK 在进程内共享，只用**一条**约束：滚动 1 秒窗口内放行的**配额单位**不超过 `routes_per_second`。每个算路目标占 1 单位，地理编码或 POI 请求各占 1 单位；单独统计的 `granted_routes`、`granted_calls` 和合计 `granted_quota_units` 不会混为一项。每个单位都会记入窗口，因此累计放行量永远不可能超过「`routes_per_second` × 已流逝秒数」，而路线单批上限又保证一个路线请求吃不下整个窗口。**曾有一版额外要求「连续请求最小间隔 `1 − 1/routes_per_second`」，这个约束已被删除（`77165bd`）**，因为它不提供窗口之外的任何安全性，却造成两类实际问题：

1. **即使窗口还有余量，也要把一个请求拒之门外一整个窗口。** 请求 2 命中该规则时会睡满一个窗口，哪怕它只要 1 个目标、而 100 个预算才用掉 2 个。对长时间满载运行代价不大（1000 个目标：3/秒时 222 秒 vs 下限 333 秒；100/秒时 8.9 秒 vs 下限 10.0 秒），但**小请求与并发下的伤害很明显**：6/秒配额、4 个线程共 8 个目标实测 **5.03 秒**，而预算只要求 **1.00 秒**；删除后即为 1.00 秒。
2. **并发退化成排队跳变**，而且这个代价只在并发测试里才暴露出来。

教训：「配置的速率」与「每次请求之间必须隔多久」不是一回事。前者由滚动窗口正确表达；后者在配额未被用尽时只是额外的延迟。

请求按 FIFO 排队；只有等待会超过 `max_wait_seconds` 时才拒绝，并表现为既有契约里可重试的 `RATE_LIMITED`。`RoutingService` 的拆批大小改为读取 Provider 的 `max_batch_routes`，因此**配额窗口决定拆批粒度**，而不是写死的 50。`app/providers/baidu/live.py` 保证 `MVPToolGateway.from_baidu_ak(...)` 与 `app/api/main.py` 共用同一份按 AK 预算。

配置（均只从本机环境读取，默认对应免费版）：

```text
BAIDU_ROUTES_PER_SECOND=3
BAIDU_QUOTA_MAX_WAIT_SECONDS=180
```

离线验证（假时钟，不睡觉、不联网）：192 个算路目标 → 64 次调用、最大单批 3、虚拟耗时 63 秒，符合 3 目标/秒。

### 2.2 真实链路验收入口

`scripts/verify_agent_live_e2e.py` 按用户实际路径运行 `run_agent(...)` → `MVPToolGateway` → Core Tool 1–6 → 百度，并由 `DeepSeekLLM` 驱动三个 Agent。三种模式：

- `probe`：**一次**地理编码调用，用来把“AK 未生效或 IP 不在白名单”与真正的链路故障分开，几乎不消耗配额；
- `acceptance`：固定案例矩阵；
- `agent "<消息>"`：单条临时消息。

另有 `--self-check`：用确定性 Tool 结果与固定 Brief 离线跑通同一套运行、检查与报告逻辑，**不消耗配额与模型额度**。自检已经抓到两个会让首次真实运行直接失败、但离线测试没覆盖的缺陷：`run_case` 直接读 `gateway.quota_guard`（无 guard 的 gateway 抛 `AttributeError`），以及 Run ID 用 `phase10-<case>` 被 schema 的 `run:` 前缀规则拒绝。

### 2.3 固定案例矩阵

| case_id | 输入 | 期望终态 | 规划/审查 | 需要体检 |
| --- | --- | --- | --- | --- |
| `poi-address` | 地址 | `completed` | 否 | 否 |
| `walk-time-coordinate` | 坐标 | `completed` | 否 | 否 |
| `planning-coordinate` | 坐标 | `completed` | 是 | **是** |
| `missing-location` | 无地点 | `waiting_for_input` | 否 | 否 |

含完整体检的案例需要 `--include-diagnosis`：默认三类设施一次 Diagnosis 约 1600 个算路目标，按 3 目标/秒**仅节流本身就要约十分钟**。默认矩阵不触发它。

每条案例除终态外还核对：`completion_mode`、Brief 的意图/设施/缺失字段、Evidence 中**每个 `EvidenceRef.json_pointer` 都能在 bundle 内解析**、Proposal 的 `evidence_bundle_id` 与引用集合、Review 与被审 Proposal 的身份与轮次绑定、`waiting_for_input` 必须零取证。`--report` 写出机器可读结果，AK 与模型密钥都会被替换。

## 2.5 真实链路实测结果（2026-09-23）

运行条件：`feat/mvp-tool-gateway` @ `6f5e4bf`；模型 `deepseek-flash`；`BAIDU_ROUTES_PER_SECOND=3`、窗口 1s、最大单批 3、最长等待 180s；出口 IP 已加入百度白名单。运行命令：

```bash
set -a; source .env; set +a
RUN_BAIDU_SMOKE=1 python -m scripts.verify_agent_live_e2e probe
RUN_BAIDU_SMOKE=1 RUN_DEEPSEEK_SMOKE=1 python -m scripts.verify_agent_live_e2e acceptance --include-diagnosis \
  --report data/cache/phase10-acceptance-full.json
```

| 案例 | 终态 | 意图 | 证据引用 | 算路目标 | 时长 | 判定 |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `poi-address`（地址） | `completed` / normal | `facility_query` | 29（location+poi） | 13 | 5.1s | **PASS** |
| `walk-time-coordinate`（坐标） | `completed` / normal | `accessibility_query` | 17（location+poi+routing） | 14 | 6.0s | **PASS** |
| `planning-coordinate`（坐标+规划） | `completed` / **normal** | `planning_analysis` | 3（diagnosis） | **1465** | **528.5s** | **PASS**（规划轮次 1，Reviewer `approved`） |
| `missing-location`（缺地点） | `waiting_for_input` | `facility_query` | 0 | 0 | 0.9s | **PASS**（零取证、列出缺失字段） |

`probe` 返回 `PROBE OK`，地理编码解析出四川北路街道真实坐标 `(121.492506, 31.268909)`。

**限流有效性（退出条件之一）：** 规划案例共放行 1465 个算路目标、耗时 528.5 秒，即 **2.77 目标/秒**，低于 3 目标/秒的配额；全程未触发 `RATE_LIMITED`，最大单批为 3。这证明共享守卫在真实运行条件下确实生效。

**证据追踪：** 三个成功案例的**每一条** `EvidenceRef.json_pointer` 都在其 bundle 内解析成功。坐标案例 23 条引用（location 1 + poi 11 + routing 11）全部通过；规划案例的 Proposal 与 Review 均绑定当前 bundle，Reviewer 返回 `approved`。

**规划案例的 warnings（必须并列记录）：** `PARTIAL_DIAGNOSIS`、`PARTIAL_POI_RESULTS`、`BLINDSPOT_UNAVAILABLE`、`NO_ROUTE_FOR_TARGETS`，且 `completion_mode` 仍为 `normal`。按契约这**不是矛盾**——交接文件明确 `completed` 只代表程序走到终态、不自动证明回答充分，而 `mode` 目前只反映规划工作流的 Review 结论、不反映证据完整度。但这意味着两种截然不同的运行会得到同一个 `mode=normal`：事实完整的，和事实不完整的。**消费者不能把 `mode=normal` 读作“证据完整”。** 该缺口的实际风险已在 2.6 专项核查。

### 2.6 不完全证据下的建议质量（针对上述 warnings 的专项核查）

2.5 的 warnings 引出一个真实风险：体检数据不完整时，Planner 会不会产出**过度自信**的建议、而 Reviewer 放行？为此在重试修复后重跑同案例并完整读取 Proposal 与 Review（`run:inspect3`，状态 `completed`，`mode=normal`，各次模型调用均一次成功）。

诊断事实上确实**不完整**——三类设施都有约 20%–27.5% 的未知网格，`covered_ratio` 仅 0.69–0.80，`confidence=low`：

| 类别 | POI | 15 分钟可达 | 已覆盖 | 盲区 | **未知** |
| --- | ---: | ---: | ---: | ---: | ---: |
| 市场 | 35 | 4 | 0.797 | 0.000 | **0.203** |
| 药店 | 43 | 7 | 0.797 | 0.000 | **0.203** |
| 小学 | 22 | 2 | 0.689 | 0.036 | **0.275** |

**结论：未观察到过度自信，担心不成立。** Planner 与 Reviewer 都正确处理了不完全证据：

- Proposal 的 `limitations` 逐条写明：整体置信度 low、盲区阶段为 partial、约 20%–27% 未知网格“既不能判为已覆盖也不能判为盲区，无法据此得出全区结论”、并声明未指定候选点位；
- 4 条建议里有 2 条的**主题本身就是证据缺口**（“补齐未知网格证据”、“稳定步行路线证据”），而非给出确定性定论；
- 对市场/药店明确写“维持观察而非定论”，并注明“`0.0` 为当前样本下的结果，不能表述为确定无盲区”；
- 建议的限制里写明“本建议不重新计算 GIS，不调用地图工具”；
- Reviewer 返回 `approved` 且 0 issues，与该 Proposal 的实际质量相符。

因此第 4.4 节第 2 项的风险**降级**：`mode` 的语义模糊依然存在（消费者不能把 `normal` 读作“证据完整”），但**这不是正确性缺陷**——限制已写进 Proposal 正文与 `limitations`，警告也在同一响应里。是否需要让不完全证据降级为 `with_limitations`，现可作为一个**可用性/标注**决策从容处理，而非风险修复。

**同时记录一个必须诚实说明的限度：这是 n=1。** 一次规划运行的 Proposal 表现良好，**不足以**支撑“建议质量达标”的结论；4.3 的语义抽样退出条件因此仍不勾选。

### 2.7 模型侧传输层失败（修复前实测两次）

为核对 2.5 中 warnings 的内部事实，同一规划案例被重跑两次，**两次都在证据取得之前失败**：

- 一次 `status=failed`、`errors=[UNEXPECTED_ERROR]`，消息为 `DeepSeek request failed: The read operation timed out`（Orchestrator 首次调用即超时，`brief` 为 `None`，`tool_retry_count=0`）；
- 另一次长时间无返回，被主动终止。

根因是结构性的：适配器默认 `timeout_seconds=60`，而 Runner **只对 Tool 失败消耗重试预算，对模型失败不重试**，因此一次模型超时会让整个 Run 以 `failed` 终止。**该问题已修复**（见 4.4 第 1 项）：修复后 2.6 那次同案例重跑各次模型调用均一次成功、状态 `completed`。

## 3. 真实模型侧结论（已执行）

### 3.1 此前那次“未定位的 Planning 校验拒绝”已定位

`docs/test-report/phase10-prep-deepseek-adapter-2026-09-22.md` 第 4 节记录：4 次真实 Planning 调用中有 1 次被 `PlanningAgent` 的确定性校验拒绝，原因未定位，因为当时脚本只打印拒绝文本、不保留输出。`scripts/diagnose_agent_contracts.py` 复现并归因到 `app/agents/planning.py` 第 77 行的四个条件之一。

`deepseek-flash`，各 12 次真实调用：

| 证据包 | 引用数 | ok | `unknown_evidence_ref` | 其它合同拒绝 |
| --- | ---: | ---: | ---: | ---: |
| `smoke`（原冒烟夹具） | 1 | 9/12 | **1** | 2 |
| `realistic`（每设施指标一条） | 3 | **11/12** | **0** | 1 |

**结论：那次拒绝是夹具不真实造成的，不是模型缺陷。** 原冒烟夹具只有一条引用 `diagnosis:facility-market`，而真实 Runner 会为每个指标生成 `diagnosis:facility-<type>`（见 `app/agent_runtime/runner.py`）。模型合理地引用了药店/小学的体检证据，却因为夹具里根本不存在该引用而被确定性校验正确拒绝——**校验逻辑是对的，harness 不真实**。

换成三条引用的 `realistic` 证据包后，**未知引用这一类拒绝没有再出现**（该次 12 轮与 3.4 节的 10 轮均为 0）。但确定性拒绝并未完全归零：两次 realistic 运行各出现 1 次 `PlanningOutputError`，样本不足以断言原因，仍需更大样本。

### 3.2 真实模型确实存在的失败模式

`realistic` 证据包下 1/12 失败，加上 `smoke` 下的 2 次，共观察到三种**模型自身的形状错误**（与夹具无关）：

1. **schema 回显**：模型把 JSON Schema 本身当作对象返回（顶层出现 `$defs`/`properties`/`required`/`type`），1 次；
2. **`evidence_refs` 为空数组**：`min_length=1` 被违反，1 次；
3. **`recommendations[].rationale` 缺失**：模型给出 `recommendations[].description`——`PlanningRecommendation` 里**没有** `description` 字段，而 `rationale` 是必需字段，1 次。

这三种都是“很接近正确”的输出被契约正确拦下。它们**不是**语义质量结论，只说明形状稳定性。

### 3.4 三个角色各自的合同结果（`262a885`、`1cb7931`）

第 3.1 节的旧观测是「Orchestrator 4/4、Planning 3/4、Reviewer 4/4」，样本太少。`scripts/diagnose_agent_contracts.py` 现在**直接调用三个 Agent 的真实校验代码**（`create_brief` / `create_proposal` / `review_proposal`），所以测到的就是 Runner 会接受或拒绝的行为，而不是重写一遍规则。`deepseek-flash`，10 轮/角色、`realistic` 证据包、共 30 次请求：

| 角色 | 合同有效 | 失败 |
| --- | ---: | --- |
| Orchestrator | **10/10** | — |
| Planning | **9/10** | 1 次确定性拒绝（`PlanningOutputError`） |
| Reviewer | **10/10** | — |

Orchestrator 的意图归类与输入的 6 条消息轮转一致：`facility_query` 5、`accessibility_query` 1、`blindspot_query` 2、`planning_analysis` 2——说明它不是恒定输出。

**Reviewer 的判别能力第一次被真正测到。** 这里必须记录一个我自己犯的测量错误：`proposal_for` 最初按索引轮流分配证据引用，导致「菜市场缺口」这条建议引用了**药店**证据；于是 10 次调用**全部**返回 `revision_required`——那只证明夹具是坏的，不能证明 Reviewer 的质量，因为只喂坏提案的测试分不清「仔细的 Reviewer」和「一律拒绝的 Reviewer」。修好夹具后（`1cb7931`），改为交替投喂「有据可依」与「故意错引」两类提案：

| 输入 | 判定 |
| --- | --- |
| 有据可依的提案 | `approved` ×3、`revision_required` ×1、`insufficient_evidence` ×1 |
| 故意错引的提案 | `revision_required` ×**5** |

**错引 5/5 全部被抓出**，有据可依的两例偏保守（一次要求返工、一次判证据不足）。这是目前唯一能说明 Reviewer 的判定**跟着证据走、而非恒定输出**的证据。仍属形状与判别层面，**不是**语义正确性评测。

### 3.5 必须并列声明的限制

- **样本量小**：每角色 10–12 次，不足以给出稳定的失败率；上述比例只是观测值。
- **仍是样例级输入**：诊断脚本用的是固定 `contracts/v1` 样例证据，不含真实百度数据，也不含多轮返工。
- **未评测语义质量**：所有通过率只证明“输出符合 Schema 与确定性引用校验”。3.4 节的 Reviewer 判别只是“能否发现故意的错引”，**不证明**真实场景下不漏判、规划建议是否真的合理。意图识别、设施归类、建议是否越界，都需要真实数据 + 人工抽样。
- **Reviewer 的保守偏多**：有据可依的 5 例里 2 例未批准，方向上是保守而非漏放，但需要更大样本才能说清比例。
- **未覆盖真实全链路**：三 Agent 之间仍由固定夹具驱动，没有经过真实 Gateway、真实 Diagnosis 或多轮回路的联合运行。
- 诊断输出只保留字段路径与身份形状（如 `diagnosis:<other>`），不含模型原文、标识符与提示词。

## 4. 复现条件与待办

### 4.1 环境前置（已解决，记录以备复现）

1. **`BAIDU_MAP_AK`**：本机 `.env` 第 2 行原先为空，已填入服务端 AK。**该 AK 曾以明文出现在协作对话中，验收后应轮换**；轮换只需改 `.env` 一行，代码无需改动。
2. **出口 IP 白名单**：曾观察到两类出口。开启代理时为 `103.155.100.0/24` 段内的地址池（采样到 8 个不同 IP，逐个加白名单不可行）；关闭代理后为单一固定地址 `36.113.103.187`，已加入百度控制台白名单。未加白名单时百度返回 HTTP 200 + `status=210`（`APP IP 校验失败`）——该错误现在会被三个 Provider 明确映射为不可重试的 `INVALID_REQUEST`，不再伪装成通用服务故障。
3. **出口 IP 白名单**：曾观察到两类出口。开启代理时为 `103.155.100.0/24` 段内的地址池（采样到 8 个不同 IP，逐个加白名单不可行）；关闭代理后为单一固定地址 `36.113.103.187`；改用手机热点后为 `223.160.206.240`。未加白名单时百度返回 HTTP 200 + `status=210`（`APP IP 校验失败`）——该错误现在会被三个 Provider 明确映射为不可重试的 `INVALID_REQUEST`，不再伪装成通用服务故障。
4. **IPv4 强制（关键，`ba53318`）**：白名单里 IPv4 地址写得完全正确，请求却**仍然**被 `210` 拒绝，真因是**双栈**：

   - `api.map.baidu.com` 同时有 A 与 AAAA 记录，双栈主机**优先走 IPv6**，请求从 IPv6 地址发出；
   - 控制台白名单只登记 **IPv4** 地址，因此白名单再正确也拦不住 IPv6 来源；
   - 同一台机器、同一 AK，只改地址族即见分晓：**强制 IPv4 → `status 0`；强制 IPv6 → `status 210`**。

   这个故障的误导性在于：白名单看着是对的、运维能查到的 IP 也是对的、错误又写着"IP 校验失败"，于是会一直往控制台方向返工。它已造成两次取样作废（S2/S3 在等时圈阶段失败，看起来像地图数据缺口）。现在 `install_ipv4_only()` 在构造 Provider 时把百度流量固定到 IPv4，`BAIDU_FORCE_IPV4=0` 可在白名单改为 IPv6 时关闭。**部署到任何双栈环境都必须保留此行为。**
5. 热点/家宽 IP 可能改变；变化后表现与未加白名单相同（`210`）。排查顺序：`curl -s https://api.ipify.org` 与 `curl -6 -s https://icanhazip.com` 分别看 IPv4/IPv6 → 确认白名单登记的是实际使用的那一族（本项目固定 IPv4）→ 重跑 `probe`。

### 4.2 复现命令

```bash
cd "/Users/liuyanhe/Desktop/15分钟生活圈智能体检与规划助手"
set -a; source .env; set +a

# 1) 1 次地理编码调用：确认 AK 与白名单生效
RUN_BAIDU_SMOKE=1 python -m scripts.verify_agent_live_e2e probe

# 2) 默认矩阵（不含完整体检，约 15 秒）
RUN_BAIDU_SMOKE=1 RUN_DEEPSEEK_SMOKE=1 \
  python -m scripts.verify_agent_live_e2e acceptance \
  --report data/cache/phase10-acceptance-lite.json

# 3) 含完整体检的规划案例（约 9 分钟、约 1465 个算路目标）
RUN_BAIDU_SMOKE=1 RUN_DEEPSEEK_SMOKE=1 \
  python -m scripts.verify_agent_live_e2e acceptance --include-diagnosis \
  --only planning-coordinate --report data/cache/phase10-planning.json
```

### 4.3 Phase 10 退出条件

- [x] 限速/并发保护在 Provider/执行层落地，且不污染 Tool Contract
- [x] 离线测试与 Contract 校验通过（`537 passed, 4 skipped`；Contract 校验通过）
- [x] 真实模型侧的 Planning 拒绝已定位（夹具只有一条引用所致，非模型缺陷）
- [x] 真实 DeepSeek + 真实百度经实际 Gateway 与 Runner 跑通预定案例（四类案例全部到达预期终态，见 2.5）
- [x] 限流在该真实运行条件下被证明有效（1465 目标 / 528.5s = 2.77 目标/秒，未触发 `RATE_LIMITED`，最大单批 3）
- [x] 结果、错误与证据引用逐项检查（三个成功案例的每条 JSON Pointer 均解析成功；失败路径亦已记录）
- [x] **真实语义抽样结论**（2026-09-24 补测）：三个预设样本经固定量表人工复核后 A1–A8 均通过；这满足该量表的抽样退出条件，不代表稳定通过率、独立专家评审或现场验证。详见 [补测记录](phase10-semantic-sampling-supplement-2026-09-24.md)。

### 4.4 已知问题与待办

1. **[已修复] 模型失败不重试**：适配器现在对**可恢复**失败做有界重试——网络/超时、HTTP 429 与 5xx、以及 JSON 模式偶发的空内容；**不重试**确定性失败（4xx、`max_tokens` 截断、内容非法 JSON），以免把配置错误藏在重试后面。默认 `max_retries=2` + 指数退避（0.5s、1s），`sleep` 可注入，`last_attempts` 记录实际尝试次数，耗尽后消息注明重试次数。修复后真实链路不再因单次模型抖动丢掉整个 Run。**仍未做**：跨 Run 的持久化重试/断点续跑——同步接口下若模型在诊断第 8 分钟彻底不可用，该次运行仍会失败，只是不再因一次抖动失败。
2. **`completion_mode` 不反映证据完整度（风险已核查，降级为标注决策）**：`mode=normal` 同时覆盖“事实完整”和“`PARTIAL_DIAGNOSIS`”两种运行，消费者不能把 `normal` 读作“证据完整”。**但 2.6 的专项核查未发现正确性缺陷**：在 20%–27.5% 未知网格、`confidence=low` 的输入下，Planner 把限制写进了 Proposal 正文与 `limitations`（含“`0.0` 不能表述为确定无盲区”），4 条建议中 2 条的主题就是补齐证据，Reviewer 的 `approved` 与实际质量相符。因此这只是**可用性/标注**问题，可从从容决策，不必作为风险修复。若要改，属于冻结语义变更，需明示决策。
3. **语义评分表未建立**：此为本文写作时的状态。固定量表已建立、执行并补充机器误报修复，见 [补测记录](phase10-semantic-sampling-supplement-2026-09-24.md)。
4. **长任务与并发**：此为本文写作时的状态。现已新增有限容量、单进程内存的 Agent Job 提交与轮询接口；没有取消能力，服务重启会丢失任务记录。当前限制见 [README 的 Agent Job 说明](../README.md#agent-run-http-入口)。

## 5. 复现与验证

```bash
# 离线基线：不得因为本阶段改动而下降
env -u BAIDU_MAP_AK -u RUN_BAIDU_SMOKE -u DEEPSEEK_API_KEY -u RUN_DEEPSEEK_SMOKE \
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/validate_contracts.py

# 不消耗任何配额与模型额度地验证验收脚本自身
python -m scripts.verify_agent_live_e2e acceptance --self-check

# 复现第 3 节的诊断（消耗真实模型调用，不消耗百度配额）
RUN_DEEPSEEK_SMOKE=1 python -m scripts.diagnose_agent_contracts \
  --rounds 10 --bundle realistic --report data/cache/agent-contract-diagnosis.json
```

本机离线结果（`1cb7931`）：**523 passed、4 skipped、2 warnings**；Contract 校验输出 `Core MVP Tool 1-6 and Agent v1 contracts validated successfully.`。本轮新增 92 项离线测试（起点 431）。

新增测试覆盖：配额窗口与「小请求不被压成每秒一次」的回归、跨线程 FIFO 串行、并发下**聚合**放行量不超过该时段预算、拒绝上界、按 AK 共享与隔离、拆批传播、`--self-check` 全链路、JSON Pointer 解析、Evidence/Proposal/Review 绑定核对、Run ID 格式、以及拒绝归因分类器的每个分支（含“指纹不得携带输出原文”）。

未提交、也未写入本文的内容：`.env`、AK、完整带密钥 URL、百度原始响应、模型原始输出。`data/cache/` 下的诊断 JSON 属本机临时产物（该目录已被 Git 忽略）。
