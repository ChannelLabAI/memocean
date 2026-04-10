"""
ask_opus.py — Ask Claude Opus for high-level business judgment.

Used by Anya (running on Sonnet) to escalate strategic decisions, intent
clarification, and complex spec writing to Opus as a senior advisor.

Daily cap: 20 calls/day (counted from opus-advisor-usage.jsonl).
Context limit: 2000 tokens hard cap (truncates to last 6000 chars).
Timeout: 30 seconds.
"""
import datetime
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("memocean_mcp.ask_opus")

_OPUS_MODEL = "claude-opus-4-6"
_DAILY_CAP = 20
_LOG_PATH = Path(os.environ.get("CLAUDE_BOTS_ROOT", str(Path.home() / ".claude-bots"))) / "logs" / "opus-advisor-usage.jsonl"
_CONTEXT_CHAR_LIMIT = 6000  # ~2000 tokens for Chinese text (3 chars/token)
_TIMEOUT_SECONDS = 30
_MAX_TOKENS_CAP = 4000  # prevent accidental runaway costs (~$0.30/call ceiling)

_SYSTEM_PROMPT = (
    "你是一個資深商業顧問。你的 client 是一個 AI 特助（Anya），"
    "她在幫老闆處理事情時遇到需要高階判斷的問題。"
    "請給出精準、有深度的建議。"
)

_CAP_REACHED_MSG = "今日 Opus 諮詢次數已達上限（20/20），Sonnet 先自行判斷。"
_ERROR_MSG = "Opus 暫時無法回應，Sonnet 先自行判斷。"


def _ensure_log_dir() -> None:
    """Ensure the logs directory exists."""
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _count_today_calls() -> int:
    """Count how many Opus calls have been made today (UTC)."""
    if not _LOG_PATH.exists():
        return 0

    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    count = 0
    try:
        with open(_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    # Count entries from today that are successful calls (no error field)
                    ts = entry.get("ts", "")
                    if ts.startswith(today) and "error" not in entry:
                        count += 1
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass

    return count


def _truncate_context(context: str) -> tuple[str, bool]:
    """
    Truncate context to _CONTEXT_CHAR_LIMIT chars.
    Returns (truncated_context, was_truncated).
    """
    if len(context) <= _CONTEXT_CHAR_LIMIT:
        return context, False
    truncated = context[-_CONTEXT_CHAR_LIMIT:]
    return truncated, True


def _log_usage(
    question: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    """Append a successful call entry to usage log."""
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {
        "ts": ts,
        "question_preview": question[:100],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 6),
    }
    try:
        _ensure_log_dir()
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("ask_opus: failed to log usage: %s", e)


def _log_error(question: str, error: str) -> None:
    """Append an error entry to usage log."""
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {
        "ts": ts,
        "question_preview": question[:100],
        "error": error,
    }
    try:
        _ensure_log_dir()
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("ask_opus: failed to log error: %s", e)


def ask_opus(question: str, context: str = "", max_tokens: int = 1000) -> str:
    """向 Opus 顧問請教重大決策。

    Args:
        question: 要問 Opus 的問題
        context: 相關背景（可選，會增加 token 消耗）
        max_tokens: 回覆上限（預設 1000，控制成本）

    Returns:
        Opus 的建議，或拒絕/錯誤訊息字串
    """
    # --- Daily cap check ---
    today_count = _count_today_calls()
    if today_count >= _DAILY_CAP:
        logger.info("ask_opus: daily cap reached (%d/%d)", today_count, _DAILY_CAP)
        return _CAP_REACHED_MSG

    # --- Context size limit ---
    context_truncated = False
    if context:
        context, context_truncated = _truncate_context(context)

    # --- Build user message ---
    if context:
        prefix = "[注意：context 已截斷至最後 6000 字]\n\n" if context_truncated else ""
        user_message = f"{prefix}背景資訊：\n{context}\n\n問題：\n{question}"
    else:
        user_message = question

    # --- API key check ---
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        err = "ANTHROPIC_API_KEY not set"
        logger.warning("ask_opus: %s", err)
        _log_error(question, err)
        return _ERROR_MSG

    # --- Call Opus API ---
    try:
        import anthropic
    except ImportError:
        err = "anthropic package not installed"
        logger.error("ask_opus: %s", err)
        _log_error(question, err)
        return _ERROR_MSG

    try:
        max_tokens = min(max_tokens, _MAX_TOKENS_CAP)
        client = anthropic.Anthropic(api_key=api_key, timeout=_TIMEOUT_SECONDS)
        response = client.messages.create(
            model=_OPUS_MODEL,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        result_text = response.content[0].text.strip()
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        # Cost formula: Opus pricing per token
        cost_usd = input_tokens * 0.000015 + output_tokens * 0.000075

        _log_usage(question, input_tokens, output_tokens, cost_usd)

        logger.info(
            "ask_opus: success (input=%d output=%d cost=$%.4f)",
            input_tokens,
            output_tokens,
            cost_usd,
        )

        return result_text

    except Exception as e:
        err = str(e)
        logger.warning("ask_opus: API call failed: %s", err)
        _log_error(question, err)
        return _ERROR_MSG
