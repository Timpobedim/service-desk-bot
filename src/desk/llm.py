"""Языковая модель — необязательная деталь, а не сердце бота.

Поддерживаются локальная Ollama и любой сервис с OpenAI-совместимым API. Ответ модели проверяется по
схеме: неизвестные поля выбрасываются, неверные значения отбрасываются, а на непохожий на JSON ответ
делается одна попытка починки. Не получилось — работаем по правилам, а не падаем.

Ответ модели не отдаётся в json.loads как есть: лишний абзац не превращается в «не удалось понять
запрос», а выдумка модели — в напоминание с чужой датой.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
BRACES = re.compile(r"\{.*\}", re.DOTALL)
TIMEOUT = httpx.Timeout(60.0, connect=5.0)
REPAIR = "Ответ не разобрался как JSON: {error}. Верни ТОЛЬКО объект JSON с полями {keys}, без пояснений и без ```."


class ModelError(RuntimeError):
    pass


class Model(Protocol):
    name: str

    async def complete(self, system: str, user: str) -> str: ...


@dataclass
class NullModel:
    """Модель не настроена: бот работает на правилах."""

    name: str = "нет модели"

    async def complete(self, system: str, user: str) -> str:
        raise ModelError("Модель не настроена")


class OllamaModel:
    """Локальная модель через Ollama: POST /api/chat с format=json."""

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", client: httpx.AsyncClient | None = None):
        self.name = f"ollama:{model}"
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = client

    async def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        data = await _post(self._client, f"{self.base_url}/api/chat", payload, {})
        message = data.get("message") or {}
        return str(message.get("content", ""))


class OpenAIModel:
    """Любой сервис с OpenAI-совместимым API: POST /v1/chat/completions."""

    def __init__(self, model: str, base_url: str, api_key: str, client: httpx.AsyncClient | None = None):
        self.name = f"openai:{model}"
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client

    async def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = await _post(self._client, f"{self.base_url}/v1/chat/completions", payload, headers)
        choices = data.get("choices") or [{}]
        return str((choices[0].get("message") or {}).get("content", ""))


async def _post(
    client: httpx.AsyncClient | None, url: str, payload: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    own = client is None
    session = client or httpx.AsyncClient(timeout=TIMEOUT)
    try:
        response = await session.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise ModelError(f"Модель ответила {response.status_code}: {response.text[:200]}")
        return dict(response.json())
    except httpx.HTTPError as exc:
        raise ModelError(f"Модель недоступна: {exc}") from exc
    finally:
        if own:
            await session.aclose()


def parse_json(text: str) -> dict[str, Any]:
    """JSON из ответа модели: голый, в ```-ограждении или окружённый рассуждениями."""
    for candidate in (text.strip(), *(match.group(1) for match in FENCE.finditer(text))):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    match = BRACES.search(text)
    if match:
        try:
            data = json.loads(match.group())
        except ValueError as exc:
            raise ModelError(f"ответ не похож на JSON: {exc}") from exc
        if isinstance(data, dict):
            return data
    raise ModelError("в ответе нет объекта JSON")


@dataclass(frozen=True)
class Answer:
    data: dict[str, Any]
    attempts: int
    raw: str


async def ask_json(model: Model, system: str, user: str, keys: tuple[str, ...], repairs: int = 1) -> Answer:
    """Спросить модель и получить словарь. Одна попытка починки — и хватит."""
    question, error = user, ""
    for attempt in range(1, repairs + 2):
        raw = await model.complete(system, question)
        try:
            data = parse_json(raw)
        except ModelError as exc:
            error = str(exc)
            question = f"{user}\n\n{REPAIR.format(error=error, keys=', '.join(keys))}"
            continue
        return Answer({key: value for key, value in data.items() if key in keys}, attempt, raw)
    raise ModelError(f"Модель не вернула JSON после {repairs + 1} попыток: {error}")
