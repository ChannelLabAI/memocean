# FTS5 跨 bot 訊息搜尋

SQLite + FTS5 PoC，把 5 隻 bot 的 inbox 訊息灌進 `~/.claude-bots/memory.db`，提供快速全文搜尋以彌補 bot 失憶。

任務：`AOT` 旁邊那條 `20260408-021250-3532-fts5-bot-memory-search-poc`。

---

## 檔案

```
shared/fts5/
├── schema.sql        # FTS5 虛擬表 schema (trigram tokenizer + BM25)
├── lib.py            # 共用 ingest 函式 (open_db / parse / insert / ingest_dir)
├── backfill.py       # 一次性 backfill：掃所有 state/*/inbox/messages/
├── ingest_one.py     # 增量 ingest：給 hook 呼叫（接 messages_dir 路徑）
└── search.py         # 查詢 CLI（trigram FTS5 + LIKE fallback）

shared/hooks/
└── fts5-ingest.sh    # PostToolUse hook：fork 背景跑 ingest_one.py，<10ms 返回
```

DB 路徑：`~/.claude-bots/memory.db`（WAL mode）

---

## Schema

```sql
CREATE VIRTUAL TABLE messages USING fts5(
  bot_name UNINDEXED,
  ts UNINDEXED,
  source UNINDEXED,
  chat_id UNINDEXED,
  user UNINDEXED,
  message_id UNINDEXED,
  text,
  tokenize = 'trigram case_sensitive 0'
);

CREATE TABLE seen (key TEXT PRIMARY KEY);  -- bot_name|source|chat_id|message_id
```

### 為什麼 trigram 不 unicode61

`unicode61 remove_diacritics 2`（task spec 預設）對中文不友善：把連續 CJK 當單一 token，導致「短詞」「NOX 質押」這類常見 query 找不到。改 `trigram case_sensitive 0` 後 CJK 子字串可搜，英文也照樣 case-insensitive。

trigram 限制：query token < 3 char 找不到（例如 2 字中文「重啟」「分析」）。`search.py` 在 FTS5 0 結果時 fallback 到 `instr(text, ?) > 0` 子字串比對，~1k 行下仍 < 5ms。

⚠️ 注意：FTS5 虛擬表上 `LIKE` / `GLOB` 不可靠（會被 tokenizer 攔截，CJK 子字串會回 0），用 `instr()` 才正確。

---

## 用法

### 一次性 backfill

```bash
python3 ~/.claude-bots/shared/fts5/backfill.py
```

掃 `~/.claude-bots/state/*/inbox/messages/` 全部 `*.json` + `*.json.delivered`，INSERT OR IGNORE。

實測：1195 檔 → 1194 ingested（1 個沒 text 跳過）→ 0.14s。

### 搜尋 CLI

```bash
python3 ~/.claude-bots/shared/fts5/search.py 'NOX 質押'
python3 ~/.claude-bots/shared/fts5/search.py '關鍵詞' --limit 5
python3 ~/.claude-bots/shared/fts5/search.py 'FTS5' --bot anya
python3 ~/.claude-bots/shared/fts5/search.py 'Bonk' --json    # 給程式呼叫
```

支援的 FTS5 query 語法：
- 多字 AND：`NOX 質押`
- OR：`NOX OR Bonk`
- Phrase：`"主廚 重啟"`
- NEAR：`NEAR(關鍵詞 另一個詞, 5)`

### Incremental hook

`shared/hooks/fts5-ingest.sh` 設計為 PostToolUse hook，掛在 `inbox-inject.sh` 旁邊（不取代它）。觸發時 fork `ingest_one.py` 到背景，主流程立刻返回 6-10ms（實測），不阻塞 inbox-inject。

啟用方式（接到 settings.json 的 PostToolUse hooks 陣列）：
```json
{
  "type": "command",
  "command": "$HOME/.claude-bots/shared/hooks/fts5-ingest.sh"
}
```

idempotent：重複呼叫安全（seen 表去重）。

---

## 5 query 實測（v3 trigram + instr fallback）

| Query | 結果數 | 耗時 |
|---|---|---|
| `NOX 質押` | 3 | 1.7ms |
| `短詞` | 3 (LIKE fallback) | 2.4ms |
| `主廚 重啟` | 1 (LIKE fallback) | 2.4ms |
| `Bonk 提案` | 2 | 1.1ms |
| `FTS5` | 3 | 1.2ms |

