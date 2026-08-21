# Supply Chain & Partnership Research Service

可复现的供应链与合作伙伴关系研究服务。本仓库交付一个**公司无关（company-agnostic）**的研究服务：代码通过既定 JSON schema 加载任意研究目标的数据快照，切换目标公司只需更换 `data/` 下的数据文件，零代码改动。当前快照研究对象为 **NVIDIA Corporation**。

> ⚠️ **不构成投资建议。** 本仓库是面试研究挑战交付物，所有结论仅基于公开资料，供评审与教学使用，不构成任何投资、交易或法律建议。

---

## 1. 研究对象与边界

| 项目 | 内容 |
|---|---|
| 研究对象 | NVIDIA Corporation（英伟达，NASDAQ: **NVDA**） |
| 公司实体 | NVIDIA Corporation（美国特拉华州注册，总部 Santa Clara, CA） |
| 证券标识 | 股票代码 **NVDA**，交易所 **NASDAQ** |
| 研究时间截点（as-of） | **2026-08-21**（`data/dataset.json` 中的 `as_of`） |
| 覆盖范围 | 20 家关联上市公司、5 类关系（20 条）、26 条证据 |
| 覆盖实体 | TSMC、SK Hynix、Micron、ASML、Microsoft、Meta、Amazon、Alphabet、Dell、Accenture、ServiceNow、Snowflake、Cisco、CoreWeave、Recursion、SoundHound、AMD、Intel、Broadcom、Qualcomm |

**不覆盖边界（explicitly out of scope）：**

- 非上市公司（除台积电等同时有上市标识的实体外）与纯私有公司不作为关系端点；
- 宇树科技及 NVIDIA 之外的其他研究目标不在本快照内（架构支持，换 `data/` 即可）；
- 置信度不足以支撑结论（评分 < 40，即 unknown 带）的关系**不入库**——本项目只收录 confirmed/inferred 关系，未知信息在 `docs/scoring_methodology.md` 的局限性章节说明，避免用"可能存在"污染结论；
- 不包含 2026-08-21 之后发生的关系事件（快照式数据截点，详见第 8 节数据更新）；
- 不包含需要付费墙/登录/验证码才能获取的内容细节（合规要求，见第 3 节）。

## 2. 关系研究与状态语义

五类关系均带**方向**（`source -> target`）、**实体身份**、**事实/推断/未知状态**、**时效窗口**（`valid_from` / `valid_until`）与**可解释置信度**（0–100）：

| 关系类型 | 本快照条数 | 方向语义 |
|---|---|---|
| `supplier` | 4 | 供应商 → NVIDIA（如 TSMC 晶圆代工、SK Hynix/Micron HBM 存储） |
| `customer` | 5 | NVIDIA → 客户（如 Microsoft、Meta、Amazon、Alphabet、Dell） |
| `partner` | 4 | 合作伙伴（如 Accenture、ServiceNow、Snowflake、Cisco） |
| `investor_or_investee` | 3 | 投资或被投（如 NVIDIA → CoreWeave、Recursion；NVIDIA → SoundHound 已于 2025-02 退出，`valid_until` 标记） |
| `peer` | 4 | 可比/竞争对手（AMD、Intel、Broadcom、Qualcomm） |

**状态三态**（`RelationshipStatus`）：

- `confirmed`（已确认，16 条）：由权威/官方来源直接佐证，评分 ≥ 70；
- `inferred`（合理推断，4 条）：由二手来源或间接证据合理推断，评分 40–69；
- `unknown`（未知）：本项目不收录该档关系，仅记录盲区。

**时效性**：每条关系带 `valid_from`/`valid_until`。`valid_until = null` 表示截至 as-of 仍然有效；已终止关系（如 SoundHound 投资 2025-02-14 退出）保留时间窗，评分按已终止关系衰减。

## 3. 合规的数据采集

