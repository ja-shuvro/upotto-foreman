"""Track Anthropic token usage and USD cost from ChatAnthropic response_metadata."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

try:
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.outputs import LLMResult
except ImportError:  # pragma: no cover - optional until deps installed
    BaseCallbackHandler = object  # type: ignore[misc, assignment]
    LLMResult = Any  # type: ignore[misc, assignment]

# Verified from https://platform.claude.com/docs/en/about-claude/pricing (2026-07-30)
# Claude Sonnet 5 intro pricing through 2026-08-31; standard thereafter.
# Sonnet 4.x / standard Sonnet rates remain $3 / $15 per 1M tokens.
SONNET_INTRO_CUTOFF = date(2026, 8, 31)
SONNET_INTRO_INPUT_PER_MTOK = 2.0
SONNET_INTRO_OUTPUT_PER_MTOK = 10.0
SONNET_STANDARD_INPUT_PER_MTOK = 3.0
SONNET_STANDARD_OUTPUT_PER_MTOK = 15.0


def sonnet_rates(as_of: date | None = None) -> tuple[float, float]:
    """Return (input_$/MTok, output_$/MTok) for Claude Sonnet on the given date."""
    day = as_of or date.today()
    if day <= SONNET_INTRO_CUTOFF:
        return SONNET_INTRO_INPUT_PER_MTOK, SONNET_INTRO_OUTPUT_PER_MTOK
    return SONNET_STANDARD_INPUT_PER_MTOK, SONNET_STANDARD_OUTPUT_PER_MTOK


@dataclass
class UsageRecord:
    timestamp: str
    agent: str
    input_tokens: int
    output_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "agent": self.agent,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "input_cost_usd": self.input_cost_usd,
            "output_cost_usd": self.output_cost_usd,
            "total_cost_usd": self.total_cost_usd,
            "model": self.model,
        }


@dataclass
class CostTracker:
    """Accumulates token usage and cost across LLM calls in a run."""

    records: list[UsageRecord] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0

    def calculate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        as_of: date | None = None,
    ) -> tuple[float, float, float]:
        in_rate, out_rate = sonnet_rates(as_of)
        input_cost = (input_tokens / 1_000_000) * in_rate
        output_cost = (output_tokens / 1_000_000) * out_rate
        return input_cost, output_cost, input_cost + output_cost

    def log_usage(
        self,
        *,
        agent: str,
        input_tokens: int,
        output_tokens: int,
        model: str = "",
        as_of: date | None = None,
    ) -> UsageRecord:
        input_cost, output_cost, total = self.calculate_cost(
            input_tokens, output_tokens, as_of=as_of
        )
        record = UsageRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent=agent,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost_usd=round(input_cost, 8),
            output_cost_usd=round(output_cost, 8),
            total_cost_usd=round(total, 8),
            model=model,
        )
        self.records.append(record)
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost_usd += total

        in_rate, out_rate = sonnet_rates(as_of)
        logger.info(
            "LLM usage agent=%s model=%s in=%d out=%d cost=$%.6f (rates $%.2f/$%.2f per 1M)",
            agent,
            model or "unknown",
            input_tokens,
            output_tokens,
            total,
            in_rate,
            out_rate,
        )
        return record

    def extract_and_log(
        self,
        response: Any,
        *,
        agent: str = "unknown",
        model: str = "",
    ) -> UsageRecord | None:
        """Pull usage from ChatAnthropic response_metadata / usage_metadata."""
        input_tokens = 0
        output_tokens = 0
        resolved_model = model

        usage_meta = getattr(response, "usage_metadata", None)
        if isinstance(usage_meta, dict):
            input_tokens = int(usage_meta.get("input_tokens") or usage_meta.get("prompt_tokens") or 0)
            output_tokens = int(
                usage_meta.get("output_tokens") or usage_meta.get("completion_tokens") or 0
            )

        meta = getattr(response, "response_metadata", None) or {}
        if isinstance(meta, dict):
            resolved_model = resolved_model or meta.get("model") or meta.get("model_name") or ""
            usage = meta.get("usage") or meta.get("token_usage") or {}
            if isinstance(usage, dict):
                input_tokens = input_tokens or int(
                    usage.get("input_tokens") or usage.get("prompt_tokens") or 0
                )
                output_tokens = output_tokens or int(
                    usage.get("output_tokens") or usage.get("completion_tokens") or 0
                )

        if input_tokens == 0 and output_tokens == 0:
            logger.warning("No token usage found on response for agent=%s", agent)
            return None

        return self.log_usage(
            agent=agent,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=resolved_model,
        )

    def summary(self) -> dict[str, Any]:
        in_rate, out_rate = sonnet_rates()
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "pricing": {
                "input_per_mtok_usd": in_rate,
                "output_per_mtok_usd": out_rate,
                "source": "https://platform.claude.com/docs/en/about-claude/pricing",
                "note": (
                    f"Sonnet 5 intro $2/$10 through {SONNET_INTRO_CUTOFF.isoformat()}; "
                    f"standard $3/$15 thereafter"
                ),
            },
            "calls": [r.to_dict() for r in self.records],
        }

    def reset(self) -> None:
        self.records.clear()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost_usd = 0.0

    def as_callback(self, *, agent: str = "unknown") -> "UsageCallbackHandler":
        return UsageCallbackHandler(self, agent=agent)


class UsageCallbackHandler(BaseCallbackHandler):  # type: ignore[misc]
    """LangChain callback that logs token usage from ChatAnthropic responses."""

    def __init__(self, tracker: CostTracker, *, agent: str = "unknown") -> None:
        try:
            super().__init__()
        except TypeError:
            pass
        self.tracker = tracker
        self.agent = agent

    def on_llm_end(self, response: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        input_tokens = 0
        output_tokens = 0
        model = ""

        llm_output = getattr(response, "llm_output", None) or {}
        if isinstance(llm_output, dict):
            model = str(llm_output.get("model_name") or llm_output.get("model") or "")
            token_usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
            if isinstance(token_usage, dict):
                input_tokens = int(
                    token_usage.get("input_tokens")
                    or token_usage.get("prompt_tokens")
                    or 0
                )
                output_tokens = int(
                    token_usage.get("output_tokens")
                    or token_usage.get("completion_tokens")
                    or 0
                )

        if not input_tokens and not output_tokens:
            for gen_list in getattr(response, "generations", None) or []:
                for gen in gen_list:
                    meta = getattr(gen, "message", None)
                    if meta is not None:
                        self.tracker.extract_and_log(
                            meta, agent=self.agent, model=model
                        )
                        return
                    info = getattr(gen, "generation_info", None) or {}
                    if isinstance(info, dict):
                        usage = info.get("usage") or {}
                        input_tokens = int(
                            usage.get("input_tokens") or usage.get("prompt_tokens") or 0
                        )
                        output_tokens = int(
                            usage.get("output_tokens")
                            or usage.get("completion_tokens")
                            or 0
                        )

        if input_tokens or output_tokens:
            self.tracker.log_usage(
                agent=self.agent,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model=model,
            )
