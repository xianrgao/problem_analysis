"""Custom model adapter example for gateways that are not OpenAI-compatible.

settings.json::

    "model": {"driver": "python", "callable": "examples.adapters.custom_model:complete",
              "base_url": "https://company-gateway/api/generate", "model": "company-model", "timeout": 120}

The host calls ``complete(messages, options)``; ``options`` contains the model
settings without credentials. Return the assistant text (or a dict). The text
may contain one JSON instruction anywhere in it; the host extracts and
validates it, so native tool calling is not required.

The credential is read here from the work computer (environment variable or
protected file). It is never sent to the browser, examples or exported logs.
"""
from __future__ import annotations

import os

import httpx


def complete(messages: list[dict], options: dict) -> str:
    token = os.environ.get("COMPANY_MODEL_TOKEN", "")
    payload = {"model": options.get("model"), "prompt": [{"role": m["role"], "text": m["content"]} for m in messages], "temperature": 0.15}
    headers = {"X-Auth-Token": token} if token else {}
    response = httpx.post(options["base_url"], json=payload, headers=headers, timeout=float(options.get("timeout", 120)))
    response.raise_for_status()
    body = response.json()
    # Map the gateway's response shape to plain assistant text.
    return body["output"]["text"]