- **仅使用合法可公开访问的来源**：SEC EDGAR 公开 API（10-K 年报）、上市公司官网/IR/新闻稿、财经媒体、行业协会页面等；
- **不绕过任何访问控制**：不破解 robots.txt、不绕过登录/付费墙/验证码/限流；
- **SEC EDGAR 调用规范**：`scripts/fetch_edgar.py` 使用带联系信息的 User-Agent（见 `.env.example` 的 `SCR_EDGAR_USER_AGENT`）、遵守 EDGAR 速率限制（≤10 req/s）；
- **不提交受限数据**：仓库不含密钥、个人数据、客户机密；付费墙内容仅使用其公开摘要并标注访问限制（`access_restriction: paywall`）；
- **证据留痕**：每条证据记录 `source_url`、`publisher`、`published_at`/`accessed_at`、`evidence_locator`（章节/段落定位）、`access_restriction`、`license_note`。

**实体歧义 / 来源冲突 / 过期信息 / 共现误判的处理**：证据引用使用原文 quote 而非转述；同名公司通过证券标识与正文全称消歧；来源冲突时优先官方来源并在 summary 中标注分歧；过期关系用 `valid_until` 显式终止并让评分衰减（见评分方法文档）。

## 4. 数据存储（评审者无需重新抓取）

所有数据以 **JSON fixture 快照** 提交在仓库内，评审者可离线复核，不依赖任何受限来源或网络：

```
data/
  dataset.json         # schema_version / as_of / research_target
  companies.json       # 21 家公司
  relationships.json   # 20 条关系（含 confidence_score、status、时效、证据引用）
  evidence.json        # 26 条证据（URL、publisher、时间、locator、许可）
  raw_edgar/           # NVIDIA 10-K 原文与公司提及抽取结果（采集管线副产品）
```

关系数据中的 `confidence_score` 与 `status` 由评分引擎统一生成并写回（`scripts/sync_scores.py --write`），保证"提交的数据 = 引擎 + 证据"的**完全可复现**（见第 8 节）。

## 5. 可解释置信度评分（0–100）

每条关系由 5 个加权维度加权求和，`score_breakdown` 随 API/CLI 返回，评审者可以逐维追溯：

| 维度 | 权重 | 含义 |
|---|---|---|
| `authority` | 25 | 最权威证据的来源类型（SEC 文件 25 > 政府 22 > 公司 IR/新闻稿 20 > 媒体 16 > 百科 10 …） |
| `evidence_quality` | 25 | 独立来源数量与证据深度（1 个来源 16 / 2 个 21 / 3+ 个 24–25） |
| `recency` | 20 | 最新证据的时效衰减（官方确认的存续关系按"持续有效"计满分，见方法文档） |
| `specificity` | 20 | 关系被描述的明确程度（强关系词加分、模糊词扣分、官方直接点名对手 +5） |
| `quantifiability` | 10 | 是否带有可量化信息（金额、百分比、数字） |

评分 → 状态带映射：**≥70 = confirmed，40–69 = inferred，<40 = unknown**。完整细则与两项研究判断细化（官方存续关系的时效规则、直接陈述加成）见 **[docs/scoring_methodology.md](docs/scoring_methodology.md)**。

## 6. HTTP JSON API

启动（详见第 8 节）：

```bash
uvicorn src.api:app --host 127.0.0.1 --port 8000
# 或 SCR_PORT=8123 python -m src.api
```

| 端点 | 说明 |
|---|---|
| `GET /health` | 服务与数据集健康检查 |
| `GET /api/v1/stats` | 数据集统计（按类型/状态分桶） |
| `GET /api/v1/companies` | 公司列表（`name`、`entity_type` 过滤 + 分页） |
| `GET /api/v1/companies/{id}` | 单个公司 |
| `GET /api/v1/relationships` | 关系列表，支持 `company_id`、`relationship_type`、`status`、`min_confidence`、`max_confidence`、`valid_as_of` 过滤 + 分页 |
| `GET /api/v1/relationships/{id}` | 关系详情（含内嵌 evidence + `score_breakdown`） |
| `GET /api/v1/relationships/{id}/evidence` | 关系证据列表 |
| `GET /api/v1/evidence/{id}` | 单条证据 |
| `GET /api/v1/graph` | 关系图（nodes + edges） |