全部 < 5ms，遠低於驗收標準 100ms。

---

## v0.2 新增來源

v0.2 把覆蓋面從 inbox 擴到 bot 的**外送回覆**和**長期記憶**：

| source 標籤 | 來源 | 備註 |
|---|---|---|
| `telegram` | `state/*/inbox/messages/*.json[.delivered]` | v0.1，收到的訊息 |
| `relay-msg` | `state/*/relay-messages.log` | v0.2，bot 送出的回覆（含 system startup） |
| `memory-md` | `~/.claude/projects/*/memory/*.md` | v0.2，auto-memory 檔（跨 session 記憶） |

### 為什麼是 `relay-messages.log` 不是 `relay.log`

`relay.log` 只有 metadata（時間、方向、chat id），沒有訊息內容，對全文搜尋沒幫助；`relay-messages.log` 每行帶完整 body，是唯一有搜尋價值的外送來源。未來寫入格式如果變動要同步更新 `RELAY_LINE_RE` in `lib.py`。

格式範例：
```
[2026-03-25T06:00:24.153Z] system → threedishes_bot (chat:self): @threedishes_bot startup self-check
[2026-04-07T15:33:55.435Z] threedishes_bot → * (chat:-5180494548): @CarrotAAA_bot 收到 ✅
```

不符合 regex 的行會視為**前一則訊息的續行**，以 `\n` 接上去（多行 JSON / log payload 常見）。

### memory/*.md 的 bot_name 推導

`~/.claude/projects/-home-<USER>--claude-bots-bots-anna/memory/foo.md` → `bot_name='anna'`。策略：找 `bots-` 之後的最後一段；失敗則整個 project dir 名當 fallback。

### 新 synthetic keys（保證 idempotent）

- relay-msg：`relay-msg|{bot_name}|{line_no}` — line number 穩定，rerun 不重複。
- memory-md：absolute file path — 天然唯一。若檔案被改寫，因為 `seen` 去重，**內容變化不會重新 ingest**（v0.2 限制，用戶需手動 wipe db 才會重灌）。

---

## Hook wiring

`shared/hooks/fts5-ingest.sh` v0.2 會同時 fork 兩個背景 ingest：

1. `$TELEGRAM_STATE_DIR/inbox/messages`（若為目錄）
2. `$TELEGRAM_STATE_DIR/relay-messages.log`（若為檔案）

兩者都 `nohup ... &` + `disown`，foreground 實測 36–46ms（5 樣本），仍 < 50ms。

### settings.json 手動 wiring（v0.2 未自動套用）

自動 wiring 被跳過：檢查 `~/.claude/settings.json`，發現**沒有**既有的 inbox-inject hook（該 hook 由 telegram plugin 自己管，不在 user settings 裡），無參考模板可 mirror，自動改 user global settings 風險高於收益。請手動加入：

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "",
        "hooks": [
          { "type": "command", "command": "$HOME/.claude-bots/shared/hooks/fts5-ingest.sh" }
        ]
      }
    ]
  }
}
```

（或掛在 plugin 層級的 hooks config，與 inbox-inject 同位置。）

---

## v0.2 5 query 實測

DB total = 4000 rows（inbox 1203 + relay 2720 + memory 77）

| Query | 結果數 | 耗時 |
|---|---|---|
| `REJECT` | 10 | 3.1ms |
| `approve` | 10 | 3.7ms |
| `sub-agent` | 10 | 14.1ms |
| `Bonk 提案` | 10 | 2.4ms |
| `搜尋詞` | 10 | 10.1ms |

全部 < 100ms 驗收線。

---

## PRAGMA busy_timeout

v0.2 在 `open_db()` 兩條路徑（first-init / existing-db）都加 `PRAGMA busy_timeout = 5000`，避免 hook 並發 ingest 時撞 WAL writer lock。

---

## 不做（P2）

- ❌ vector embedding（另一個 ticket）
- ❌ 改 relay / inbox-inject 既有邏輯
- ❌ 改 telegram plugin 內部
- ❌ memory-md 內容變動自動重灌（v0.2 用 path 當 key，rerun 需 wipe）

---

## 注意事項

- DB 不該進 git：`memory.db` `memory.db-wal` `memory.db-shm`
- 第一次 init：`open_db()` 自動跑 `schema.sql`
- 大量寫入用 WAL + synchronous=NORMAL，已在 schema 設好
