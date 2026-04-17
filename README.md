# MemOcean MCP

![MemOcean](assets/memocean-banner.jpg)

> A memory system built for CJK developers.
> 為中文開發者而建的 AI 記憶系統。

[Why MemOcean](#why-memocean--為什麼做-memocean) | [What is MemOcean](#what-is-memocean--memocean-是什麼) | [Multi-Agent Design](#multi-agent-design--多-agent-協作設計)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Status: Production](https://img.shields.io/badge/Status-Production-green.svg)]()

---

## Why MemOcean / 為什麼做 MemOcean

We were excited when we first saw [MemPalace](https://github.com/milla-jovovich/mempalace) — finally, someone building long-term memory for LLMs, and it was Milla Jovovich's team no less.

But during testing, we hit two problems that were hard to work around:

**1. Chinese token costs are brutal.**

Mainstream tokenizers are hostile to Chinese — the same semantic content costs 2-3x the tokens in Chinese vs English. MemPalace assumes English whitespace tokenization (`text.split()`, uppercase acronyms as entities, LIKE string matching), none of which work for Chinese. We tried dictionary compression but it was a dead end (BPE-encoded replacement tags ended up longer than the originals). We finally solved it by pivoting to lossy sonar summaries.

Chinese-specific compression solutions do exist in the Qwen/mainland LLM ecosystem — but we needed something that runs natively in the Claude and Codex ecosystem. That combination didn't exist, so we built it.

**2. Multi-agent memory drift.**

We run 10+ agents daily — assistants, builders, reviewers, designers, each with their own role. Since each agent's session memory is isolated, after a few days their "memories" start diverging. One agent remembers last week's decision, another doesn't; one cites outdated info, another has contradictory context.

This isn't a prompting problem — it's an architecture problem: **we needed a single source of truth as a persistent knowledge base, shared across all agents**.

MemOcean is what we built to solve these two things.

<br>

第一次看到 [MemPalace](https://github.com/milla-jovovich/mempalace) 的時候我們非常興奮——終於有人做 LLM 的 long-term memory，而且還是那個蜜拉喬娃。

然而試跑過程中，我們卻發現了兩個不好克服的問題：

**1. 中文 token 消耗真大**

主流 tokenizer 對中文極不友善——同一語意的內容，中文 token 數量是英文的 2-3 倍。MemPalace 原始設計假設英文 whitespace tokenization（`text.split()` 切 token、全大寫縮寫當 entity、LIKE 全字串匹配），這些在中文場景全部失效。我們嘗試過字典壓縮，但走不通（替換後的 tag 在 BPE tokenizer 上比原文還長），最後轉向 sonar lossy summary 才真正解決。

**2. 多 Agent 記憶漂移**

我們日常跑十多隻 Agent——特助、Builder、Reviewer、Designer 各司其職。每隻 Agent 的 session memory 是隔離的，跑幾天之後各自的「記憶」開始分叉。某隻 Agent 記得上週的決策，另一隻不記得；有的引用了過期的資訊，有的拿到矛盾的上下文。

這不是 prompt 寫得不好的問題，是架構問題：**我們需要一個 single source of truth 的持久知識庫，讓所有 Agent 共享同一份事實基礎**。

MemOcean 就是我們為了解決這兩件事而做的。

---

## What is MemOcean / MemOcean 是什麼

MemOcean is a Chinese fork of MemPalace, with three core changes:

1. **Chinese NER pipeline** — jieba POS tagging replaces `text.split()` for entity extraction, handling Traditional Chinese + Simplified Chinese + mixed CJK-English
2. **Multi-agent support** — multiple agents read/write concurrently, append-only writes eliminate conflicts
3. **Ocean metaphor naming** — from palaces to oceans, a naming system that better reflects how knowledge flows

The knowledge base is built on [Obsidian](https://obsidian.md) vaults — everything is Markdown + `[[wikilink]]`, readable and writable by both humans and agents with the same tools, no proprietary database or format required.

**Core capabilities:**
- BM25/INSTR hybrid search — **92.9% Hit@5** on Chinese (no AI components required)
  - CJK queries: pure SQLite INSTR string search on `radar.clsc` column
  - English queries: FTS5 BM25, fallback to INSTR
- CLSC semantic sonar extraction — **92.5% token reduction** (13x compression), semantic links preserved
- Temporal knowledge graph with non-destructive invalidation
- Cross-bot memory sharing via a single shared `memory.db`

<br>

MemOcean 是 MemPalace 的中文 fork，核心改動有三：

1. **中文 NER pipeline**——用 jieba POS tagging 替代 `text.split()` 做 entity 抽取，處理繁體+簡體+英文混語
2. **多 Agent 支援**——支援多隻 Agent 同時讀寫，append-only 寫入規則避免衝突
3. **海洋隱喻命名**——從宮殿到海洋，命名體系更貼合「知識流動」的本質

知識庫基於 [Obsidian](https://obsidian.md) vault——所有內容都是 Markdown + `[[wikilink]]`，人類和 Agent 用同一套工具讀寫，不需要額外的資料庫或專用格式。

命名從宮殿轉到海洋，不只是品牌差異。宮殿是靜態的、封閉的；海洋是流動的、開放的。當多隻 Agent 同時往知識庫寫入，知識的狀態更像洋流而不是房間。

**核心能力：**
- BM25/INSTR 搜尋，中文 **92.9% Hit@5**（無需 AI 組件）
  - CJK 查詢：純 SQLite INSTR 字串搜尋 `radar.clsc` 欄位
  - 英文查詢：FTS5 BM25，miss 時 fallback INSTR
- CLSC 語意 Sonar 萃取，**92.5% token 精簡**（13x 壓縮），保留語意連結
- 時序知識圖譜（KG），支援事實 invalidate 不刪除
- 跨 bot 記憶共享，同一個 memory.db 服務整個 bot 團隊

---

## Multi-Agent Design / 多 Agent 協作設計

This is the most fundamental difference between MemOcean and MemPalace. MemPalace is designed for a single LLM session; MemOcean was built for multi-agent scenarios from day one.

這是 MemOcean 跟 MemPalace 最根本的差異。MemPalace 設計給單一 LLM session 用；MemOcean 從第一天就是為多 Agent 場景設計的。

### Shared knowledge / Agent 團隊共用同一知識體系

Agents are organized by function — Assistant (requirements analysis, task dispatch), Builder (development), Reviewer (code review, QA), Designer (UI/UX). Each role can scale horizontally, communicating via [claude-telegram-bots](https://github.com/ChannelLabAI/claude-telegram-bots).

All agents read and write the same Ocean directory. Knowledge written by any agent is immediately available to all others.

Agent 團隊按職能分工——Assistant（需求分析、任務調度）、Builder（開發實作）、Reviewer（Code review、QA）、Designer（UI/UX 設計）。各角色可橫向擴展，透過 [claude-telegram-bots](https://github.com/ChannelLabAI/claude-telegram-bots) 實現跨 Agent 通訊。

所有 Agent 讀寫同一個 Ocean 目錄。任何一隻 Agent 寫入的知識，其他 Agent 立即可讀。

### Write rules / 寫入規則

The biggest risk with multi-agent writes is conflict. MemOcean solves this with three rules:

1. **Append-only** — only append, never overwrite. Each write adds a timestamp and source tag at the bottom
2. **Source tagging** — `<!-- appended by {agent_id} at {datetime} -->`, full traceability of who wrote what and when
3. **Periodic lint** — automated dedup, formatting cleanup, and cross-reference insertion

No locks, no conflict resolution — append-only eliminates write conflicts at the root.

多 Agent 同時寫入最怕衝突。MemOcean 用三條規則解決：

1. **Append-only**——只追加，不覆蓋。每次寫入在底部加時間戳和來源標記
2. **來源標記**——`<!-- appended by {agent_id} at {datetime} -->`，可追溯誰在什麼時候寫了什麼
3. **定期 lint**——自動合併重複、整理格式、補交叉索引

沒有鎖機制、沒有 conflict resolution——append-only 從根本上消除了寫入衝突。

### Five search paths / 五條搜尋路徑

Different scenarios need different search strategies. MemOcean provides five paths:

| Path | What it searches | Speed | Use case |
|------|-----------------|-------|----------|
| `memocean_radar_search` | Radar (CLSC sonar) | Fast | Quick locate: "Is there anything about X?" |
| `memocean_seabed_get` | Full verbatim content | Medium | Get full text: "Give me the complete doc on X" |
| `fts_search` | Cross-agent messages | <10ms | History search: "Who said X and when?" |
| `kg_query` | Temporal knowledge graph | Medium | Relationship query: "What's the relation between X and Y?" |
| Direct read | Vault files | Fast | When you know the exact path |

An agent's first step on any task is to query Ocean (we call it Step 0) — check for related past decisions or precedent, avoiding redundant work or contradictory decisions.

不同場景需要不同的搜尋策略。MemOcean 提供五條路徑：

| 路徑 | 搜什麼 | 速度 | 場景 |
|------|--------|------|------|
| `memocean_radar_search` | Radar（CLSC sonar）| 快 | 快速定位：「有沒有關於 X 的素材？」 |
| `memocean_seabed_get` | 原文 verbatim | 中 | 拿完整內容：「把那篇 X 的全文給我」 |
| `fts_search` | 跨 Agent 訊息 | <10ms | 歷史搜尋：「誰什麼時候說過 X？」 |
| `kg_query` | 時序知識圖譜 | 中 | 關係查詢：「X 跟 Y 什麼關係？」 |
| 直接讀取 | vault 檔案 | 快 | 知道確切路徑時直接讀 |

Agent 接到任務的第一步是查 Ocean（我們叫 Step 0），看有沒有相關的歷史決策或前例，避免重複勞動或做出矛盾的決策。

### Session memory vs persistent knowledge / Session 記憶 vs 持久知識

MemOcean strictly separates two kinds of memory:

- **Session memory** — each agent's own `session.json`, storing current work state and in-flight tasks. Isolated, clearable on restart
- **Persistent knowledge** — the Ocean directory, shared by all agents. Once written, knowledge persists

This separation is key to solving memory drift. Agents' session memories can differ, but the underlying factual foundation (Ocean) is unified.

MemOcean 嚴格區分兩種記憶：

- **Session 記憶**——每隻 Agent 自己的 `session.json`，存當前工作狀態、in-flight 任務。隔離的，重啟可清
- **持久知識**——Ocean 目錄，所有 Agent 共享。知識一旦寫入就持久存在

這個分離是解決記憶漂移的關鍵。Agent 的 session 記憶可以各自不同，但底層的事實基礎（Ocean）是統一的。

---

## Comparison / 比較表

| Dimension | [MemPalace](https://github.com/milla-jovovich/mempalace) | [GBrain](https://github.com/garrytan/gbrain) | MemOcean |
|-----------|---------|---------|---------|
| **Language assumption** / 設計語言假設 | English (whitespace tokenization) | English | **CJK-first** (HanNER + jieba) |
| **Search architecture** / 搜尋架構 | BM25 + LIKE | Vector search | **CJK: pure INSTR / EN: FTS5 BM25** |
| **Chinese Hit@5** / 中文搜尋命中率 | ~60% (est.) | ~75% (est.) | **92.9%** (measured, no AI required) |
| **External benchmark** / 外部 benchmark | — | — | DRCD(繁中) **91.9%** / CMRC(簡中) **93.3%** |
| **Memory format** / 記憶格式 | AAAK skeleton (Closet) | Compiled Truth + Timeline | CLSC Radar (.clsc.md) |
| **Knowledge graph** / 知識圖譜 | -- | Entity-relation graph | **Temporal KG** (with invalidation) |
| **Nightly consolidation** / 夜間整合 | -- | Dream Cycle | Dream Cycle (Phase 1 live) |
| **Multi-bot sharing** / 多 bot 共享 | -- | -- | **Shared memory.db** |
| **Deployment** / 部署方式 | Local Python | Local Python | **MCP server** (Claude Code native) |
| **Token reduction** / Token 精簡率 | ~91% (AAAK) | N/A | **92.5% (CLSC Sonar, 13x compression)** |
| **AI dependency** / AI 依賴 | Varies | Varies | **Zero** (all AI components disabled by default) |
| **License** / 授權 | MIT | MIT | MIT |

---

## Architecture / 架構

### Ocean naming system / 海洋命名體系

MemPalace uses a palace metaphor (Palace > Wing > Room > Skeleton > Drawer). MemOcean uses an ocean metaphor:

MemPalace 用宮殿隱喻，MemOcean 用海洋隱喻，對應關係如下：

| Function / 功能 | Ocean name / 海洋名 | Path / 路徑 | MemPalace equivalent |
|----------------|---------------------|-------------|---------------------|
| Knowledge base / 知識總庫 | Ocean | `Ocean/` | Palace |
| Project category / 專案分類 | Current (洋流) | `Currents/` | Wing |
| Subcategory / 子分類 | Reef (珊瑚礁) | Current subdirectory | Room |
| Semantic index / 語意骨架 | Radar (聲納) | `*.clsc` | Skeleton (Closet) |
| Raw material / 原始素材 | Seabed (海床) | `Seabed/` | Drawer |
| Insight cards / 洞見卡片 | Pearl (珍珠) | `Pearl/` | Cards |
| Technical docs / 技術文檔 | Chart (海圖) | `Chart/` | Concepts |
| Research reports / 研究報告 | Research | `Research/` | Research |
| Archive / 封存 | Depth (深處) | `Depth/` | Archive |

### Directory structure / 目錄結構

```
Ocean/
├── Currents/
│   ├── ProjectAlpha/
│   │   ├── Sales/             # Reef: sales / 業務
│   │   ├── Product/           # Reef: product / 產品線
│   │   └── Org/               # Reef: internal / 組織內部
│   ├── ProjectBeta/
│   └── ProjectGamma/
├── Pearl/                      # Insight cards (cross-project) / 洞見卡片（跨專案）
├── Chart/                      # Technical docs (cross-project) / 技術文檔（跨專案）
├── Research/                   # Research reports (cross-project) / 研究報告（跨專案）
├── Seabed/                     # Raw material / 原始素材
├── Depth/                      # Archive / 封存
├── _schema.md                  # Write schema / 寫入規範
└── _index.md                   # Auto-generated index / 自動生成索引
```

**Boundary principle / 分界原則：** Project-bound content goes inside Currents (People, Companies, Deals, raw material). Cross-project content goes at the top level (Pearl, Chart, Research). Currents link to each other via `[[wikilink]]` — no file moves.

專案綁定的內容放 Current 內，跨專案通用的放頂層。Current 之間用 `[[wikilink]]` 連結，不搬檔。

### Dual-engine retrieval / 雙引擎檢索架構

MemOcean has two independent retrieval engines, each serving a different purpose:

```
Seabed (raw material) ──→ Radar (machine index)
                         Teaches agents "what exists": fact location, fast retrieval

Various sources ──→ Pearl (distilled insights)
  ├── Conversations     Teaches agents "how to think": judgment frameworks, decision logic
  ├── Research
  ├── Meetings
  └── Insights from reading
```

- **Radar** is semantic sonar extraction (MemPalace calls it Closet) — auto-generates sonar indexes from Seabed originals (~9% tokens), helping agents find things fast
- **Pearl** is human distillation — atomic insights (100-300 words) refined from conversations, research, meetings, and work discussions, teaching agents to think like the boss

Both exist independently, `[[linked]]` to each other — they are not an upstream/downstream compression pipeline.

MemOcean 有兩條獨立的檢索引擎，各自服務不同目的：

- **Radar** 是語意 Sonar 萃取（MemPalace 稱為 Closet）——從 Seabed 原文自動生成 Sonar 索引（約 9% token），讓 Agent 快速定位事實
- **Pearl** 是人工蒸餾——從對話、研究、會議、工作討論中提煉出的原子洞見（100-300 字），教 Agent 學習老闆的判斷框架

兩層獨立存在、`[[wikilink]]` 互相連結——不是上下游的壓縮管線。

### Search pipeline / 搜尋管線

Two-path search, zero AI dependency by default:

```
CJK query  ──→  _search_instr_fallback()  ──→  SQLite INSTR on radar.clsc, sorted by match_count
EN query   ──→  _search_fts5() (FTS5 BM25)
                    └── on miss ──→  _search_instr_fallback()
```

- **CJK path**: Pure SQLite `INSTR()` string search on the `radar.clsc` column, ranked by match count. FTS5 trigram performs poorly on Chinese — INSTR is more accurate.
- **English path**: FTS5 BM25 first (better ranking for English), fallback to INSTR on miss.
- **AI components** (all disabled by default): Query Expansion (`ENABLE_QUERY_EXPANSION=1`), KNN vector search (`KNN_ENABLED=true`), Haiku reranker (`ENABLE_HAIKU_RERANKER=1`), MiniLM reranker (`ENABLE_MINIML_RERANKER=1`). Benchmarks confirm all AI components hurt performance — enable only if you have a specific use case.

雙路徑搜尋，預設零 AI 依賴：

- **中文路徑**：純 SQLite `INSTR()` 字串搜尋 `radar.clsc` 欄位，依命中次數排序。FTS5 trigram 在中文表現差——INSTR 更準確。
- **英文路徑**：FTS5 BM25 優先（英文排序品質更好），miss 時 fallback INSTR。
- **AI 組件**（預設全部關閉）：Query Expansion（`ENABLE_QUERY_EXPANSION=1`）、KNN 向量搜尋（`KNN_ENABLED=true`）、Haiku reranker（`ENABLE_HAIKU_RERANKER=1`）、MiniLM reranker（`ENABLE_MINIML_RERANKER=1`）。Benchmark 確認所有 AI 組件均負優化——除非有特定需求否則不啟用。

---

## CLSC Engine / CLSC 中文 Sonar 萃取引擎

**CLSC** (ChannelLab Lossy Summary for Chinese) is MemOcean's core engine, forked from MemPalace's AAAK skeleton format (MemPalace calls it Closet), with the entire NER + search pipeline rewritten for Chinese.

CLSC 是 MemOcean 的核心引擎，fork 自 MemPalace 的 AAAK skeleton 格式，針對中文場景重寫了整條 NER + 搜尋 pipeline。

### Differences from upstream AAAK / 跟 upstream AAAK 的差異

| Upstream AAAK assumption | CLSC Chinese implementation |
|--------------------------|----------------------------|
| `text.split()` tokenization | jieba POS tagging + auto NER |
| Uppercase acronyms as entities | Chinese entities via pinyin initials + token-aware gate |
| LIKE full-string matching | FTS5 trigram + BM25 ranking, fallback OR-match |
| Fixed budget truncation | Content-proportional scaling (dynamic adjustment by source length) |

### Sonar format / Sonar 格式

Each piece of material is extracted into a single-line sonar entry, stored as `.clsc`:

每筆素材萃取成一行 Sonar，存為 `.clsc`：

```
[SLUG|ENTITIES|topics|"key_quote"|WEIGHT|EMOTIONS|FLAGS]
```

### Performance data / 效果數據

Measured on real Chinese corpora (148 Obsidian vault documents):

在真實中文語料上的實測表現（148 篇 Obsidian vault 文件）：

| Metric / 指標 | Value / 數值 |
|--------------|-------------|
| Test scale / 測試規模 | 148 documents |
| Original tokens / 原始 token 總量 | 459,490 |
| Sonar tokens / Sonar token 總量 | 43,392 |
| Sonar reduction / Sonar 精簡率 | **9.4%** |
| Token savings / Token 節省 | **90.6%** |
| Average search savings / 搜尋場景平均節省 | **78.2%** |

Sonar-first search (read sonar first, fetch originals on demand) saves ~78% token consumption vs reading originals directly.

### Search iterations / 搜尋迭代

| Query type / 查詢類型 | v1 ALL-match | v2 OR-match | v3 FTS5+BM25 |
|----------------------|-------------|-------------|--------------|
| Structured queries / 結構化查詢 | 50% | 89% | 89% |
| Natural language / 自然語言查詢 | 0% | 55% | 55% |
| Known document / 已知文件查詢 | 85% | 95% | 95% |

v3 hit rates match v2 (FTS5 auto-fallbacks to OR-match on miss), but ranking quality improves dramatically — BM25 pushes the most relevant document to top-1 instead of high-frequency but imprecise results.

v3 命中率與 v2 相同（FTS5 miss 時自動 fallback OR-match），但排序品質大幅提升——BM25 讓最相關的文件排到 top-1，而非只是出現頻率高的結果。

### Benchmark

MemPalace is designed for English; MemOcean is designed for Chinese work scenarios. For context: MemPalace achieves 96.6% on LongMemEval in **raw verbatim mode** (no compression), but drops to **84.2% with AAAK compression enabled** (−12.4pp). MemOcean CLSC Sonar achieves 92.9% **with compression active** — 8.7pp above MemPalace's compressed mode.

Benchmarks run 2026-04-16 with pure BM25/INSTR (zero AI components):

| Benchmark | Language | Hit@5 | Notes |
|---|---|---|---|
| Internal | Chinese (mixed) | **92.9%** | Primary working corpus |
| DRCD | Traditional Chinese | **91.9%** | External dataset, gap −1.0% confirms no self-referential bias |
| CMRC | Simplified Chinese | **93.3%** | External dataset, gap +0.4% |
| BEIR SciFact | English | **70.7%** | gap −22.2%, language limitation — MemOcean is not optimized for English |
| CLSC A/B | — | tag vs no-tag **0pp** gap | Confound resolved: +1.9pp was FTS5 vs INSTR diff; tag format has no independent recall effect |

CLSC token compression: 1,716,211 raw tokens → 129,529 sonar tokens = **13x compression (92.5% reduction)**. Median per-entry ratio: 18.9%.

### Known limitations / 已知限制

Two known limitations, stated honestly:

1. **jieba Traditional Chinese accuracy** — jieba's dictionary is Simplified Chinese-primary; Traditional Chinese relies on statistical fallback. NER recall has no quantified baseline yet.
2. **Cold word coverage** — the hybrid recall embedding path handles common inference-word misses, but extremely rare terminology or heavy abbreviations (unseen by both paths) can still slip through. Query expansion is needed as a supplement.

誠實說明兩個已知限制：

1. **jieba 繁體中文準確率**——jieba 字典以簡體為主，繁體依賴統計 fallback。NER recall 目前沒有量化基準。
2. **冷門詞覆蓋**——混合召回路徑能處理常見推斷詞 miss，但極罕見術語或大量縮寫（兩條路徑都未見過的詞）仍可能漏掉，需要 Query Expansion 補充。

---

## Install

```bash
pip install -e ~/.claude-bots/shared/memocean-mcp
claude mcp add memocean python -m memocean_mcp
```

> **安裝說明：** 安裝至本地環境後，用 `claude mcp add` 註冊為 MCP server 即可使用。

---

## Tools / 工具

### `memocean_fts_search`

Full-text search over cross-bot Telegram message history.

跨 bot Telegram 訊息全文搜尋。

```json
{
  "query": "NOX OR Bonk",
  "limit": 5
}
```
Returns: `{ "query": "...", "count": 3, "results": [{ "bot_name": "anna", "ts": "...", "snippet": "...", "rank": -0.4 }, ...] }`

### `memocean_seabed_get`

Retrieve knowledge content (Radar/Seabed) by slug.

以 slug 取得知識內容（Radar/Seabed）。

```json
{
  "slug": "channellab-pricing",
  "mode": "verbatim"
}
```
Returns: `{ "slug": "channellab-pricing", "mode": "verbatim", "content": "# ChannelLab GEO pricing..." }`

### `memocean_radar_search`

Search Radar (CLSC sonar index) using multi-term AND matching.

搜尋 Radar（CLSC sonar 索引），多詞 AND 匹配。

```json
{
  "query": "Knowledge Infra",
  "limit": 5
}
```
Returns: `{ "query": "...", "count": 2, "results": [{ "slug": "...", "clsc": "...", "tokens": 42 }, ...] }`

### `memocean_kg_query`

Query the temporal knowledge graph.

查詢時序知識圖譜。

```json
{
  "entity": "老兔",
  "direction": "both"
}
```
Returns: `{ "entity": "老兔", "count": 4, "facts": [{ "subject": "老兔", "predicate": "role", "object": "CEO", ... }] }`

### `memocean_skill_list`

List approved skills, or get a specific skill's content.

列出已核准的技能，或取得特定技能的內容。

```json
{}
```
Returns: `{ "count": 2, "skills": ["parallel-builder-reviewer-pools", "tg-supergroup-id-migration"] }`

```json
{ "name": "parallel-builder-reviewer-pools" }
```
Returns: `{ "name": "parallel-builder-reviewer-pools", "content": "# Parallel Builder..." }`

### `memocean_task_create`

Create a new task in the pending queue.

在待辦佇列建立新任務。

```json
{
  "title": "Build NOX staking dashboard",
  "description": "Create a dashboard showing real-time NOX staking stats.",
  "assigned_to": "anna",
  "priority": "high",
  "acceptance_criteria": ["Shows APY", "Mobile responsive"]
}
```
Returns: `{ "task_id": "20260408-120000-a1b2", "filename": "...", "file_path": "...", "status": "pending" }`

### `memocean_ingest_file`

Ingest a local file (PDF, PPT, Word, Excel, HTML, CSV, JSON) into MemOcean's Radar seabed. Converts to markdown via [MarkItDown](https://github.com/microsoft/markitdown), stores in `group='files'`. Deduplicates by file path — re-ingesting the same path updates both the DB row and the `.clsc.md` sonar file.

將本地檔案（PDF、PPT、Word、Excel、HTML、CSV、JSON）透過 MarkItDown 轉成 Markdown，存入 MemOcean Radar seabed。同路徑重複 ingest 自動更新。

```json
{
  "file_path": "/home/user/Documents/report.pdf"
}
```
Returns (success): `{ "slug": "file:report-a3f9c2", "group": "files", "chars": 12500, "radar_id": 341, "format": "pdf", "truncated": false }`

Returns (error): `{ "error": "File not found: /path/to/file", "code": "FILE_NOT_FOUND" }`

**Error codes:**
| Code | Meaning |
|---|---|
| `FILE_NOT_FOUND` | Path does not exist / 路徑不存在 |
| `FILE_TOO_LARGE` | File exceeds 50 MB / 檔案超過 50 MB |
| `MARKITDOWN_FAIL` | MarkItDown conversion raised an exception / MarkItDown 轉換失敗 |
| `EMPTY_CONTENT` | Converted content is under 100 characters / 轉換後內容不足 100 字 |

**Supported formats / 支援格式:** PDF, PPTX, DOCX, XLSX, HTML, CSV, JSON (and any format MarkItDown supports).

**Requires:** `markitdown[all]` installed in the active Python environment (`pip install "markitdown[all]"`).

---

## Security model / 安全模型

**Data locality / 資料本地性。** Your data never leaves your machine. The MCP server runs as a local subprocess over stdio — no outbound network connections, no telemetry, no cloud sync. The code is open source; your data is not.

你的資料不會離開你的機器。MCP server 以本地 subprocess 透過 stdio 執行——無對外網路連線、無遙測、無雲端同步。程式碼開源，你的資料不開源。

**Trust boundary / 信任邊界。** This library assumes the caller (Claude Code / your local Claude session) is trusted. Tool inputs such as `slug` and `skill name` may originate from LLM-generated content or indexed external data. To prevent prompt-injection-driven path traversal, all slug/name parameters are validated against `[A-Za-z0-9_-]{1,100}` before any filesystem access. `task_create` validates `assigned_to` and `priority` at runtime against allowlists.

**Install isolation / 安裝隔離。** We recommend installing inside a dedicated virtual environment to avoid PEP 668 conflicts with system Python packages:

```bash
python3 -m venv ~/.venvs/memocean-mcp
source ~/.venvs/memocean-mcp/bin/activate
pip install -e /path/to/memocean-mcp
```

Then register with the venv Python:

```bash
claude mcp add memocean ~/.venvs/memocean-mcp/bin/python -m memocean_mcp
```

**What this library does NOT do:** multi-tenant isolation, authentication, rate limiting, or network access. It is designed for single-user local use only.

本工具不做：多租戶隔離、身分驗證、速率限制、網路存取。僅供單一使用者本地使用。

---

## Configuration / 設定

Environment variable overrides (all optional):

| Variable | Default | Description |
|---|---|---|
| `CHANNELLAB_BOTS_ROOT` | `~/.claude-bots` | Root of the bots directory / bots 根目錄 |

Derived paths (all under `BOTS_ROOT`):

| Path | Description |
|---|---|
| `memory.db` | FTS5 SQLite database |
| `kg.db` | Temporal knowledge graph SQLite |
| `tasks/` | Task queue directories / 任務佇列目錄 |
| `seabed/` | Radar bundle storage (sonar index) |
| `shared/learned-skills/approved/` | Approved skill markdown files / 已核准技能檔 |
| `shared/fts5/` | FTS5 search module |
| `shared/clsc/v0.7/` | Radar decoder module (CLSC sonar) |
| `shared/kg/` | KG helper module |

---

## Tide (潮汐文件)

Tide is MemOcean's third-layer output format. Data flows:
- Seabed stores raw material (messages, conversations)
- Radar creates compressed indexes (CLSC sonar)
- **Tide** is a time-dimensional integrated document. Each TideDoc contains:
  - Upper layer — Compiled Truth: current best understanding, overwritable, date-stamped
  - Lower layer — Timeline: append-only event log, never modified

Tide 是 MemOcean 的第三層輸出格式。資料流向：Seabed 存原始素材 → Radar 做壓縮索引 → Tide 是有時間維度的整合文件。每份 TideDoc 包含上層 Compiled Truth（當前最佳理解，可覆寫）和下層 Timeline（append-only 事件紀錄，只增不改）。

---

## Recent updates / 最近更新

### 2026-04-17
- **MEMO-011: Radar Summary Layer** — `radar.summary` TEXT column added. On insert, best-effort Haiku auto-summary triggered for SOP/spec/guide content. Falls back to NULL on failure; write path never blocked. Sole write path: `insert_row.py`.
- **CLSC full name finalized**: CLSC = **ChannelLab Lossy Summary for Chinese** (was "Chinese Lossy Skeleton Codec"). Terminology updated across codebase.
- **Skeleton → Sonar rename**: All CLSC-context "skeleton" references in active code updated to "sonar".

### 2026-04-16
- **Search pipeline finalized**: CJK queries use pure SQLite `INSTR()` on `radar.clsc` (not FTS5 — FTS5 trigram performs poorly on Chinese). English queries use FTS5 BM25, fallback to INSTR.
- **All AI components disabled by default**: Query Expansion (`ENABLE_QUERY_EXPANSION=1`), KNN vector search (`KNN_ENABLED=true`), Haiku reranker (`ENABLE_HAIKU_RERANKER=1`), MiniLM reranker (`ENABLE_MINIML_RERANKER=1`) all require explicit env var. Benchmarks confirm all hurt performance.
- **Benchmark update**: internal Hit@5=92.9%; DRCD Traditional Chinese=91.9%; CMRC Simplified Chinese=93.3%; BEIR SciFact English=70.7%; CLSC A/B gap = **0pp** (confound resolved — prior +1.9pp was FTS5 vs INSTR diff, not tag format effect).
- **CLSC compression confirmed**: 1,716,211 raw tokens → 129,529 sonar tokens = 13x (92.5% reduction), median per-entry 18.9%.

### 2026-04-14
- **MEMO-003: messages_vec Phase 2** — Hybrid search over TG messages. `messages_vec` virtual table (vec0, 7,234 rows). Note: KNN components are now disabled by default.
- **MEMO-002: BGE-m3 ONNX acceleration** — ONNX Runtime INT8 path (93–104ms warm). Note: BGE-m3 KNN is disabled by default.
- **MEMO-001: radar_vec backfill** — 337/337 radar entries embedded. Note: KNN path disabled by default.

### 2026-04-12
- **`memocean_ingest_file` (Phase 1)**: New MCP tool to ingest local files into MemOcean Radar. Converts PDF/PPT/Word/Excel/HTML/CSV/JSON to markdown via MarkItDown, stores in `group='files'`. Deduplicates by file path. Slug format: `file:{stem}-{hash6}`. Truncates at 50k chars. Requires `markitdown[all]`.
- **Closet → Radar rename sweep**: All internal references updated across `shared/clsc/`, `shared/fts5/`, `shared/scripts/`, and `memocean_mcp/`.
- **Dream Cycle FTS gap monitoring**: `_check_fts_gap()` runs at end of each Dream Cycle to detect radar→FTS sync gaps. Additional daily cron at 18:00.
- **Dream Cycle Phase 2 — stale knowledge detection**: Compares contradictory triples in the KG, marks `valid_to` on superseded facts. Non-destructive invalidation.

### 2026-04-11
- **Removed `memocean_ask_opus`**: Replaced by native `Agent` tool with `model: "opus"` in Claude Code — more direct, fewer tokens.
- **Terminology fix**: CLSC is "sonar extraction" not "compression" — lossy and irreversible by design. Closet → Radar rename (MemPalace original term preserved as Closet).
- **Dream Cycle (Phase 1 shipped)**: Nightly knowledge consolidation pipeline (`shared/scripts/dream_cycle.py`). 6-step pipeline: Collect → Extract → Normalize → Diff → Write → Report. Lock file, 30-min timeout, crash-recovery checkpoint, content-hash idempotency, dry-run/live modes, TG notification, graceful LLM degradation. Runs daily at 19:00 UTC. 39 tests passing.
- **Radar FTS sync fix**: `store_sonar()` now syncs to `memory.db` radar table + `radar_fts` (with `source_hash`, DELETE-before-INSERT on FTS).
- **Alias table**: `shared/config/alias_table.yaml` — 19-entity alias table for entity normalization in Dream Cycle.

---

## Acknowledgements / 致謝

MemOcean stands on the shoulders of two excellent open-source projects:

MemOcean 站在兩個優秀開源專案的肩膀上：

**[MemPalace](https://github.com/milla-jovovich/mempalace)** ([@milla-jovovich](https://github.com/milla-jovovich))

The dual-layer architecture (Seabed + Closet), AAAK skeleton format, and the core idea of lossy summary — these are all original designs from the MemPalace team. Without MemPalace paving the way, MemOcean wouldn't exist. Thank you.

記憶宮殿的雙層架構（Seabed + Closet）、AAAK skeleton 格式、lossy summary 的核心理念——這些都是 MemPalace 團隊的原創設計。沒有 MemPalace 鋪路，MemOcean 不會有今天的樣子。謝謝你們。

**[GBrain](https://github.com/garrytan/gbrain)** ([@garrytan](https://github.com/garrytan))

The Compiled Truth + Timeline dual-layer design and the Dream Cycle nightly consolidation concept — this is what we most wanted to learn from after reading GBrain. You solved the fundamental tension between "knowledge should be updatable" and "knowledge should be traceable" in the most elegant way. Thank you.

Compiled Truth + Timeline 雙層設計、Dream Cycle 夜間知識整合概念——這是我們讀 GBrain 之後最想借鑑的東西。你用最簡潔的方式解決了「知識可更新 vs 可溯源」這個根本矛盾。謝謝。

We are users of both tools, and that's how we got the chance to stand here and keep pushing forward. We hope MemOcean can make its own small contribution to the Chinese developer community.

我們是兩個工具的使用者，才有機會站在這裡繼續往前走。希望 MemOcean 能為中文開發者社群做出一點屬於自己的貢獻。

---

## License

MIT