**输入校验**：`relationship_type` 限定五类枚举、`status` 限定三态（非法值 422）；`min_confidence > max_confidence` 返回 422 `invalid_range`；`page`/`page_size` 有界；未知资源返回结构化 404（`{"detail": {"error": "...", "message": "..."}}`）。

**分页**：响应含 `page` / `page_size` / `total` / `total_pages` / `has_next` / `has_previous`。

示例：

```bash
curl "http://127.0.0.1:8000/api/v1/relationships?relationship_type=supplier&min_confidence=70&page_size=2"
curl "http://127.0.0.1:8000/api/v1/relationships/rel_inv_001"   # 含 score_breakdown
curl "http://127.0.0.1:8000/api/v1/graph"
```

每个端点的**实测请求/响应记录**（含错误路径）见 **[docs/api_examples.md](docs/api_examples.md)**；交互式 OpenAPI 文档由 FastAPI 自动提供于 `http://127.0.0.1:8000/docs`。

## 6.5. Docker 一键运行（推荐评审方式）

无需安装 Python 或任何依赖——直接拉取镜像运行：

```bash
# 方式一：直接 docker run
docker run --rm -p 8000:8000 ghcr.io/iloveopt/supply-chain-research:latest

# 方式二：docker compose（克隆仓库后）
docker compose up
```

启动后打开浏览器：

| 地址 | 说明 |
|---|---|
| `http://localhost:8000/` | **交互式仪表盘**（21 家公司 / 20 条关系 / 26 条证据，可筛选/搜索/排序/关系图谱） |
| `http://localhost:8000/docs` | OpenAPI / Swagger UI（交互式 API 文档） |
| `http://localhost:8000/api/v1/stats` | 数据集统计 JSON |
| `http://localhost:8000/api/v1/graph` | 关系图 JSON |

镜像基于 `python:3.12-slim` 多阶段构建，非 root 用户运行，内置 healthcheck，镜像约 120 MB。

> 镜像由 GitHub Actions 自动构建并发布至 GHCR（`ghcr.io/iloveopt/supply-chain-research`），每次 push 到 `main` 或打 `v*` tag 自动更新 `latest`。

## 7. CLI（`scrs`）

与 API 共享同一 store，可脚本化（所有输出支持 `--json`）：

```bash
scrs health
scrs stats
scrs companies [--name nvidia] [--entity-type related] [--json]
scrs company <company-id>
scrs relationships [--company nvidia] [--type supplier] [--status confirmed]
                  [--min-score 70] [--max-score 85] [--valid-as-of 2026-08-21] [--json]
scrs relationship <relationship-id> [--json]   # 详情 + 证据 + score_breakdown
scrs evidence <evidence-id>
scrs score <relationship-id>                    # 重算并解释单条关系评分
scrs graph [--json]
```

安装后可直接使用 `scrs`；未安装时 `python -m src.cli` 等价。未知 id 退出码 1，非法日期报错并退出 1。

## 8. 环境变量、启动与复现

**依赖**：Python ≥ 3.11；`pip install -e ".[dev]"`（fastapi / uvicorn / typer / pydantic / pytest）。

