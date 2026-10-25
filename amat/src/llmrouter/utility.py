"""Bounded synchronous model calls for optional narrative-only consumers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from llmrouter.chain import Router, Target
from llmrouter.contracts import ChatRequest, ChatResponse
from llmrouter.policy import default_chain


class UsageAccounting:
    """Per-attempt metadata returned to the caller for report provenance."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def reserve(self, target: Target, request: ChatRequest) -> int:
        self.calls.append(
            {
                "provider": target.provider,
                "requested_model": target.model,
                "max_output_tokens": request.max_output_tokens,
            }
        )
        return len(self.calls) - 1

    def settle(self, call_id: int, response: ChatResponse) -> None:
        self.calls[call_id].update(
            {
                "served_model": response.served_model,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "error_class": response.error_class,
            }
        )


def complete_json(
    request: ChatRequest, *, validate: Callable[[str], object]
) -> tuple[ChatResponse | None, list[dict[str, object]]]:
    accounting = UsageAccounting()
    response = asyncio.run(Router(default_chain()).complete(request, accounting, validate=validate))
    return response, accounting.calls
