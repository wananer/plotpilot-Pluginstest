"""Runtime prompt resolver for Prompt Plaza backed prompts.

Resolution order:
1. Prompt Plaza DB active version (PromptManager)
2. prompts_defaults.json seed (PromptLoader)
3. caller-provided fallback strings
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from domain.ai.value_objects.prompt import Prompt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedPrompt:
    system: str
    user: str
    source: str

    def to_prompt(self) -> Prompt:
        return Prompt(system=self.system, user=self.user)


class PromptResolver:
    """Resolve runtime prompts from editable registry with safe fallbacks."""

    def render(
        self,
        node_key: str,
        variables: Optional[Dict[str, Any]] = None,
        *,
        fallback_system: str = "",
        fallback_user: str = "",
    ) -> ResolvedPrompt:
        variables = variables or {}

        db_rendered = self._render_from_db(node_key, variables)
        if db_rendered:
            system = (db_rendered.get("system") or "").strip() or fallback_system
            user = (db_rendered.get("user") or "").strip() or fallback_user
            if system.strip() or user.strip():
                return ResolvedPrompt(system=system, user=user, source="prompt_manager")

        json_rendered = self._render_from_json(node_key, variables)
        if json_rendered:
            system = (json_rendered.get("system") or "").strip() or fallback_system
            user = (json_rendered.get("user") or "").strip() or fallback_user
            if system.strip() or user.strip():
                return ResolvedPrompt(system=system, user=user, source="prompt_loader")

        return ResolvedPrompt(system=fallback_system, user=fallback_user, source="fallback")

    def _render_from_db(self, node_key: str, variables: Dict[str, Any]) -> Optional[Dict[str, str]]:
        try:
            from infrastructure.ai.prompt_manager import get_prompt_manager

            mgr = get_prompt_manager()
            mgr.ensure_seeded()
            return mgr.render(node_key, variables)
        except Exception as exc:
            logger.debug("PromptResolver DB render skipped for %s: %s", node_key, exc)
            return None

    def _render_from_json(self, node_key: str, variables: Dict[str, Any]) -> Optional[Dict[str, str]]:
        try:
            from infrastructure.ai.prompt_loader import get_prompt_loader

            loader = get_prompt_loader()
            if not loader.exists(node_key):
                return None
            system = loader.render(node_key, template_field="system", variables=variables)
            user = loader.render(node_key, template_field="user_template", variables=variables)
            return {"system": system, "user": user}
        except Exception as exc:
            logger.debug("PromptResolver JSON render skipped for %s: %s", node_key, exc)
            return None


_resolver: Optional[PromptResolver] = None


def get_prompt_resolver() -> PromptResolver:
    global _resolver
    if _resolver is None:
        _resolver = PromptResolver()
    return _resolver


def resolve_prompt(
    node_key: str,
    variables: Optional[Dict[str, Any]] = None,
    *,
    fallback_system: str = "",
    fallback_user: str = "",
) -> ResolvedPrompt:
    return get_prompt_resolver().render(
        node_key,
        variables,
        fallback_system=fallback_system,
        fallback_user=fallback_user,
    )
