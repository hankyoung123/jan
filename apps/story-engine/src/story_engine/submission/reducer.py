"""Deterministic reducer for conversation-authored submission deltas."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

from story_engine.domain.models import InitialFact

if TYPE_CHECKING:
    from story_engine.submission.service import (
        SubmissionCharacterProposal,
        SubmissionDraft,
        SubmissionDraftDelta,
        SubmissionFactProposal,
    )


def character_proposals(
    draft: SubmissionDraft,
) -> tuple[SubmissionCharacterProposal, ...]:
    from story_engine.submission.service import SubmissionCharacterProposal

    return tuple(
        SubmissionCharacterProposal(
            character_ref=f"c{index}",
            display_name=item.display_name,
            identity=item.identity,
            core_desire=item.core_desire,
            current_goal=item.current_goal,
            location=item.location,
            emotional_state=item.emotional_state,
            resources=item.resources,
        )
        for index, item in enumerate(draft.characters)
    )


def fact_proposals(draft: SubmissionDraft) -> tuple[SubmissionFactProposal, ...]:
    from story_engine.submission.service import SubmissionFactProposal

    character_refs = {
        item.id: f"c{index}" for index, item in enumerate(draft.characters)
    }
    fact_refs = {item.id: f"f{index}" for index, item in enumerate(draft.facts)}
    return tuple(
        SubmissionFactProposal(
            fact_ref=f"f{index}",
            statement=item.statement,
            visibility=item.visibility,
            known_by=tuple(character_refs[owner] for owner in item.known_by),
            supersedes_ref=(
                fact_refs[item.supersedes_fact_id]
                if item.supersedes_fact_id is not None
                else None
            ),
        )
        for index, item in enumerate(draft.facts)
    )


def reduce_submission_draft(
    draft: SubmissionDraft,
    delta: SubmissionDraftDelta,
) -> SubmissionDraft:
    """Apply semantic proposal fields and regenerate all local identifiers."""
    from story_engine.submission.service import (
        SubmissionCharacter,
    )

    character_inputs = delta.characters or character_proposals(draft)
    existing_characters_by_ref = {
        f"c{index}": item for index, item in enumerate(draft.characters)
    }
    existing_characters_by_name = {
        item.display_name.casefold(): item for item in draft.characters
    }
    character_ids: list[str] = []
    used_character_ids: set[str] = set()
    for character_index, character_input in enumerate(character_inputs):
        existing_character = (
            existing_characters_by_ref.get(character_input.character_ref)
            if character_input.character_ref is not None
            else existing_characters_by_name.get(
                character_input.display_name.casefold()
            )
        )
        character_id = existing_character.id if existing_character is not None else None
        character_id = character_id or _local_identifier(
            character_input.display_name,
            prefix="character-",
            index=character_index,
        )
        character_id_base = character_id
        character_id_suffix = 2
        while character_id in used_character_ids:
            character_id = f"{character_id_base}-{character_id_suffix}"
            character_id_suffix += 1
        used_character_ids.add(character_id)
        character_ids.append(character_id)

    character_ids_by_ref = {
        character_input.character_ref or f"c{index}": character_ids[index]
        for index, character_input in enumerate(character_inputs)
    }
    if len(character_ids_by_ref) != len(character_inputs):
        raise ValueError("submission characters must use unique character refs")

    facts: list[InitialFact]
    if delta.facts is None:
        facts = list(draft.facts)
    else:
        fact_inputs = delta.facts
        existing_facts_by_ref = {
            f"f{index}": item for index, item in enumerate(draft.facts)
        }
        existing_facts_by_statement = {
            item.statement.casefold(): item for item in draft.facts
        }
        fact_ids: list[str] = []
        used_fact_ids: set[str] = set()
        for fact_index, fact_input in enumerate(fact_inputs):
            existing_fact = (
                existing_facts_by_ref.get(fact_input.fact_ref)
                if fact_input.fact_ref is not None
                else existing_facts_by_statement.get(fact_input.statement.casefold())
            )
            fact_id = existing_fact.id if existing_fact is not None else None
            fact_id = fact_id or _local_identifier(
                fact_input.statement,
                prefix="fact:",
                index=fact_index,
            )
            fact_id_base = fact_id
            fact_id_suffix = 2
            while fact_id in used_fact_ids:
                fact_id = f"{fact_id_base}-{fact_id_suffix}"
                fact_id_suffix += 1
            used_fact_ids.add(fact_id)
            fact_ids.append(fact_id)

        fact_ids_by_ref = {
            fact_input.fact_ref or f"f{index}": fact_ids[index]
            for index, fact_input in enumerate(fact_inputs)
        }
        if len(fact_ids_by_ref) != len(fact_inputs):
            raise ValueError("submission facts must use unique fact refs")

        facts = []
        for fact_index, fact_input in enumerate(fact_inputs):
            known_by = tuple(
                _resolve_character_ref(
                    ref,
                    character_ids_by_ref=character_ids_by_ref,
                    character_ids=character_ids,
                )
                for ref in fact_input.known_by
            )
            supersedes_fact_id = (
                _resolve_fact_ref(
                    fact_input.supersedes_ref,
                    fact_ids_by_ref=fact_ids_by_ref,
                    fact_ids=fact_ids,
                )
                if fact_input.supersedes_ref is not None
                else None
            )
            if supersedes_fact_id == fact_ids[fact_index]:
                raise ValueError("submission fact cannot supersede itself")
            facts.append(
                InitialFact(
                    id=fact_ids[fact_index],
                    statement=fact_input.statement,
                    visibility=fact_input.visibility,
                    known_by=known_by,
                    supersedes_fact_id=supersedes_fact_id,
                )
            )

    known_facts_by_character = {
        character_id: tuple(fact.id for fact in facts if character_id in fact.known_by)
        for character_id in character_ids
    }
    characters = tuple(
        SubmissionCharacter(
            id=character_ids[index],
            display_name=character_input.display_name,
            identity=character_input.identity,
            core_desire=character_input.core_desire,
            current_goal=character_input.current_goal,
            known_fact_ids=known_facts_by_character[character_ids[index]],
            location=character_input.location,
            emotional_state=character_input.emotional_state,
            resources=character_input.resources,
        )
        for index, character_input in enumerate(character_inputs)
    )
    updates = {
        name: value
        for name, value in delta.model_dump().items()
        if value is not None and name not in {"facts", "characters"}
    }
    return draft.__class__(
        **{
            **draft.model_dump(),
            **updates,
            "facts": tuple(facts),
            "characters": characters,
        }
    )


def _local_identifier(text: str, *, prefix: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    suffix = slug or hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}{suffix or index + 1}"


def _resolve_character_ref(
    ref: str | int,
    *,
    character_ids_by_ref: dict[str, str],
    character_ids: list[str],
) -> str:
    if isinstance(ref, int):
        if 0 <= ref < len(character_ids):
            return character_ids[ref]
    elif ref in character_ids_by_ref:
        return character_ids_by_ref[ref]
    raise ValueError(f"submission fact has unknown character ref: {ref!r}")


def _resolve_fact_ref(
    ref: str | int,
    *,
    fact_ids_by_ref: dict[str, str],
    fact_ids: list[str],
) -> str:
    if isinstance(ref, int):
        if 0 <= ref < len(fact_ids):
            return fact_ids[ref]
    elif ref in fact_ids_by_ref:
        return fact_ids_by_ref[ref]
    raise ValueError(f"submission fact has unknown supersedes_ref: {ref!r}")
