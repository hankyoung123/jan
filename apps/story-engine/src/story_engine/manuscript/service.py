import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from story_engine.domain.message import ModelMessageContext
from story_engine.domain.models import ReviewIssue, ReviewResult
from story_engine.domain.narrative import (
    EditorContext,
    NarrativeSource,
    NarrativeSourceSummary,
    WriterContext,
)
from story_engine.manuscript.models import (
    EditorReviewProposal,
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
from story_engine.models.errors import StructuredOutputError
from story_engine.models.gateway import ModelGateway
from story_engine.persistence.branch_store import BranchStore
from story_engine.wiki.store import WikiStore
from story_engine.workspace.project_store import ProjectStore
from story_engine.workspace.scene_store import SceneDraftStore, SceneStore
from story_engine.workspace.transaction import AtomicBatch

WRITER_FIRST_CONTENT_TIMEOUT_SECONDS = 300
WRITER_PROTOCOL = (
    "Immutable protocol: use only the supplied Writer Context; preserve viewpoint "
    "permissions; never use Director Instructions as facts; never modify or invent "
    "simulation history; return only Markdown with exactly one level-one title on "
    "the first line, one blank line, and the scene prose. Do not use a code fence."
)
EDITOR_PROTOCOL = (
    "Immutable protocol: verify prose only against the supplied Editor Context; "
    "never modify simulation history; return exactly the requested JSON schema."
)


class VersionConflictError(RuntimeError):
    """Raised when a scene changed since the client loaded it."""


class ManuscriptAgent(Protocol):
    async def generate(
        self,
        source: WriterContext,
        *,
        project: ProjectCreativeContext,
    ) -> WriterOutput: ...

    async def review(
        self,
        source: EditorContext,
        *,
        title: str,
        body: str,
    ) -> ManuscriptReviewOutput: ...


def _writer_source_context(source: WriterContext) -> str:
    return json.dumps(
        {
            "lineage": {
                **source.source.model_dump(
                    mode="json",
                    exclude={"event_ids", "memory_record_ids"},
                ),
                "event_ids": [event.event_id for event in source.events],
                "memory_record_ids": [
                    record.record_id for record in source.viewpoint_memories
                ],
            },
            "resolved_events": [
                event.model_dump(mode="json") for event in source.events
            ],
            "selected_viewpoint_memory": [
                record.model_dump(mode="json") for record in source.viewpoint_memories
            ],
            "world_wiki": source.world_wiki_context,
        },
        ensure_ascii=False,
        default=str,
    )


def _editor_source_context(source: EditorContext) -> str:
    return json.dumps(
        {
            "lineage": source.source.model_dump(mode="json"),
            "resolved_events": [
                event.model_dump(mode="json") for event in source.events
            ],
            "factual_memory": [
                record.model_dump(mode="json") for record in source.memories
            ],
            "historical_wiki": source.world_wiki_context,
        },
        ensure_ascii=False,
        default=str,
    )


def parse_writer_markdown(content: str) -> WriterOutput:
    markdown = content.strip()
    lines = markdown.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise StructuredOutputError(
            "Writer output must start with a level-one Markdown title"
        )
    title = lines[0].removeprefix("# ").strip()
    body = "\n".join(lines[1:]).strip()
    try:
        return WriterOutput(title=title, body=body)
    except ValidationError as error:
        raise StructuredOutputError(
            "Writer Markdown must contain a non-empty title and body"
        ) from error


class GatewayManuscriptAgent:
    """Run Writer and Editor directly over a privacy-filtered runtime source."""

    def __init__(
        self,
        gateway: ModelGateway,
    ) -> None:
        self.gateway = gateway

    async def generate(
        self,
        source: WriterContext,
        *,
        project: ProjectCreativeContext,
    ) -> WriterOutput:
        viewpoint = source.source.viewpoint_actor_id or "automatic_primary_viewpoint"
        task_context = (
            "Turn the supplied resolved simulation "
            "history into one novel scene. Every concrete world fact and outcome must "
            "be supported by the supplied source. Sensory description and stylistic "
            "language are allowed, but do not invent causes, objects, locations, "
            "discoveries, or outcomes. The selected viewpoint memory is the only "
            "character-private memory you may use. When the viewpoint is automatic, "
            "no character-private memory is supplied or permitted. This is automatic "
            "primary-viewpoint selection, not an omniscient narration mode. "
            f"Project: {project.model_dump_json()}. Viewpoint: {viewpoint}. "
            f"Runtime source: {_writer_source_context(source)}"
        )
        writer_profile = self.gateway.registry.get_profile("writer")
        response = await self.gateway.complete(
            ModelRequest(
                profile_id="writer",
                task_type="writer",
                messages=(
                    Message(role="system", content=WRITER_PROTOCOL),
                    Message(
                        role="system",
                        content=writer_profile.default_system_prompt,
                    ),
                    Message(role="user", content=task_context),
                ),
                max_output_tokens=writer_profile.max_output_tokens,
                output_token_limit=(
                    "provider"
                    if writer_profile.max_output_tokens is None
                    else "profile"
                ),
                first_content_timeout_seconds=WRITER_FIRST_CONTENT_TIMEOUT_SECONDS,
                timeout_seconds=writer_profile.timeout_seconds,
                temperature=writer_profile.temperature,
                reasoning_effort=writer_profile.reasoning_effort,
            ),
            context=ModelMessageContext(
                project_id=source.source.project_id,
                agent_name=writer_profile.name,
                task_label="正文生成",
                branch_id=source.source.branch_id,
                step=source.source.to_step,
                stage="writer",
            ),
        )
        return parse_writer_markdown(response.content)

    async def review(
        self,
        source: EditorContext,
        *,
        title: str,
        body: str,
    ) -> ManuscriptReviewOutput:
        task_context = (
            "Compare the prose to the "
            "supplied runtime source. List each concrete fact asserted by the prose "
            "that the source does not support in issues. Pure description, "
            "simile, rhythm, and wording are not unsupported facts. A passing review "
            "must have no issues. Never propose changing simulation "
            "history. Return only a concise summary and the issue strings. "
            f"Runtime source: {_editor_source_context(source)}. "
            f"Scene title: {title}. Prose: {body}"
        )
        editor_profile = self.gateway.registry.get_profile("editor")
        response = await self.gateway.complete(
            ModelRequest(
                profile_id="editor",
                task_type="editor",
                messages=(
                    Message(role="system", content=EDITOR_PROTOCOL),
                    Message(
                        role="system",
                        content=editor_profile.default_system_prompt,
                    ),
                    Message(role="user", content=task_context),
                ),
                output_schema=json.dumps(
                    EditorReviewProposal.model_json_schema(),
                    ensure_ascii=False,
                ),
                max_output_tokens=editor_profile.max_output_tokens,
                output_token_limit=(
                    "provider"
                    if editor_profile.max_output_tokens is None
                    else "profile"
                ),
                timeout_seconds=editor_profile.timeout_seconds,
                temperature=editor_profile.temperature,
                reasoning_effort=editor_profile.reasoning_effort,
            ),
            context=ModelMessageContext(
                project_id=source.source.project_id,
                agent_name=editor_profile.name,
                task_label="正文审校",
                branch_id=source.source.branch_id,
                step=source.source.to_step,
                stage="editor",
            ),
        )
        proposal = EditorReviewProposal.model_validate(response.parsed_output)
        issues = tuple(
            dict.fromkeys(item.strip() for item in proposal.issues if item.strip())
        )
        return ManuscriptReviewOutput(
            review=ReviewResult(
                mode="manuscript_review",
                passed=not issues,
                summary=proposal.summary,
                issues=tuple(
                    ReviewIssue(
                        code="unsupported_fact",
                        message=issue,
                        severity="blocking",
                    )
                    for issue in issues
                ),
            ),
            unsupported_facts=issues,
        )


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
        wiki = WikiStore(self.root, self.branch_id).view()
        if wiki.stale:
            detail = wiki.degradation_reason or "Wiki requires rebuilding"
            raise ValueError(f"cannot generate manuscript from stale Wiki: {detail}")
        source = self.reader.build_source(
            branch_id=self.branch_id,
            checkpoint_id=checkpoint_id,
            from_step=from_step,
            to_step=to_step,
            viewpoint_actor_id=viewpoint_actor_id,
        )
        writer_context = self.reader.load_writer_context(source)
        output = await self.agent.generate(
            writer_context,
            project=self._creative_context(source.content_locale),
        )
        scene_id, sequence = self.scenes.next_identifier()
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
            source_wiki_branch_id=source.wiki_branch_id,
            source_wiki_version_id=source.wiki_version_id,
            viewpoint_actor_id=source.viewpoint_actor_id,
        )
        self.drafts.save(draft, overwrite=False)
        editor_context = self.reader.load_editor_context(source)
        review = await self.agent.review(
            editor_context,
            title=output.title,
            body=output.body,
        )
        grounded = review.review.passed and not review.unsupported_facts
        reviewed = draft.model_copy(
            update={
                "review": review,
                "status": "reviewed" if grounded else "needs_revision",
            }
        )
        self.drafts.save(reviewed)
        return reviewed

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
            or source.wiki_branch_id != draft.source_wiki_branch_id
            or source.wiki_version_id != draft.source_wiki_version_id
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
        content_changed = draft.title != request.title or draft.body != request.body
        updated = (
            draft.with_content(title=request.title, body=request.body)
            if content_changed
            else draft
        )
        source = self._source_from_draft(updated)
        review = updated.review
        if content_changed or review is None:
            context = self.reader.load_editor_context(source)
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

        reviewed = updated.model_copy(update={"review": review, "status": "reviewed"})
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
        saved = reviewed.model_copy(
            update={"base_scene_version": scene.version, "status": "saved"}
        )
        scene_path, scene_content = self.scenes.prepare(scene)
        draft_path, draft_content = self.drafts.prepare(saved)
        batch = AtomicBatch(self.root)
        batch.add(
            scene_path.relative_to(self.root).as_posix(),
            scene_content,
            overwrite=current is not None,
        )
        batch.add(
            draft_path.relative_to(self.root).as_posix(),
            draft_content,
            overwrite=True,
        )
        batch.commit()
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
        current_chapter: str | None = None
        for scene in self.scenes.list_scenes():
            if scene.chapter_id != current_chapter:
                current_chapter = scene.chapter_id
                sections.append(f"## {current_chapter}")
            sections.append(f"### {scene.title}\n\n{scene.body}")
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
            source_wiki_branch_id=draft.source_wiki_branch_id,
            source_wiki_version_id=draft.source_wiki_version_id,
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
