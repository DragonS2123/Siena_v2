"""OpenAI-compatible llama-server transport with Siena response normalization."""
from __future__ import annotations

import asyncio
import copy
import json
import math
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from core.errors import SienaInfraError, SienaTimeoutError


@dataclass(frozen=True)
class LlamaCppConfig:
    url: str
    model: str
    profile: str
    context_size: int
    output_tokens: int | None = None
    timeout: float = 120
    connect_timeout: float = 10
    stream_idle_timeout: float = 45
    health_timeout: float = 3
    generation_options: dict = field(default_factory=dict)
    reasoning: bool = True
    reasoning_budget_tokens: int = 512
    model_path: str | None = None  # Diagnostic only; never loaded by this client.
    managed: bool = False

    def __post_init__(self):
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("llama.cpp URL must be an http(s) server URL")
        if not self.model.strip() or not self.profile.strip():
            raise ValueError("model and profile must not be empty")
        if self.context_size <= 0 or self.timeout <= 0:
            raise ValueError("context size and timeout must be positive")
        if self.output_tokens is not None and self.output_tokens <= 0:
            raise ValueError("output token limit must be positive")
        if type(self.reasoning_budget_tokens) is not int or not 0 <= self.reasoning_budget_tokens <= 65536:
            raise ValueError("reasoning budget must be an integer between 0 and 65536; unrestricted reasoning is disabled")


