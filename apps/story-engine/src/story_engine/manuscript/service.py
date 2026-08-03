import json
from pathlib import Path
from typing import Protocol

from story_engine.domain.narrative import (
    NarrativeContext,
    NarrativeSource,
    NarrativeSourceSummary,
)
from story_engine.manuscript.models import (
    ManuscriptExport,
    ManuscriptReviewOutput,
    ProjectCreativeContext,
    Scene,
    SceneDraft,
    SceneMutationResult,
    SceneUpdateRequest,
    WriterOutput,
)
from story_engine.manuscript.source import NarrativeSourceReader
from story_engine.models.contracts import Message, ModelRequest
from story_engine.models.gateway import ModelGateway
from story_engine.persistence.branch_store import BranchStore
from story_engine.workspace.project_store import ProjectStore
from story_engine.workspace.scene_store import SceneDraftStore, SceneStore


class VersionConflictError(RuntimeError):
    """Raised when a scene changed since the client loaded it."""


class ManuscriptAgent(Protocol):
    async def generate(
        self,
        source: NarrativeContext,
        *,
        project: ProjectCreativeContext,
    ) -> WriterOutput: ...

    async def review(
        self,
        source: NarrativeContext,
        *,
        title: str,
        body: str,
    ) -> ManuscriptReviewOutput: ...


def _source_context(source: NarrativeContext) -> str:
    return json.dumps(
        {
            "lineage": source.source.model_dump(mode="json"),
            "resolved_events": [
                event.model_dump(mode="json") for event in source.events
            ],
            "game_master_memory": [
                record.model_dump(mode="json")
                for record in source.game_master_memories
            ],
            "selected_viewpoint_memory": [
                record.model_dump(mode="json")
                for record in source.viewpoint_memories
            ],
            "world_wiki": source.world_wiki_context,
        },
        ensure_ascii=False,
        default=str,
    )


