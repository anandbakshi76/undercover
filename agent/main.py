"""The UnderCover Agent: what the queue agent and the claim agent share."""

import json
import os
from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

BACKEND_URL = "http://127.0.0.1:8000"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
MODEL_TIMEOUT = 120
RESULT_LENGTH = 300
RETRY_DELAY = 1.0


def backend() -> httpx.Client:
    """Return a client for the UnderCover backend."""
    return httpx.Client(base_url=BACKEND_URL, timeout=30)


def call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """Call the backend. A refusal comes back as the backend's message."""
    with backend() as client:
        response = client.request(method, path, json=body)
    if response.is_server_error:
        response.raise_for_status()
    return response.json()


def model() -> ChatOpenAI:
    """Return the model named in MODEL_NAME, or DeepSeek V4.1 Flash by default."""
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else "low",
        timeout=MODEL_TIMEOUT,
    )


def tracing(session_id: str) -> RunnableConfig:
    """Trace a run in Langfuse under a session, if the Langfuse keys are set."""
    if not os.getenv("LANGFUSE_SECRET_KEY"):
        return {}
    from langfuse.langchain import CallbackHandler  # noqa: PLC0415

    return {
        "callbacks": [CallbackHandler()],
        "metadata": {"langfuse_session_id": session_id},
    }


def steps(message: BaseMessage) -> list[tuple[str, str]]:
    """Describe a message from an agent as steps for the dashboard."""
    if isinstance(message, ToolMessage):
        described = [("tool_result", f"{message.name}: {message.text[:RESULT_LENGTH]}")]
        if message.artifact:
            described.append(("section", message.artifact))
        return described
    if isinstance(message, AIMessage) and message.tool_calls:
        return [
            (
                "tool_call",
                f"{call['name']}({json.dumps(call['args'], ensure_ascii=False)})",
            )
            for call in message.tool_calls
        ]
    if isinstance(message, AIMessage):
        return [("reply", message.text)]
    return []