class LlamaCppProvider:
    def __init__(self, config: LlamaCppConfig, *, transport=None, async_transport=None):
        self.config = config
        self._model = config.model
        self._transport = transport
        self._async_transport = async_transport
        self._server_context = None

    @property
    def num_ctx(self):
        return self.config.context_size

    @property
    def num_predict(self):
        return self.config.output_tokens

    def diagnostics(self) -> dict:
        return {"provider": "llama_cpp", "url": self.config.url.rstrip("/"),
                "model": self._model, "profile": self.config.profile,
                "context_size": self.num_ctx, "server_context_size": self._server_context,
                "output_tokens": self.num_predict, "model_path": self.config.model_path,
                "reasoning_budget_tokens": self.config.reasoning_budget_tokens,
                "managed": self.config.managed, "structured_json": True,
                "tool_choice_modes": ["auto", "none", "required", "function"],
                "external_context_configuration": True}

    def _client(self, *, health=False):
        timeout = self.config.health_timeout if health else self.config.timeout
        return httpx.Client(base_url=self.config.url.rstrip("/") + "/",
                            timeout=httpx.Timeout(timeout, connect=self.config.connect_timeout),
                            transport=self._transport, trust_env=False)

    def _async_client(self):
        return httpx.AsyncClient(base_url=self.config.url.rstrip("/") + "/",
                                 timeout=httpx.Timeout(self.config.stream_idle_timeout,
                                                       connect=self.config.connect_timeout),
                                 transport=self._async_transport, trust_env=False)

    def _error(self, exc):
        description = f"llama.cpp request failed (url={self.config.url}, model={self._model})"
        if isinstance(exc, httpx.TimeoutException):
            return SienaTimeoutError(description + ": timeout")
        if isinstance(exc, httpx.HTTPStatusError):
            return SienaInfraError(description + f": HTTP {exc.response.status_code}")
        return SienaInfraError(description + f": {type(exc).__name__}")

    @staticmethod
    def _malformed(reason):
        # Never include prompts, model content, or raw bodies in exception text.
        return SienaInfraError(f"malformed llama.cpp response: {reason}")

    @classmethod
    def _tool_call(cls, call):
        if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
            raise cls._malformed("tool call must contain a function")
        function = call["function"]
        name, arguments = function.get("name"), function.get("arguments")
        if not isinstance(name, str) or not name:
            raise cls._malformed("tool function name is missing")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError as exc:
                raise cls._malformed("tool arguments are not JSON") from exc
        if not isinstance(arguments, dict):
            raise cls._malformed("tool arguments must be an object")
        call_id = call.get("id") or f"siena-{uuid4()}"
        if not isinstance(call_id, str):
            raise cls._malformed("tool call id must be a string")
        return {"id": call_id, "type": "function",
                "function": {"name": name, "arguments": arguments}}

    @classmethod
    def _messages(cls, messages):
        """Translate Siena/Ollama history without changing the caller's objects."""
        translated = []
        pending = []
        for message in messages:
            item = {"role": message["role"], "content": message.get("content") or ""}
            if message.get("tool_calls"):
                calls = [cls._tool_call(c) for c in message["tool_calls"]]
                pending.extend(calls)
                item["tool_calls"] = [
                    {**c, "function": {**c["function"], "arguments": json.dumps(c["function"]["arguments"], ensure_ascii=False)}}
                    for c in calls
                ]
            if item["role"] == "tool":
                name = message.get("tool_name") or message.get("name")
                call_id = message.get("tool_call_id")
                if not call_id:
                    try:
                        payload = json.loads(item["content"])
                        call_id = payload.get("tool_call_id") if isinstance(payload, dict) else None
                    except (ValueError, TypeError):
                        pass
                match = next((c for c in pending if c["id"] == call_id), None) if call_id else next(
                    (c for c in pending if c["function"]["name"] == name), None)
                if match is None:
                    raise ValueError("tool result has no matching assistant tool call in context")
                item["tool_call_id"] = match["id"]
                item["name"] = match["function"]["name"]
                pending.remove(match)
            translated.append(item)
        return translated

    def _payload(self, messages, tools, model, response_format, tool_choice, *, stream=False):
        options = copy.deepcopy(self.config.generation_options)
        allowed = {"temperature", "top_p", "top_k", "min_p", "repeat_penalty",
                   "seed", "presence_penalty", "frequency_penalty"}
        if set(options) - allowed:
            raise ValueError("unsupported inference generation option")
        body = {**options, "model": model or self._model, "messages": self._messages(messages),
                "stream": stream}
        if not self.config.reasoning:
            body["reasoning_effort"] = "none"
        else:
            body["reasoning_budget_tokens"] = self.config.reasoning_budget_tokens
        if self.num_predict is not None:
            body["max_tokens"] = self.num_predict
        if response_format is not None:
            body["response_format"] = copy.deepcopy(response_format)
        definitions = copy.deepcopy(tools) if tools is not None else []
        if not isinstance(definitions, list) or any(
            not isinstance(t, dict) or t.get("type") != "function"
            or not isinstance(t.get("function"), dict)
            or not isinstance(t["function"].get("name"), str) or not t["function"]["name"]
            for t in definitions
        ):
            raise ValueError("tools must be a list of function definitions")
        if isinstance(tool_choice, dict):
            if tool_choice.get("type") != "function":
                raise ValueError("forced tool_choice must select a function")
            function = tool_choice.get("function")
            if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"]:
                raise ValueError("forced tool_choice must name a function")
            name = function["name"]
            definitions = [t for t in definitions if (t.get("function") or {}).get("name") == name]
            if len(definitions) != 1:
                raise ValueError("forced function must match exactly one supplied tool")
            tool_choice = "required"
        elif not isinstance(tool_choice, str) or tool_choice not in {"none", "auto", "required"}:
            raise ValueError("unsupported tool_choice")
        # b11429: none still puts tools in the template; object choice falls back to auto.
        # Keep both workarounds entirely inside the provider boundary.
        if tool_choice == "none":
            definitions = []
        if definitions:
            body["tools"] = definitions
            body["tool_choice"] = tool_choice
        elif tool_choice == "required":
            raise ValueError("required tool_choice needs at least one tool")
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    @classmethod
    def _normalize(cls, data, model, elapsed):
        if not isinstance(data, dict) or data.get("error"):
            raise cls._malformed("expected a completion object")
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise cls._malformed("expected one completion choice")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise cls._malformed("expected an assistant message")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise cls._malformed("content must be text")
        normalized_message = {"role": "assistant", "content": content or ""}
        thinking = message.get("reasoning_content")
        if thinking is not None:
            if not isinstance(thinking, str):
                raise cls._malformed("reasoning content must be text")
            normalized_message["thinking"] = thinking
        calls = message.get("tool_calls")
        if calls is not None:
            if not isinstance(calls, list):
                raise cls._malformed("tool_calls must be a list")
            normalized_message["tool_calls"] = [cls._tool_call(c) for c in calls]
        reason = choice.get("finish_reason")
        if not isinstance(reason, str) or reason not in {"stop", "length", "tool_calls", "content_filter"}:
            raise cls._malformed("missing or unsupported finish_reason")
        usage = data.get("usage") if data.get("usage") is not None else {}
        timings = data.get("timings") if data.get("timings") is not None else {}
        if not isinstance(usage, dict) or not isinstance(timings, dict):
            raise cls._malformed("usage and timings must be objects")
        for key in ("prompt_tokens", "completion_tokens"):
            value = usage.get(key, 0)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise cls._malformed("token counts must be nonnegative integers")
        actual_model = data.get("model", model)
        if not isinstance(actual_model, str) or not actual_model:
            raise cls._malformed("model must be a nonempty string")
        result = {"provider": "llama_cpp", "model": actual_model,
                  "message": normalized_message, "done": True,
                  "done_reason": reason, "finish_reason": reason,
                  "eval_count": usage.get("completion_tokens", 0),
                  "prompt_eval_count": usage.get("prompt_tokens", 0),
                  "total_duration": int(elapsed * 1e9), "usage": usage, "timings": timings}
        for source, target in (("predicted_ms", "eval_duration"), ("prompt_ms", "prompt_eval_duration")):
            value = timings.get(source)
            if value is not None:
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                    raise cls._malformed("invalid timing value")
                result[target] = int(value * 1e6)
        return result

    def chat(self, messages, tools=None, model=None, *, response_format=None, tool_choice="auto"):
        body = self._payload(messages, tools, model, response_format, tool_choice)
        started = time.monotonic()
        try:
            with self._client() as client:
                response = client.post("v1/chat/completions", json=body)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise self._error(exc) from exc
        except ValueError as exc:
            raise self._malformed("invalid JSON body") from exc
        return self._normalize(data, body["model"], time.monotonic() - started)

    def generate(self, prompt, *, system=None, response_format=None):
        messages = ([{"role": "system", "content": system}] if system is not None else [])
        return self.chat([*messages, {"role": "user", "content": prompt}], response_format=response_format)

    def _probe(self):
        try:
            with self._client(health=True) as client:
                health = client.get("health")
                health.raise_for_status()
                if health.json().get("status") != "ok":
                    raise SienaInfraError("external llama-server is not ready")
                response = client.get("v1/models")
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise self._error(exc) from exc
        except (ValueError, AttributeError) as exc:
            raise self._malformed("invalid health/model JSON") from exc
        models = data.get("data") if isinstance(data, dict) else None
        if not isinstance(models, list) or any(not isinstance(m, dict) or not isinstance(m.get("id"), str) or not m["id"] for m in models):
            raise self._malformed("invalid model catalog")
        for item in models:
            aliases = item.get("aliases", [])
            if not isinstance(aliases, list) or any(not isinstance(a, str) or not a for a in aliases):
                raise self._malformed("invalid model aliases")
        target = next((m for m in models if m["id"] == self._model or self._model in m.get("aliases", [])), None)
        if target is None:
            raise SienaInfraError(f"configured model is not served by external llama-server: {self._model}")
        meta = target.get("meta") or {}
        if not isinstance(meta, dict):
            raise self._malformed("invalid model metadata")
        context = meta.get("n_ctx")
        if context is not None and (not isinstance(context, int) or isinstance(context, bool) or context <= 0):
            raise self._malformed("invalid server context size")
        self._server_context = context
        if context is not None and context < self.num_ctx:
            raise SienaInfraError(f"external llama-server context {context} is smaller than configured {self.num_ctx}; configure the external server before using this profile")
        return models

    def health(self):
        try:
            self._probe()
            return {"available": True, "error": None, **self.diagnostics()}
        except SienaInfraError as exc:
            return {"available": False, "error": str(exc), **self.diagnostics()}

    def catalog(self):
        models = self._probe()
        items = []
        for model in models:
            meta = model.get("meta") or {}
            if not isinstance(meta, dict):
                raise self._malformed("invalid model metadata")
            for name in dict.fromkeys([model["id"], *(model.get("aliases") or [])]):
                items.append({"name": name, "tag": "GGUF", "family": None,
                              "parameter_size": str(meta.get("n_params")) if meta.get("n_params") else None,
                              "quantization": meta.get("ftype"), "size": meta.get("size"),
                              "modified_at": None, "digest": None, "loaded": True,
                              "context_size": meta.get("n_ctx"), "capabilities": ["completion"]})
        return {"available": True, "models": items, "error": None}

    @classmethod
    async def _events(cls, response):
        lines = []
        async for line in response.aiter_lines():
            if line == "":
                if lines:
                    yield "\n".join(lines)
                    lines = []
            elif line.startswith("data:"):
                lines.append(line[5:].removeprefix(" "))
        if lines:
            yield "\n".join(lines)

    async def stream_chat(self, messages, tools=None, model=None, *, response_format=None,
                          tool_choice="auto"):
        body = self._payload(messages, tools, model, response_format, tool_choice, stream=True)
        started = time.monotonic()
        pending = {}
        finish = None
        usage, timings = {}, {}
        actual_model = body["model"]
        saw_done = False
        try:
            async with self._async_client() as client:
                async with client.stream("POST", "v1/chat/completions", json=body) as response:
                    response.raise_for_status()
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        raise self._malformed("expected SSE content type")
                    async for event in self._events(response):
                        if event == "[DONE]":
                            saw_done = True
                            break
                        try:
                            data = json.loads(event)
                        except ValueError as exc:
                            raise self._malformed("invalid SSE JSON") from exc
                        if not isinstance(data, dict) or data.get("error"):
                            raise self._malformed("invalid SSE completion object")
                        actual_model = data.get("model") or actual_model
                        if data.get("usage") is not None:
                            usage = data["usage"]
                        if data.get("timings") is not None:
                            timings = data["timings"]
                        choices = data.get("choices")
                        if not isinstance(choices, list) or len(choices) > 1:
                            raise self._malformed("invalid SSE choices")
                        if not choices:
                            continue  # Usage-only trailer.
                        choice = choices[0]
                        if not isinstance(choice, dict) or not isinstance(choice.get("delta"), dict):
                            raise self._malformed("invalid SSE delta")
                        delta = choice["delta"]
                        message = {"role": "assistant", "content": ""}
                        for source, target in (("content", "content"), ("reasoning_content", "thinking")):
                            value = delta.get(source)
                            if value is not None:
                                if not isinstance(value, str):
                                    raise self._malformed("SSE text delta must be text")
                                message[target] = value
                        fragments = delta.get("tool_calls") or []
                        if not isinstance(fragments, list):
                            raise self._malformed("SSE tool_calls must be a list")
                        for fragment in fragments:
                            if not isinstance(fragment, dict):
                                raise self._malformed("invalid SSE tool fragment")
                            index = fragment.get("index")
                            function = fragment.get("function") or {}
                            if not isinstance(index, int) or isinstance(index, bool) or index < 0 or not isinstance(function, dict):
                                raise self._malformed("invalid SSE tool index/function")
                            call = pending.setdefault(index, {"function": {"name": "", "arguments": ""}})
                            if fragment.get("id"):
                                call["id"] = fragment["id"]
                            for key in ("name", "arguments"):
                                piece = function.get(key, "")
                                if not isinstance(piece, str):
                                    raise self._malformed("invalid SSE function fragment")
                                call["function"][key] += piece
                        if choice.get("finish_reason") is not None:
                            finish = choice["finish_reason"]
                        if message["content"] or message.get("thinking"):
                            yield {"provider": "llama_cpp", "model": actual_model,
                                   "message": message, "done": False}
                    if not saw_done:
                        raise self._malformed("truncated SSE stream (missing DONE)")
                    final_message = {"role": "assistant", "content": ""}
                    if pending:
                        final_message["tool_calls"] = [pending[i] for i in sorted(pending)]
                    final = self._normalize({"model": actual_model, "choices": [{"message": final_message,
                                              "finish_reason": finish}], "usage": usage, "timings": timings},
                                            body["model"], time.monotonic() - started)
                    yield final
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as exc:
            raise self._error(exc) from exc
