"""Model adapter for the repository agent; never serialize credentials."""

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


def create_model():
    load_dotenv(Path(".env"), override=False)
    for name in ("MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_NAME"):
        if not os.environ.get(name):
            raise ValueError(f"Set {name} before making a model request")
    return ChatOpenAI(model=os.environ["MODEL_NAME"], api_key=os.environ["MODEL_API_KEY"],
                      base_url=os.environ["MODEL_BASE_URL"], timeout=90, max_retries=0,
                      use_responses_api=False)
