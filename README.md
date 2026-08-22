# Supply Chain & Partnership Research Service

可复现的供应链与合作伙伴关系研究服务。**多目标（multi-target）架构**：`data/targets.json` 注册任意数量的研究目标，API/CLI/仪表盘可在目标间一键切换，接入新公司零代码改动。当前注册表包含：
- **NVIDIA Corporation**（默认，22 家公司 / 21 条关系 / 29 条证据）
- **宇树科技 Unitree Robotics**（6 家公司 / 5 条关系 / 9 条证据，由 agent 研究通道全程采集验证）
- **Tesla, Inc.**（3 家公司 / 6 条关系 / 6 条证据）与 **比亚迪**（6 家公司 / 15 条关系 / 24 条证据）作为辅助目标，identity 字段较 sparse，用于验证多目标切换与 onboarding 流程。

> ⚠️ **不构成投资建议。** 本仓库是面试研究挑战交付物，所有结论仅基于公开资料，供评审与教学使用，不构成任何投资、交易或法律建议。

---

## 1. 研究对象与边界

**默认目标：NVIDIA**

| 项目 | 内容 |
|---|---|
| 研究对象 | NVIDIA Corporation（英伟达，NASDAQ: **NVDA**） |
| 公司实体 | NVIDIA Corporation（美国特拉华州注册，总部 Santa Clara, CA） |
| 证券标识 | 股票代码 **NVDA**，交易所 **NASDAQ** |
| 研究时间截点（as-of） | **2026-08-21**（`data/targets/nvidia/dataset.json` 中的 `as_of`） |
| 覆盖范围 | 22 家关联上市公司、5 类关系（21 条）、29 条证据 |
| 覆盖实体 | TSMC、SK Hynix、Micron、ASML、Microsoft、Meta、Amazon、Alphabet、Dell、Accenture、ServiceNow、Snowflake、Cisco、Oracle、CoreWeave、Recursion、SoundHound、AMD、Intel、Broadcom、Qualcomm |

**第二目标：宇树科技（Unitree Robotics）** —— 完整走 agent 研究通道接入（搜索 → 核验 → staging → 合入 → 重算）：

| 项目 | 内容 |
|---|---|
| 研究对象 | 宇树科技 Unitree Robotics（**688836.SH**，上交所科创板，2026-08-19 上市） |
| 覆盖范围 | 5 家关联上市公司、3 类关系（5 条）、9 条证据 |
| 覆盖实体 | NVIDIA（合作伙伴）、美团/腾讯/阿里巴巴（投资方）、优必选（竞争对手） |

**辅助研究目标（auxiliary targets）：**

| 项目 | 内容 |
|---|---|
| Tesla, Inc.（`tesla`） | 辅助目标，3 家公司 / 6 条关系 / 6 条证据（as-of 2026-08-22）；identity 字段较 sparse，数据规模小于 NVIDIA/宇树，用于验证多目标切换与 onboarding 流程。 |
| 比亚迪（`c_1de9a5e2`） | 辅助目标，6 家公司 / 15 条关系 / 24 条证据（as-of 2026-08-22）；由 agent 自动注册生成，identity 字段待人工补齐。 |

> 注：tesla 与比亚迪为**辅助/补充数据集**，质量与字段完整度低于 NVIDIA/宇树；评审时请以 NVIDIA 与宇树科技为主要研究对象。

**不覆盖边界（explicitly out of scope）：**

- 非上市公司（除台积电等同时有上市标识的实体外）与纯私有公司不作为关系端点（宇树的私有投资方如红杉中国、智元机器人等因此未收录）；
- 置信度不足以支撑结论（评分 < 40，即 unknown 带）的关系**不入库**——本项目只收录 confirmed/inferred 关系，未知信息在 `docs/scoring_methodology.md` 的局限性章节说明，避免用"可能存在"污染结论；
- 不包含 2026-08-21 之后发生的关系事件（快照式数据截点，详见第 8 节数据更新）；
- 不包含需要付费墙/登录/验证码才能获取的内容细节（合规要求，见第 3 节）。

## 2. 关系研究与状态语义

五类关系均带**方向**（`source -> target`）、**实体身份**、**事实/推断/未知状态**、**时效窗口**（`valid_from` / `valid_until`）与**可解释置信度**（0–100）：

