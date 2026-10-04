"""The only place the project talks to a model.

Deliberately no vendor SDK: every call goes through LiteLLM, so switching a stage
from Anthropic to Gemini or OpenAI is a string change in profile.yaml. The cost of
that choice is that provider-specific features (strict schema enforcement, prompt
caching) are not handled for us — hence the explicit JSON repair path below.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any, TypeVar

import litellm
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from .models import Usage

litellm.suppress_debug_info = True
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

# Gemini 3+ deprecates temperature/top_p/top_k and asks for sampling guidance in the
# system prompt instead. Sending them still works but warns on every call.
_NO_SAMPLING = re.compile(r"gemini-(?:[3-9]|flash-latest|pro-latest)")


class LLMError(RuntimeError):
    pass


# Overload and rate-limit responses are routine on shared capacity and say nothing
# about the request. An unattended 07:00 run must ride them out rather than deliver
# nothing; a bad request or a rejected key is not retried, because it will not fix
# itself.
TRANSIENT = (
    litellm.exceptions.ServiceUnavailableError,
    litellm.exceptions.RateLimitError,
    litellm.exceptions.InternalServerError,
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.Timeout,
)

# A per-DAY quota wall arrives as a 429 like any other, but no amount of backoff
# clears it — Gemini's free tier returns a retryDelay measured in hours. Retrying
# it burns minutes to produce nothing, so it is separated out and raised at once.
_EXHAUSTED = re.compile(r"PerDay|free_tier_requests|exceeded your current quota", re.IGNORECASE)


class QuotaExhausted(LLMError):
    """The quota is spent for the period; retrying will not help."""


def _is_exhausted(exc: BaseException) -> bool:
    return isinstance(exc, litellm.exceptions.RateLimitError) and bool(_EXHAUSTED.search(str(exc)))


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, TRANSIENT) and not _is_exhausted(exc)


def _extract_json(text: str) -> str:
    """Models wrap JSON in prose or fences often enough to be worth handling."""
    if match := _FENCE.search(text):
        return match.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


class LLMProvider:
    """One model, one responsibility: return a validated object and report spend."""

    def __init__(
        self,
        model: str,
        temperature: float = 0.0,
        timeout: int = 90,
        extra: Mapping[str, Any] | None = None,
        retries: int = 4,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.retries = retries
        # Provider-specific arguments (e.g. vertex_project/vertex_location). Keeping
        # them opaque here is what lets the rest of the pipeline stay provider-blind.
        self.extra: Mapping[str, Any] = extra or {}

    def _complete(self, messages: list[Any], max_tokens: int, kwargs: dict[str, Any]) -> Any:
        """One provider call, retried on transient capacity errors with backoff."""

        @retry(
            retry=retry_if_exception(_is_retryable),
            wait=wait_exponential(multiplier=3, min=4, max=60),
            stop=stop_after_attempt(self.retries),
            reraise=True,
        )
        def _once() -> Any:
            return litellm.completion(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                timeout=self.timeout,
                response_format={"type": "json_object"},
                **kwargs,
            )

        return _once()

    def _usage(self, response: Any) -> Usage:
        raw = getattr(response, "usage", None)
        try:
            cost = float(litellm.completion_cost(completion_response=response))
        except Exception:
            cost = 0.0
        return Usage(
            model=self.model,
            input_tokens=getattr(raw, "prompt_tokens", 0) or 0,
            output_tokens=getattr(raw, "completion_tokens", 0) or 0,
            cost_usd=round(cost, 6),
        )

    def structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 1024,
    ) -> tuple[T, Usage]:
        """Ask for JSON matching `schema`. One repair attempt, then give up loudly."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        total = Usage(model=self.model)

        kwargs: dict[str, Any] = dict(self.extra)
        if not _NO_SAMPLING.search(self.model):
            kwargs["temperature"] = self.temperature

        for attempt in (1, 2):
            try:
                response = self._complete(messages, max_tokens, kwargs)
            except TRANSIENT as exc:
                if _is_exhausted(exc):
                    raise QuotaExhausted(
                        f"{self.model}: quota spent for this period, not a transient "
                        "error. Switch route (vertex_ai/*) or enable billing."
                    ) from exc
                raise LLMError(
                    f"{self.model} is unavailable after {self.retries} attempts: "
                    f"{type(exc).__name__}. Capacity problem, not your request."
                ) from exc
            except Exception as exc:  # provider errors vary too much to narrow usefully
                raise LLMError(f"{self.model}: {exc}") from exc

            total = total + self._usage(response)
            choice = response.choices[0]
            text = choice.message.content or ""
            truncated = getattr(choice, "finish_reason", None) == "length"

            # Reasoning models spend output budget on thinking before emitting JSON, so
            # a low ceiling truncates mid-object. Say that plainly instead of letting it
            # surface as a parser error, and give the retry more room.
            if truncated:
                if attempt == 2:
                    raise LLMError(
                        f"{self.model} hit the {max_tokens}-token output ceiling before "
                        "finishing the JSON. Raise max_tokens for this stage."
                    )
                max_tokens *= 3
                continue

            try:
                return schema.model_validate_json(_extract_json(text)), total
            except (ValidationError, json.JSONDecodeError) as exc:
                if attempt == 2:
                    raise LLMError(
                        f"{self.model} returned unusable JSON after a repair attempt: {exc}"
                    ) from exc
                messages += [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            f"That did not validate against the schema: {exc}\n"
                            "Reply with the corrected JSON object and nothing else."
                        ),
                    },
                ]

        raise LLMError("unreachable")
