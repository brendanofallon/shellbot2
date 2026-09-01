"""Azure / Azure AI Foundry provider setup for pydantic-ai models."""

import os

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.azure import AzureProvider
from pydantic_ai.providers.openai import OpenAIProvider


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _is_azure_foundry_v1_endpoint(endpoint: str | None) -> bool:
    return bool(endpoint and "/openai/v1" in endpoint.rstrip("/"))


def create_azure_provider(conf: dict | None = None):
    """Create a provider for Azure-hosted OpenAI models.

    Prefers ``AZURE_FOUNDRY_ENDPOINT`` / ``AZURE_FOUNDRY_API_KEY``, then the
    classic ``AZURE_OPENAI_*`` names. Azure AI Foundry exposes an
    OpenAI-compatible v1 API at ``/openai/v1/``, which must use
    ``OpenAIProvider``. Classic Azure OpenAI deployments use ``AzureProvider``.
    """
    conf = conf or {}
    azure_endpoint = _env_value("AZURE_FOUNDRY_ENDPOINT") or _env_value("AZURE_OPENAI_ENDPOINT")
    api_key = _env_value("AZURE_FOUNDRY_API_KEY") or _env_value("AZURE_OPENAI_API_KEY")

    if not azure_endpoint:
        raise ValueError(
            "Must provide an Azure endpoint via AZURE_FOUNDRY_ENDPOINT or AZURE_OPENAI_ENDPOINT"
        )
    if not api_key:
        raise ValueError(
            "Must provide an Azure API key via AZURE_FOUNDRY_API_KEY or AZURE_OPENAI_API_KEY"
        )

    if _is_azure_foundry_v1_endpoint(azure_endpoint):
        return OpenAIProvider(base_url=azure_endpoint.rstrip("/"), api_key=api_key)

    azure_conf = conf.get("azure", {})
    return AzureProvider(
        azure_endpoint=azure_endpoint,
        api_key=api_key,
        api_version=azure_conf.get("api_version") or _env_value("OPENAI_API_VERSION"),
    )


def create_azure_chat_model(
    model_name: str,
    conf: dict | None = None,
    settings: dict | None = None,
):
    """Return an OpenAI chat model wired to the Azure / Foundry provider."""
    if model_name.startswith("azure:"):
        model_name = model_name.split(":", 1)[1]
    kwargs: dict = {"provider": create_azure_provider(conf)}
    if settings is not None:
        kwargs["settings"] = settings
    return OpenAIChatModel(model_name, **kwargs)