| 关系类型 | NVIDIA 快照条数 | 方向语义 |
|---|---|---|
| `supplier` | 4 | 供应商 → NVIDIA（如 TSMC 晶圆代工、SK Hynix/Micron HBM 存储） |
| `customer` | 5 | NVIDIA → 客户（如 Microsoft、Meta、Amazon、Alphabet、Dell） |
| `partner` | 5 | 合作伙伴（如 Accenture、ServiceNow、Snowflake、Cisco、Oracle） |
| `investor_or_investee` | 3 | 投资或被投（如 NVIDIA → CoreWeave、Recursion；NVIDIA → SoundHound 已于 2025-02 退出，`valid_until` 标记） |
| `peer` | 4 | 可比/竞争对手（AMD、Intel、Broadcom、Qualcomm） |

**状态三态**（`RelationshipStatus`）：

- `confirmed`（已确认，17 条）：由权威/官方来源直接佐证，评分 ≥ 70；
- `inferred`（合理推断，4 条）：由二手来源或间接证据合理推断，评分 40–69；
- `unknown`（未知）：本项目不收录该档关系，仅记录盲区。

**时效性**：每条关系带 `valid_from`/`valid_until`。`valid_until = null` 表示截至 as-of 仍然有效；已终止关系（如 SoundHound 投资 2025-02-14 退出）保留时间窗，评分按已终止关系衰减。

## 3. 合规的数据采集

- **仅使用合法可公开访问的来源**：SEC EDGAR 公开 API（10-K 年报）、上市公司官网/IR/新闻稿、财经媒体、行业协会页面等；
- **不绕过任何访问控制**：不破解 robots.txt、不绕过登录/付费墙/验证码/限流；所有采集 URL 在暂存前经 `scripts/robots_check.py` 程序化校验,被 `Disallow` 的 URL 一律丢弃；
- **SEC EDGAR 调用规范**：`scripts/fetch_edgar.py` 使用带联系信息的 User-Agent（见 `.env.example` 的 `SCR_EDGAR_USER_AGENT`）、遵守 EDGAR 速率限制（≤10 req/s）；
- **亚太交易所披露接入（Track A）**：`scripts/fetch_exchange.py` 提供 SSE STAR Market（688xxx,上交所 e-interaction 披露平台）与 HKEX（HKEXnews 搜索）两个适配器,产出的 filing 经 `filing_to_staging_candidate()` 归一化为 `source_type = exchange_filing`（评分权威层级 25,无需改评分引擎）的 staging 骨架,再走与 EDGAR 相同的 agent 核验 + 合入流程。两个适配器共享同一速率限制（≤10 req/s,令牌桶节流）与带联系信息的 User-Agent（`SCR_USER_AGENT`）,并复用 robots.txt 合规门;
- **不提交受限数据**：仓库不含密钥、个人数据、客户机密；付费墙内容仅使用其公开摘要并标注访问限制（`access_restriction: paywall`）；
- **证据留痕**：每条证据记录 `source_url`、`publisher`、`published_at`/`accessed_at`、`evidence_locator`（章节/段落定位）、`access_restriction`、`license_note`、`content_hash`（原文哈希指纹,见 §10）。

**采集脚本一览**：

| 脚本 | 数据源 | 速率限制 | robots 校验 |
|---|---|---|---|
| `scripts/fetch_edgar.py` | SEC EDGAR 公开 API | ≤10 req/s,带联系信息 UA | 文档承诺不绕过 robots.txt |
| `scripts/fetch_exchange.py` | SSE STAR Market / HKEXnews | ≤10 req/s,令牌桶节流 | `scripts/robots_check.py` 程序化校验 |
| `scripts/research_harvest.py` | Tavily / Brave 搜索 API | 后端自带限速 | 暂存前 robots 门过滤 |

**实体歧义 / 来源冲突 / 过期信息 / 共现误判的处理**：证据引用使用原文 quote 而非转述；同名公司通过证券标识与正文全称消歧；来源冲突时优先官方来源并在 summary 中标注分歧；过期关系用 `valid_until` 显式终止并让评分衰减（见评分方法文档）。

## 4. 数据存储（评审者无需重新抓取）

所有数据以 **JSON fixture 快照** 提交在仓库内，评审者可离线复核，不依赖任何受限来源或网络。**多目标布局**——`data/targets.json` 是目标注册表，每个研究目标一个独立数据集目录：

