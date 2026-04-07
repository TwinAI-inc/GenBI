"""
Claude (Anthropic) client for GenBI.

Provides the same interface as services.azure_ai_client so it can be used
as a drop-in replacement.

Environment variables:
  ANTHROPIC_API_KEY  - Anthropic API key
  ANTHROPIC_MODEL    - Model ID (default: claude-sonnet-4-6)
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

_DEFAULT_MODEL = 'claude-sonnet-4-6'
_READ_TIMEOUT = 60      # seconds
_MAX_RETRIES = 2        # transient retry attempts


def _get_config():
    """Read and validate Anthropic env vars. Raises RuntimeError if missing."""
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    model = os.environ.get('ANTHROPIC_MODEL', _DEFAULT_MODEL).strip() or _DEFAULT_MODEL

    if not api_key:
        raise RuntimeError('Anthropic not configured. Missing: ANTHROPIC_API_KEY')

    return api_key, model


def _build_client():
    """Build an Anthropic client."""
    from anthropic import Anthropic

    api_key, _ = _get_config()
    logger.info('Anthropic: using API key authentication')
    client = Anthropic(
        api_key=api_key,
        timeout=_READ_TIMEOUT,
        max_retries=_MAX_RETRIES,
    )
    return client


def _get_client():
    """Return a singleton Anthropic client (thread-safe lazy init)."""
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


def _split_messages(messages):
    """
    Convert OpenAI-style messages to Anthropic format.

    Anthropic uses a separate `system` parameter and only `user`/`assistant`
    roles in the messages array.
    """
    system_parts = []
    chat_messages = []
    for m in messages:
        role = m.get('role', 'user')
        content = m.get('content', '')
        if role == 'system':
            if content:
                system_parts.append(content)
        else:
            # Map any other role to user/assistant
            mapped_role = 'assistant' if role == 'assistant' else 'user'
            chat_messages.append({'role': mapped_role, 'content': content})

    # Anthropic requires at least one message
    if not chat_messages:
        chat_messages.append({'role': 'user', 'content': ''})

    return '\n\n'.join(system_parts) if system_parts else None, chat_messages


def chat_completion(messages, *, temperature=0.7, max_tokens=2048,
                    response_format=None):
    """
    Call Anthropic Claude messages API.

    Same interface as services.azure_ai_client.chat_completion.

    Parameters
    ----------
    messages : list[dict]
        OpenAI-style messages, e.g. [{"role": "user", "content": "..."}]
    temperature : float
    max_tokens : int
    response_format : dict | None
        Optional, e.g. {"type": "json_object"} — when set, a JSON-only
        instruction is appended to the system prompt to coax JSON output.

    Returns
    -------
    dict with keys:
        content  : str   - the assistant reply text
        usage    : dict  - {prompt_tokens, completion_tokens, total_tokens}
    """
    client = _get_client()
    _, model = _get_config()

    system_prompt, chat_messages = _split_messages(messages)

    # If JSON mode requested, strengthen the system prompt
    if response_format and response_format.get('type') == 'json_object':
        json_instruction = (
            'You must respond with a single valid JSON object only. '
            'Do not include any prose, explanations, or markdown code fences. '
            'Output raw JSON starting with { and ending with }.'
        )
        system_prompt = (
            system_prompt + '\n\n' + json_instruction
            if system_prompt else json_instruction
        )

    kwargs = dict(
        model=model,
        messages=chat_messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if system_prompt:
        kwargs['system'] = system_prompt

    response = client.messages.create(**kwargs)

    # Extract text from content blocks
    content_text = ''
    for block in response.content:
        if hasattr(block, 'text'):
            content_text += block.text

    usage = {}
    if response.usage:
        usage = {
            'prompt_tokens': response.usage.input_tokens,
            'completion_tokens': response.usage.output_tokens,
            'total_tokens': response.usage.input_tokens + response.usage.output_tokens,
        }

    return {
        'content': content_text,
        'usage': usage,
    }


def chat_completion_json(prompt, *, system=None, temperature=0.3,
                         max_tokens=2048):
    """
    Convenience wrapper: send a single prompt, get parsed JSON back.

    Same interface as services.azure_ai_client.chat_completion_json.
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

    # Extract JSON object if model included extra prose (defensive)
    if not reply_text.startswith('{'):
        start = reply_text.find('{')
        end = reply_text.rfind('}')
        if start >= 0 and end > start:
            reply_text = reply_text[start:end + 1]

    parsed = json.loads(reply_text)
    return parsed, result.get('usage', {})
