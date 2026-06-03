"""Local GGUF backend via llama-cpp-python.

Heavy optional dep - installing llama-cpp-python on Windows with CUDA wants
VS Build Tools + CMake + a CUDA toolkit, and the prebuilt wheels are
platform-specific. This module lazy-imports so an absent install doesn't
break uvicorn startup; the backend just reports unhealthy and the registry
skips it.

Configure in yagami.toml:
    [llama_cpp]
    model_path = "C:/models/llama-3.1-8b-instruct.Q4_K_M.gguf"
    n_ctx      = 8192
    n_gpu_layers = -1
"""

from __future__ import annotations

from typing import AsyncIterator

from pathlib import Path

from ..config import LlamaCppConfig, YagamiConfig
from .base import Backend, BackendChunk, BackendOptions, Capability, Message, Pricing


def build(cfg: YagamiConfig, _secrets_get) -> "LlamaCppBackend | None":
    # Only instantiate if model_path is set AND the file exists. Avoids the
    # log warning every startup when the user isn't using llama-cpp.
    if not cfg.llama_cpp.model_path or not Path(cfg.llama_cpp.model_path).exists():
        return None
    return LlamaCppBackend(cfg.llama_cpp)


class LlamaCppBackend(Backend):
    name = "llama_cpp"
    capabilities = {Capability.TEXT, Capability.CODE}
    is_local = True
    pricing = Pricing()  # local - free

    def __init__(self, config: LlamaCppConfig) -> None:
        self._config = config
        self._llm = None  # lazy

    def _load(self):
        if self._llm is not None:
            return self._llm
        try:
            from llama_cpp import Llama  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "llama-cpp-python not installed. "
                "Install via: pip install llama-cpp-python "
                "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124"
            ) from exc
        self._llm = Llama(
            model_path=self._config.model_path,
            n_ctx=self._config.n_ctx,
            n_gpu_layers=self._config.n_gpu_layers,
            verbose=False,
        )
        return self._llm

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]:
        try:
            llm = self._load()
        except RuntimeError as exc:
            yield {"type": "error", "content": str(exc), "meta": {}}
            yield {"type": "done", "content": "", "meta": {}}
            return

        # llama-cpp accepts a chat-message list directly via .create_chat_completion.
        system_parts = [m.content for m in messages if m.role == "system"]
        if options.system_prompt:
            system_parts = [options.system_prompt]
        msgs = []
        if system_parts:
            msgs.append({"role": "system", "content": "\n\n".join(system_parts)})
        for m in messages:
            if m.role in ("user", "assistant"):
                msgs.append({"role": m.role, "content": m.content})

        try:
            stream = llm.create_chat_completion(
                messages=msgs,
                max_tokens=options.max_tokens,
                temperature=options.temperature,
                stream=True,
            )
            for chunk in stream:
                delta = chunk["choices"][0].get("delta", {})
                txt = delta.get("content", "")
                if txt:
                    yield {"type": "text", "content": txt, "meta": {}}
            yield {"type": "done", "content": "", "meta": {}}
        except Exception as exc:  # noqa: BLE001 - surface ANY llama-cpp error
            yield {"type": "error", "content": f"llama_cpp error: {exc}", "meta": {}}
            yield {"type": "done", "content": "", "meta": {}}

    async def health(self) -> bool:
        # Only reports healthy when the model file is present. Don't try to
        # actually load - that allocates VRAM and takes seconds.
        return bool(self._config.model_path and Path(self._config.model_path).exists())