```
data/
  targets.json            # 目标注册表（default_target + targets 列表）
  targets/
    nvidia/               # 默认目标：NVIDIA
      dataset.json        # schema_version / as_of / research_target
      companies.json      # 22 家公司
      relationships.json  # 21 条关系（含 confidence_score、status、时效、证据引用）
      evidence.json       # 29 条证据（URL、publisher、时间、locator、许可）
      raw_edgar/          # NVIDIA 10-K 原文与公司提及抽取结果（采集管线副产品）
      staging/            # agent 研究通道的 staging 区（含审计标记）
    unitree/              # 第二目标：宇树科技
      dataset.json        # as_of 2026-08-21
      companies.json      # 6 家公司
      relationships.json  # 5 条关系
      evidence.json       # 9 条证据
      staging/            # 5 份 staging 文件（含 merged 审计标记）
    tesla/                # demo 目标：占位数据集
      dataset.json
      companies.json
      relationships.json
      evidence.json
    c_1de9a5e2/           # demo 目标：比亚迪占位数据集
      dataset.json
      companies.json
      relationships.json
      evidence.json
```

关系数据中的 `confidence_score` 与 `status` 由评分引擎统一生成并写回（`scripts/sync_scores.py --write`），保证"提交的数据 = 引擎 + 证据"的**完全可复现**（见第 8 节）。

**目标切换**：API 用 `?target=` 查询参数（如 `/api/v1/stats?target=unitree`）或 `GET /api/v1/targets` 查看注册目标；CLI 用全局 `--target` 选项（如 `scrs --target unitree stats`）或 `SCR_TARGET` 环境变量；仪表盘右上角下拉框直接切换。

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
| `GET /api/v1/targets` | **研究目标注册表**（默认目标 + 全部目标列表） |
| `GET /api/v1/stats` | 数据集统计（按类型/状态分桶） |
| `GET /api/v1/companies` | 公司列表（`name`、`entity_type` 过滤 + 分页） |
| `GET /api/v1/companies/{id}` | 单个公司 |
| `GET /api/v1/relationships` | 关系列表，支持 `company_id`、`relationship_type`、`status`、`min_confidence`、`max_confidence`、`valid_as_of` 过滤 + 分页 |
| `GET /api/v1/relationships/{id}` | 关系详情（含内嵌 evidence + `score_breakdown`） |
| `GET /api/v1/relationships/{id}/evidence` | 关系证据列表 |
| `GET /api/v1/evidence/{id}` | 单条证据 |
| `GET /api/v1/graph` | 关系图（nodes + edges） |
| `POST /api/v1/research` | **在线研究新公司**：启动异步 agent 任务（搜索 → 核验 → 合入 → 评分），返回 `job_id` |
| `GET /api/v1/research/{job_id}` | 轮询研究任务状态（running + 步骤轨迹 → done / failed） |
| `GET /api/v1/targets/{id}/dataset` | 单目标完整快照（仪表盘动态加载用） |

**多目标**：所有数据端点支持 `?target=<id>` 查询参数（默认 nvidia）；未知目标返回 404 `target_not_found`。

**在线研究（dashboard 搜索框 / POST /api/v1/research）**：**零配置即可用**——默认走 Bing 免费搜索后端（无需密钥）+ 规则核验降级（无 LLM 时）；配置 `SCR_TAVILY_API_KEY`（或 `SCR_BRAVE_API_KEY`）+ `SCR_LLM_BASE_URL` / `SCR_LLM_API_KEY`（任意 OpenAI 兼容网关，见 `.env.example`）可升级为 LLM 核验的高质量模式。在仪表盘右上角搜索框输入任意公司名 → agent 后台执行完整管线（身份解析 → 脚手架 → 分类搜索 → 核验 → 合入 → 引擎重算 → 独立校验）→ 结果缓存到 `data/targets/<id>/` 并自动出现在切换列表。重复研究已存在目标返回 409；在线研究产出的目标建议人工抽检后再对外展示（标 `needs_review`）。命令行等价物：`python scripts/research_agent.py "公司名"`。

**输入校验**：`relationship_type` 限定五类枚举、`status` 限定三态（非法值 422）；`min_confidence > max_confidence` 返回 422 `invalid_range`；`page`/`page_size` 有界；未知资源返回结构化 404（`{"detail": {"error": "...", "message": "..."}}`）。

**分页**：响应含 `page` / `page_size` / `total` / `total_pages` / `has_next` / `has_previous`。

示例：

