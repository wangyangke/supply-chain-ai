# 置信度评分方法（Scoring Methodology）

每条关系（relationship）的 `confidence_score` 是 0–100 的可解释分数，由五个加权维度求和得出。API 详情端点与 CLI `scrs relationship` / `scrs score` 都会返回 `score_breakdown`（各维度分数 + 总分 + 状态带），评审者可以把任意分数逐维追溯到证据。

## 1. 五个维度与权重

| 维度 | 权重 | 满分语义 | 说明 |
|---|---|---|---|
| `authority` | 25 | 证据来自最高权威来源 | 取该关系所有证据中来源类型的最高权威值 |
| `evidence_quality` | 25 | ≥4 个独立来源 | 按**独立 URL 数**计分（同一 URL 只算一次） |
| `recency` | 20 | 证据足够新 / 官方确认存续 | 按最新证据年龄分档衰减，官方存续关系不衰减（见 §3.1） |
| `specificity` | 20 | 关系被明确、直接地描述 | 强关系词加分、模糊/传闻词扣分、官方直接点名 +5（见 §3.2） |
| `quantifiability` | 10 | 带有可量化信息 | 百分比 +5、金额 +3、数字 +2，上限 10 |

### 1.1 authority 来源权威分层

| 来源类型（`source_type`） | 分值 |
|---|---|
| `sec_filing` / `exchange_filing`（法定文件） | 25 |
| `government`（政府/监管） | 22 |
| `company_ir` / `company_press_release`（公司官方渠道） | 20 |
| `analyst_research`（分析师报告） | 18 |
| `business_media`（财经媒体） | 16 |
| `industry_database`（行业数据库） | 15 |
| `reference`（百科/二手参考） | 10 |
| `informal`（博客/论坛/社交） | 4 |
| `unknown` | 0 |

注意：`authority` 衡量的是**来源信任度**，与访问限制（`access_restriction`）分开记录——访问限制是合规字段，不影响权威评分。

### 1.2 evidence_quality：独立来源计分

| 独立来源数 | 分值 |
|---|---|
| 0 | 0 |
| 1 | 16 |
| 2 | 21 |
| 3 | 24 |
| ≥4 | 25 |

"独立"按 source URL 判重：同一文档的重复引用只算 1 个；同一发布者的不同文档（如不同年度的 10-K）视为独立。

### 1.3 recency：时效分档

按最新证据的发布年龄分档（`as_of` 为研究截点）：

| 年龄（天） | 分值 |
|---|---|
| ≤ 180 | 20 |
| ≤ 365 | 16 |
| ≤ 730 | 12 |
| ≤ 1095 | 8 |
| ≤ 1825 | 4 |
| > 1825 | 1（保底） |

证据无 `published_at` 时用 `accessed_at` 代替。

### 1.4 specificity：明确程度

扫描证据 quote 与关系 summary 的合并文本：

- **强关系词**（foundry / supplier / purchase / competitor / partnership / invest / acquired / exclusive / multi-year / joint / strategic …）每个 +3，上限 20；
- **模糊/传闻词**（reportedly / may / might / likely / possibly / believed / rumor / according to reports / sources say / indirect / could）每个 −2；
- **直接陈述加成**：见 §3.2。

### 1.5 quantifiability：可量化信息

合并文本中出现：百分比（`%` / percent）+5；金额（`$` / usd / billion / million）+3；任意数字 +2；上限 10。

## 2. 分数 → 状态带映射

| 分数 | 状态（`status`） | 语义 |
|---|---|---|
| ≥ 70 | `confirmed` | 已确认：由权威/官方来源直接佐证 |
| 40 – 69 | `inferred` | 合理推断：间接或二手证据支撑 |
| < 40 | `unknown` | 未知：证据不足以支撑结论 |

状态带由引擎从分数推导，与人工判定解耦——`scripts/sync_scores.py --write` 会用引擎分数重写 `confidence_score` 并推导 `status` 写回 `data/relationships.json`，保证提交数据与引擎完全一致。

## 3. 两项研究判断细化

### 3.1 官方确认的存续关系 = 持续新鲜（recency 规则）

**规则**：若关系仍有效（`valid_until is None` 或 ≥ `as_of`）**且**证据含官方/法定来源（`sec_filing` / `exchange_filing` / `government` / `company_ir` / `company_press_release`），`recency` 直接给满分 20。

**理由**：官方文件（如 10-K）和公司公告是对"关系**现在仍然存在**"的持续断言，不是一次性新闻。文件每年刷新、合作公告在关系存续期间不失效，因此按"持续有效的事实"处理，而不是按新闻年龄衰减。

**反例（衰减路径）**：已终止关系（`valid_until` 在过去）不享受该规则——例如 NVIDIA→SoundHound 投资已于 2025-02-14 退出，即使有官方 13F 文件，其 recency 也按正常年龄衰减（事实已历史化）。

### 3.2 直接陈述加成（specificity 规则）

**规则**：若关系证据含官方/法定来源，**且官方来源在明确的关系语境中点名对方公司**（如 10-K 中 "Our current competitors include ... AMD"），`specificity` +5。

**判定条件**（`_is_direct_statement`）：

1. 至少一条证据来自官方/法定来源；
2. 对方公司名（关系两端中非研究目标的一端）出现在证据文本中——用**容错名称匹配**（见 §3.3）；
3. 文本中出现与该关系类型匹配的关系词（如 peer 关系出现 competitor/compete；supplier 关系出现 supplier/foundry/purchase）。

**为什么第三方估计不加成**：媒体/分析师说"我们估计 X 的竞争对手包括 Y"是外部推断，官方说"我们的竞争对手包括 Y"是**当事人陈述**。这区分了"具名的声明"与"外部的估计"，也是 confirmed 与 inferred 的核心差异之一。

### 3.3 容错公司名匹配

匹配证据文本中的公司名时，先尝试全名，再退化为"显著 token 匹配"（剔除法律后缀与虚词：inc / corp / corporation / ltd / limited / company / com / plc / holdings / group / the / and / for / with / of / co）：

- `Cisco Systems, Inc.` → token `{cisco, systems}`，可匹配 "Cisco (NASDAQ: CSCO)"；
- `Advanced Micro Devices, Inc. (AMD)` → token `{advanced, micro, devices, amd}`，可匹配 "Advanced Micro Devices, Inc., or AMD"。

## 4. 可复现性

评分管线是确定性的：

```
data/relationships.json（方向/时效/证据引用，人工核验）
        +  data/evidence.json（quote 原文，人工核验）
        +  src/scoring.py（引擎规则）
        ── scripts/sync_scores.py --write ──► 重写 confidence_score + status
```

`sync_scores.py` 以 dry-run 运行会报告所有与引擎不一致的人工状态；`--write` 写回后，`pytest` 中的全数据集断言（`TestReproducibility`）验证"存储分数/状态 == 引擎重算结果"。

## 5. 局限性与已知盲区

- 评分基于**证据集本身**：证据缺失或来源质量低的关系会得到低分，这是设计意图（宁缺毋滥）；
- `quantifiability` 只做浅层数字识别，不区分数字的语义重要性（"19%" 与 "第 19 条" 权重相同），未来可引入结构化量化字段；
- `evidence_quality` 用 URL 判重，未区分"同文转载"（不同媒体转载同一新闻稿会被计为独立来源）；
- 官方来源的存续关系假设"官方不更新即仍有效"，对上市公司基本成立，但极端情形（公司静默终止合作且未披露）无法覆盖；
- 以上局限通过记录在 README「已知限制与盲区」章节，配合证据原文 quote，评审者可自行复核。
