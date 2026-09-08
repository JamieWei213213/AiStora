from types import SimpleNamespace

from services.llm_service import (
    GeminiModel,
    is_gemini_quota_error,
    is_transient_gemini_error,
)


def test_gemini_model_configures_native_tools(monkeypatch):
    captured = {}

    class Chats:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace()

    fake_client = SimpleNamespace(chats=Chats())
    client_config = {}

    def fake_genai_client(**kwargs):
        client_config.update(kwargs)
        return fake_client

    monkeypatch.setattr("google.genai.Client", fake_genai_client)

    model = GeminiModel(
        "test-key",
        "test-model",
        request_timeout_seconds=12,
        max_output_tokens=321,
    )
    model.start_agent("agent instructions", [{
        "name": "finish",
        "description": "Finish.",
        "parameters": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    }])

    assert captured["model"] == "test-model"
    assert captured["config"].automatic_function_calling.disable is True
    assert captured["config"].tools[0].function_declarations[0].name == "finish"
    function_config = captured["config"].tool_config.function_calling_config
    assert function_config.mode.value == "VALIDATED"
    assert function_config.allowed_function_names == ["finish"]
    assert captured["config"].max_output_tokens == 321
    assert client_config["http_options"].timeout == 12_000


def test_quota_error_detection_handles_sdk_text_and_nested_causes():
    direct = RuntimeError(
        "429 RESOURCE_EXHAUSTED: You exceeded your current quota"
    )
    nested = RuntimeError("request failed")
    nested.__cause__ = direct

    assert is_gemini_quota_error(direct) is True
    assert is_gemini_quota_error(nested) is True
    assert is_gemini_quota_error(RuntimeError("500 server error")) is False


def test_transient_errors_retry_but_quota_errors_do_not(monkeypatch):
    fake_client = SimpleNamespace()
    monkeypatch.setattr("google.genai.Client", lambda **kwargs: fake_client)
    model = GeminiModel(
        "test-key",
        "test-model",
        max_retries=2,
        retry_base_seconds=0,
    )
    attempts = {"count": 0}

    def transient_operation():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("503 SERVICE_UNAVAILABLE")
        return "ok"

    assert model._request(transient_operation) == "ok"
    assert attempts["count"] == 3
    assert is_transient_gemini_error(RuntimeError("503 UNAVAILABLE")) is True
    assert is_transient_gemini_error(
        RuntimeError("429 RESOURCE_EXHAUSTED quota")
    ) is False
