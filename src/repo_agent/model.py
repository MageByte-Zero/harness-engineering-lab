"""Model adapter for the repository agent; never serialize credentials."""

import os
from pathlib import Path

from dotenv import load_dotenv
from agents import OpenAIChatCompletionsModel
from openai import AsyncOpenAI


class ConfiguredModel(OpenAIChatCompletionsModel):
    """Own the HTTP client for exactly one application run."""

    def __init__(self, name, client):
        self.client = client
        super().__init__(model=name, openai_client=client)

    async def aclose(self):
        await self.client.close()


def create_model():
    load_dotenv(Path(".env"), override=False)
    for name in ("MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_NAME"):
        if not os.environ.get(name):
            raise ValueError(f"Set {name} before making a model request")
    return ConfiguredModel(
        os.environ["MODEL_NAME"],
        AsyncOpenAI(api_key=os.environ["MODEL_API_KEY"],
                                  base_url=os.environ["MODEL_BASE_URL"],
                                  timeout=90, max_retries=0),
    )
