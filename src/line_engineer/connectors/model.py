from __future__ import annotations
import asyncio
import importlib
import inspect
import json
import os
import httpx


class ModelClient:
    """Default OpenAI-compatible chat protocol, or a custom adapter callable.

    driver="python" loads ``callable`` ("module:function"). The function receives
    ``(messages, options)`` and returns the assistant text or a dict; it may be
    async. Structured instructions in plain text are parsed and validated by
    the caller, so native tool calling is not required. See examples/adapters.
    """

    def __init__(self, settings):
        self.settings = settings

    async def complete(self, messages, json_mode=True):
        cfg = self.settings.values["model"]
        if cfg.get("driver", "openai") == "python":
            return await self._custom(cfg, messages, json_mode)
        if not cfg.get("base_url") or not cfg.get("model"):
            raise ValueError("请先在设置中填写公司模型 API 地址和模型名称；真实模式不会回退为演示。")
        base = cfg["base_url"].rstrip("/")
        url = cfg.get("endpoint") or (base if base.endswith("/chat/completions") else base + "/chat/completions")
        key = os.environ.get("LINE_ENGINEER_API_KEY") or cfg.get("api_key", "")
        headers = {**cfg.get("headers", {})}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {"model": cfg["model"], "messages": messages, "temperature": 0.15, "stream": False}
        async with httpx.AsyncClient(timeout=float(cfg.get("timeout", 120))) as client:
            for attempt in range(3):
                try:
                    result = await client.post(url, json=payload, headers=headers)
                    if result.status_code in (429, 502, 503, 504) and attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    result.raise_for_status()
                    raw = result.json()["choices"][0]["message"]["content"]
                    if not json_mode:
                        return raw
                    return self.parse_json(raw)
                except (httpx.TimeoutException, httpx.ConnectError):
                    if attempt == 2:
                        raise RuntimeError("公司模型连接超时或不可用，请检查地址和网络。") from None
                    await asyncio.sleep(2 ** attempt)
                except httpx.HTTPStatusError as exc:
                    raise RuntimeError(f"模型 API 返回 HTTP {exc.response.status_code}，请检查接入配置。") from None

    async def _custom(self, cfg, messages, json_mode):
        target = cfg.get("callable") or ""
        if ":" not in target:
            raise ValueError("自定义模型适配器需配置 callable，格式为 module:function；真实模式不会回退为演示。")
        module, name = target.split(":", 1)
        function = getattr(importlib.import_module(module), name)
        options = {k: v for k, v in cfg.items() if k not in ("api_key", "headers", "callable", "driver")}
        timeout = float(cfg.get("timeout", 120))
        try:
            if inspect.iscoroutinefunction(function):
                raw = await asyncio.wait_for(function(messages, options), timeout)
            else:
                raw = await asyncio.wait_for(asyncio.to_thread(function, messages, options), timeout)
        except asyncio.TimeoutError:
            raise RuntimeError("公司模型适配器响应超时，请检查网络或调整超时设置。") from None
        if not json_mode:
            return raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        return self.parse_json(raw)

    @staticmethod
    def parse_json(text):
        if isinstance(text, dict):
            return text
        raw = text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                return json.loads(raw[start:end + 1])
            raise ValueError("模型未返回有效 JSON 指令")
