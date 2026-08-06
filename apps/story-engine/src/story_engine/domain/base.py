from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue

LocaleCode = Annotated[
    str,
    Field(pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$", min_length=2, max_length=16),
]
Identifier = Annotated[
    str,
    Field(pattern=r"^[a-z0-9][a-z0-9._:-]{0,127}$"),
]
StatePayload = dict[str, JsonValue]


class RuntimeModel(BaseModel):
    """Strict, immutable base for persisted simulation contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )
