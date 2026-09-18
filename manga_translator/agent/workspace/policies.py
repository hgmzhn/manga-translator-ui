"""Style policy registration, historical reads, and explicit updates."""

from __future__ import annotations

from copy import deepcopy

from pydantic import ValidationError

from ..domain.tool_models import StylePolicyPatch, ToolContext, ToolError
from .common import _patch


class WorkspacePolicies:
    """Use Workspace's policy state, lock, and command replay coordination."""
    def register_style_policy(
        self,
        scope_id: str,
        policy: dict | StylePolicyPatch | None = None,
        *,
        version: int = 1,
    ) -> None:
        """Host-only initial registration. Rules are guidance, not implicit edits."""
        with self._lock:
            if scope_id in self._policies or version < 1:
                raise ToolError(
                    "invalid_policy", "Policy already registered or invalid version"
                )
            parsed = (
                policy
                if isinstance(policy, StylePolicyPatch)
                else StylePolicyPatch.model_validate(policy or {})
            )
            value = {"scope_id": scope_id, "version": version, **_patch(parsed)}
            self._policies[scope_id] = value
            self._policy_history[scope_id, version] = deepcopy(value)

    def read_style_policy(
        self, ctx: ToolContext, scope_id: str, version: int | None = None
    ) -> dict:
        with self._lock:
            self._active(ctx)
            value = (
                self._policies.get(scope_id)
                if version is None
                else self._policy_history.get((scope_id, version))
            )
            if value is None:
                raise ToolError(
                    "policy_not_found", "Requested policy/version is not registered"
                )
            return deepcopy(value)

    def update_style_policy(
        self,
        ctx: ToolContext,
        scope_id: str,
        expected_version: int,
        patch: StylePolicyPatch | dict,
        command_id: str,
    ) -> dict:
        with self._lock:
            self._authorize(ctx, "style_scopes", scope_id)
            try:
                parsed = (
                    patch
                    if isinstance(patch, StylePolicyPatch)
                    else StylePolicyPatch.model_validate(patch)
                )
            except ValidationError as exc:
                raise ToolError("invalid_policy", str(exc)) from exc
            changes = _patch(parsed)
            if not changes:
                raise ToolError("invalid_patch", "Policy patch cannot be empty")
            if changes.get("source_scope_id"):
                self.read_style_policy(ctx, changes["source_scope_id"])
            payload = {
                "op": "update_style_policy",
                "scope_id": scope_id,
                "expected_version": expected_version,
                "patch": changes,
            }
            replay = self._replay(ctx, command_id, payload)
            if replay is not None:
                return replay
            current = self._policies.get(scope_id)
            if current is None:
                raise ToolError(
                    "policy_not_found", "Policy must be registered by the host"
                )
            if current["version"] != expected_version:
                raise ToolError(
                    "policy_conflict",
                    "Policy version differs from command precondition",
                )
            if len(self._policy_history) >= self.max_history:
                raise ToolError("resource_limit", "Policy history capacity reached")
            updated = deepcopy(current)
            for key, value in changes.items():
                if value is None:
                    updated.pop(key, None)
                else:
                    updated[key] = value
            updated["version"] += 1
            self._authorize(ctx, "style_scopes", scope_id)
            self._policies[scope_id] = updated
            self._policy_history[scope_id, updated["version"]] = deepcopy(updated)
            result = {
                "status": "accepted",
                "policy": deepcopy(updated),
                "existing_pages_unchanged": True,
            }
            self._record(
                ctx, command_id, payload, [("style_scopes", scope_id, None)], result
            )
            return result
