# MemOcean

> 為中文多 Agent 場景打造的共享知識庫系統。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Status: Production](https://img.shields.io/badge/Status-Production-green.svg)]()

---

## Table of Contents

- [為什麼做 MemOcean](#為什麼做-memocean)
- [MemOcean 是什麼](#memocean-是什麼)
- [多 Agent 協作設計](#多-agent-協作設計)
- [架構：海洋隱喻 + 雙引擎檢索](#架構海洋隱喻--雙引擎檢索)
- [CLSC 中文壓縮引擎](#clsc-中文壓縮引擎)
- [致謝](#致謝)

---

## 為什麼做 MemOcean

第一次看到 [MemPalace](https://github.com/milla-jovovich/mempalace) 的時候我們非常興奮——終於有人做 LLM 的 long-term memory，而且還是那個蜜拉喬娃。

試跑過程中，我們發現了兩個不好克服的現實問題：

**1. 中文 token 稅**

主流 tokenizer 對中文極不友善——同一語意的內容，中文 token 數量是英文的 2-3 倍。MemPalace 原始設計假設英文 whitespace tokenization（`text.split()` 切 token、全大寫縮寫當 entity、LIKE 全字串匹配），這些在中文場景全部失效。我們嘗試過字典壓縮，但走不通（替換後的 tag 在 BPE tokenizer 上比原文還長），最後轉向 skeleton lossy summary 才真正解決。

**2. 多 Agent 記憶漂移**

我們日常跑十多隻 Agent——特助、Builder、Reviewer、Designer 各司其職。每隻 Agent 的 session memory 是隔離的，跑幾天之後各自的「記憶」開始分叉。某隻 Agent 記得上週的決策，另一隻不記得；有的引用了過期的資訊，有的拿到矛盾的上下文。

這不是 prompt 寫得不好的問題，是架構問題：**我們需要一個 single source of truth 的持久知識庫，讓所有 Agent 共享同一份事實基礎**。

MemOcean 就是我們為了解決這兩件事而做的。

---

## MemOcean 是什麼

MemOcean 是 MemPalace 的中文 fork，核心改動有三：

1. **中文 NER pipeline**——用 jieba POS tagging 替代 `text.split()` 做 entity 抽取，處理繁體+簡體+英文混語
2. **多 Agent 支援**——支援多隻 Agent 同時讀寫，append-only 寫入規則避免衝突
3. **海洋隱喻命名**——從宮殿到海洋，命名體系更貼合「知識流動」的本質

知識庫基於 [Obsidian](https://obsidian.md) vault——所有內容都是 Markdown + `[[wikilink]]`，人類和 Agent 用同一套工具讀寫，不需要額外的資料庫或專用格式。

命名從宮殿轉到海洋，不只是品牌差異。宮殿是靜態的、封閉的；海洋是流動的、開放的。當多隻 Agent 同時往知識庫寫入，知識的狀態更像洋流而不是房間。

---

## 多 Agent 協作設計

這是 MemOcean 跟 MemPalace 最根本的差異。MemPalace 設計給單一 LLM session 用；MemOcean 從第一天就是為多 Agent 場景設計的。

### Agent 團隊共用同一知識體系

我們的 Agent 團隊分四個角色：

Agent 團隊按職能分工——Assistant（需求分析、任務調度）、Builder（開發實作）、Reviewer（Code review、QA）、Designer（UI/UX 設計）。各角色可橫向擴展，透過 [claude-telegram-bots](https://github.com/ChannelLabAI/claude-telegram-bots) 實現跨 Agent 通訊。

所有 Agent 讀寫同一個 Ocean 目錄。任何一隻 Agent 寫入的知識，其他 Agent 立即可讀。

### 寫入規則

多 Agent 同時寫入最怕衝突。MemOcean 用三條規則解決：

1. **Append-only**——只追加，不覆蓋。每次寫入在底部加時間戳和來源標記
2. **來源標記**——`<!-- appended by {agent_id} at {datetime} -->`，可追溯誰在什麼時候寫了什麼
3. **定期 lint**——自動合併重複、整理格式、補交叉索引

沒有鎖機制、沒有 conflict resolution——append-only 從根本上消除了寫入衝突。

### 五條搜尋路徑

不同場景需要不同的搜尋策略。MemOcean 提供五條路徑：

| 路徑 | 搜什麼 | 速度 | 場景 |
|------|--------|------|------|
| `closet_search` | CLSC skeleton | 快 | 快速定位：「有沒有關於 X 的素材？」 |
| `closet_get` | 原文 verbatim | 中 | 拿完整內容：「把那篇 X 的全文給我」 |
| `fts_search` | 跨 Agent 訊息 | <10ms | 歷史搜尋：「誰什麼時候說過 X？」 |
| `kg_query` | 時序知識圖譜 | 中 | 關係查詢：「X 跟 Y 什麼關係？」 |
| 直接讀取 | vault 檔案 | 快 | 知道確切路徑時直接讀 |

Agent 接到任務的第一步是查 Ocean（我們叫 Step 0），看有沒有相關的歷史決策或前例，避免重複勞動或做出矛盾的決策。

### Session 記憶 vs 持久知識

MemOcean 嚴格區分兩種記憶：

- **Session 記憶**——每隻 Agent 自己的 `session.json`，存當前工作狀態、in-flight 任務。隔離的，重啟可清
- **持久知識**——Ocean 目錄，所有 Agent 共享。知識一旦寫入就持久存在

這個分離是解決記憶漂移的關鍵。Agent 的 session 記憶可以各自不同，但底層的事實基礎（Ocean）是統一的。

---

## 架構：海洋隱喻 + 雙引擎檢索

### 海洋命名體系

MemPalace 用宮殿隱喻（Palace > Wing > Room > Skeleton > Drawer）。MemOcean 用海洋隱喻，對應關係如下：

| 功能 | 海洋名 | 路徑 | MemPalace 對應 |
|------|--------|------|----------------|
| 知識總庫 | Ocean | `Ocean/` | Palace |
| 專案分類 | Current（洋流） | `Currents/` | Wing |
| 子分類 | Reef（珊瑚礁） | Current 下子目錄 | Room |
| 壓縮骨架 | Sonar（聲納） | `*.clsc` | Skeleton |
| 原始素材 | Seabed（海床） | `Seabed/` | Drawer |
| 洞見卡片 | Pearl（珍珠） | `Pearl/` | Cards |
| 技術文檔 | Chart（海圖） | `Chart/` | Concepts |
| 研究報告 | Research | `Research/` | Research |
| 封存 | Depth（深處） | `Depth/` | Archive |

### 目錄結構

```
Ocean/
├── Currents/
│   ├── ProjectAlpha/
│   │   ├── Sales/             # Reef: 業務
│   │   ├── Product/           # Reef: 產品線
│   │   └── Org/               # Reef: 組織內部
│   ├── ProjectBeta/
│   └── ProjectGamma/
├── Pearl/                      # 洞見卡片（跨專案）
├── Chart/                      # 技術文檔（跨專案）
├── Research/                   # 研究報告（跨專案）
├── Seabed/                     # 原始素材
├── Depth/                      # 封存
├── _schema.md                  # 寫入規範
└── _index.md                   # 自動生成索引
```

**分界原則**：專案綁定的內容放 Current 內（People、Companies、Deals、raw 素材），跨專案通用的放頂層（Pearl、Chart、Research）。Current 之間用 `[[wikilink]]` 連結，不搬檔。

### 雙引擎檢索架構

MemOcean 有兩種獨立的檢索引擎，各自服務不同目的：

```
Seabed（原始素材）──→ Sonar（機器索引）
                     教 Agent「有什麼」：事實定位、快速檢索

各種來源 ──→ Pearl（蒸餾洞見）
  ├── 對話        教 Agent「怎麼想」：判斷框架、決策邏輯
  ├── 調研
  ├── 會議
  └── 原文閱讀後的領悟
```

- **Sonar** 是機器壓縮——從 Seabed 原文自動產生 skeleton 索引（~9% token），幫 Agent 快速找到東西
- **Pearl** 是人類蒸餾——從對話、調研、會議、工作討論中提煉出的原子洞見（100-300 字），教 Agent 用老闆的邏輯思考

兩者獨立存在、互相 `[[連結]]`，不是上下游壓縮關係。

---

## CLSC 中文壓縮引擎

**CLSC**（Chinese Lossy Summary Compression）是 MemOcean 的核心引擎，fork 自 MemPalace 的 AAAK skeleton 格式，針對中文場景重寫了整條 NER + 搜尋 pipeline。

### 跟 upstream AAAK 的差異

| Upstream AAAK 假設 | CLSC 中文實作 |
|-------------------|--------------|
| `text.split()` 切 token | jieba POS tagging 自動 NER |
| 全大寫縮寫當 entity | 中文 entity 用拼音首字母 + token-aware gate |
| LIKE 全字串匹配 | FTS5 trigram + BM25 ranking，fallback OR-match |
| 固定 budget truncation | Content-proportional scaling（按原文長度動態調整） |

### Skeleton 格式

每篇素材壓縮成單行 skeleton，存為 `.clsc` 檔：

```
[SLUG|ENTITIES|topics|"key_quote"|WEIGHT|EMOTIONS|FLAGS]
```

### 效果數據

在真實中文語料上的實測表現（148 篇 Obsidian vault 文件）：

| 指標 | 數值 |
|------|------|
| 測試規模 | 148 篇文件 |
| 原始 token 總量 | 459,490 |
| Sonar token 總量 | 43,392 |
| 整體壓縮率 | **9.4%** |
| Token 節省 | **90.6%** |
| 搜尋場景平均節省 | **78.2%** |

Sonar-first 搜尋路徑（先讀 skeleton 再按需讀原文）比直接讀原文節省約 78% 的 token 消耗。

### 搜尋優化

我們經歷了三輪搜尋迭代：

| 查詢類型 | v1 ALL-match | v2 OR-match | v3 FTS5+BM25 |
|---------|-------------|-------------|--------------|
| 結構化查詢 | 50% | 89% | 89% |
| 自然語言查詢 | 0% | 55% | 55% |
| 已知文件查詢 | 85% | 95% | 95% |

v3 的命中率與 v2 相同（FTS5 miss 時自動 fallback 到 OR-match），但排序品質大幅提升——BM25 把最相關的文件推到 top-1，而非高頻但不精準的結果。英文查詢的 top-1 準確度提升尤其明顯。

### 已知限制

誠實說，CLSC 目前有兩個已知限制：

1. **jieba 繁體精度**——jieba 字典以簡中為主，繁體靠統計回退，NER recall 還沒有量化 baseline
2. **語意推斷詞 miss**——skeleton 只存字面 entity，「隊長」「三引擎」這類推斷詞會搜不到。需要 query rewrite layer 補上

---

## 致謝

MemOcean 的架構基礎來自 [MemPalace](https://github.com/milla-jovovich/mempalace) 和它的 AAAK skeleton 格式。記憶宮殿的隱喻、drawer/closet 雙層架構、lossy summary 的核心理念——這些都是 MemPalace 團隊的原創設計。

我們在這個基礎上做的是把它帶到中文場景、帶到多 Agent 協作場景。命名從宮殿換成了海洋，但骨子裡的設計哲學沒變：**不要讓 Agent 讀完全文才能思考，給它一個精準的索引就夠了。**

---

## License

MIT
