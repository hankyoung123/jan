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
            display_name=item.display_name,
            identity=item.identity,
            core_desire=item.core_desire,
            current_goal=item.current_goal,
            location=item.location,
            emotional_state=item.emotional_state,
            resources=item.resources,
        )
        for item in draft.characters
    )


def fact_proposals(draft: SubmissionDraft) -> tuple[SubmissionFactProposal, ...]:
    from story_engine.submission.service import SubmissionFactProposal

    character_refs = {item.id: index for index, item in enumerate(draft.characters)}
    fact_refs = {item.id: index for index, item in enumerate(draft.facts)}
    return tuple(
        SubmissionFactProposal(
            statement=item.statement,
            visibility=item.visibility,
            known_by=tuple(character_refs[owner] for owner in item.known_by),
            supersedes_ref=(
                fact_refs[item.supersedes_fact_id]
                if item.supersedes_fact_id is not None
                else None
            ),
        )
        for item in draft.facts
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
    existing_characters_by_name = {
        item.display_name.casefold(): item for item in draft.characters
    }
    character_ids: list[str] = []
    used_character_ids: set[str] = set()
    for character_index, character_input in enumerate(character_inputs):
        existing_character = existing_characters_by_name.get(
            character_input.display_name.casefold()
        )
        character_id = existing_character.id if existing_character is not None else None
        if character_id is None and character_index < len(draft.characters):
            positional_character_id = draft.characters[character_index].id
            if positional_character_id not in used_character_ids:
                character_id = positional_character_id
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

    facts: list[InitialFact]
    if delta.facts is None:
        facts = list(draft.facts)
    else:
        fact_inputs = delta.facts
        existing_facts_by_statement = {
            item.statement.casefold(): item for item in draft.facts
        }
        fact_ids: list[str] = []
        used_fact_ids: set[str] = set()
        for fact_index, fact_input in enumerate(fact_inputs):
            existing_fact = existing_facts_by_statement.get(
                fact_input.statement.casefold()
            )
            fact_id = existing_fact.id if existing_fact is not None else None
            if fact_id is None and fact_index < len(draft.facts):
                positional_fact_id = draft.facts[fact_index].id
                if positional_fact_id not in used_fact_ids:
                    fact_id = positional_fact_id
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

        facts = []
        for fact_index, fact_input in enumerate(fact_inputs):
            unknown_refs = set(fact_input.known_by) - set(range(len(character_ids)))
            if unknown_refs:
                raise ValueError(
                    "submission fact has unknown character refs: "
                    f"{sorted(unknown_refs)}"
                )
            if (
                fact_input.supersedes_ref is not None
                and not 0 <= fact_input.supersedes_ref < len(fact_ids)
            ):
                raise ValueError(
                    "submission fact has unknown supersedes_ref: "
                    f"{fact_input.supersedes_ref}"
                )
            if fact_input.supersedes_ref == fact_index:
                raise ValueError("submission fact cannot supersede itself")
            facts.append(
                InitialFact(
                    id=fact_ids[fact_index],
                    statement=fact_input.statement,
                    visibility=fact_input.visibility,
                    known_by=tuple(character_ids[ref] for ref in fact_input.known_by),
                    supersedes_fact_id=(
                        fact_ids[fact_input.supersedes_ref]
                        if fact_input.supersedes_ref is not None
                        else None
                    ),
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
