"""Pure helpers for validating and retrying structured model output."""

import json
from collections.abc import Mapping
from typing import Any

from jsonschema import ValidationError
from jsonschema.validators import validator_for
from pydantic import JsonValue

from story_engine.models.errors import ResponseLimitError, StructuredOutputError

MAX_REQUEST_BYTES = 1_048_576


def prompt_only_payload(
    payload: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    fallback = dict(payload)
    fallback.pop("response_format", None)
    raw_messages = fallback.get("messages")
    messages = list(raw_messages) if isinstance(raw_messages, list) else []
    instruction = {
        "role": "system",
        "content": (
            "Return only valid JSON matching this JSON Schema. Do not use "
            "Markdown fences or add explanatory text. JSON SCHEMA: "
            f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
        ),
    }
    insert_at = 0
    while (
        insert_at < len(messages)
        and isinstance(messages[insert_at], Mapping)
        and messages[insert_at].get("role") == "system"
    ):
        insert_at += 1
    messages.insert(insert_at, instruction)
    fallback["messages"] = messages
    _check_request_size(fallback)
    return fallback


def retry_payload(payload: Mapping[str, Any], feedback: str) -> dict[str, Any]:
    retry = dict(payload)
    raw_messages = retry.get("messages")
    messages = list(raw_messages) if isinstance(raw_messages, list) else []
    messages.append(
        {
            "role": "system",
            "content": (
                "Your previous response was rejected. Return a complete, "
                "corrected response matching the requested format. Error: "
                f"{feedback}"
            ),
        }
    )
    retry["messages"] = messages
    _check_request_size(retry)
    return retry


def validate_output(
    content: str,
    schema: Mapping[str, Any] | None,
) -> JsonValue:
    if schema is None:
        return None
    if not content.strip():
        raise StructuredOutputError(
            "model returned empty JSON content",
            retryable=True,
        )
    try:
        parsed: JsonValue = json.loads(content)
    except json.JSONDecodeError as error:
        raise StructuredOutputError(
            "model output is not valid JSON",
            retryable=True,
        ) from error
    try:
        validator_for(schema)(schema).validate(parsed)
    except ValidationError as error:
        path = ".".join(str(item) for item in error.absolute_path)
        location = f" at {path}" if path else ""
        raise StructuredOutputError(
            f"model output failed schema validation{location}",
            retryable=True,
        ) from error
    return parsed


def expanded_budget(
    current_budget: int,
    *,
    reasoning_tokens: int | None,
    ceiling: int,
) -> int:
    next_budget = max(
        current_budget * 2,
        (reasoning_tokens if reasoning_tokens is not None else current_budget) + 128,
    )
    return min(ceiling, next_budget)


def _check_request_size(payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ResponseLimitError(
            f"model request exceeded {MAX_REQUEST_BYTES} bytes"
        )