```bash
curl "http://127.0.0.1:8000/api/v1/targets"                                    # 目标注册表
curl "http://127.0.0.1:8000/api/v1/relationships?relationship_type=supplier&min_confidence=70&page_size=2"
curl "http://127.0.0.1:8000/api/v1/relationships/rel_inv_001"                  # 含 score_breakdown
curl "http://127.0.0.1:8000/api/v1/stats?target=unitree"                       # 切换到宇树科技
curl "http://127.0.0.1:8000/api/v1/graph"
```

每个端点的**实测请求/响应记录**（含错误路径）见 **[docs/api_examples.md](docs/api_examples.md)**；交互式 OpenAPI 文档由 FastAPI 自动提供于 `http://127.0.0.1:8000/docs`。

## 6.5. Docker 一键运行（推荐评审方式）

无需安装 Python 或任何依赖——直接拉取镜像运行：

```bash
# 方式一：直接 docker run
docker run --rm -p 8000:8000 ghcr.io/wangyangke/supply-chain-ai:latest

# 方式二：docker compose（克隆仓库后）
docker compose up
```

启动后打开浏览器：

| 地址 | 说明 |
|---|---|
| `http://localhost:8000/` | **交互式仪表盘**（右上角下拉切换研究目标：NVIDIA / 宇树科技 / Tesla / 比亚迪；搜索框可在线研究新公司，可筛选/搜索/排序/关系图谱） |
| `http://localhost:8000/docs` | OpenAPI / Swagger UI（交互式 API 文档） |
| `http://localhost:8000/api/v1/targets` | 研究目标注册表 JSON |
| `http://localhost:8000/api/v1/stats` | 数据集统计 JSON（默认 nvidia；`?target=unitree` 切换） |
| `http://localhost:8000/api/v1/graph` | 关系图 JSON |

镜像基于 `python:3.12-slim` 多阶段构建，非 root 用户运行，内置 healthcheck，镜像约 120 MB。

> 镜像由 GitHub Actions 自动构建并发布至 GHCR（`ghcr.io/wangyangke/supply-chain-ai`），每次 push 到 `main` 或打 `v*` tag 自动更新 `latest`。

> **在线研究配置**：默认零配置即可用（容器内 Bing 免费搜索 + 规则核验）。如需更高质量结果，可传入密钥升级（`docker run -e SCR_TAVILY_API_KEY=... -e SCR_LLM_BASE_URL=... -e SCR_LLM_API_KEY=...`）；研究结果写入容器内 `data/targets/`，挂卷可持久化（`-v $(pwd)/data:/app/data`）。

## 7. CLI（`scrs`）

与 API 共享同一 store，可脚本化（所有输出支持 `--json`）：

```bash
scrs targets                                  # 列出注册的研究目标
scrs health
scrs --target unitree health                  # 切换到宇树科技
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

安装后可直接使用 `scrs`；未安装时 `python -m src.cli` 等价。未知 id 退出码 1，非法日期报错并退出 1。全局 `--target`（或 `SCR_TARGET` 环境变量）选择研究目标，默认 nvidia。

## 8. 环境变量、启动与复现

**依赖**：Python ≥ 3.11；`pip install -e ".[dev]"`（fastapi / uvicorn / typer / pydantic / pytest）。

**环境变量**（全部可选，无真实凭据，见 [`.env.example`](.env.example)）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SCR_DATA_ROOT` | `./data` | 多目标数据根目录（含 targets.json） |
| `SCR_DATA_DIR` | — | 向后兼容：可直接指向单个数据集目录或数据根 |
| `SCR_TARGET` | `nvidia` | 默认研究目标（CLI 全局 `--target` 等价） |
| `SCR_HOST` / `SCR_PORT` | `127.0.0.1` / `8000` | API 监听地址 |
| `SCR_EDGAR_USER_AGENT` | 模板值 | 仅供采集脚本使用，EDGAR 要求带联系信息的 UA |
| `SCR_SEARCH_BACKEND` | `bing` | 在线研究的搜索后端（bing 免费默认 / tavily / brave；tavily/brave 需对应密钥） |
| `SCR_TAVILY_API_KEY` / `SCR_BRAVE_API_KEY` | 空 | 在线研究搜索密钥（可选；未配置则使用免费的 Bing 后端 + 规则核验降级） |
| `SCR_LLM_BASE_URL` / `SCR_LLM_API_KEY` / `SCR_LLM_MODEL` | 空 | 在线研究的 LLM 网关（OpenAI 兼容，用于身份解析与证据核验） |

