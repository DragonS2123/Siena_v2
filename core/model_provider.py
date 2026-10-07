"""Inference boundary. Dicts retain Siena's existing message/diagnostic shape.

Tool arguments are objects, stream content/thinking are deltas, and complete
stream tool calls are emitted once. A terminal chunk carries done/reason/usage.
Cancellation is request-scoped: cancel the async task or close its iterator.
No provider owns or launches a server process.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ModelProvider(Protocol):
    @property
    def num_ctx(self) -> int | None: ...

    @property
    def num_predict(self) -> int | None: ...

    def health(self) -> dict[str, Any]: ...

    def catalog(self) -> dict[str, Any]: ...

    def diagnostics(self) -> dict[str, Any]: ...

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             model: str | None = None, *, response_format: dict | None = None,
             tool_choice: str | dict = "auto") -> dict: ...

    def generate(self, prompt: str, *, system: str | None = None,
                 response_format: dict | None = None) -> dict: ...

    def stream_chat(self, messages: list[dict], tools: list[dict] | None = None,
                    model: str | None = None, *, response_format: dict | None = None,
                    tool_choice: str | dict = "auto") -> AsyncIterator[dict]: ...
