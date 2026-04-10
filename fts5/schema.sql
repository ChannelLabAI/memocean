-- FTS5 跨 bot 訊息搜尋 schema
-- 路徑：~/.claude-bots/memory.db
-- tokenizer: trigram case_sensitive 0（CJK 子字串友善；unicode61 把連續中文當單一 token 不可用）
-- ranking:   BM25（FTS5 預設）

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- 主表：FTS5 虛擬表
-- 只 index `text` 欄位；其他 metadata 加 UNINDEXED 不進倒排索引
CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
  bot_name UNINDEXED,
  ts UNINDEXED,
  source UNINDEXED,
  chat_id UNINDEXED,
  user UNINDEXED,
  message_id UNINDEXED,
  text,
  -- trigram tokenizer 對 CJK 友善（unicode61 把連續中文當單一 token，
  -- 會導致「短詞」「NOX 質押」這類混合 query 找不到）。
  -- trigram + case_sensitive 0 同時支援中英、子字串、case-insensitive。
  tokenize = 'trigram case_sensitive 0'
);

-- 去重表：避免 backfill / hook 重複插入
-- key = bot_name + '|' + source + '|' + chat_id + '|' + message_id
CREATE TABLE IF NOT EXISTS seen (
  key TEXT PRIMARY KEY
);

-- 加速 ts UNINDEXED 欄的時序查詢（FTS5 不會自動 index UNINDEXED）
-- FTS5 不能加普通 index 在 UNINDEXED 欄，所以另開個 ts 補表（可選）
-- → 階段 1 不需要，先簡單
