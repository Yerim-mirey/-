# Phase 1 final-fix report

日期：2026-09-21
分支：`feat/agent-contracts`
修复基线：`d3181c7f7767303eb0f7bbe7fc160f0274bba6a3`
提交消息：`fix: harden agent reference contracts`

## 根因

- I1：`EvidenceRequest.required_json_pointers` 原来是 `list[NonEmptyText]`，after-validator 只检查字符串是否以 `/diagnosis/` 开头。因此非法 `~` 转义和解码后为 `root` 的内部 `RootModel` token 都能通过。`EvidenceRef` 已有同类约定，但请求字段没有复用。
- I2：定义端 ID 字段各自内联了正则，而 Planning/Review 的 `evidence_refs`、`recommendation_ids` 使用宽松的 `NonEmptyText`。因此 `garbage`、URL、错误前缀和非法后缀等无法匹配定义端对象的值可以作为消费端引用通过；这不属于跨对象存在性解析问题，而是同一 ID 语法未复用。

## TDD：RED

先加入 I1 最小负例，未改生产代码时运行：

```text
$ env PYTHONDONTWRITEBYTECODE=1 RUN_BAIDU_SMOKE=0 BAIDU_MAP_AK= .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_agent_contracts.py::test_evidence_request_rejects_invalid_public_pointer
FF                                                                       [100%]
... DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'> ...
2 failed in 0.09s
```

随后加入 I2 最小负例，未改生产代码时运行：

```text
$ env PYTHONDONTWRITEBYTECODE=1 RUN_BAIDU_SMOKE=0 BAIDU_MAP_AK= .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_agent_contracts.py::test_planning_consumers_reject_invalid_reference_id
F                                                                        [100%]
... DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'> ...
1 failed in 0.07s
```

补齐 I1/I2 的合法值、非法值、重复路径和未解析 ID 回归后，生产实现前的 focused 汇总仍为预期 RED：I1 `2 failed, 4 passed`；I2 `11 failed, 2 passed`。失败均为期望的 `pytest.raises(ValidationError)` 未触发，而不是测试收集或导入错误。

## 修复与 GREEN

- 新增共享 `JsonPointer` Annotated 类型，供 `EvidenceRef` 与 `EvidenceRequest` 使用；复用同一个 JSON Pointer token 解码 helper，拒绝非法 `~` 转义、decoded `root` token，并继续限制 EvidenceRequest 的根 token 为 `diagnosis`。
- 新增并复用 `EvidenceId`、`EvidenceBundleId`、`IssueId`、`RecommendationId`、`ProposalId`、`RequestId`、`ReviewIssueId`、`RunId` Annotated ID 类型。定义端和 Planning/Review 消费端字段现在共享同一语法；未做跨对象存在性解析。
- 保留原有必填、非空、重复路径、重复引用和对象 ID 唯一性校验；没有改 Core Tool 1–6、Runtime、解析器或百度调用。

新增回归 focused GREEN：

```text
$ env PYTHONDONTWRITEBYTECODE=1 RUN_BAIDU_SMOKE=0 BAIDU_MAP_AK= .venv/bin/python -m pytest -q -p no:cacheprovider <I1/I2 focused nodes>
...................                                                      [100%]
19 passed in 0.08s
```

Agent 契约测试：

```text
$ env PYTHONDONTWRITEBYTECODE=1 RUN_BAIDU_SMOKE=0 BAIDU_MAP_AK= .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_agent_contracts.py
.......................................................                  [100%]
55 passed in 0.08s
```

## 完整验证命令输出

Validator：

```text
$ env PYTHONDONTWRITEBYTECODE=1 RUN_BAIDU_SMOKE=0 BAIDU_MAP_AK= .venv/bin/python scripts/validate_contracts.py
Core MVP Tool 1-6 and Agent v1 contracts validated successfully.
```

全量强制离线：

```text
$ env RUN_BAIDU_SMOKE=0 BAIDU_MAP_AK= .venv/bin/python -m pytest -q -p no:cacheprovider
......................................................sss............... [ 28%]
........................................................................ [ 56%]
........................................................................ [ 85%]
.....................................                                    [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  ... StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
.venv/lib/python3.12/site-packages/starlette/testclient.py:53
  ... DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use `anyio.from_thread.BlockingPortal` instead.
250 passed, 3 skipped, 2 warnings in 0.47s
```

Diff whitespace check：

```text
$ git diff --check
[no output; exit 0]
```

All test commands explicitly disabled the Baidu smoke path and cleared `BAIDU_MAP_AK`; no real AK was read and no Baidu request was made.

## 变更范围与自审

变更文件仅为：

- `app/schemas/agent.py`
- `tests/test_agent_contracts.py`
- 本报告

自审确认：I1 只增加公开 JSON Pointer 语法/Root token 的结构校验，不检查路径真实存在；I2 只统一 ID 语法，不解析跨对象引用存在性。未改 Runtime、Gateway、解析器、README、Core Tool 1–6 或任何外部服务调用。

## Commit

将以以下单一提交消息提交本次修复：

```text
fix: harden agent reference contracts
```
