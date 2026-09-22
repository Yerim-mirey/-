# Agent Phase 10 进展与未完成记录（2026-09-22）

> **状态：未通过，不得标为完成。** 真实百度链路尚未执行——本机 `.env` 的 `BAIDU_MAP_AK` 为空，且出口 IP 白名单未核对。本文记录已完成的 Phase 10 基础设施、一次真实模型诊断的结论，以及把 Phase 10 收尾所需的确切步骤。完成真实链路后须更新本文，而不是另写一份“通过”报告覆盖它。

## 1. 代码版本与范围

| 项目 | 值 |
| --- | --- |
| 仓库 | `/Users/liuyanhe/Desktop/15分钟生活圈智能体检与规划助手/` |
| 分支 | `feat/mvp-tool-gateway` |
| 本文基准 HEAD | `c4b39e5`，`test: attribute Planning contract rejections to a specific cause` |
| 本轮起点 | `aaf9d0c`（Phase 9 交接文档） |
| 本轮新增提交 | 5 个：`a406e70` 配额层、`e8ec055` 验收入口、`2be62ba` 自检、`f41ce30` 文档、`c4b39e5` 拒绝诊断 |
| 改动规模 | 17 个文件、+2474 / −21 行 |

对齐的冻结基线：`docs/architecture/15分钟生活圈_Agent_App_Architecture_v1.0_交接文档.md` 第 4、11–16、20–22 节。本阶段未改动 Tool 1–6 的业务算法、任何 v1 Contract/Schema，也未改动百度单批 50 目标的限制。

## 2. 已完成的 Phase 10 基础设施（离线已验证）

### 2.1 按 AK 共享的百度配额保护

`docs/test-report/phase9-mvp-tool-gateway-2026-09-22.md` 记录：队友的正式验收之所以通过，靠的是**验收器自己的临时节奏**（每批最多 3 个目标、批间约 1.05 秒），而 `RoutingService` 与 API 都没有跨请求共享的限速。`app/providers/baidu/quota.py` 把这个保护放到真正执行限流的位置——HTTP 调用之前。

`BaiduQuotaGuard` 按 AK 在进程内共享，两条约束缺一不可：

1. 滚动 1 秒窗口内放行的**算路目标**数不超过 `routes_per_second`（对应控制台按目标计量的配额）；
2. 连续算路请求之间至少间隔 `1 − 1/routes_per_second` 秒，避免单批请求在一个瞬间吃掉整秒预算——只做窗口约束时，一批 3 个目标会立刻打满窗口并以限流告终。

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

## 3. 真实模型侧结论（已执行）

### 3.1 此前那次“未定位的 Planning 校验拒绝”已定位

`docs/test-report/phase10-prep-deepseek-adapter-2026-09-22.md` 第 4 节记录：4 次真实 Planning 调用中有 1 次被 `PlanningAgent` 的确定性校验拒绝，原因未定位，因为当时脚本只打印拒绝文本、不保留输出。`scripts/diagnose_planning_rejections.py` 复现并归因到 `app/agents/planning.py` 第 77 行的四个条件之一。

`deepseek-flash`，各 12 次真实调用：

| 证据包 | 引用数 | ok | `unknown_evidence_ref` | 其它合同拒绝 |
| --- | ---: | ---: | ---: | ---: |
| `smoke`（原冒烟夹具） | 1 | 9/12 | **1** | 2 |
| `realistic`（每设施指标一条） | 3 | **11/12** | **0** | 1 |

**结论：那次拒绝是夹具不真实造成的，不是模型缺陷。** 原冒烟夹具只有一条引用 `diagnosis:facility-market`，而真实 Runner 会为每个指标生成 `diagnosis:facility-<type>`（见 `app/agent_runtime/runner.py`）。模型合理地引用了药店/小学的体检证据，却因为夹具里根本不存在该引用而被确定性校验正确拒绝——**校验逻辑是对的，harness 不真实**。换成三条引用的 `realistic` 证据包后，确定性拒绝归零。

### 3.2 真实模型确实存在的失败模式

`realistic` 证据包下 1/12 失败，加上 `smoke` 下的 2 次，共观察到三种**模型自身的形状错误**（与夹具无关）：

1. **schema 回显**：模型把 JSON Schema 本身当作对象返回（顶层出现 `$defs`/`properties`/`required`/`type`），1 次；
2. **`evidence_refs` 为空数组**：`min_length=1` 被违反，1 次；
3. **`recommendations[].rationale` 缺失**：模型给出 `recommendations[].description`——`PlanningRecommendation` 里**没有** `description` 字段，而 `rationale` 是必需字段，1 次。

这三种都是“很接近正确”的输出被契约正确拦下。它们**不是**语义质量结论，只说明形状稳定性。

### 3.3 必须并列声明的限制

