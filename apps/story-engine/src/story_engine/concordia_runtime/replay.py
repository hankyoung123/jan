import re
from collections.abc import Collection, Mapping, Sequence
from threading import RLock
from typing import Any

from concordia.language_model import language_model  # type: ignore[import-untyped]


class ReplayExhaustedError(RuntimeError):
    """Raised when a deterministic replay has no response for the next call."""


class ReplayLanguageModel(language_model.LanguageModel):  # type: ignore[misc]
    """Queue-backed Concordia model for repeatable engine and recovery tests."""

    def __init__(
        self,
        *,
        text_responses: Sequence[str] = (),
        choice_responses: Sequence[str] = (),
    ) -> None:
        self._text_responses = tuple(text_responses)
        self._choice_responses = tuple(choice_responses)
        self._text_position = 0
        self._choice_position = 0
        self._prompts: list[str] = []
        self._lock = RLock()

    @property
    def prompts(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._prompts)

    def get_state(self) -> dict[str, object]:
        with self._lock:
            return {
                "text_position": self._text_position,
                "choice_position": self._choice_position,
                "prompts": tuple(self._prompts),
            }

    def set_state(self, state: Mapping[str, object]) -> None:
        required = {"text_position", "choice_position", "prompts"}
        if set(state) != required:
            raise ValueError("replay state has unexpected fields")
        text_position = state["text_position"]
        choice_position = state["choice_position"]
        prompts = state["prompts"]
        if not isinstance(text_position, int) or isinstance(text_position, bool):
            raise ValueError("replay text position must be an integer")
        if not isinstance(choice_position, int) or isinstance(choice_position, bool):
            raise ValueError("replay choice position must be an integer")
        if not isinstance(prompts, (list, tuple)) or not all(
            isinstance(prompt, str) for prompt in prompts
        ):
            raise ValueError("replay prompts must be a string sequence")
        if not 0 <= text_position <= len(self._text_responses):
            raise ValueError("replay text position is out of range")
        if not 0 <= choice_position <= len(self._choice_responses):
            raise ValueError("replay choice position is out of range")
        with self._lock:
            self._text_position = text_position
            self._choice_position = choice_position
            self._prompts = list(prompts)

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
        del max_tokens, temperature, top_p, top_k, timeout, seed
        with self._lock:
            self._prompts.append(prompt)
            if self._text_position >= len(self._text_responses):
                raise ReplayExhaustedError("no queued text response remains")
            response = self._text_responses[self._text_position]
            self._text_position += 1
        end = min(
            (response.find(item) for item in terminators if item in response),
            default=len(response),
        )
        return response[:end]

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
        with self._lock:
            self._prompts.append(prompt)
            if self._choice_position >= len(self._choice_responses):
                raise ReplayExhaustedError("no queued choice response remains")
            queued_choice = self._choice_responses[self._choice_position]
            self._choice_position += 1
        choice = queued_choice
        if choice not in responses:
            for response in responses:
                semantic_option = re.compile(
                    rf"\({re.escape(response)}\)\s+{re.escape(queued_choice)}(?:\n|$)"
                )
                if semantic_option.search(prompt):
                    choice = response
                    break
        if choice not in responses:
            raise language_model.InvalidResponseError(
                f"queued replay choice {queued_choice!r} is not an available response"
            )
        return (
            responses.index(choice),
            choice,
            {
                "source": "replay",
                "semantic_choice": queued_choice,
            },
        )
