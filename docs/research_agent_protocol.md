# Agent 研究作业协议（Research Agent Protocol）

> 本文档定义「agent 驱动的证据采集」作业规范。它是 `scripts/fetch_edgar.py`（10-K 抓取）之外的第二条采集通道——面向**目标公司自己不披露**的关系（对方的官宣、媒体交叉验证、历史变迁等）。
>
> 机械部分（搜索执行、去重、字段归一化、schema 校验）由 `scripts/research_harvest.py` 完成；判断部分（实体消歧、来源仲裁、共现防误判、时效判定）由 agent 按本协议执行。

---

## 1. 为什么需要 agent 而不只是 parser

| 任务 | parser（正则/规则） | agent（搜索 + 判断） |
|---|---|---|
| 抓 10-K 原文 | ✅ | ✅ |
| 从 10-K 抽公司名 | ✅ | ✅ |
| 判断"同段共现 ≠ 有关系" | ❌ | ✅ |
| 同名实体消歧（如 "苹果" 公司 vs 水果、同名私企） | ❌ | ✅ |
| 两个来源说法冲突时仲裁 | ❌ | ✅（按来源分级 + 备注分歧） |
| 判断关系是否已终止、设 `valid_until` | ❌ | ✅ |
| 识别 paywall / 登录墙 / 许可限制 | 部分 | ✅ |
| 把非 10-K 来源（官网、新闻稿、媒体）纳入证据链 | ❌ | ✅ |

10-K 是**单视角**的：只覆盖目标公司选择披露的内容。对方官宣、终止公告、媒体调查都需要搜索引擎式的外部采集。

## 2. 分工：脚本做机械，agent 做判断

```
agent（本协议）                scripts/research_harvest.py（机械）
─────────────                ─────────────────────────────────
1. 制定查询词 ──────────────> 2. 执行搜索（manual/tavily/brave 后端）
                              3. 命中去重（按 URL canonical）
                              4. 猜测 source_type / access_restriction
5. 逐条核验 <──────────────── 4b. 输出 staged candidates
   - 实体消歧                  （含 needs_review 标记位）
   - 共现防误判
   - 原文 quote 抽取
   - locator 定位
6. 冲突仲裁 / 时效判定
7. 写入 evidence.json ──────> 8. validate_data.py 校验
                              9. sync_scores.py --write 重算分数
10. pytest + commit
```

**红线：staged candidate 未经 agent 逐条核验，禁止直接合入 `data/evidence.json`。**

## 3. 来源分级（与 SourceType / authority 评分对齐）

| 级别 | source_type | authority | 典型来源 | 使用规则 |
|---|---|---|---|---|
| T0 | `sec_filing` / `exchange_filing` | 25 / 23 | SEC EDGAR、港交所披露易 | 最高优先；quote 必须来自原文 |
| T1 | `government` | 22 | 监管公告、政府采购 | 直接可用 |
| T2 | `company_ir` / `company_press_release` | 20 / 19 | 官网 IR 页、官方新闻稿、官方博客 | 直接可用；注意区分"宣传话术"与"事实陈述" |
| T3 | `business_media` | 16 | Reuters、Bloomberg、FT、CNBC | 需 2 家以上独立媒体交叉，或用于佐证官方来源 |
| T4 | `analyst_research` / `industry_database` | 14 / 12 | Omdia、Gartner（公开摘要） | 只用于估算类结论（如客户排名），summary 注明"估算" |
| T5 | `reference` | 10 | Wikipedia、百科 | 仅交叉验证，不作为唯一证据 |
| T6 | `informal` | 5 | 博客、论坛、社媒 | 原则上不采；除非是一手亲历且可被 T0–T2 佐证 |

**冲突仲裁规则**：不同级别来源冲突时，高级别胜出；同级别冲突时，两条都保留，`summary` 中显式写明分歧（"A 称 X，B 称 Y，以 A 为准因…"），该关系 `confidence_score` 上限压到 69（inferred 带）。

## 4. 查询模式（按关系类型）

对每条候选关系 `{candidate}` × `{target}`，按类型生成查询：

| 类型 | 查询模式（示例） |
|---|---|
| supplier | `"{candidate}" supplier NVIDIA`、`"{candidate}" NVIDIA 10-K supplier`、`site:sec.gov NVIDIA "{candidate}"` |
| customer | `"{candidate}" NVIDIA GPUs customer`、`"{candidate}" deploys NVIDIA`、`"{candidate}" AI infrastructure NVIDIA press release` |
| partner | `"{candidate}" NVIDIA partnership`、`"{candidate}" NVIDIA "press release" collaborate` |
| investor_or_investee | `NVIDIA invested "{candidate}"`、`"{candidate}" funding round NVIDIA`、`site:sec.gov "{candidate}" NVIDIA stake` |
| peer | `NVIDIA 10-K competitors "{candidate}"`、`"{candidate}" vs NVIDIA datacenter GPU` |
| 终止/变更 | `NVIDIA sold stake "{candidate}"`、`"{candidate}" NVIDIA partnership ended` |

