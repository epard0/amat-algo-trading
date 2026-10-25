"""Shared default routing. Explicit/frozen chains remain authoritative."""

import os
from pathlib import Path

from llmrouter.chain import Chain, Target

POLICY_VERSION = "gemini-before-space-bunny/3"
COMPLEX_MODEL = "gpt-5.6-luna"


def default_chain() -> Chain:
    if path := os.getenv("AMAT_MODEL_CHAIN"):
        return Chain.load(Path(path))
    return Chain(
        targets=(
            Target(provider="groq", model="openai/gpt-oss-120b"),
            Target(provider="gemini", model="gemini-3.8-flash"),
            Target(provider="openrouter", model="stealth/space-bunny-alpha"),
        ),
        coding_targets=(
            Target(provider="openai", model=COMPLEX_MODEL),
            Target(provider="gemini", model="gemini-3.8-flash"),
            Target(provider="groq", model="openai/gpt-oss-120b"),
            Target(provider="openrouter", model="stealth/space-bunny-alpha"),
        ),
        complex_targets=(Target(provider="openai", model=COMPLEX_MODEL),),
    )
