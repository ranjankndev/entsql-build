import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from benchlib.config import LlmProfile
from benchlib.llm.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from benchlib.llm.base import LLMError, extract_json_object, get_provider
from benchlib.llm.ollama_provider import OllamaProvider
from benchlib.llm.openai_compat_provider import OpenAICompatProvider

SCHEMA = {"type": "object", "properties": {"rows": {"type": "array"}}, "required": ["rows"], "additionalProperties": False}


class StubServer:
    """Local HTTP server that answers POSTs from a queue of (status, body) and records requests."""

    def __init__(self, replies: list[tuple[int, Any]]) -> None:
        self.replies = replies
        self.requests: list[dict[str, Any]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
                status, reply = stub.replies.pop(0)
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def chat_reply(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


class HelpersTest(unittest.TestCase):
    def test_extract_json_object(self) -> None:
        self.assertEqual(extract_json_object('Sure! ```json\n{"rows": [{"a": "{x}"}]}\n```'), {"rows": [{"a": "{x}"}]})
        self.assertEqual(extract_json_object('{bad} then {"ok": 1}'), {"ok": 1})
        with self.assertRaisesRegex(LLMError, "no JSON object"):
            extract_json_object("[1, 2]")

    def test_get_provider(self) -> None:
        log_dir = Path(tempfile.gettempdir())
        self.assertIsNone(get_provider(LlmProfile("none", "none"), log_dir, {}))
        with self.assertRaisesRegex(LLMError, "unknown provider 'gpt'"):
            get_provider(LlmProfile("x", "gpt"), log_dir, {})
        profile = LlmProfile("openrouter", "openai_compat", {"base_url": "http://h", "model": "m", "api_key_env": "OPENROUTER_API_KEY"})
        with self.assertRaisesRegex(LLMError, "OPENROUTER_API_KEY is not set"):
            get_provider(profile, log_dir, {})
        provider = get_provider(profile, log_dir, {"OPENROUTER_API_KEY": "k"})
        self.assertIsInstance(provider, OpenAICompatProvider)
        with self.assertRaisesRegex(LLMError, r"\[llm.o\] is missing model"):
            get_provider(LlmProfile("o", "ollama", {"base_url": "http://h"}), log_dir, {})


class HttpProvidersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.tmp.name) / "logs" / "llm"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_openai_compat_uses_json_schema(self) -> None:
        stub = StubServer([(200, chat_reply('{"rows": [1]}'))])
        try:
            provider = OpenAICompatProvider(stub.url + "/v1/", "m", "secret", self.log_dir)
            self.assertEqual(provider.complete_json("sys", "user", SCHEMA), {"rows": [1]})
        finally:
            stub.close()
        request = stub.requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["auth"], "Bearer secret")
        self.assertEqual(request["body"]["response_format"]["json_schema"]["schema"], SCHEMA)
        logs = list(self.log_dir.glob("*_openai_compat.json"))
        self.assertEqual(len(logs), 1)
        self.assertNotIn("secret", logs[0].read_text())

    def test_openai_compat_falls_back_to_prompt(self) -> None:
        stub = StubServer([(400, {"error": "response_format not supported"}), (200, chat_reply('Here you go: {"rows": []}'))])
        try:
            provider = OpenAICompatProvider(stub.url, "m", None, self.log_dir)
            self.assertEqual(provider.complete_json("sys", "user", SCHEMA), {"rows": []})
        finally:
            stub.close()
        second = stub.requests[1]["body"]
        self.assertNotIn("response_format", second)
        self.assertIn("matching this JSON schema", second["messages"][0]["content"])
        self.assertIsNone(stub.requests[0]["auth"])
        self.assertEqual(len(list(self.log_dir.glob("*.json"))), 2)

    def test_openai_compat_server_error_is_not_retried(self) -> None:
        stub = StubServer([(500, {"error": "boom"})])
        try:
            with self.assertRaisesRegex(LLMError, "HTTP 500"):
                OpenAICompatProvider(stub.url, "m", None, self.log_dir).complete_json("s", "u", SCHEMA)
        finally:
            stub.close()

    def test_ollama(self) -> None:
        stub = StubServer([(200, {"message": {"role": "assistant", "content": '{"rows": ["a"]}'}})])
        try:
            self.assertEqual(OllamaProvider(stub.url, "llama", self.log_dir).complete_json("s", "u", SCHEMA), {"rows": ["a"]})
        finally:
            stub.close()
        body = stub.requests[0]["body"]
        self.assertEqual((stub.requests[0]["path"], body["format"], body["stream"]), ("/api/chat", SCHEMA, False))

    def test_unreachable(self) -> None:
        with self.assertRaisesRegex(LLMError, "cannot reach"):
            OllamaProvider("http://127.0.0.1:9", "m", self.log_dir, timeout=2).complete_json("s", "u", SCHEMA)


class FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.response


def anthropic_response(text: str, stop_reason: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
        to_dict=lambda: {"text": text, "stop_reason": stop_reason},
    )


class AnthropicProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def client(self, response: Any) -> Any:
        return SimpleNamespace(messages=FakeMessages(response), beta=SimpleNamespace(messages=FakeMessages(response)))

    def test_structured_output_request(self) -> None:
        client = self.client(anthropic_response('{"rows": []}'))
        provider = AnthropicProvider(client, "claude-opus-5", self.log_dir, effort="medium")
        self.assertEqual(provider.complete_json("sys", "user", SCHEMA), {"rows": []})
        kwargs = client.messages.kwargs
        self.assertEqual(kwargs["output_config"], {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "medium"})
        self.assertEqual((kwargs["system"], kwargs["messages"]), ("sys", [{"role": "user", "content": "user"}]))
        self.assertNotIn("betas", kwargs)
        self.assertEqual(len(list(self.log_dir.glob("*_anthropic.json"))), 1)

    def test_fallbacks_use_beta_endpoint(self) -> None:
        client = self.client(anthropic_response('{"rows": []}'))
        AnthropicProvider(client, "claude-opus-5", self.log_dir, fallbacks="default").complete_json("s", "u", SCHEMA)
        self.assertEqual(client.beta.messages.kwargs["betas"], [FALLBACK_BETA])
        self.assertEqual(client.beta.messages.kwargs["fallbacks"], "default")

    def test_refusal_and_truncation(self) -> None:
        refused = AnthropicProvider(self.client(anthropic_response("", "refusal")), "m", self.log_dir)
        with self.assertRaisesRegex(LLMError, "declined the request \\(category: cyber\\)"):
            refused.complete_json("s", "u", SCHEMA)
        cut = AnthropicProvider(self.client(anthropic_response('{"rows": [', "max_tokens")), "m", self.log_dir)
        with self.assertRaisesRegex(LLMError, "max_tokens"):
            cut.complete_json("s", "u", SCHEMA)

    def test_api_error_is_logged_and_wrapped(self) -> None:
        class Failing:
            def create(self, **kwargs: Any) -> Any:
                raise RuntimeError("overloaded")

        client = SimpleNamespace(messages=Failing())
        provider = AnthropicProvider(client, "m", self.log_dir, error_types=(RuntimeError,))
        with self.assertRaisesRegex(LLMError, "overloaded"):
            provider.complete_json("s", "u", SCHEMA)
        self.assertIn("overloaded", next(self.log_dir.glob("*.json")).read_text())


if __name__ == "__main__":
    unittest.main()