**site 限定符优先打 T0–T2**：`site:sec.gov`、`site:<company>.com`、`site:ir.<company>.com`。

## 5. 证据采集契约（每条证据必须满足的字段）

每条 `Evidence` 入库前必须填全：

| 字段 | 要求 |
|---|---|
| `id` | `ev_<type>_<seq>`，全局唯一 |
| `relationship_id` | 指向已存在的 relationship |
| `source_url` | 权威 canonical URL（去掉 tracking 参数；媒体用原始报道而非转载） |
| `publisher` | 发布主体全称（含文档名，如 "U.S. SEC (NVIDIA FY2026 Form 10-K)"） |
| `source_type` | 按第 3 节分级 |
| `published_at` | 来源发布时间；实在查不到则留 `null` 并在 locator 说明 |
| `accessed_at` | 采集当天（ISO 日期），**必填** |
| `evidence_locator` | 精确定位：文档名 + 章节/段落（如 "10-K FY2026, Item 1 — Competition section, competitors list"） |
| `access_restriction` | `public` / `paywall` / `login` / `registration`；paywall 内容只用其公开摘要并标注 |
| `license_note` | 许可说明（SEC=public domain、新闻稿=public content、Wikipedia=CC BY-SA 等） |
| `quote` | **原文直引**，不是转述；英文来源保留英文原文 |

## 6. 实体消歧规则

写库前必须确认证据里的公司就是数据集里的那个实体：

1. **证券标识优先**：证据中出现 ticker/ISIN 时与 `companies.json` 比对，匹配即消歧；
2. **全称匹配**：用法律全称（"Advanced Micro Devices, Inc." 而非 "AMD"）在证据正文中确认；
3. **总部国家/业务一致性**：同名私企/异名实体用 country + sector 排除（如 "SoundHound" 语音 AI ≠ 其他同名实体）；
4. **无法消歧时不入库**：宁可漏收，不污染。

## 7. 共现防误判（co-occurrence guard）★

**同一段落同时提到两家公司 ≠ 存在关系。** quote 必须包含**直接关系陈述**才算数：

| ✅ 有效（直接陈述） | ❌ 无效（仅共现） |
|---|---|
| "We purchase memory from SK Hynix" | "SK Hynix and NVIDIA both saw shares rise"（行情并列） |
| "Oracle deploys NVIDIA GPUs in OCI" | "NVIDIA, Oracle, and Microsoft attended the summit"（名单罗列） |
| "NVIDIA invested $50M in Recursion" | "Analysts compared Recursion with NVIDIA-backed firms"（转述类比） |
| 10-K 点名 "competitors such as AMD" | "AMD and NVIDIA are both semiconductor companies"（行业常识） |

判断口诀：**动词归属**——句子里必须有一个明确的动词把两个实体连成关系（buy from / supply to / invest in / partner with / compete with），且主语宾语是这两个实体本身。

## 8. 时效与过期处理

- 每条关系必须有 `valid_from`（首次可证时间）；存续中关系 `valid_until = null`；
- 发现终止证据（减持公告、合作终止声明）→ 设 `valid_until` 为终止日期，评分引擎自动衰减；
- 最新证据超过 2 年且无存续佐证 → 标 `needs_review: recency`，agent 需补一条近期证据或终止标记；
- 官方确认的存续关系（如 10-K 持续点名）按"持续有效"处理，recency 满分（见 `docs/scoring_methodology.md`）。

## 9. Staging → Review → Merge 流程

```bash
# 1) agent 执行搜索，产出原始命中（JSONL：title/url/snippet/published_at）
#    或直接调用搜索后端（需 API key）
python scripts/research_harvest.py --backend manual --input hits.jsonl \
    --target nvidia --out data/staging/candidates.json

# 2) agent 逐条核验 staged candidates（第 6/7/8 节），合格的补全 quote/locator
#    写入 data/evidence.json + data/relationships.json（新关系需先建 relationship）

# 3) 校验 + 重算 + 测试 + 提交
python scripts/validate_data.py
python scripts/sync_scores.py --data data --write
pytest
git add data/ && git commit -m "data: add <relationship> evidence via agent research"
```

## 10. Definition of Done（每条新证据）

- [ ] `source_url` 可公开访问（或已标注 access_restriction）
- [ ] `quote` 为原文直引且通过第 7 节动词归属检查
- [ ] 实体按第 6 节消歧完成
- [ ] `evidence_locator` 精确到章节/段落
- [ ] 与其他来源无冲突，或冲突已按第 3 节仲裁并写入 summary
- [ ] 时效按第 8 节处理（含终止关系的 valid_until）
- [ ] `validate_data.py` 通过，`sync_scores.py --write` 无 diff
- [ ] `pytest` 全绿
