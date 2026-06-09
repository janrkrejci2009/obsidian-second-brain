"""Soft cost tracking for Grok and Tavily API calls. No blocking, just visibility."""

from datetime import datetime
from pathlib import Path
import json
import sys

from .config import USAGE_LOG

# Pricing per 1M tokens (as of 2026-04, https://docs.x.ai/docs/models)
GROK_PRICING = {
    "grok-4.20-reasoning": {"input": 3.00, "output": 15.00},
    "grok-4": {"input": 3.00, "output": 15.00},
    "grok-3": {"input": 2.00, "output": 10.00},
}

# Tavily bills in credits, not tokens. We log the nominal market cost even on the
# free tier (1000 credits/month) so the usage report reflects the real value the
# user is consuming - logging $0 while free would make the report misleading.
TAVILY_CREDIT_USD = 0.008  # pay-as-you-go, as of 2026-06, https://tavily.com/pricing
TAVILY_CREDIT_WARN = 50  # warn on stderr if a single operation exceeds this many credits


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = GROK_PRICING.get(model, GROK_PRICING["grok-4.20-reasoning"])
    return (input_tokens / 1_000_000) * rates["input"] + (output_tokens / 1_000_000) * rates["output"]


def log_call(command: str, model: str, input_tokens: int, output_tokens: int, cost_usd: float, extra: dict | None = None) -> None:
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "command": command,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 4),
    }
    if extra:
        entry.update(extra)
    with USAGE_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def log_tavily(command: str, credits: int) -> None:
    """Log a Tavily credit spend (search or extract). Cost is nominal market value
    even on the free tier so month_total() reflects real consumption. Warns on
    stderr when a single operation burns more than TAVILY_CREDIT_WARN credits."""
    cost = credits * TAVILY_CREDIT_USD
    log_call(command, "tavily", 0, 0, cost, extra={"credits": credits})
    if credits > TAVILY_CREDIT_WARN:
        print(
            f"[{command}] used {credits} Tavily credits (~${cost:.3f}) in one operation",
            file=sys.stderr,
        )


def month_total() -> tuple[float, int]:
    if not USAGE_LOG.exists():
        return 0.0, 0
    now = datetime.now()
    month_prefix = now.strftime("%Y-%m")
    total = 0.0
    count = 0
    for line in USAGE_LOG.read_text().splitlines():
        try:
            entry = json.loads(line)
            if entry["ts"].startswith(month_prefix):
                total += entry["cost_usd"]
                count += 1
        except (json.JSONDecodeError, KeyError):
            continue
    return total, count
