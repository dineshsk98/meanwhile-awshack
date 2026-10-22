"""Plumbing tests for the sagemaker:<endpoint> provider, with a fake SageMaker runtime client (no AWS calls)."""

from __future__ import annotations

import io
import json

import pytest

from meanwhile.pipeline import models

SCHEMA = {"type": "object", "properties": {"found": {"type": "boolean"}}, "required": ["found"]}


class FakeRuntime:
    """Records invoke_endpoint calls and answers with a canned OpenAI-style reply or raises."""

    def __init__(self, content: str | None = '{"found": true}', error: Exception | None = None, raw=None):
        self.calls: list[dict] = []
        self.content, self.error, self.raw = content, error, raw

    def invoke_endpoint(self, **kwargs):
        """Store the call and return a body like the LMI container's."""
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        body = self.raw if self.raw is not None else {"choices": [{"message": {"content": self.content}}]}
        return {"Body": io.BytesIO(json.dumps(body).encode())}


@pytest.fixture
def fake(monkeypatch):
    """Install a fake runtime client and return it."""
    runtime = FakeRuntime()
    monkeypatch.setattr(models, "_sagemaker_client", lambda: runtime)
    return runtime


def test_sends_openai_chat_with_images_and_schema(fake):
    """Images go first as JPEG data URLs, then the text; the schema goes in response_format."""
    answer = models.ask([{"role": "system", "content": "be brief"},
                         {"role": "user", "content": "find it", "images": [b"\xff\xd8a", b"\xff\xd8b"]}],
                        SCHEMA, "sagemaker:meanwhile-qwen3vl")
    assert answer == {"found": True}
    call = fake.calls[0]
    assert call["EndpointName"] == "meanwhile-qwen3vl" and call["ContentType"] == "application/json"
    body = json.loads(call["Body"])
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    parts = body["messages"][1]["content"]
    assert [p["type"] for p in parts] == ["image_url", "image_url", "text"]
    assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert parts[2]["text"] == "find it"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["temperature"] == 0


def test_endpoint_error_is_model_unavailable(monkeypatch):
    """A failing invoke (endpoint deleted, throttled, no credentials) becomes ModelUnavailable."""
    monkeypatch.setattr(models, "_sagemaker_client", lambda: FakeRuntime(error=RuntimeError("Endpoint not found")))
    with pytest.raises(models.ModelUnavailable, match="Endpoint not found"):
        models.ask([{"role": "user", "content": "hi"}], SCHEMA, "sagemaker:gone")


@pytest.mark.parametrize("runtime", [FakeRuntime(content="not json"), FakeRuntime(raw={"error": "bad request"})])
def test_unusable_reply_is_model_unavailable(monkeypatch, runtime):
    """Non-JSON content or a reply without choices becomes ModelUnavailable."""
    monkeypatch.setattr(models, "_sagemaker_client", lambda: runtime)
    with pytest.raises(models.ModelUnavailable):
        models.ask([{"role": "user", "content": "hi"}], SCHEMA, "sagemaker:x")


def test_check_skips_sagemaker():
    """check() only verifies local Ollama models; a SageMaker model passes without any call."""
    models.check("sagemaker:meanwhile-qwen3vl")


class FakeBoto3:
    """A stand-in boto3 module that records the Session arguments."""

    def __init__(self):
        self.sessions: list[dict] = []

    def Session(self, **kwargs):
        """Record the arguments and return an object whose client() is a marker."""
        self.sessions.append(kwargs)
        return type("S", (), {"client": staticmethod(lambda name: f"client:{name}")})()


@pytest.mark.parametrize("profile, expected", [("", None), ("meanwhile", "meanwhile")])
def test_client_uses_the_profile_or_the_instance_role(monkeypatch, profile, expected):
    """An empty profile setting gives profile_name=None (the instance role); the region comes from config."""
    from meanwhile import config

    boto3 = FakeBoto3()
    monkeypatch.setitem(__import__("sys").modules, "boto3", boto3)
    monkeypatch.setattr(config, "AWS_PROFILE", profile)
    monkeypatch.setattr(config, "AWS_REGION", "ap-south-1")
    models._sagemaker_client.cache_clear()
    try:
        assert models._sagemaker_client() == "client:sagemaker-runtime"
    finally:
        models._sagemaker_client.cache_clear()
    assert boto3.sessions == [{"profile_name": expected, "region_name": "ap-south-1"}]