**启动 / 测试**：

```bash
pip install -e ".[dev]"
uvicorn src.api:app --port 8000            # HTTP API
scrs stats                                  # CLI
pytest --cov=src                            # 121 个测试，src 覆盖率 ≥90%（下限 90%）
python scripts/validate_data.py --data data/targets/nvidia    # 独立数据校验（schema + 完整性 + 引擎一致性）
```

**数据更新流程**（以 NVIDIA 为例）：

```bash
# 1) 采集（合规：SEC EDGAR 公开 API + 限速 + 质控 UA）
python scripts/fetch_edgar.py --ticker NVDA --out data/targets/nvidia/raw_edgar
# 2) 从 10-K 抽取公司提及，辅助发现关系候选
python scripts/extract_company_mentions.py --data data/targets/nvidia/raw_edgar --out data/targets/nvidia/raw_edgar/company_mentions.json
# 3) 人工核验：补充证据，编写 relationships.json / evidence.json（含方向、时效、来源字段）
# 4) 独立校验 + 引擎重算并写回分数与状态（保证可复现）
python scripts/validate_data.py --data data/targets/nvidia
python scripts/sync_scores.py --data data/targets/nvidia --write
# 5) 更新 dataset.json 的 as_of，运行测试并提交
pytest --cov=src
```

**接入全新研究目标**（任意公司，宇树科技即按此流程接入）：

```bash
# 1) 脚手架：注册目标 + 创建空数据集
python scripts/onboard_target.py --id unitree --name "Unitree Robotics (宇树科技)" \
    --stock-code "688836.SH" --exchange "SSE STAR Market" --country CN \
    --sector "Humanoid & quadruped robots" --description "..."
# 2) agent 搜索并核验证据（docs/research_agent_protocol.md），产出 staging 文件
#    （可用 scripts/research_harvest.py 做机械采集，或 agent 直接按 staging 格式写核验结论）
# 3) 合入（红线：只合入 agent_approved 的候选）
python scripts/merge_staged.py --staging data/targets/unitree/staging/<rel>.json --data data/targets/unitree
# 4) 引擎重算 + 独立校验
python scripts/sync_scores.py --data data/targets/unitree --write
python scripts/validate_data.py --data data/targets/unitree
```

**Agent 研究通道**（10-K 之外的第二条采集线）：来源分级、实体消歧、来源冲突仲裁、时效判定与共现防误判需要搜索 + 判断能力，由 agent 按 **[docs/research_agent_protocol.md](docs/research_agent_protocol.md)** 作业，机械部分（搜索执行 / 去重 / 字段归一化 / review 标记）由 `scripts/research_harvest.py` 自动化：

```bash
# agent 搜索产出原始命中（默认 Bing 免费后端无需密钥；或配 SCR_TAVILY_API_KEY/SCR_BRAVE_API_KEY 使用 tavily/brave 后端）
python scripts/research_harvest.py --backend manual --input hits.jsonl \
    --target nvidia --out data/targets/nvidia/staging/candidates.json
# agent 按 protocol §5-§8 逐条核验（消歧 / 共现防误判 / 原文引用 / 仲裁）后合入
python scripts/research_harvest.py --check data/targets/nvidia/staging/candidates.json
```

staged candidate 未经核验**禁止**直接合入数据集（红线见 protocol §2）。

**已完成的 agent 通道案例**：

- **NVIDIA ↔ Oracle（`rel_par_005`）**：agent 真实搜索 → 4 条候选 → 3 条核验通过（Oracle 官方 PR ×2 + NVIDIA 官方博客）+ 1 条被共现防误判拒绝 → 引擎重算 80（confirmed）。存档：`data/targets/nvidia/staging/oracle_candidates.json`。
- **宇树科技全目标接入（5 条关系）**：onboard_target 脚手架 → agent 搜索核验 9 条证据（NVIDIA 官方新闻稿、中国金融新闻网引招股意向书、中华网/虎嗅竞品对比）→ merge_staged 逐条合入 → 引擎重算 62–69（inferred，无 SEC 级来源所以低于 NVIDIA 数据集，符合预期）。存档：`data/targets/unitree/staging/`。

评分/状态是**派生物**而非人工输入：`sync_scores.py --write` 用引擎 + 证据重算 `confidence_score` 并按带映射推导 `status` 写回，dry-run 会报告任何与引擎不一致的人工状态；`validate_data.py` 独立检查 schema、引用完整性、时间窗合法性与引擎一致性。

