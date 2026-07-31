import json
from pathlib import Path
from typing import Protocol

from story_engine.domain.errors import DomainError
from story_engine.domain.models import StoryEvent
from story_engine.events.commit import EventCommitService, VersionConflictError
from story_engine.manuscript.models import (
    AmendmentCommitResult,
    EventAmendmentCandidate,
    ManuscriptExport,
    ManuscriptReviewOutput,
    Scene,
    SceneDraft,
    SceneMutationResult,
    SceneUpdateRequest,
    WriterOutput,
)
from story_engine.models.contracts import Message, ModelRequest
from story_engine.models.gateway import ModelGateway
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.project_store import ProjectStore
from story_engine.workspace.scene_store import (
    AmendmentStore,
    SceneDraftStore,
    SceneStore,
)


class ManuscriptAgent(Protocol):
    async def generate(
        self,
        events: tuple[StoryEvent, ...],
        *,
        project_title: str,
        genre: str,
        theme: str,
        tone: str,
    ) -> WriterOutput: ...

    async def review(
        self,
        events: tuple[StoryEvent, ...],
        *,
        title: str,
        body: str,
        public_fact_ids: tuple[str, ...],
    ) -> ManuscriptReviewOutput: ...


def _event_context(events: tuple[StoryEvent, ...]) -> str:
    values = [
        {
            "id": event.id,
            "summary": event.summary,
            "participants": event.participants,
            "public_results": event.public_results,
            "character_changes": [
                change.model_dump(mode="json") for change in event.character_changes
            ],
            "world_changes": [
                change.model_dump(mode="json") for change in event.world_changes
            ],
        }
        for event in events
    ]
    return json.dumps(values, ensure_ascii=False, default=str)


class GatewayManuscriptAgent:
    """Run Writer and manuscript Editor through the existing Jan model bridge."""

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    async def generate(
        self,
        events: tuple[StoryEvent, ...],
        *,
        project_title: str,
        genre: str,
        theme: str,
        tone: str,
    ) -> WriterOutput:
        context = _event_context(events)
        prompt = (
            "You are the Story Engine Writer. Write one vivid scene using only "
            "the confirmed events supplied below. Do not invent a person, object, "
            "location, cause, discovery, or outcome that is absent from them. "
            "Return exactly the requested JSON schema. "
            f"Project: {project_title}; genre: {genre}; theme: {theme}; tone: {tone}. "
            f"Confirmed events: {context}"
        )
        response = await self.gateway.complete(
            ModelRequest(
                profile_id="writer",
                task_type="writer",
                messages=(Message(role="system", content=prompt),),
                output_schema=json.dumps(
                    WriterOutput.model_json_schema(), ensure_ascii=False
                ),
                max_output_tokens=4096,
                timeout_seconds=60,
                temperature=0.7,
            )
        )
        return WriterOutput.model_validate(response.parsed_output)

    async def review(
        self,
        events: tuple[StoryEvent, ...],
        *,
        title: str,
        body: str,
        public_fact_ids: tuple[str, ...],
    ) -> ManuscriptReviewOutput:
        prompt = (
            "You are the Story Engine manuscript Editor. Compare the prose to "
            "the confirmed events and public fact IDs. List every concrete new "
            "fact asserted by the prose but unsupported by those sources. Style, "
            "sensory language, simile, and non-factual description are not new "
            "facts. A passing review must have no new_facts. Return exactly the "
            "requested JSON schema. "
            f"Confirmed events: {_event_context(events)}. "
            f"Public fact IDs: {json.dumps(public_fact_ids, ensure_ascii=False)}. "
            f"Scene title: {title}. Prose: {body}"
        )
        response = await self.gateway.complete(
            ModelRequest(
                profile_id="editor",
                task_type="editor",
                messages=(Message(role="system", content=prompt),),
                output_schema=json.dumps(
                    ManuscriptReviewOutput.model_json_schema(), ensure_ascii=False
                ),
                max_output_tokens=2048,
                timeout_seconds=60,
                temperature=0.1,
            )
        )
        return ManuscriptReviewOutput.model_validate(response.parsed_output)


