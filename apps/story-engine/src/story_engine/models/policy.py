from pathlib import Path
from threading import RLock

from pydantic import ValidationError

from story_engine.domain.model_policy import ProjectModelPolicy, ProjectModelPolicyPatch
from story_engine.models.contracts import ModelProfile
from story_engine.models.errors import ModelConfigurationError, ProfileNotFoundError
from story_engine.models.registry import ProfileRegistry
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import dump_json_envelope, load_json_envelope
from story_engine.workspace.project_store import ProjectStore


class ProjectModelPolicyStore:
    def __init__(self, project_root: Path, registry: ProfileRegistry) -> None:
        self.project_root = project_root
        self.registry = registry
        self.path = project_root / ".story-engine/config/model-policy.md"
        self._lock = RLock()

    def load(self) -> ProjectModelPolicy:
        with self._lock:
            if not self.path.exists():
                policy = ProjectModelPolicy()
            else:
                try:
                    policy = ProjectModelPolicy.model_validate(
                        load_json_envelope(
                            self.path,
                            schema="story-engine/model-policy/v1",
                        )
                    )
                except (OSError, ValueError, ValidationError) as error:
                    raise ModelConfigurationError(
                        "project model policy is invalid"
                    ) from error
            return self._validate_assignments(policy)

    def save(self, policy: ProjectModelPolicy) -> ProjectModelPolicy:
        with self._lock:
            try:
                validated = ProjectModelPolicy.model_validate(
                    policy.model_dump(mode="json")
                )
            except ValidationError as error:
                raise ModelConfigurationError(
                    "project model policy is invalid"
                ) from error
            validated = self._validate_assignments(validated)
            content = dump_json_envelope(
                schema="story-engine/model-policy/v1",
                title="Project Model Policy",
                payload=validated.model_dump(mode="json"),
            )
            atomic_write_text(self.path, content)
            return validated

    def patch(self, patch: ProjectModelPolicyPatch) -> ProjectModelPolicy:
        current = self.load()
        task_profile_ids = dict(current.task_profile_ids)
        if patch.task_profile_ids is not None:
            task_profile_ids.update(patch.task_profile_ids)
        agent_profile_ids = dict(current.agent_profile_ids)
        if patch.agent_profile_ids is not None:
            for agent_id, profile_id in patch.agent_profile_ids.items():
                if profile_id is None:
                    agent_profile_ids.pop(agent_id, None)
                else:
                    agent_profile_ids[agent_id] = profile_id
        return self.save(
            ProjectModelPolicy(
                task_profile_ids=task_profile_ids,
                agent_profile_ids=agent_profile_ids,
            )
        )

    def _validate_assignments(
        self,
        policy: ProjectModelPolicy,
    ) -> ProjectModelPolicy:
        try:
            project = ProjectStore(self.project_root).load()
        except (OSError, ValueError) as error:
            raise ModelConfigurationError(
                "project model policy project is invalid"
            ) from error

        character_ids = {character.id for character in project.characters}
        unknown_agents = set(policy.agent_profile_ids) - character_ids
        if unknown_agents:
            raise ModelConfigurationError(
                f"model policy contains unknown character IDs: {sorted(unknown_agents)}"
            )

        for task_type, profile_id in policy.task_profile_ids.items():
            profile = self._profile(profile_id)
            if profile.task_type != task_type:
                raise ModelConfigurationError(
                    f"profile {profile_id!r} requires task {task_type!r}, "
                    f"but is configured for {profile.task_type!r}"
                )
        for agent_id, profile_id in policy.agent_profile_ids.items():
            profile = self._profile(profile_id)
            if profile.task_type != "actor":
                raise ModelConfigurationError(
                    f"agent {agent_id!r} requires task 'actor', "
                    f"but profile {profile_id!r} is configured for "
                    f"{profile.task_type!r}"
                )
        return policy

    def _profile(self, profile_id: str) -> ModelProfile:
        try:
            return self.registry.get_profile(profile_id)
        except ProfileNotFoundError as error:
            raise ModelConfigurationError(
                f"model policy profile {profile_id!r} does not exist"
            ) from error
