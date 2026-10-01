from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from desk.llm import ModelError, OllamaModel, OpenAIModel, ask_json, parse_json
from desk.testing import BrokenModel, ScriptedModel, json_answer

KEYS = ("title", "category", "place")


@pytest.mark.parametrize(
    "answer",
    [
        '{"title": "Принтер", "category": "техника"}',
        '```json\n{"title": "Принтер", "category": "техника"}\n```',
        'Конечно! Вот результат:\n```\n{"title": "Принтер", "category": "техника"}\n```\nГотово.',
        'Вот заявка: {"title": "Принтер", "category": "техника"} — надеюсь, помог.',
    ],
)
def test_json_is_found_in_any_wrapping(answer: str) -> None:
    assert parse_json(answer) == {"title": "Принтер", "category": "техника"}


@pytest.mark.parametrize("answer", ["совсем не json", "", "[1, 2, 3]"])
def test_not_json_is_an_error(answer: str) -> None:
    with pytest.raises(ModelError):
        parse_json(answer)


async def test_unknown_fields_are_dropped() -> None:
    model = ScriptedModel(json_answer(title="Принтер", category="техника", лишнее="ага"))
    answer = await ask_json(model, "система", "вопрос", KEYS)
    assert answer.data == {"title": "Принтер", "category": "техника"}
    assert answer.attempts == 1


async def test_one_repair_attempt_then_success() -> None:
    model = ScriptedModel("я не понял задачу", json_answer(title="Принтер"))
    answer = await ask_json(model, "система", "вопрос", KEYS)
    assert answer.data == {"title": "Принтер"}
    assert answer.attempts == 2
    assert "Верни ТОЛЬКО объект JSON" in model.asked[1]


async def test_hopeless_model_raises() -> None:
    with pytest.raises(ModelError, match="не вернула JSON"):
        await ask_json(BrokenModel(), "система", "вопрос", KEYS)


async def test_ollama_request_and_answer() -> None:
    seen: dict[str, Any] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": '{"title": "Принтер"}'}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        model = OllamaModel("qwen2.5:7b", "http://localhost:11434", client=client)
        assert await model.complete("система", "вопрос") == '{"title": "Принтер"}'
    finally:
        await client.aclose()
    assert seen["url"] == "http://localhost:11434/api/chat"
    payload = seen["payload"]
    assert payload["format"] == "json"  # просим именно JSON
    assert payload["options"]["temperature"] == 0  # и без фантазии
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]


async def test_openai_request_and_answer() -> None:
    seen: dict[str, str] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"title": "Принтер"}'}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        model = OpenAIModel("gpt-4o-mini", "https://api.example.com", "secret-key", client=client)
        assert await model.complete("система", "вопрос") == '{"title": "Принтер"}'
    finally:
        await client.aclose()
    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["auth"] == "Bearer secret-key"


async def test_model_errors_are_explained() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503, text="перегружен")))
    try:
        with pytest.raises(ModelError, match="503"):
            await OllamaModel("qwen", "http://localhost:11434", client=client).complete("s", "u")
    finally:
        await client.aclose()

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет соединения", request=request)

    broken = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    try:
        with pytest.raises(ModelError, match="недоступна"):
            await OllamaModel("qwen", "http://localhost:11434", client=broken).complete("s", "u")
    finally:
        await broken.aclose()
