# API 实测示例（Recorded API Examples）

以下输出为 **2026-08-21** 对 `uvicorn src.api:app --host 127.0.0.1 --port 8123` 的**实际请求记录**，评审者可据此比对本地运行结果。部分冗长响应做了节选（以 `...` 标注）。

## 1. 健康检查

```bash
curl http://127.0.0.1:8123/health
```

```json
{"status":"ok","dataset":"nvidia","as_of":"2026-08-21","companies":21,"relationships":20,"evidence":26,"server_time":"2026-08-21T08:36:37.140137Z"}
```

## 2. 数据集统计

```bash
curl http://127.0.0.1:8123/api/v1/stats
```

```json
{"companies":21,"relationships":20,"evidence":26,"as_of":"2026-08-21","research_target":"nvidia",
 "relationships_by_type":{"customer":5,"investor_or_investee":3,"partner":4,"peer":4,"supplier":4},
 "relationships_by_status":{"confirmed":16,"inferred":4}}
```

## 3. 公司查询（名称过滤）

```bash
curl "http://127.0.0.1:8123/api/v1/companies?name=nvidia"
```

```json
{"items":[{"id":"nvidia","name":"NVIDIA Corporation","stock_code":"NVDA","exchange":"NASDAQ","isin":"US67066G1040","country":"US","entity_type":"target","sector":"Semiconductors (GPU / accelerated computing)","description":"Research target. NVIDIA designs GPUs and accelerated-computing platforms for AI training/inference, data centers, gaming, and automotive. Fabless: relies on foundries and contract manufacturers."}],
 "page":1,"page_size":20,"total":1,"total_pages":1,"has_next":false,"has_previous":false}
```

## 4. 单个公司

```bash
curl http://127.0.0.1:8123/api/v1/companies/nvidia
```

```json
{"id":"nvidia","name":"NVIDIA Corporation","stock_code":"NVDA","exchange":"NASDAQ","isin":"US67066G1040","country":"US","entity_type":"target","sector":"Semiconductors (GPU / accelerated computing)","description":"Research target. NVIDIA designs GPUs and accelerated-computing platforms for AI training/inference, data centers, gaming, and automotive. Fabless: relies on foundries and contract manufacturers."}
```

## 5. 关系列表（类型 + 置信度过滤 + 分页）

```bash
curl "http://127.0.0.1:8123/api/v1/relationships?relationship_type=supplier&min_confidence=70&page_size=2"
```

```json
{"items":[
  {"id":"rel_sup_001","source_company_id":"tsmc","target_company_id":"nvidia","type":"supplier","direction":"tsmc -> nvidia","status":"confirmed","confidence_score":83,"valid_from":"2010-01-01","valid_until":null,"evidence_ids":["ev_sup_001"],"summary":"TSMC is NVIDIA's primary foundry partner for its most advanced GPU wafers (fabless manufacturing); NVIDIA's 10-K explicitly names TSMC as a foundry supplier."},
  {"id":"rel_sup_002","source_company_id":"sk_hynix","target_company_id":"nvidia","type":"supplier","direction":"sk_hynix -> nvidia","status":"confirmed","confidence_score":77,"valid_from":"2019-01-01","valid_until":null,"evidence_ids":["ev_sup_002"],"summary":"SK Hynix is a named memory (incl. HBM) supplier to NVIDIA per NVIDIA's 10-K; HBM is critical for AI accelerators."}
 ],
 "page":1,"page_size":2,"total":3,"total_pages":2,"has_next":true,"has_previous":false}
```

> `total=3`：supplier 共 4 条，`min_confidence=70` 排除 ASML（60 分，inferred）。

## 6. 关系详情（含内嵌证据 + 可解释评分分解）

```bash
curl http://127.0.0.1:8123/api/v1/relationships/rel_inv_001
```

关键字段（`evidence` 数组为完整 3 条，此处省略）：

```json
{
  "id": "rel_inv_001",
  "source_company_id": "nvidia",
  "target_company_id": "coreweave",
  "type": "investor_or_investee",
  "direction": "nvidia -> coreweave (investor -> investee)",
  "status": "confirmed",
  "confidence_score": 86,
  "valid_from": "2023-04-01",
  "valid_until": null,
  "evidence_ids": ["ev_inv_001", "ev_inv_001b", "ev_inv_001c"],
  "score_breakdown": {
    "total": 86.0,
    "band": "confirmed",
    "dimensions": {"authority": 20.0, "evidence_quality": 21.0, "recency": 20.0, "specificity": 20.0, "quantifiability": 5.0}
  }
}
```

评分可解释性示例：`recency=20`（CoreWeave 官方新闻稿 2026-01-26 确认存续合作 + 官方来源规则）、`specificity=20`（新闻稿直接点名对方 + 强关系词）、`quantifiability=5`（$2B 金额）。

## 7. 单条证据

```bash
curl http://127.0.0.1:8123/api/v1/evidence/ev_sup_001
```

```json
{"id":"ev_sup_001","relationship_id":"rel_sup_001","source_url":"https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm","publisher":"U.S. Securities and Exchange Commission (NVIDIA FY2026 Form 10-K)","source_type":"sec_filing","published_at":"2026-02-25","accessed_at":"2026-08-21","evidence_locator":"10-K FY2026, Item 1 Business — Manufacturing section, sentence listing foundries","access_restriction":"public","license_note":"SEC filings are public domain under U..."}
```

## 8. 错误响应：404

```bash
curl -w "\nHTTP %{http_code}\n" http://127.0.0.1:8123/api/v1/relationships/nope
```

```json
{"detail":{"error":"relationship_not_found","message":"Unknown relationship id 'nope'"}}
```

```
HTTP 404
```

同类：`company_not_found`、`evidence_not_found` 采用一致的结构化格式。

## 9. 错误响应：422 参数校验

```bash
curl -w "\nHTTP %{http_code}\n" "http://127.0.0.1:8123/api/v1/relationships?min_confidence=90&max_confidence=50"
```

```json
{"detail":{"error":"invalid_range","message":"min_confidence must be <= max_confidence"}}
```

```
HTTP 422
```

非法枚举（`relationship_type=banana`、`status=maybe`）同样返回 422（FastAPI pattern 校验）。

## 10. 关系图

```bash
curl http://127.0.0.1:8123/api/v1/graph
```

```json
{"research_target":"nvidia","as_of":"2026-08-21","nodes":[21 items...],"edges":[20 items...]}
```

节点为 21 家公司（含 target），边为 20 条关系；每条边带 `type`、`status`、`confidence_score`、时效窗口。

## 11. 时间点过滤（时效性演示）

```bash
curl "http://127.0.0.1:8123/api/v1/relationships?valid_as_of=2024-06-30&page_size=100"
# => "total": 19
```

2024-06-30 时 Cisco 合作关系（`valid_from=2025-02-01`）尚未开始，被正确排除；SoundHound 投资（2017-01-01 ~ 2025-02-14）当时仍有效，保留在结果中。

---

**复现方式**：`pip install -e ".[dev]" && uvicorn src.api:app --port 8000`，将上述 `8123` 换成 `8000` 即可逐条比对。
