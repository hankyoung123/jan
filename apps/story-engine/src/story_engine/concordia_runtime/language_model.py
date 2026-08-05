import asyncio
import hashlib
import json
import logging
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
    AgentProfile,
    Message,
    ModelRequest,
    ModelResponse,
    ModelTask,
)
from story_engine.models.errors import ModelTimeoutError
from story_engine.models.gateway import ModelGateway

TraceSink = Callable[[ModelCallTrace], None]
logger = logging.getLogger(__name__)

_TASK_TYPES: dict[ModelTask, TaskType] = {
    "actor": TaskType.ACTOR,
    "game_master": TaskType.GAME_MASTER,
    "wiki_maintainer": TaskType.WIKI_MAINTENANCE,
    "editor": TaskType.EDITOR,
    "writer": TaskType.WRITER,
    "submission_editor": TaskType.EDITOR,
}


class ModelCallCancelledError(RuntimeError):
    """Raised when a Concordia model request is cancelled."""


class JanConcordiaLanguageModel(language_model.LanguageModel):  # type: ignore[misc]
    """Infrastructure-only bridge from Concordia to the Jan ModelGateway."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        profile_id: str | None = None,
        task_type: ModelTask,
        content_locale: str,
        prompt_version: str = "concordia-runtime-v1",
        output_schema: str | None = None,
        profile_resolver: Callable[[], str] | None = None,
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
        if profile_id is None and profile_resolver is None:
            raise ValueError("profile_id or profile_resolver is required")
        self._gateway = gateway
        self._profile_id = profile_id
        self._profile_resolver = profile_resolver
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

    def _current_profile_id(self) -> str:
        if self._profile_resolver is not None:
            return self._profile_resolver()
        if self._profile_id is None:
            raise ValueError("model has no profile resolver")
        return self._profile_id

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
        request: ModelRequest,
        profile: AgentProfile,
    ) -> None:
        trace_sink = self._trace_sink
        if trace_sink is None:
            return
        try:
            self._record_trace(
                trace_sink=trace_sink,
                call_id=call_id,
                prompt=prompt,
                started_at=started_at,
                duration_ms=duration_ms,
                response=response,
                error=error,
                request=request,
                profile=profile,
            )
        except Exception:
            logger.exception(
                "failed to record model call trace",
                extra={"call_id": call_id},
            )

    def _record_trace(
        self,
        *,
        trace_sink: TraceSink,
        call_id: str,
        prompt: str,
        started_at: datetime,
        duration_ms: int,
        response: ModelResponse | None,
        error: Exception | None,
        request: ModelRequest,
        profile: AgentProfile,
    ) -> None:
        if isinstance(error, ModelCallCancelledError):
            status = ModelCallStatus.CANCELLED
        elif isinstance(error, ModelTimeoutError):
            status = ModelCallStatus.TIMED_OUT
        elif error is not None:
            status = ModelCallStatus.FAILED
        else:
            status = ModelCallStatus.SUCCEEDED
        attempt_usage = getattr(error, "usage", None) if error is not None else None
        trace = ModelCallTrace(
            call_id=call_id,
            task_id=f"task:{call_id.removeprefix('call:')}",
            task_type=_TASK_TYPES[self._task_type],
            status=status,
            session_id=self._session_id,
            branch_id=self._branch_id,
            step=self._step,
            actor_id=self._actor_id,
            profile_id=request.profile_id,
            model_ref=response.model_ref if response else profile.model,
            prompt_version=self._prompt_version,
            content_locale=self._content_locale,
            component_ids=self._component_ids,
            source_record_ids=self._source_record_ids,
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            prompt_tokens=(
                response.usage.prompt_tokens
                if response
                else getattr(attempt_usage, "prompt_tokens", 0)
            ),
            completion_tokens=(
                response.usage.completion_tokens
                if response
                else getattr(attempt_usage, "completion_tokens", 0)
            ),
            reasoning_tokens=(
                response.usage.reasoning_tokens
                if response
                else getattr(attempt_usage, "reasoning_tokens", None)
            ),
            finish_reason=(
                response.finish_reason
                if response
                else getattr(error, "finish_reason", None)
            ),
            max_tokens=(
                response.max_tokens
                if response
                else getattr(error, "max_tokens", request.max_output_tokens)
            ),
            reasoning_effort=(
                request.reasoning_effort
                if request.reasoning_effort is not None
                else profile.reasoning_effort
            ),
            temperature=(
                request.temperature
                if request.temperature is not None
                else profile.temperature
            ),
            timeout_seconds=request.timeout_seconds,
            retry_count=(
                response.retry_count if response else getattr(error, "retry_count", 0)
            ),
            duration_ms=duration_ms,
            error_code=(
                getattr(error, "code", type(error).__name__.lower())
                if error is not None
                else None
            ),
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )
        trace_sink(trace)

    def _complete(
        self,
        prompt: str,
        *,
        max_tokens: int | None,
        timeout: float | None,
        temperature: float | None,
        output_schema: str | None,
        budget_kind: str = "full",
    ) -> tuple[str, object]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("model calls must run outside the API event loop")

        profile_id = self._current_profile_id()
        profile = self._gateway.registry.get_profile(profile_id)
        del max_tokens, timeout, temperature, budget_kind
        request = ModelRequest(
            profile_id=profile_id,
            task_type=self._task_type,
            messages=(
                Message(role="system", content=profile.default_system_prompt),
                Message(role="user", content=prompt),
            ),
            output_schema=output_schema,
            max_output_tokens=profile.max_output_tokens,
            output_token_limit=(
                "provider" if profile.max_output_tokens is None else "profile"
            ),
            timeout_seconds=profile.timeout_seconds,
            temperature=profile.temperature,
            reasoning_effort=profile.reasoning_effort,
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
                request=request,
                profile=profile,
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
            max_tokens=None,
            timeout=None,
            temperature=temperature,
            output_schema=self._output_schema,
            budget_kind=(
                "short_json" if self._output_schema is not None else "full"
            ),
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
            max_tokens=None,
            timeout=None,
            temperature=0,
            output_schema=schema,
            budget_kind="choice",
        )
        if not isinstance(parsed, dict) or not isinstance(parsed.get("choice"), str):
            raise ValueError("model gateway returned an invalid choice")
        choice = parsed["choice"]
        if choice not in responses:
            raise ValueError("model gateway returned an unknown choice")
        return responses.index(choice), choice, {}

    def sample_json(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        *,
        temperature: float = 0.1,
    ) -> Mapping[str, Any]:
        """Request one structured object with a call-specific schema."""
        _, parsed = self._complete(
            prompt,
            max_tokens=None,
            timeout=None,
            temperature=temperature,
            output_schema=json.dumps(schema, ensure_ascii=False),
            budget_kind="short_json",
        )
        if not isinstance(parsed, dict):
            raise ValueError("model gateway returned a non-object JSON result")
        return parsed