class GatewayManuscriptAgent:
    """Run Writer and Editor directly over a privacy-filtered runtime source."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        writer_profile_id: str = "writer",
        editor_profile_id: str = "editor",
    ) -> None:
        self.gateway = gateway
        self.writer_profile_id = writer_profile_id
        self.editor_profile_id = editor_profile_id

    async def generate(
        self,
        source: NarrativeContext,
        *,
        project: ProjectCreativeContext,
    ) -> WriterOutput:
        viewpoint = source.source.viewpoint_actor_id or "omniscient"
        prompt = (
            "You are the Story Engine Writer. Turn the supplied resolved simulation "
            "history into one novel scene. Every concrete world fact and outcome must "
            "be supported by the supplied source. Sensory description and stylistic "
            "language are allowed, but do not invent causes, objects, locations, "
            "discoveries, or outcomes. The selected viewpoint memory is the only "
            "character-private memory you may use. When the viewpoint is omniscient, "
            "no character-private memory is supplied or permitted. Return exactly the "
            "requested JSON schema. "
            f"Project: {project.model_dump_json()}. Viewpoint: {viewpoint}. "
            f"Runtime source: {_source_context(source)}"
        )
        response = await self.gateway.complete(
            ModelRequest(
                profile_id=self.writer_profile_id,
                task_type="writer",
                messages=(Message(role="system", content=prompt),),
                output_schema=json.dumps(
                    WriterOutput.model_json_schema(),
                    ensure_ascii=False,
                ),
                max_output_tokens=4096,
                timeout_seconds=60,
                temperature=0.7,
            )
        )
        return WriterOutput.model_validate(response.parsed_output)

    async def review(
        self,
        source: NarrativeContext,
        *,
        title: str,
        body: str,
    ) -> ManuscriptReviewOutput:
        prompt = (
            "You are the Story Engine manuscript Editor. Compare the prose to the "
            "supplied runtime source. List each concrete fact asserted by the prose "
            "that the source does not support in unsupported_facts. Pure description, "
            "simile, rhythm, and wording are not unsupported facts. A passing review "
            "must have no unsupported_facts. Never propose changing simulation "
            "history. Return exactly the requested JSON schema. "
            f"Runtime source: {_source_context(source)}. "
            f"Scene title: {title}. Prose: {body}"
        )
        response = await self.gateway.complete(
            ModelRequest(
                profile_id=self.editor_profile_id,
                task_type="editor",
                messages=(Message(role="system", content=prompt),),
                output_schema=json.dumps(
                    ManuscriptReviewOutput.model_json_schema(),
                    ensure_ascii=False,
                ),
                max_output_tokens=2048,
                timeout_seconds=60,
                temperature=0.1,
            )
        )
        return ManuscriptReviewOutput.model_validate(response.parsed_output)


class ManuscriptService:
    def __init__(
        self,
        root: Path,
        branch_id: str,
        *,
        agent: ManuscriptAgent,
    ) -> None:
        self.root = root
        self.branch_id = branch_id
        self.agent = agent
        self.projects = ProjectStore(root)
        self.branches = BranchStore(root)
        branch = self.branches.load(branch_id)
        snapshot = self.projects.load()
        if branch.project_id != snapshot.project.id:
            raise ValueError("branch belongs to another project")
        self.reader = NarrativeSourceReader(root)
        self.scenes = SceneStore(root, branch_id)
        self.drafts = SceneDraftStore(root, branch_id)

    def list_sources(
        self,
        *,
        after_step: int | None = None,
    ) -> tuple[NarrativeSourceSummary, ...]:
        return self.reader.list_sources(self.branch_id, after_step=after_step)

    def _creative_context(self, content_locale: str) -> ProjectCreativeContext:
        project = self.projects.load().project
        return ProjectCreativeContext(
            project_id=project.id,
            title=project.title,
            genre=project.genre,
            theme=project.theme,
            tone=project.tone,
            content_locale=content_locale,
        )

    async def generate_scene(
        self,
        *,
        checkpoint_id: str,
        from_step: int,
        to_step: int,
        chapter_id: str,
        viewpoint_actor_id: str | None,
    ) -> SceneDraft:
        source = self.reader.build_source(
            branch_id=self.branch_id,
            checkpoint_id=checkpoint_id,
            from_step=from_step,
            to_step=to_step,
            viewpoint_actor_id=viewpoint_actor_id,
        )
        context = self.reader.load_source(source)
        output = await self.agent.generate(
            context,
            project=self._creative_context(source.content_locale),
        )
        review = await self.agent.review(
            context,
            title=output.title,
            body=output.body,
        )
        scene_id, sequence = self.scenes.next_identifier()
        grounded = review.review.passed and not review.unsupported_facts
        draft = SceneDraft(
            id=scene_id,
            project_id=source.project_id,
            branch_id=source.branch_id,
            sequence=sequence,
            chapter_id=chapter_id,
            title=output.title,
            body=output.body,
            source_checkpoint_id=source.checkpoint_id,
            source_from_step=source.from_step,
            source_to_step=source.to_step,
            source_event_ids=source.event_ids,
            source_memory_ids=source.memory_record_ids,
            viewpoint_actor_id=source.viewpoint_actor_id,
            review=review,
            status="reviewed" if grounded else "needs_revision",
        )
        self.drafts.save(draft, overwrite=False)
        return draft

    def _source_from_draft(self, draft: SceneDraft) -> NarrativeSource:
        source = self.reader.build_source(
            branch_id=draft.branch_id,
            checkpoint_id=draft.source_checkpoint_id,
            from_step=draft.source_from_step,
            to_step=draft.source_to_step,
            viewpoint_actor_id=draft.viewpoint_actor_id,
        )
        if (
            source.event_ids != draft.source_event_ids
            or source.memory_record_ids != draft.source_memory_ids
        ):
            raise ValueError("scene source lineage changed")
        return source

    def get_scene(self, scene_id: str) -> SceneDraft:
        try:
            return self.drafts.load(scene_id)
        except FileNotFoundError:
            scene = self.scenes.load(scene_id)
            return self._draft_from_scene(scene)

    def list_scenes(self) -> tuple[SceneDraft, ...]:
        drafts = {draft.id: draft for draft in self.drafts.list_drafts()}
        for scene in self.scenes.list_scenes():
            drafts.setdefault(scene.id, self._draft_from_scene(scene))
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
        source = self._source_from_draft(updated)
        context = self.reader.load_source(source)
        review = await self.agent.review(
            context,
            title=updated.title,
            body=updated.body,
        )
        if not review.review.passed or review.unsupported_facts:
            rejected = updated.model_copy(
                update={"review": review, "status": "needs_revision"}
            )
            self.drafts.save(rejected)
            return SceneMutationResult(
                status="rejected",
                draft=rejected,
                review=review,
            )

        reviewed = updated.model_copy(
            update={"review": review, "status": "reviewed"}
        )
        scene = self._scene_from_draft(reviewed)
        try:
            current = self.scenes.load(scene.id)
        except FileNotFoundError:
            current = None
        if reviewed.base_scene_version == 0 and current is not None:
            raise VersionConflictError("scene already exists")
        if reviewed.base_scene_version > 0 and (
            current is None or current.version != reviewed.base_scene_version
        ):
            raise VersionConflictError("scene version changed")
        self.scenes.save(scene, overwrite=current is not None)
        saved = reviewed.model_copy(
            update={"base_scene_version": scene.version, "status": "saved"}
        )
        self.drafts.save(saved)
        return SceneMutationResult(
            status="saved",
            draft=saved,
            review=review,
            scene=scene,
        )

    def export_markdown(self) -> ManuscriptExport:
        project = self.projects.load().project
        branch = self.branches.load(self.branch_id)
        sections = [f"# {project.title}"]
        for scene in self.scenes.list_scenes():
            sections.append(f"## {scene.title}\n\n{scene.body}")
        return ManuscriptExport(
            project_id=project.id,
            branch_id=self.branch_id,
            checkpoint_id=branch.head_checkpoint_id,
            filename=f"{project.id}-{self.branch_id}-manuscript.md",
            markdown="\n\n".join(sections).rstrip() + "\n",
        )

    @staticmethod
    def _scene_from_draft(draft: SceneDraft) -> Scene:
        return Scene(
            id=draft.id,
            project_id=draft.project_id,
            branch_id=draft.branch_id,
            sequence=draft.sequence,
            chapter_id=draft.chapter_id,
            title=draft.title,
            body=draft.body,
            source_checkpoint_id=draft.source_checkpoint_id,
            source_from_step=draft.source_from_step,
            source_to_step=draft.source_to_step,
            source_event_ids=draft.source_event_ids,
            source_memory_ids=draft.source_memory_ids,
            viewpoint_actor_id=draft.viewpoint_actor_id,
            version=draft.base_scene_version + 1,
        )

    @staticmethod
    def _draft_from_scene(scene: Scene) -> SceneDraft:
        return SceneDraft(
            **scene.model_dump(exclude={"version"}),
            base_scene_version=scene.version,
            status="saved",
        )
