"""
Azure OpenAI client for GenBI.

Uses Managed Identity (DefaultAzureCredential) in production and
az-cli/VS Code credentials in local development. No API keys stored.

Environment variables:
  AZURE_OPENAI_ENDPOINT     - e.g. https://<resource>.openai.azure.com/
  AZURE_OPENAI_DEPLOYMENT   - deployment name in Azure OpenAI
  AZURE_OPENAI_API_VERSION  - e.g. 2024-12-01-preview
"""

import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

# Module-level singleton; initialised lazily on first call.
_client = None
_lock = threading.Lock()

# ── Configuration ───────────────────────────────────────────────────────────

_CONNECT_TIMEOUT = 10   # seconds
_READ_TIMEOUT = 60      # seconds
_MAX_RETRIES = 2        # transient retry attempts


def _get_config():
    """Read and validate Azure OpenAI env vars. Raises RuntimeError if missing."""
    endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT', '').strip()
    deployment = os.environ.get('AZURE_OPENAI_DEPLOYMENT', '').strip()
    api_version = os.environ.get('AZURE_OPENAI_API_VERSION', '').strip()

    missing = []
    if not endpoint:
        missing.append('AZURE_OPENAI_ENDPOINT')
    if not deployment:
        missing.append('AZURE_OPENAI_DEPLOYMENT')
    if not api_version:
        missing.append('AZURE_OPENAI_API_VERSION')

    if missing:
        raise RuntimeError(
            f"Azure OpenAI not configured. Missing: {', '.join(missing)}"
        )

    return endpoint, deployment, api_version


def _build_client():
    """Build an OpenAI client configured for Azure with Managed Identity."""
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from openai import AzureOpenAI

    endpoint, deployment, api_version = _get_config()
    scope = os.environ.get(
        'AZURE_OPENAI_SCOPE',
        'https://cognitiveservices.azure.com/.default',
    )

    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(credential, scope)

    client = AzureOpenAI(
        azure_endpoint=endpoint,
        azure_deployment=deployment,
        api_version=api_version,
        azure_ad_token_provider=token_provider,
        timeout=_READ_TIMEOUT,
        max_retries=_MAX_RETRIES,
    )
    return client


def _get_client():
    """Return a singleton AzureOpenAI client (thread-safe lazy init)."""
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is not None:
            return _client
        _client = _build_client()
        return _client


# ── Public API ──────────────────────────────────────────────────────────────

def is_configured():
    """Return True if all required env vars are set (non-empty)."""
    try:
        _get_config()
        return True
    except RuntimeError:
        return False


def chat_completion(messages, *, temperature=0.7, max_tokens=2048,
                    response_format=None):
    """
    Call Azure OpenAI chat completion.

    Parameters
    ----------
    messages : list[dict]
        OpenAI-style messages, e.g. [{"role": "user", "content": "..."}]
    temperature : float
    max_tokens : int
    response_format : dict | None
        Optional, e.g. {"type": "json_object"} for JSON mode.

    Returns
    -------
    dict with keys:
        content  : str   - the assistant reply text
        usage    : dict  - {prompt_tokens, completion_tokens, total_tokens}
    """
    client = _get_client()
    deployment = os.environ.get('AZURE_OPENAI_DEPLOYMENT', '')

    kwargs = dict(
        model=deployment,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if response_format:
        kwargs['response_format'] = response_format

    response = client.chat.completions.create(**kwargs)

    choice = response.choices[0]
    usage = {}
    if response.usage:
        usage = {
            'prompt_tokens': response.usage.prompt_tokens,
            'completion_tokens': response.usage.completion_tokens,
            'total_tokens': response.usage.total_tokens,
        }

    return {
        'content': choice.message.content or '',
        'usage': usage,
    }


def chat_completion_json(prompt, *, system=None, temperature=0.3,
                         max_tokens=2048):
    """
    Convenience wrapper: send a single prompt, get parsed JSON back.

    Replaces the old _call_gemini(api_key, prompt) pattern.
    Raises json.JSONDecodeError if the model output is not valid JSON.
    """
    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': prompt})

    result = chat_completion(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={'type': 'json_object'},
    )

    reply_text = result['content'].strip()

    # Strip markdown fences if present (defensive)
    if reply_text.startswith('```'):
        reply_text = reply_text.split('\n', 1)[-1]
        if reply_text.endswith('```'):
            reply_text = reply_text[:-3].strip()
        elif '```' in reply_text:
            reply_text = reply_text[:reply_text.rfind('```')].strip()

    parsed = json.loads(reply_text)
    return parsed, result.get('usage', {})
