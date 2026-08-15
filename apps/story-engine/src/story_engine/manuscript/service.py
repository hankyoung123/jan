import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from story_engine.domain.message import ModelMessageContext
from story_engine.domain.models import ReviewIssue, ReviewResult
from story_engine.manuscript.models import (
    EditorReviewProposal,
    ManuscriptContext,
    ManuscriptExport,
    ManuscriptGenerationRequest,
    ManuscriptReviewOutput,
    ManuscriptSourceCandidate,
    ManuscriptSourceManifest,
    ManuscriptWritingIntent,
    ProjectCreativeContext,
    Scene,
    SceneDraft,
    SceneMutationResult,
    SceneUpdateRequest,
    SourceSelectionResult,
    WriterOutput,
)
from story_engine.manuscript.source import (
    CandidateSourceBuilder,
    ManuscriptContextBuilder,
    SourceSelectionNotReadyError,
    SourceSelectionValidator,
)
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
    "Immutable protocol: FACTS determine what happened. CONTINUITY is only for "
    "transitions and must never add facts. INTENT only controls expression. Use "
    "only the supplied Manuscript Context; preserve viewpoint permissions; never "
    "use Director Instructions as facts; never modify or invent simulation history; "
    "return only Markdown with exactly one level-one title on the first line, one "
    "blank line, and the scene prose. Do not use a code fence."
)
EDITOR_PROTOCOL = (
    "Immutable protocol: verify prose only against the supplied Source Manifest "
    "and Fact Context. CONTINUITY, INTENT, and writer selection reasons are not "
    "facts and are not supplied. Never modify simulation history; return exactly "
    "the requested JSON schema."
)


class VersionConflictError(RuntimeError):
    """Raised when a scene changed since the client loaded it."""


class ManuscriptAgent(Protocol):
    async def select_source(
        self,
        candidates: tuple[ManuscriptSourceCandidate, ...],
        *,
        project: ProjectCreativeContext,
        chapter_id: str,
        viewpoint_actor_id: str | None,
        target_words: int | None,
        instruction: str | None,
    ) -> SourceSelectionResult: ...

    async def generate(self, context: ManuscriptContext) -> WriterOutput: ...

    async def review(
        self,
        context: ManuscriptContext,
        *,
        title: str,
        body: str,
    ) -> ManuscriptReviewOutput: ...


def _writer_context(context: ManuscriptContext) -> str:
    visible_source = context.source.model_copy(
        update={
            "event_ids": tuple(event.event_id for event in context.facts.events),
            "memory_ids": tuple(memory.record_id for memory in context.facts.memories),
        }
    )
    return json.dumps(
        {
            "FACTS": {
                "source_manifest": visible_source.model_dump(mode="json"),
                "project": context.facts.project.model_dump(mode="json"),
                "resolved_events": [
                    event.model_dump(mode="json") for event in context.facts.events
                ],
                "viewpoint_allowed_memory": [
                    record.model_dump(mode="json") for record in context.facts.memories
                ],
                "historical_wiki": context.facts.wiki_context,
            },
            "CONTINUITY": {
                "previous_scene_title": context.continuity.previous_scene_title,
                "previous_scene_excerpt": context.continuity.previous_scene_excerpt,
            },
            "INTENT": context.intent.model_dump(mode="json"),
        },
        ensure_ascii=False,
        default=str,
    )