**持续集成**：仓库附带 GitHub Actions：`.github/workflows/ci.yml`（Python 3.11/3.12/3.13 上执行 `pytest`、`validate_data.py` 与 `sync_scores.py` 可复现性检查）+ `.github/workflows/docker.yml`（自动构建并推送 Docker 镜像至 GHCR）。

## 9. 测试与限制

**测试**（`tests/`，124 个用例，src 覆盖率 ≥90%）：store 层加载/完整性/过滤/分页、评分引擎各维度与两项细化、API 全部端点与 404/422 错误路径、CLI 命令/退出码/人类可读输出、多目标注册表与 `?target=` 切换、在线研究任务生命周期（202 接受 / 409 重复 / 404 未知 + mock agent 全流程）；一条**全数据集可复现性断言**——每条关系的存储分数与状态必须与引擎重算结果完全一致（`test_scoring.py::TestReproducibility`）；以及 **XSS 安全三层测试**（`tests/test_dashboard_xss.py`：纯 Python 静态审计 + Node 运行 dashboard 内真实 `esc()`/`safeUrl()` 函数中和经典 payload + linkedom 端到端渲染断言）。独立校验脚本 `scripts/validate_data.py` 与一键验收门禁 `scripts/verify_gate.py` 提供同等的评审 / CI 入口（CI 对全部四个目标执行）。

**本地验证入口**：

```bash
# 1) 单元测试 + 覆盖率门禁（≥90%）
pytest --cov=src --cov-report=term-missing
#    DOM 级 XSS 渲染测试需要 linkedom；缺失时自动跳过：
npm install --no-save linkedom

# 2) 一键验收门禁：数据校验 + 评分可复现 + 方法论端点一致性
python scripts/verify_gate.py

# 3) 仅校验某个目标的数据快照
python scripts/validate_data.py --data data/targets/nvidia
```

**Schema 2.0 证据谱系**：每条证据含 `independence_group`（来源谱系组，区分转载 / 聚合避免重复计分）、`support_level`（direct / indirect / contextual，独立于 source_type，专治共现误判与来源冲突）、`access_notes`（可访问性 / 生成注意事项）三字段，由采集 / 合并脚本自动填充，并由 `validate_data.py` 校验。

**评分方法论（防漂移）**：`docs/scoring_methodology.md` 与运行时端点 `GET /api/v1/scoring-methodology`（由引擎函数 `scoring_methodology()` 单源产出）保持一致；dashboard 的 Scoring 标签页在联网时实时渲染该端点，离线时回退到内嵌的规范快照。完整验收证据见 `ACCEPTANCE_REPORT.md`（其中 XSS 一项由 `tests/test_dashboard_xss.py` 的三层验证覆盖）。

**已知限制与盲区**：

- **单时点快照**：数据截止 2026-08-21，之后的公司行为不会反映；需按第 8 节流程刷新；
- **来源权威性决定分数上限**：NVIDIA 数据集以 SEC 10-K（T0）为锚，分数普遍 confirmed；宇树数据集无 SEC 级来源（招股书引用经媒体转述），分数落在 inferred 带——这是评分体系的预期行为而非缺陷；
- **来源以英文为主**：SEC 文件、官方新闻稿为权威来源；中文来源已通过宇树数据集部分覆盖（中国金融新闻网、中华网、虎嗅等），英文/中文之外的语种未覆盖；
- **二手来源**：Wikipedia 作为二手参考仅用于交叉验证，权威性评分低（10 分）；
- **共现误判防护**：同段出现不代表关系，direct-statement 加成仅限官方来源明确点名对手的语境；
- **私有公司不收录**：大量 NVIDIA 生态私有伙伴（如部分云厂商/初创）与宇树的私有股东（红杉中国、顺为等未持上市主体的基金实体）不在端点范围内；
- **`unknown` 不收录**：无法验证的关系不入库，避免噪音，代价是可能漏掉真实但证据不足的关系。

**未来数据质量改进方向**：接入更多交易所文件（HKEX/韩交所/台交所）覆盖亚太供应商；引入官方结构化数据（如 SEC XBRL、客户集中度披露）；对证据做时间线交叉核验；增加对中文信源（公司财报/官方公众号）的采集。

## 10. AI 使用披露（Research & Engineering Judgment Disclosure）