- **样本量小**：每配置 12 次，不足以给出稳定的失败率；上述比例只是观测值。
- **仍是冒烟级输入**：`diagnose_planning_rejections` 用的是固定 `contracts/v1` 样例证据，不含真实百度数据，也不含多轮返工。
- **未评测语义质量**：所有通过率只证明“输出符合 Schema 与确定性引用校验”，**不证明规划建议正确**。意图识别、设施归类、建议是否越界、Reviewer 是否漏判，都需要真实数据 + 人工抽样。
- **未覆盖三 Agent 全链路**：本节只测 Planning 单角色；Orchestrator/Reviewer 的相对旧观测（4/4、4/4）仍未被更大样本复验。
- 诊断输出只保留字段路径与身份形状（如 `diagnosis:<other>`），不含模型原文、标识符与提示词。

## 4. 阻塞项与收尾步骤

### 4.1 当前阻塞

1. **`BAIDU_MAP_AK` 为空**：本机 `.env` 第 2 行只有变量名，值为空（长度 0）。`probe` 因此输出 `SKIPPED: BAIDU_MAP_AK 未配置。`
2. **出口 IP 白名单未核对**：本机出口并非单一 IP，观察到同一 `/24` 段内的 `103.155.100.100` 与 `103.155.100.96`。百度 Web Service 的白名单在所有接口上统一生效，未生效时地理编码、POI、批量算路会**全部**失败——这也是先跑 `probe` 的原因。

### 4.2 收尾步骤（按顺序）

```bash
cd "/Users/liuyanhe/Desktop/15分钟生活圈智能体检与规划助手"
set -a; source .env; set +a

# 1) 一次地理编码调用：确认 AK 与白名单生效
RUN_BAIDU_SMOKE=1 python -m scripts.verify_agent_live_e2e probe

# 2) 默认矩阵（不含完整体检，成本低）
RUN_BAIDU_SMOKE=1 RUN_DEEPSEEK_SMOKE=1 \
  python -m scripts.verify_agent_live_e2e acceptance \
  --report data/cache/phase10-acceptance-lite.json

# 3) 含完整体检的案例（约 10 分钟、上千次算路，确认配额后单独执行）
RUN_BAIDU_SMOKE=1 RUN_DEEPSEEK_SMOKE=1 \
  python -m scripts.verify_agent_live_e2e acceptance --include-diagnosis \
  --only planning-coordinate --report data/cache/phase10-acceptance-diagnosis.json
```

收尾后必须回填本文：每条案例的实际终态、实际调用量与时长、`completed`/`waiting_for_input`/`with_limitations`/`failed` 的实际归属、Evidence 引用逐条追踪结果、限流是否在该运行条件下生效、人工抽样结论与全部局限。若某项失败，写明“未通过”与可复现原因。

### 4.3 Phase 10 退出条件（尚未满足）

- [x] 限速/并发保护在 Provider/执行层落地，且不污染 Tool Contract
- [x] 离线测试与 Contract 校验通过
- [x] 真实模型侧的 Planning 拒绝已定位
- [ ] 真实 DeepSeek + 真实百度经实际 Gateway 与 Runner 跑通预定案例
- [ ] 限流/超时在该真实运行条件下被证明有效
- [ ] 结果、错误与证据引用逐项检查
- [ ] 真实语义抽样结论与局限写入报告

## 5. 复现与验证

```bash
# 离线基线：不得因为本阶段改动而下降
env -u BAIDU_MAP_AK -u RUN_BAIDU_SMOKE -u DEEPSEEK_API_KEY -u RUN_DEEPSEEK_SMOKE \
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/validate_contracts.py

# 不消耗任何配额与模型额度地验证验收脚本自身
python -m scripts.verify_agent_live_e2e acceptance --self-check

# 复现第 3 节的诊断（消耗真实模型调用，不消耗百度配额）
RUN_DEEPSEEK_SMOKE=1 python -m scripts.diagnose_planning_rejections \
  --rounds 12 --bundle realistic --report data/cache/planning-rejection-realistic.json
```

本机离线结果（`c4b39e5`）：**515 passed、4 skipped、2 warnings**；Contract 校验输出 `Core MVP Tool 1-6 and Agent v1 contracts validated successfully.`。本轮新增 84 项离线测试（起点 431）。

新增测试覆盖：配额窗口与反突发间隔、跨线程 FIFO 串行、拒绝上界、按 AK 共享与隔离、拆批传播、`--self-check` 全链路、JSON Pointer 解析、Evidence/Proposal/Review 绑定核对、Run ID 格式、以及拒绝归因分类器的每个分支（含“指纹不得携带输出原文”）。

未提交、也未写入本文的内容：`.env`、AK、完整带密钥 URL、百度原始响应、模型原始输出。`data/cache/` 下两个诊断 JSON 属本机临时产物（该目录已被 Git 忽略）。
