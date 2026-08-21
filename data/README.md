# Dataset snapshot（数据快照）

本目录是研究服务的**唯一数据源**，以 JSON fixture 提交在仓库内——评审者**无需重新抓取任何受限来源**即可完整复核。

## 文件清单

| 文件 | 内容 |
|---|---|
| `dataset.json` | 快照元数据：`schema_version`、`as_of`（研究截点）、`research_target`（研究对象 id） |
| `companies.json` | 公司实体列表（21 家，含 1 个 target + 20 个 related） |
| `relationships.json` | 关系列表（20 条，五类关系；含方向、状态、置信度、时效窗口、证据引用） |
| `evidence.json` | 证据列表（26 条；URL、publisher、时间、locator、访问/许可说明、原文 quote） |
| `raw_edgar/` | 采集管线副产品：NVIDIA 10-K 原文（`10k_*.htm` / `10k_text.txt`）、关键词上下文（`edgar_supply_chain.json`）、公司提及（`company_mentions.json`） |

## Schema（字段速查）

### dataset.json

```json
{"schema_version": "1.0", "as_of": "2026-08-21", "research_target": "nvidia"}
```

- `as_of`：所有关系的时效判断基准（评分 recency 的锚点）。
- `research_target`：必须存在于 `companies.json` 且 `entity_type == "target"`。

### companies.json —— 公司实体

| 字段 | 说明 |
|---|---|
| `id` | 稳定 slug（如 `nvidia`、`tsmc`），关系端点引用它 |
| `name` | 公司法定/通用名 |
| `stock_code` / `exchange` | 证券标识（如 `NVDA` / `NASDAQ`），未上市为 `null` |
| `isin` | ISIN（本快照部分公司留空） |
| `entity_type` | `target`（研究对象，全数据集恰好 1 个）或 `related` |
| `country` / `sector` / `description` | 属地、行业、简介 |

### relationships.json —— 关系

| 字段 | 说明 |
|---|---|
| `source_company_id` / `target_company_id` | 关系两端（必须都在 companies.json） |
| `type` | `supplier` \| `customer` \| `partner` \| `investor_or_investee` \| `peer` |
| `direction` | 人类可读方向，如 `"tsmc -> nvidia"` |
| `status` | `confirmed`（≥70）/ `inferred`（40–69）/ `unknown`（<40）——**由引擎从分数推导** |
| `confidence_score` | 0–100，引擎计算并写回（`scripts/sync_scores.py --write`） |
| `valid_from` / `valid_until` | 已知时效窗口；`null` = 未知起点 / 截至 as-of 仍有效 |
| `evidence_ids` | 支持的证据 id 列表（必须都在 evidence.json，且属于该关系） |
| `summary` | 一段话结论（含事实/推断说明） |

> 关系数据中 `status` 与 `confidence_score` 是**派生物**：`sync_scores.py --write` 会用引擎 + 证据重算并写回，人工只维护方向、时效、证据引用与 summary。dry-run 会报告任何不一致。

### evidence.json —— 证据

| 字段 | 说明 |
|---|---|
| `relationship_id` | 支持哪条关系 |
| `source_url` | 来源权威 URL（https） |
| `publisher` | 发布方（如 `SEC EDGAR`、`NVIDIA Blog`） |
| `source_type` | 来源类型（`sec_filing` / `company_press_release` / `business_media` / `reference` …），驱动权威性评分 |
| `published_at` / `accessed_at` | 发布时间 / 采集时间（`published_at` 缺失时评分用 `accessed_at`） |
| `evidence_locator` | 精确定位（文件名、章节、段落、页码） |
| `access_restriction` | `public` / `paywall` / `login` / `registration` / `unknown`（合规字段，不影响评分） |
| `license_note` | 许可/使用条款说明 |
| `quote` | 原文引用（评审者据以复核判断，不依赖跳转阅读） |

## 校验与复现

```bash
python scripts/validate_data.py                 # 独立校验（schema + 完整性 + 引擎一致性）
python scripts/sync_scores.py --data data       # dry-run：报告与引擎不一致的人工状态
python scripts/sync_scores.py --data data --write  # 重写分数/状态（保证可复现）
```

测试套件中的 `TestReproducibility` 断言本目录所有关系的存储分数/状态与引擎重算完全一致。
