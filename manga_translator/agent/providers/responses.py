"""Shared Responses model construction for interactive and delegated agents."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIResponsesModel


def create_responses_model(
    model_name: str, *, client: AsyncOpenAI,
) -> OpenAIResponsesModel:
    """Apply provider capabilities without changing the endpoint or model name."""
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(openai_client=client)
    # Also recognize namespaced DeepSeek models served through compatible gateways.
    short_name = model_name.lower().rsplit("/", 1)[-1]
    is_deepseek_endpoint = urlsplit(str(client.base_url)).hostname == "api.deepseek.com"
    if not (is_deepseek_endpoint or short_name.startswith("deepseek-")):
        return OpenAIResponsesModel(model_name, provider=provider)

    from pydantic_ai.profiles import merge_profile
    from pydantic_ai.profiles.openai import OpenAIModelProfile
    from pydantic_ai.providers.deepseek import DeepSeekProvider

    # A custom base URL does not make OpenAIProvider select DeepSeek's profile.
    # Keep structured output tools and validation; the profile makes PydanticAI
    # use auto instead of implicit required while thinking is active, on every
    # request. Setting model_settings["tool_choice"] = "auto" alone does not:
    # structured tool output would still resolve it to required.
    compatibility = OpenAIModelProfile(
        openai_supports_forced_tool_choice_with_thinking=False,
    )
    if short_name == "deepseek-flash":
        # PydanticAI 2.43 recognizes V4 names but not the current Flash name.
        # Flash thinks by default; an explicit reasoning effort of none still
        # permits forced tool use, as determined by PydanticAI per request.
        compatibility.update(
            supports_thinking=True,
            openai_reasoning_enabled_by_default=True,
        )
    profile = DeepSeekProvider.model_profile(short_name) or {}
    if not is_deepseek_endpoint:
        # A gateway's wire format is not determined by the underlying model.
        # Carry over thinking capabilities only, leaving history serialization
        # and native JSON schema support at the gateway's existing defaults.
        profile = OpenAIModelProfile(
            supports_thinking=profile.get("supports_thinking", False),
            thinking_always_enabled=profile.get("thinking_always_enabled", False),
            openai_supports_tool_choice_required=profile.get("openai_supports_tool_choice_required", True),
            openai_reasoning_enabled_by_default=profile.get("openai_reasoning_enabled_by_default", False),
        )
    profile = merge_profile(profile, compatibility)
    return OpenAIResponsesModel(model_name, provider=provider, profile=profile)
