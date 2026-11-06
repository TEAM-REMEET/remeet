"""playground 온톨로지 스크립트가 사용하는 기본 OpenAI 클라이언트"""
import json
import unittest
from io import BytesIO
from unittest.mock import patch

from followup.adapters.llm.openai_client import OpenAIClient


class OpenAIClientTests(unittest.TestCase):
    def test_model_adapter_without_network(self):
        response = {"model": "fake-version", "usage": {"total_tokens": 12},
                    "choices": [{"finish_reason": "stop", "message": {"content": "summary"}}]}
        client = OpenAIClient("fake")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch(
            "followup.adapters.llm.openai_client.urlopen",
            return_value=BytesIO(json.dumps(response).encode()),
        ) as request:
            self.assertEqual(client("system", "input"), "summary")
            payload = json.loads(request.call_args.args[0].data)
            self.assertEqual(payload["model"], "fake")
            self.assertEqual(client.calls[0]["usage"]["total_tokens"], 12)
        response["choices"][0]["finish_reason"] = "length"
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch(
            "followup.adapters.llm.openai_client.urlopen",
            return_value=BytesIO(json.dumps(response).encode()),
        ), self.assertRaises(ValueError):
            client("system", "input")


if __name__ == "__main__":
    unittest.main()