def _editor_context(context: ManuscriptContext) -> str:
    visible_source = context.source.model_copy(
        update={
            "event_ids": tuple(event.event_id for event in context.facts.events),
            "memory_ids": tuple(memory.record_id for memory in context.facts.memories),
        }
    )
    return json.dumps(
        {
            "SOURCE_MANIFEST": visible_source.model_dump(mode="json"),
            "FACTS": {
                "project": context.facts.project.model_dump(mode="json"),
                "resolved_events": [
                    event.model_dump(mode="json") for event in context.facts.events
                ],
                "viewpoint_allowed_memory": [
                    record.model_dump(mode="json") for record in context.facts.memories
                ],
                "historical_wiki": context.facts.wiki_context,
            },
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
    """Use the existing writer/editor profiles over the unified context."""

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    async def select_source(
        self,
        candidates: tuple[ManuscriptSourceCandidate, ...],
        *,
        project: ProjectCreativeContext,
        chapter_id: str,
        viewpoint_actor_id: str | None,
        target_words: int | None,
        instruction: str | None,
    ) -> SourceSelectionResult:
        profile = self.gateway.registry.get_profile("writer")
        candidate_payload = [
            candidate.model_dump(
                mode="json",
                exclude={"event_ids", "wiki_version_id", "available_viewpoint_ids"},
            )
            for candidate in candidates
        ]
        task_context = (
            "Choose a contiguous range from the supplied deterministic candidates. "
            "Do not invent source IDs, cross branches, or choose covered ranges. "
            "Return not_ready when no candidate can support a coherent scene. "
            f"Project: {project.model_dump_json()}. Chapter: {chapter_id}. "
            f"Viewpoint: {viewpoint_actor_id or 'automatic'}. Target words: "
            f"{target_words or 'provider default'}. Instruction: {instruction or ''}. "
            f"Candidates: {json.dumps(candidate_payload, ensure_ascii=False)}"
        )
        response = await self.gateway.complete(
            ModelRequest(
                profile_id="writer",
                task_type="writer",
                messages=(
                    Message(role="system", content=WRITER_PROTOCOL),
                    Message(role="system", content=profile.default_system_prompt),
                    Message(role="user", content=task_context),
                ),
                output_schema=json.dumps(
                    SourceSelectionResult.model_json_schema(), ensure_ascii=False
                ),
                max_output_tokens=None,
                output_token_limit="provider",
                timeout_seconds=profile.timeout_seconds,
                temperature=profile.temperature,
                reasoning_effort=profile.reasoning_effort,
            ),
            context=ModelMessageContext(
                project_id=project.project_id,
                agent_name=profile.name,
                task_label="正文来源选择",
                branch_id=candidates[0].branch_id if candidates else None,
                step=candidates[-1].to_step if candidates else None,
                stage="writer",
            ),
        )
        return SourceSelectionResult.model_validate(response.parsed_output)

    async def generate(self, context: ManuscriptContext) -> WriterOutput:
        profile = self.gateway.registry.get_profile("writer")
        response = await self.gateway.complete(
            ModelRequest(
                profile_id="writer",
                task_type="writer",
                messages=(
                    Message(role="system", content=WRITER_PROTOCOL),
                    Message(role="system", content=profile.default_system_prompt),
                    Message(
                        role="user",
                        content=(
                            "Write one scene from the supplied Manuscript Context. "
                            "Every concrete event and outcome must be supported by "
                            "FACTS. Sensory description, dialogue organization, "
                            "pacing, and style are allowed. FACTS, CONTINUITY, and "
                            "INTENT are explicitly separated.\n\n"
                            f"Manuscript Context: {_writer_context(context)}"
                        ),
                    ),
                ),
                max_output_tokens=None,
                output_token_limit="provider",
                first_content_timeout_seconds=WRITER_FIRST_CONTENT_TIMEOUT_SECONDS,
                timeout_seconds=profile.timeout_seconds,
                temperature=profile.temperature,
                reasoning_effort=profile.reasoning_effort,
            ),
            context=ModelMessageContext(
                project_id=context.source.project_id,
                agent_name=profile.name,
                task_label="正文生成",
                branch_id=context.source.branch_id,
                step=context.source.to_step,
                stage="writer",
            ),
        )
        return parse_writer_markdown(response.content)

    async def review(
        self,
        context: ManuscriptContext,
        *,
        title: str,
        body: str,
    ) -> ManuscriptReviewOutput:
        profile = self.gateway.registry.get_profile("editor")
        response = await self.gateway.complete(
            ModelRequest(
                profile_id="editor",
                task_type="editor",
                messages=(
                    Message(role="system", content=EDITOR_PROTOCOL),
                    Message(role="system", content=profile.default_system_prompt),
                    Message(
                        role="user",
                        content=(
                            "Compare the prose to SOURCE_MANIFEST and FACTS. "
                            "List each concrete fact asserted by the prose that the "
                            "source does not support. Pure description, simile, "
                            "rhythm, and wording are not unsupported facts. A passing "
                            "review has no issues. Never propose changing simulation "
                            "history. Return only a concise summary and issue strings. "
                            f"Review context: {_editor_context(context)}. "
                            f"Scene title: {title}. Prose: {body}"
                        ),
                    ),
                ),
                output_schema=json.dumps(
                    EditorReviewProposal.model_json_schema(), ensure_ascii=False
                ),
                max_output_tokens=profile.max_output_tokens,
                output_token_limit=(
                    "provider" if profile.max_output_tokens is None else "profile"
                ),
                timeout_seconds=profile.timeout_seconds,
                temperature=profile.temperature,
                reasoning_effort=profile.reasoning_effort,
            ),
            context=ModelMessageContext(
                project_id=context.source.project_id,
                agent_name=profile.name,
                task_label="正文审校",
                branch_id=context.source.branch_id,
                step=context.source.to_step,
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
    def __init__(self, root: Path, branch_id: str, *, agent: ManuscriptAgent) -> None:
        self.root = root
        self.branch_id = branch_id
        self.agent = agent
        self.projects = ProjectStore(root)
        self.branches = BranchStore(root)
        branch = self.branches.load(branch_id)
        snapshot = self.projects.load()
        if branch.project_id != snapshot.project.id:
            raise ValueError("branch belongs to another project")
        self.candidates = CandidateSourceBuilder(root, branch_id)
        self.selector = SourceSelectionValidator(self.candidates)
        self.contexts = ManuscriptContextBuilder(root, branch_id)
        self.scenes = SceneStore(root, branch_id)
        self.drafts = SceneDraftStore(root, branch_id)

    def list_sources(self) -> tuple[ManuscriptSourceCandidate, ...]:
        return self.candidates.list_candidates()

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

    async def resolve_selection(
        self,
        request: ManuscriptGenerationRequest,
    ) -> tuple[ManuscriptSourceManifest, str]:
        writer_result: SourceSelectionResult | None = None
        if request.source.mode == "writer":
            candidates = self.candidates.list_candidates()
            if not candidates:
                raise SourceSelectionNotReadyError("没有可供写手选择的连续模拟来源")
            writer_result = await self.agent.select_source(
                candidates,
                project=self._creative_context(
                    self.branches.load(self.branch_id).content_locale
                ),
                chapter_id=request.chapter_id,
                viewpoint_actor_id=request.viewpoint_actor_id,
                target_words=request.target_words,
                instruction=request.instruction,
            )
        return self.selector.resolve(
            request.source,
            viewpoint_actor_id=request.viewpoint_actor_id,
            writer_result=writer_result,
        )

    async def generate(
        self,
        request: ManuscriptGenerationRequest,
        *,
        before_apply: Callable[[], None] | None = None,
    ) -> SceneDraft:
        wiki = WikiStore(self.root, self.branch_id).view()
        if wiki.stale:
            detail = wiki.degradation_reason or "Wiki requires rebuilding"
            raise ValueError(f"cannot generate manuscript from stale Wiki: {detail}")
        source, selection_reason = await self.resolve_selection(request)
        writing_intent = ManuscriptWritingIntent(
            chapter_id=request.chapter_id,
            target_words=request.target_words,
            instruction=request.instruction,
        )
        context, context_manifest = self.contexts.build_context(
            source,
            intent=writing_intent,
            selection_reason=selection_reason,
        )
        output = await self.agent.generate(context)
        scene_id, sequence = self.scenes.next_identifier()
        draft = SceneDraft(
            id=scene_id,
            project_id=source.project_id,
            branch_id=source.branch_id,
            sequence=sequence,
            chapter_id=request.chapter_id,
            title=output.title,
            body=output.body,
            source=source,
            context_manifest=context_manifest,
        )
        review = await self.agent.review(context, title=output.title, body=output.body)
        grounded = review.review.passed and not review.unsupported_facts
        reviewed = draft.model_copy(
            update={
                "review": review,
                "status": "reviewed" if grounded else "needs_revision",
            }
        )
        self.drafts.save(
            reviewed,
            overwrite=False,
            precondition=before_apply,
        )
        return reviewed

    def _context_for_draft(self, draft: SceneDraft) -> ManuscriptContext:
        context, _ = self.contexts.build_context(
            draft.source,
            intent=ManuscriptWritingIntent(
                chapter_id=draft.chapter_id,
                target_words=draft.context_manifest.target_words,
                instruction=draft.context_manifest.instruction,
            ),
            selection_reason=draft.context_manifest.selection_reason,
        )
        if context.source != draft.source:
            raise ValueError("scene source lineage changed")
        return context

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
        context = self._context_for_draft(updated)
        review = updated.review
        if content_changed or review is None:
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
            source=draft.source,
            context_manifest=draft.context_manifest,
            version=draft.base_scene_version + 1,
        )

    @staticmethod
    def _draft_from_scene(scene: Scene) -> SceneDraft:
        return SceneDraft(
            **scene.model_dump(exclude={"version"}),
            base_scene_version=scene.version,
            status="saved",
        )
