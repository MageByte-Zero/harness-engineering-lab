"""Lesson 01: supply documents to the official SDK and retain the response."""

import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


BASE_INSTRUCTIONS = "你是 TaskHub 技术支持助手。根据提供的资料回答问题；资料不足时说明不知道，不编造产品规则。"


def load_documents(directory: Path) -> list[dict[str, str]]:
    documents = []
    for path in sorted(directory.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        documents.append({"id": path.stem, "content": content,
                          "sha256": hashlib.sha256(content.encode()).hexdigest()})
    if not documents:
        raise ValueError(f"No Markdown documents in {directory}")
    return documents


def build_input(question: str, documents: list[dict[str, str]]) -> str:
    if not question.strip():
        raise ValueError("Question must not be empty")
    material = "\n\n".join(f"[{doc['id']}]\n{doc['content']}" for doc in documents)
    return f"产品资料：\n{material or '（本次未提供资料）'}\n\n用户问题：\n{question}"


def create_client() -> OpenAI:
    load_dotenv(Path(".env"), override=False)
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        raise ValueError("Set MODEL_API_KEY before making a model request")
    base_url = os.environ.get("MODEL_BASE_URL")
    if not base_url:
        raise ValueError("Set MODEL_BASE_URL explicitly before making a model request")
    return OpenAI(api_key=key, base_url=base_url, timeout=90.0, max_retries=0)


def ask(client: OpenAI, model: str, question: str,
        documents: list[dict[str, str]], *, instructions: str = BASE_INSTRUCTIONS) -> dict:
    request = {"model": model, "messages": [
        {"role": "system", "content": instructions},
        {"role": "user", "content": build_input(question, documents)},
    ]}
    response = client.chat.completions.create(**request)
    return {"question": question, "documents": documents, "request": request,
            "response": response.model_dump(mode="json"),
            "answer": response.choices[0].message.content if response.choices else None, "review": None}
