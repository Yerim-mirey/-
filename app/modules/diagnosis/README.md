# Tool 6 — Diagnosis

`diagnose()` 注入 Location、POI、Routing、Isochrone、Blindspot 五个已有 Service 调用。它只负责顺序、搜索半径、设施可达指标、阶段状态和警告汇总；Tool 5 的网格算法、Tool 4 的采样和 Provider 解析均不在这里重复。

接口语义和 `null/0` 规则见 `docs/architecture/diagnosis-contract-v1.md`。默认集成测试使用 Mock，不读取 AK；真实百度 smoke test 必须单独显式启动。
