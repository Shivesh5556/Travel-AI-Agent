"""Hindi translation endpoint: batching, caching, safe fallback. Model is mocked."""

import asyncio
import json

import config
import llm
import translate


def _fake_llm(calls):
    async def fake_ask_json(system, user, **kw):
        calls.append(user)
        items = json.loads(user)["items"]
        return {"items": [{"id": i["id"], "text": "HI:" + i["text"]} for i in items]}
    return fake_ask_json


def test_translates_dedupes_and_caches(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    calls = []
    monkeypatch.setattr(llm, "ask_json", _fake_llm(calls))

    texts = ["Visit Robber's Cave in the morning.", "Pack a jacket.",
             "Visit Robber's Cave in the morning.", "  "]
    out = asyncio.run(translate.translate_texts(texts, "hi"))
    assert out[0] == "HI:Visit Robber's Cave in the morning."
    assert out[0] == out[2]
    assert out[3] == "  "
    assert len(calls) == 1 and len(json.loads(calls[0])["items"]) == 2   # deduped

    again = asyncio.run(translate.translate_texts(["Pack a jacket."], "hi"))
    assert again == ["HI:Pack a jacket."]
    assert len(calls) == 1                                           # served from cache


def test_model_failure_returns_english_unchanged(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", True)

    async def boom(*a, **k):
        raise llm.LLMUnavailable("rate limited")

    monkeypatch.setattr(llm, "ask_json", boom)
    out = asyncio.run(translate.translate_texts(["Brand new sentence for failure test."], "hi"))
    assert out == ["Brand new sentence for failure test."]


def test_no_key_and_english_are_passthrough(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    assert asyncio.run(translate.translate_texts(["Another unseen sentence."], "hi")) == ["Another unseen sentence."]
    assert asyncio.run(translate.translate_texts(["x"], "en")) == ["x"]


def test_batches_split_on_size():
    items = [(i, "x" * 2000) for i in range(6)]
    sizes = [len(b) for b in translate._batches(items)]
    assert sum(sizes) == 6 and len(sizes) >= 3


def test_endpoint_rejects_unknown_language(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as client:
        assert client.post("/api/translate", json={"lang": "xx", "texts": ["a"]}).status_code == 400
