import asyncio
import json
import math
from collections.abc import Collection, Mapping, Sequence
from contextlib import suppress
from threading import Event
from typing import Any

from concordia.language_model import language_model  # type: ignore[import-untyped]

from story_engine.evolution.execution import TurnCancelledError
from story_engine.models.contracts import (
    Message,
    ModelRequest,
    ModelResponse,
    ModelTask,
)
from story_engine.models.gateway import ModelGateway


class JanGatewayLanguageModel(language_model.LanguageModel):  # type: ignore[misc]
    """Expose the existing Jan-backed ModelGateway to Concordia."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        profile_id: str,
        task_type: ModelTask,
        output_schema: str | None = None,
        cancellation: Event | None = None,
    ) -> None:
        self._gateway = gateway
        self._profile_id = profile_id
        self._task_type = task_type
        self._output_schema = output_schema
        self._cancellation = cancellation

    async def _complete_with_cancellation(
        self,
        request: ModelRequest,
    ) -> ModelResponse:
        if self._cancellation is None:
            return await self._gateway.complete(request)
        if self._cancellation.is_set():
            raise TurnCancelledError("turn generation was cancelled")

        completion = asyncio.create_task(self._gateway.complete(request))
        while not completion.done():
            if self._cancellation.is_set():
                completion.cancel()
                with suppress(asyncio.CancelledError):
                    await completion
                raise TurnCancelledError("turn generation was cancelled")
            await asyncio.wait({completion}, timeout=0.05)
        return completion.result()

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
            raise RuntimeError(
                "Concordia model calls must run outside the API event loop"
            )

        request = ModelRequest(
            profile_id=self._profile_id,
            task_type=self._task_type,
            messages=(Message(role="user", content=prompt),),
            output_schema=output_schema,
            max_output_tokens=min(max(max_tokens, 1), 8192),
            timeout_seconds=min(max(math.ceil(timeout), 1), 120),
            temperature=temperature,
        )
        response = asyncio.run(self._complete_with_cancellation(request))
        return response.content, response.parsed_output

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
            max_tokens=max_tokens,
            timeout=timeout,
            temperature=temperature,
            output_schema=self._output_schema,
        )
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
            raise ValueError("Concordia choice sampling requires options")
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
            timeout=language_model.DEFAULT_TIMEOUT_SECONDS,
            temperature=0,
            output_schema=schema,
        )
        if not isinstance(parsed, dict) or not isinstance(parsed.get("choice"), str):
            raise ValueError("Jan ModelGateway returned an invalid Concordia choice")
        choice = parsed["choice"]
        if choice not in responses:
            raise ValueError("Jan ModelGateway returned an unknown Concordia choice")
        return responses.index(choice), choice, {}
