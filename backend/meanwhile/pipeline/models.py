"""One function for every model call, so the provider (local Ollama, Gemini or a SageMaker endpoint) is a setting,
not a code change."""

from __future__ import annotations

import base64
import json
from functools import cache
from typing import Any, TypedDict

import httpx

from meanwhile import config

TIMEOUT_S = 120
KEEP_LOADED = "30m"
OLLAMA_NUM_CTX = 8192
SAGEMAKER_MAX_TOKENS = 1024


class Message(TypedDict, total=False):
    """One chat message: role (system, user or assistant), text, and optional JPEG images."""
    role: str
    content: str
    images: list[bytes]


class ModelUnavailable(Exception):
    """The model can't be reached, isn't installed, or gave no usable answer."""
    pass


def ask(messages: list[Message], schema: dict[str, Any], model: str) -> dict[str, Any]:
    """Ask a model ("ollama:<name>", "ollama-cpu:<name>" to keep it off the GPU, "gemini:<id>",
    "sagemaker:<endpoint-name>") for JSON matching schema."""
    provider, _, name = model.partition(":")
    if provider in ("ollama", "ollama-cpu"):
        return _ask_ollama(name, messages, schema, on_gpu=provider == "ollama")
    if provider == "gemini":
        return _ask_gemini(name, messages, schema)
    if provider == "sagemaker":
        return _ask_sagemaker(name, messages, schema)
    raise ModelUnavailable(f"Unknown model {model!r}: use ollama:<name>, ollama-cpu:<name>, gemini:<name> "
                           "or sagemaker:<endpoint-name>.")


def _ask_ollama(name: str, messages: list[Message], schema: dict[str, Any], on_gpu: bool = True) -> dict[str, Any]:
    """Ask a local Ollama model with one fixed context size (so it loads once), on the GPU or the CPU only,
    with step-by-step thinking off so answers come straight back."""
    body = {
        "model": name,
        "messages": [
            {"role": m["role"], "content": m.get("content", ""),
             **({"images": [base64.b64encode(i).decode() for i in m["images"]]} if m.get("images") else {})}
            for m in messages
        ],
        "format": schema,
        "stream": False,
        "think": False,
        "keep_alive": KEEP_LOADED,
        "options": {"temperature": 0, "seed": 1, "num_ctx": OLLAMA_NUM_CTX, **({} if on_gpu else {"num_gpu": 0})},
    }
    try:
        reply = httpx.post(f"{config.OLLAMA_URL}/api/chat", json=body, timeout=TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise ModelUnavailable(f"Ollama isn't answering at {config.OLLAMA_URL} ({exc}). Start Ollama.") from exc
    if reply.status_code != 200:
        raise ModelUnavailable(f"Ollama {name}: HTTP {reply.status_code}: {reply.text[:300]}")
    return _parse(reply.json().get("message", {}).get("content", ""), name)


@cache
def _gemini_client():
    """The Gemini client, created on first use from GEMINI_API_KEY."""
    from google import genai

    if not config.GEMINI_API_KEY:
        raise ModelUnavailable("GEMINI_API_KEY is not set in .env at the repo root.")
    return genai.Client(api_key=config.GEMINI_API_KEY)


def _ask_gemini(name: str, messages: list[Message], schema: dict[str, Any]) -> dict[str, Any]:
    """Ask a Gemini model; system messages become the system instruction."""
    from google.genai import types

    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = []
    for m in messages:
        if m["role"] == "system":
            continue
        parts = [types.Part.from_bytes(data=i, mime_type="image/jpeg") for i in m.get("images", [])]
        parts.append(types.Part.from_text(text=m.get("content", "")))
        contents.append(types.Content(role="model" if m["role"] == "assistant" else "user", parts=parts))
    config_ = types.GenerateContentConfig(temperature=0, system_instruction=system or None,
                                          response_mime_type="application/json", response_json_schema=schema)
    try:
        response = _gemini_client().models.generate_content(model=name, contents=contents, config=config_)
    except ModelUnavailable:
        raise
    except Exception as exc:
        raise ModelUnavailable(f"Gemini {name}: {exc}") from exc
    return _parse(response.text or "", name)


@cache
def _sagemaker_client():
    """The SageMaker runtime client, created on first use from MEANWHILE_AWS_PROFILE (empty: the default
    credential chain, such as an instance role) and MEANWHILE_AWS_REGION."""
    import boto3

    aws = boto3.Session(profile_name=config.AWS_PROFILE or None, region_name=config.AWS_REGION)
    return aws.client("sagemaker-runtime")


def openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Messages in the OpenAI chat format, with images as JPEG data URLs before the text."""
    out = []
    for m in messages:
        if not m.get("images"):
            out.append({"role": m["role"], "content": m.get("content", "")})
            continue
        parts: list[dict[str, Any]] = [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(i).decode()}}
            for i in m["images"]]
        parts.append({"type": "text", "text": m.get("content", "")})
        out.append({"role": m["role"], "content": parts})
    return out


def _ask_sagemaker(endpoint: str, messages: list[Message], schema: dict[str, Any]) -> dict[str, Any]:
    """Ask a SageMaker endpoint serving an OpenAI-compatible chat model (LMI/vLLM), with the answer held to
    the schema by guided decoding."""
    body = {
        "messages": openai_messages(messages),
        "temperature": 0,
        "seed": 1,
        "max_tokens": SAGEMAKER_MAX_TOKENS,
        "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}},
    }
    try:
        reply = _sagemaker_client().invoke_endpoint(EndpointName=endpoint, ContentType="application/json",
                                                    Accept="application/json", Body=json.dumps(body))
        result = json.loads(reply["Body"].read())
    except ModelUnavailable:
        raise
    except Exception as exc:
        raise ModelUnavailable(f"SageMaker {endpoint}: {exc}") from exc
    try:
        text = result["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelUnavailable(f"SageMaker {endpoint}: unexpected reply {str(result)[:200]!r}") from exc
    return _parse(text, endpoint)


def _parse(text: str, name: str) -> dict[str, Any]:
    """The model's JSON object, or ModelUnavailable if it isn't one."""
    try:
        answer = json.loads(text)
    except ValueError as exc:
        raise ModelUnavailable(f"{name} didn't return JSON: {text[:200]!r}") from exc
    if not isinstance(answer, dict):
        raise ModelUnavailable(f"{name} returned {type(answer).__name__}, not a JSON object.")
    return answer


def check(model: str) -> None:
    """Make sure a local model is pulled and Ollama is up; raise ModelUnavailable saying how to fix it."""
    provider, _, name = model.partition(":")
    if provider not in ("ollama", "ollama-cpu"):
        return
    try:
        names = [m["name"] for m in httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=5).json()["models"]]
    except httpx.HTTPError as exc:
        raise ModelUnavailable(f"Ollama isn't answering at {config.OLLAMA_URL}. Start Ollama.") from exc
    if name not in names:
        raise ModelUnavailable(f"The model {name} isn't pulled. Run: ollama pull {name}")
