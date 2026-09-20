# 15 分钟生活圈助手 — Core Tool 基线

本仓库以 Tool 1–6 的现有 Contract/Schema 和实现作为后续 API、Web 的唯一核心基线。六个 Tool 已有离线测试；完整真实百度端到端验收因步行路由限流尚未通过，不能据此宣称整个 MVP 已完成。

## 当前内容

| 部分 | 状态 |
| --- | --- |
| `contracts/v1/`、`app/schemas/` | Tool 1–6 的 v1 接口与示例；Tool 5/6 语义见 `docs/architecture/` |
| `app/modules/location/`、`app/providers/baidu/geocoding.py` | 已放入队友合并交付的 Location Service、百度 Provider 和测试；其真实联网验证由队友完成 |
| `app/modules/poi/`、`app/providers/baidu/poi.py` | 已换成双方合并交付的 POI Tool；真实百度检索结果见 `docs/test-report/poi-2026-09-18.md` |
| `app/modules/routing/`、`app/providers/baidu/routing.py` | 已从队友 Tool 1–4 交付包逐文件合入 Routing Service、百度 Provider、Contract 和测试 |
| `app/modules/isochrone/` | 已从同一交付包合入 Isochrone Service、Contract、测试和去敏样例；它复用 Routing，不直接调用百度 |
| `app/modules/blindspot/` | 已实现网格裁剪、步行路由判定、盲区/未知区及离线测试；含保存的真实等时圈样本处理测试 |
| `app/modules/diagnosis/` | 已实现 Tool 1–5 编排、设施指标与部分失败语义；有完整 Mock 集成测试 |
| `app/api/main.py` | FastAPI 薄接口：健康检查、Location、Diagnosis；直接复用现有 Tool Contract |
| `data/`、`docs/`、`demo/` | 保存去敏样本、架构与验收记录、现有地图 Demo；正式 Web 尚待开发 |

项目长期分层是：用户界面 / 未来 Agent → 业务 Tool → GIS Tool → Provider → 百度地图 API。当前阶段先完成确定性的 Core Tool，不提前搭建 Agent、MCP 或复杂 Runtime。详见 `docs/architecture/core-mvp.md`。

## 基线边界

1. `app/schemas/common.py` 是公共 `CenterPoint`、`WarningItem`、`ErrorDetail`、`Meta` 的唯一来源。API 和 Web 复用现有 Tool/Schema，不另造数据结构。
2. 原有 `demo/map-demo.template.html` 与 `LICENSE` 保留；正式 Web 尚未开发。
3. 真实百度 AK 只放在本机 `.env`，不提交 Git；仓库只包含 `.env.example`。步行路由目前出现限流，请勿反复运行真实 API 脚本。

本地测试（先安装 `requirements.txt`）：

```text
python scripts/validate_contracts.py
python -m pytest -q
```

## 本地 API

安装依赖后，将 `.env.example` 复制为不入 Git 的 `.env` 并填写服务端 `BAIDU_MAP_AK`。在项目根目录启动：

```bash
set -a
source .env
set +a
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000/docs`。接口为 `GET /health`、`POST /api/v1/location/resolve`、`POST /api/v1/diagnosis`。两个 POST 分别使用现有 `LocationRequest/Result`、`DiagnosisRequest/Result`；请求格式错误返回 HTTP 422 与对应失败 Envelope，工具执行失败则在 Result 的 `ok=false` 中表达。没有 AK 时，坐标输入的 Location 仍可用，地址解析和 Diagnosis 会返回失败 Envelope，不会发起百度请求。

开发环境 CORS 默认仅允许 `localhost:5173` 和 `127.0.0.1:5173`。正式部署时通过 `CORS_ORIGINS` 指定逗号分隔的明确来源，不允许 `*`；服务端 AK 不进入前端、API 响应或 Git。

Tool 3/4 来源为 `core-mvp-tools-v1.zip`（SHA-256：`c9028fe8ca6e2ac1782327a4864212e377ae4923b3af9014a2c12d7ba47572f7`）。其交付时的离线测试为 `120 passed, 3 skipped`。

Tool 5/6 的 Contract/Schema 和 Service 完成后，接口校验通过；离线测试 `184 passed, 3 skipped`。Tool 5 已用保存的真实等时圈样本做离线空间处理测试；受控真实百度链路结果见 `docs/test-report/tool5-6-2026-09-19.md`。`scripts/verify_diagnosis_live.py` 默认不会发起百度请求，只有显式启用并配置 AK 才联网。

三类设施、默认业务参数的正式端到端验收因百度步行路由限流而未通过；没有继续消耗配额，结果与重验条件见 `docs/test-report/tool1-6-formal-acceptance-2026-09-19.md`。

尚未补 Docker、CI 和正式 Web；这些内容按交接文档随开发逐步加入。
