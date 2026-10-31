"""Thin AI client wrapper used for cover-letter generation."""

from __future__ import annotations

import os
from typing import Any

import httpx


def has_ai_credentials() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY") or os.getenv("CLAUDE_API_KEY") or os.getenv("ANTHROPIC_API_KEY"))


async def call_ai(messages: list[dict[str, Any]], system: str = "", max_tokens: int = 1000) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("CLAUDE_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("No AI key configured.")
    use_openrouter = bool(os.getenv("OPENROUTER_API_KEY"))
    if use_openrouter:
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://jobblast.app",
            "X-Title": "JobBlast Manual Apply Assistant",
        }
        payload: dict[str, Any] = {
            "model": os.getenv("JOBBLAST_OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet"),
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            payload["messages"] = [{"role": "system", "content": system}] + messages
    else:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": os.getenv("JOBBLAST_ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = system
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    if "choices" in data:
        return str(data["choices"][0]["message"]["content"]).strip()
    if "content" in data:
        first = data["content"][0]
        if isinstance(first, dict):
            return str(first.get("text", "")).strip()
        return str(first).strip()
    raise RuntimeError(f"Unexpected AI response keys: {list(data.keys())}")

