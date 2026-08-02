import asyncio
import hashlib
import json
import math
import time
import uuid
from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from threading import Event
from typing import Any

from concordia.language_model import language_model  # type: ignore[import-untyped]

from story_engine.domain.action import TaskType
from story_engine.domain.trace import ModelCallStatus, ModelCallTrace
from story_engine.models.contracts import (
    Message,
    ModelRequest,
    ModelResponse,
    ModelTask,
)
from story_engine.models.errors import ModelTimeoutError
from story_engine.models.gateway import ModelGateway

TraceSink = Callable[[ModelCallTrace], None]

_TASK_TYPES: dict[ModelTask, TaskType] = {
    "actor": TaskType.ACTOR,
    "game_master": TaskType.GAME_MASTER,
    "reflection": TaskType.REFLECTION,
    "memory_consolidation": TaskType.MEMORY_CONSOLIDATION,
    "projection": TaskType.PROJECTION,
    "editor": TaskType.EDITOR,
    "writer": TaskType.WRITER,
    "embedding": TaskType.EMBEDDING,
}


class ModelCallCancelledError(RuntimeError):
    """Raised when a Concordia model request is cancelled."""


class JanConcordiaLanguageModel(language_model.LanguageModel):  # type: ignore[misc]
    """Infrastructure-only bridge from Concordia to the Jan ModelGateway."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        profile_id: str,
        task_type: ModelTask,
        content_locale: str,
        prompt_version: str = "concordia-runtime-v1",
        output_schema: str | None = None,
        max_output_tokens: int | None = None,
        timeout_seconds: float | None = None,
        cancellation: Event | None = None,
        trace_sink: TraceSink | None = None,
        session_id: str | None = None,
        branch_id: str | None = None,
        step: int | None = None,
        actor_id: str | None = None,
        component_ids: tuple[str, ...] = (),
        source_record_ids: tuple[str, ...] = (),
    ) -> None:
        self._gateway = gateway
        self._profile_id = profile_id
        self._task_type = task_type
        self._content_locale = content_locale
        self._prompt_version = prompt_version
        self._output_schema = output_schema
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds
        self._cancellation = cancellation
        self._trace_sink = trace_sink
        self._session_id = session_id
        self._branch_id = branch_id
        self._step = step
        self._actor_id = actor_id
        self._component_ids = component_ids
        self._source_record_ids = source_record_ids

    def set_content_locale(self, content_locale: str) -> None:
        self._content_locale = content_locale

    def set_trace_context(
        self,
        *,
        step: int,
        component_ids: tuple[str, ...],
        source_record_ids: tuple[str, ...] = (),
    ) -> None:
        self._step = step
        self._component_ids = component_ids
        self._source_record_ids = source_record_ids

    async def _complete_with_cancellation(
        self,
        request: ModelRequest,
    ) -> ModelResponse:
        if self._cancellation is None:
            return await self._gateway.complete(request)
        if self._cancellation.is_set():
            raise ModelCallCancelledError("model request was cancelled")
        completion = asyncio.create_task(self._gateway.complete(request))
        while not completion.done():
            if self._cancellation.is_set():
                completion.cancel()
                with suppress(asyncio.CancelledError):
                    await completion
                raise ModelCallCancelledError("model request was cancelled")
            await asyncio.wait({completion}, timeout=0.05)
        return completion.result()

    def _trace(
        self,
        *,
        call_id: str,
        prompt: str,
        started_at: datetime,
        duration_ms: int,
        response: ModelResponse | None,
        error: Exception | None,
    ) -> None:
        if self._trace_sink is None:
            return
        if isinstance(error, ModelCallCancelledError):
            status = ModelCallStatus.CANCELLED
        elif isinstance(error, ModelTimeoutError):
            status = ModelCallStatus.TIMED_OUT
        elif error is not None:
            status = ModelCallStatus.FAILED
        else:
            status = ModelCallStatus.SUCCEEDED
        profile = self._gateway.registry.get_profile(self._profile_id)
        trace = ModelCallTrace(
            call_id=call_id,
            task_id=f"task:{call_id.removeprefix('call:')}",
            task_type=_TASK_TYPES[self._task_type],
            status=status,
            session_id=self._session_id,
            branch_id=self._branch_id,
            step=self._step,
            actor_id=self._actor_id,
            profile_id=self._profile_id,
            provider_id=response.provider_id if response else profile.provider_id,
            model_id=response.model if response else profile.model,
            prompt_version=self._prompt_version,
            content_locale=self._content_locale,
            component_ids=self._component_ids,
            source_record_ids=self._source_record_ids,
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            prompt_tokens=response.usage.prompt_tokens if response else 0,
            completion_tokens=response.usage.completion_tokens if response else 0,
            duration_ms=duration_ms,
            error_code=(
                getattr(error, "code", type(error).__name__.lower())
                if error is not None
                else None
            ),
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )
        self._trace_sink(trace)

    def _complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
        timeout: float,
        temperature: float | None,
        output_schema: str | None,
    ) -> tuple[str, object]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("model calls must run outside the API event loop")

        request = ModelRequest(
            profile_id=self._profile_id,
            task_type=self._task_type,
            messages=(Message(role="user", content=prompt),),
            output_schema=output_schema,
            max_output_tokens=min(max(max_tokens, 1), 8192),
            timeout_seconds=min(max(math.ceil(timeout), 1), 120),
            temperature=temperature,
        )
        started_at = datetime.now(UTC)
        started = time.monotonic()
        call_id = f"call:{uuid.uuid4().hex}"
        response: ModelResponse | None = None
        error: Exception | None = None
        try:
            response = asyncio.run(self._complete_with_cancellation(request))
            return response.content, response.parsed_output
        except Exception as caught:
            error = caught
            raise
        finally:
            self._trace(
                call_id=call_id,
                prompt=prompt,
                started_at=started_at,
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                response=response,
                error=error,
            )

    def sample_text(
        self,
        prompt: str,
        *,
        max_tokens: int = language_model.DEFAULT_MAX_TOKENS,
        terminators: Collection[str] = language_model.DEFAULT_TERMINATORS,
        temperature: float = language_model.DEFAULT_TEMPERATURE,
        top_p: float = language_model.DEFAULT_TOP_P,
        top_k: int = language_model.DEFAULT_TOP_K,
        timeout: float = language_model.DEFAULT_TIMEOUT_SECONDS,
        seed: int | None = None,
    ) -> str:
        del top_p, top_k, seed
        content, _ = self._complete(
            prompt,
            max_tokens=self._max_output_tokens or max_tokens,
            timeout=self._timeout_seconds or timeout,
            temperature=temperature,
            output_schema=self._output_schema,
        )
        if self._output_schema is not None:
            return content
        end = min(
            (content.find(item) for item in terminators if item in content),
            default=len(content),
        )
        return content[:end]

    def sample_choice(
        self,
        prompt: str,
        responses: Sequence[str],
        *,
        seed: int | None = None,
    ) -> tuple[int, str, Mapping[str, Any]]:
        del seed
        if not responses:
            raise ValueError("choice sampling requires at least one option")
        schema = json.dumps(
            {
                "type": "object",
                "required": ["choice"],
                "properties": {"choice": {"enum": list(responses)}},
                "additionalProperties": False,
            },
            ensure_ascii=False,
        )
        _, parsed = self._complete(
            f"{prompt}\nReturn the selected option in the required JSON schema.",
            max_tokens=256,
            timeout=self._timeout_seconds or language_model.DEFAULT_TIMEOUT_SECONDS,
            temperature=0,
            output_schema=schema,
        )
        if not isinstance(parsed, dict) or not isinstance(parsed.get("choice"), str):
            raise ValueError("model gateway returned an invalid choice")
        choice = parsed["choice"]
        if choice not in responses:
            raise ValueError("model gateway returned an unknown choice")
        return responses.index(choice), choice, {}