**环境变量**（全部可选，无真实凭据，见 [`.env.example`](.env.example)）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SCR_DATA_DIR` | `./data` | 数据集目录 |
| `SCR_HOST` / `SCR_PORT` | `127.0.0.1` / `8000` | API 监听地址 |
| `SCR_EDGAR_USER_AGENT` | 模板值 | 仅供采集脚本使用，EDGAR 要求带联系信息的 UA |

**启动 / 测试**：

```bash
pip install -e ".[dev]"
uvicorn src.api:app --port 8000            # HTTP API
scrs stats                                  # CLI
pytest --cov=src                            # 100 个测试，src 覆盖率 97%（下限 90%）
python scripts/validate_data.py             # 独立数据校验（schema + 完整性 + 引擎一致性）
```

**数据更新流程**（NVIDIA 或切换到任意新目标）：

```bash
# 1) 采集（合规：SEC EDGAR 公开 API + 限速 + 质控 UA）
python scripts/fetch_edgar.py --ticker NVDA --out data/raw_edgar
# 2) 从 10-K 抽取公司提及，辅助发现关系候选
python scripts/extract_company_mentions.py --data data/raw_edgar --out data/raw_edgar/company_mentions.json
# 3) 人工核验：补充证据，编写 relationships.json / evidence.json（含方向、时效、来源字段）
# 4) 独立校验 + 引擎重算并写回分数与状态（保证可复现）
python scripts/validate_data.py
python scripts/sync_scores.py --data data --write
# 5) 更新 dataset.json 的 as_of，运行测试并提交
pytest --cov=src
```

评分/状态是**派生物**而非人工输入：`sync_scores.py --write` 用引擎 + 证据重算 `confidence_score` 并按带映射推导 `status` 写回，dry-run 会报告任何与引擎不一致的人工状态；`validate_data.py` 独立检查 schema、引用完整性、时间窗合法性与引擎一致性。

**持续集成**：仓库附带 GitHub Actions：`.github/workflows/ci.yml`（Python 3.11/3.12/3.13 上执行 `pytest`、`validate_data.py` 与 `sync_scores.py` 可复现性检查）+ `.github/workflows/docker.yml`（自动构建并推送 Docker 镜像至 GHCR）。

## 9. 测试与限制

**测试**（`tests/`，100 个用例，src 覆盖率 97%）：store 层加载/完整性/过滤/分页、评分引擎各维度与两项细化、API 全部端点与 404/422 错误路径、CLI 命令/退出码/人类可读输出；以及一条**全数据集可复现性断言**——每条关系的存储分数与状态必须与引擎重算结果完全一致（`test_scoring.py::TestReproducibility`）。独立校验脚本 `scripts/validate_data.py` 提供同等的评审入口。

**已知限制与盲区**：

- **单时点快照**：数据截止 2026-08-21，之后的公司行为不会反映；需按第 8 节流程刷新；
- **来源以英文为主**：SEC 文件、官方新闻稿为权威来源；中文/其他语言来源未覆盖；
- **二手来源**：Wikipedia 作为二手参考仅用于交叉验证，权威性评分低（10 分）；
- **共现误判防护**：同段出现不代表关系，direct-statement 加成仅限官方来源明确点名对手的语境；
- **私有公司不收录**：大量 NVIDIA 生态私有伙伴（如部分云厂商/初创）不在端点范围内；
- **`unknown` 不收录**：无法验证的关系不入库，避免噪音，代价是可能漏掉真实但证据不足的关系。

**未来数据质量改进方向**：接入更多交易所文件（HKEX/韩交所/台交所）覆盖亚太供应商；引入官方结构化数据（如 SEC XBRL、客户集中度披露）；对证据做时间线交叉核验；增加对中文信源（公司财报/官方公众号）的采集。

## 10. AI 使用披露（Research & Engineering Judgment Disclosure）

本项目在数据采集、清洗、证据整理、评分引擎设计与代码实现过程中使用了 **AI 编码 Agent 辅助**（包括代码生成、测试编写与文档整理）。具体说明：

- **AI 的用途**：辅助编写采集/解析脚本、评分引擎、API/CLI 代码、测试用例与本文档；辅助从公开资料中初步整理关系候选与证据引文；
- **人工验证方式**：所有关系结论、证据 quote、时效窗口、来源 URL 与评分结果均由本人（研究者）逐条核验；评分引擎规则由本人设计并复核；关键证据（10-K 竞争话语等）以原文 quote 存档于 `data/evidence.json`，评审者可直接打开原始文件核对；
- **责任归属**：研究判断与工程决策（覆盖范围、关系方向、状态判定、评分规则、合规边界）均由本人负责，AI 不承担任何研究结论责任；
- **数据安全**：未向任何 AI 工具输入密钥、个人数据、客户机密或未授权资料；数据仅来自合法公开来源。

---

**License / 数据说明**：代码遵循项目内声明；证据引用来自各自来源（SEC 文件为公共领域，公司新闻稿为公开内容，Wikipedia 为 CC BY-SA），详见各证据的 `license_note` 字段。