class ManuscriptService:
    def __init__(self, root: Path, *, agent: ManuscriptAgent) -> None:
        self.root = root
        self.agent = agent
        self.projects = ProjectStore(root)
        self.events = EventStore(root)
        self.scenes = SceneStore(root)
        self.drafts = SceneDraftStore(root)
        self.amendments = AmendmentStore(root)

    def _confirmed_events(self, event_ids: tuple[str, ...]) -> tuple[StoryEvent, ...]:
        if not event_ids or len(event_ids) != len(set(event_ids)):
            raise ValueError("scene sources must be unique confirmed events")
        by_id = {event.id: event for event in self.events.list_events()}
        selected = tuple(by_id[event_id] for event_id in event_ids if event_id in by_id)
        if len(selected) != len(event_ids) or any(
            not event.approved_by_user for event in selected
        ):
            raise ValueError("scene sources must be unique confirmed events")
        return selected

    async def generate_scene(
        self,
        event_ids: tuple[str, ...],
        *,
        chapter_id: str,
    ) -> SceneDraft:
        events = self._confirmed_events(event_ids)
        snapshot = self.projects.load()
        output = await self.agent.generate(
            events,
            project_title=snapshot.project.title,
            genre=snapshot.project.genre,
            theme=snapshot.project.theme,
            tone=snapshot.project.tone,
        )
        review = await self.agent.review(
            events,
            title=output.title,
            body=output.body,
            public_fact_ids=snapshot.world.public_fact_ids,
        )
        scene_id, sequence = self.scenes.next_identifier()
        writer_is_grounded = review.review.passed and not review.new_facts
        draft = SceneDraft(
            id=scene_id,
            project_id=snapshot.project.id,
            sequence=sequence,
            chapter_id=chapter_id,
            title=output.title,
            body=output.body,
            source_event_ids=event_ids,
            base_world_version=snapshot.world.version,
            review=review,
            status="reviewed" if writer_is_grounded else "needs_revision",
        )
        self.drafts.save(draft, overwrite=False)
        return draft

    def get_scene(self, scene_id: str) -> SceneDraft:
        try:
            draft = self.drafts.load(scene_id)
            if draft.status != "saved":
                return draft
        except FileNotFoundError:
            pass
        scene = self.scenes.load(scene_id)
        snapshot = self.projects.load()
        return SceneDraft(
            id=scene.id,
            project_id=scene.project_id,
            sequence=scene.sequence,
            chapter_id=scene.chapter_id,
            title=scene.title,
            body=scene.body,
            source_event_ids=scene.source_event_ids,
            base_world_version=snapshot.world.version,
            base_scene_version=scene.version,
            status="draft",
        )

    def list_scenes(self) -> tuple[SceneDraft, ...]:
        drafts = {draft.id: draft for draft in self.drafts.list_drafts()}
        snapshot = self.projects.load()
        for scene in self.scenes.list_scenes():
            if scene.id in drafts and drafts[scene.id].status != "saved":
                continue
            drafts[scene.id] = SceneDraft(
                id=scene.id,
                project_id=scene.project_id,
                sequence=scene.sequence,
                chapter_id=scene.chapter_id,
                title=scene.title,
                body=scene.body,
                source_event_ids=scene.source_event_ids,
                base_world_version=snapshot.world.version,
                base_scene_version=scene.version,
            )
        return tuple(sorted(drafts.values(), key=lambda item: item.sequence))

    async def update_scene(
        self,
        scene_id: str,
        request: SceneUpdateRequest,
    ) -> SceneMutationResult:
        draft = self.get_scene(scene_id)
        if draft.revision != request.expected_revision:
            raise VersionConflictError("scene draft revision changed")
        if draft.base_scene_version != request.expected_scene_version:
            raise VersionConflictError("scene version changed")
        updated = draft.with_content(title=request.title, body=request.body)
        events = self._confirmed_events(updated.source_event_ids)
        snapshot = self.projects.load()
        review = await self.agent.review(
            events,
            title=updated.title,
            body=updated.body,
            public_fact_ids=snapshot.world.public_fact_ids,
        )
        if review.new_facts:
            amendment_id = f"amendment-{updated.id}-{updated.revision:06d}"
            amendment = EventAmendmentCandidate(
                id=amendment_id,
                project_id=updated.project_id,
                scene_id=updated.id,
                source_event_ids=updated.source_event_ids,
                proposed_facts=review.new_facts,
                fact_ids=tuple(
                    f"fact:{amendment_id}:{index:02d}"
                    for index, _fact in enumerate(review.new_facts, start=1)
                ),
                base_world_version=snapshot.world.version,
                draft_revision=updated.revision,
            )
            candidate_draft = updated.model_copy(
                update={
                    "review": review,
                    "amendment_id": amendment.id,
                    "status": "amendment_required",
                }
            )
            self.drafts.save(candidate_draft)
            self.amendments.save(amendment, overwrite=False)
            return SceneMutationResult(
                status="amendment_required",
                draft=candidate_draft,
                review=review,
                amendment=amendment,
            )
        if not review.review.passed:
            rejected = updated.model_copy(
                update={"review": review, "status": "needs_revision"}
            )
            self.drafts.save(rejected)
            return SceneMutationResult(
                status="rejected",
                draft=rejected,
                review=review,
            )

        reviewed = updated.model_copy(update={"review": review, "status": "reviewed"})
        scene = self._scene_from_draft(reviewed)
        EventCommitService(self.root).commit_scene(
            scene,
            expected_version=reviewed.base_scene_version,
        )
        saved = reviewed.model_copy(
            update={
                "base_scene_version": scene.version,
                "status": "saved",
            }
        )
        self.drafts.save(saved)
        return SceneMutationResult(
            status="saved",
            draft=self.get_scene(scene.id),
            review=review,
            scene=scene,
        )

    def confirm_amendment(
        self,
        amendment_id: str,
        *,
        expected_scene_id: str,
    ) -> AmendmentCommitResult:
        amendment = self.amendments.load(amendment_id)
        if amendment.scene_id != expected_scene_id:
            raise DomainError("amendment does not match scene")
        draft = self.drafts.load(amendment.scene_id)
        if (
            draft.status != "amendment_required"
            or draft.amendment_id != amendment.id
            or draft.revision != amendment.draft_revision
        ):
            raise DomainError("amendment no longer matches the scene draft")
        scene = self._scene_from_draft(draft)
        result = EventCommitService(self.root).commit_scene_amendment(
            scene,
            amendment,
        )
        saved = draft.model_copy(update={"status": "saved"})
        self.drafts.save(saved)
        self.amendments.save(result.amendment)
        return result

    def export_markdown(self) -> ManuscriptExport:
        snapshot = self.projects.load()
        sections = [f"# {snapshot.project.title}"]
        for scene in self.scenes.list_scenes():
            sections.append(f"## {scene.title}\n\n{scene.body}")
        return ManuscriptExport(
            filename=f"{snapshot.project.id}-manuscript.md",
            markdown="\n\n".join(sections).rstrip() + "\n",
        )

    @staticmethod
    def _scene_from_draft(draft: SceneDraft) -> Scene:
        return Scene(
            id=draft.id,
            project_id=draft.project_id,
            sequence=draft.sequence,
            chapter_id=draft.chapter_id,
            title=draft.title,
            body=draft.body,
            source_event_ids=draft.source_event_ids,
            version=draft.base_scene_version + 1,
        )