本项目在**代码工程**与**证据研究**两条线上均使用了 AI 编码 Agent 与 AI 检索工具。按挑战要求，说明三要素：用途、人工验证方式、由本人负责的研究与工程判断。

**① AI / 检索工具的用途**（实际发生的全部用途，无遗漏）：

| 用途 | 具体产出 |
|---|---|
| 代码生成 | 采集/解析脚本（`fetch_edgar.py`、`extract_company_mentions.py`）、评分引擎、API/CLI、测试用例、Docker 配置、多目标架构（`TargetRegistry`、`onboard_target.py`、`merge_staged.py`）、在线研究 agent（`research_agent.py` 及 API 任务端点） |
| 文档撰写 | 本 README、`docs/` 方法文档、`data/README.md` |
| 交互式仪表盘 | `dashboard.html`（内嵌数据、多目标切换、在线研究搜索框） |
| **AI 检索与证据研究** | 通过 WebSearch 检索公开来源（SEC/公司新闻稿/财经媒体），按 [docs/research_agent_protocol.md](docs/research_agent_protocol.md) 执行实体消歧、共现防误判、原文引用抽取与来源冲突仲裁，产出带 `agent_review_notes` 的 staging 文件（NVIDIA↔Oracle、宇树科技全目标，均为此路径接入） |
| **在线研究 agent 的设计** | `scripts/research_agent.py` 把上述检索与核验自动化（LLM 经 OpenAI 兼容网关执行身份解析与证据核验，搜索经 Tavily/Brave）；其提示词、输出清洗规则与红线（只合 `agent_approved`）由本人设计 |
| 机械执行 | staging → 合入 → 引擎重算 → 校验的脚本化步骤 |

**② 人工验证方式**（机制保证 + 全程留痕，评审者可独立复核）：

- **核验留痕**：agent 对每条证据的核验结论（含消歧依据、共现判定）写入 staging 文件的 `agent_approved` / `agent_review_notes` 字段；被拒候选（如 Oracle 案例中的股价共现陷阱）同样留档。所有 staging 文件提交于 `data/targets/*/staging/`，评审者可逐条对照原文 URL 复核——**核验过程是公开可审计的，而非一句"已人工核验"**；
- **在线研究的额外约束**：`research_agent.py` 自动产出的 staging 与人工/agent 核验的 staging **走同一条红线**——必须经 `merge_staged.py` 检查（quote / locator / URL 齐全且 `agent_approved`）才能合入，合入后强制引擎重算与独立校验；在线研究的目标建议人工抽检后再对外展示（README 第 8 节已注明）；
- **独立校验**：`scripts/validate_data.py`（schema + 引用完整性 + 引擎一致性）与 `scripts/sync_scores.py`（分数可复现性 dry-run）对两个目标全部通过，且由 CI 在每次 push 时强制执行；
- **本人复核与裁决**：本人（研究者）对 agent 的核验结论行使最终复核与入库裁决权，对入库的每条关系、证据 quote、时效窗口与来源 URL 负责；
- **分数非人工输入**：所有 `confidence_score` 与 `status` 由评分引擎从证据重新计算（`sync_scores.py --write`），人工无法直接改分。

**③ 由本人负责的研究与工程判断**（AI 不拥有这些决策）：

- 评分体系设计：五维权重（authority 25 / evidence_quality 25 / recency 20 / specificity 20 / quantifiability 10）、分数→状态带映射、两项研究判断细化（官方存续关系的时效规则、直接陈述加成）；
- 来源分级（T0 SEC > T2 官方 > T3 媒体 … > T6 社交媒体）与冲突仲裁优先级；
- 覆盖边界：不收录 unknown 带关系、私有实体不作端点、付费墙内容只引公开摘要；
- 关系语义：五类关系的方向约定、三态状态语义、时效窗口定义；
- 合规边界：不绕过任何访问控制、EDGAR 限速与质控 UA、证据留痕字段设计；
- 技术选型：JSON fixture 快照 + 引擎可复现 + 多目标注册表架构。

**④ 数据安全**：未向任何 AI 工具输入密钥、个人数据、客户机密或未授权资料；数据仅来自合法公开来源。

---

**License / 数据说明**：代码遵循项目内声明；证据引用来自各自来源（SEC 文件为公共领域，公司新闻稿为公开内容，Wikipedia 为 CC BY-SA），详见各证据的 `license_note` 字段。
