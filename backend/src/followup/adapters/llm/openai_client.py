"""Single OpenAI request per call; the baseline deliberately does not retry."""
import json
import os
from urllib.request import Request, urlopen


class OpenAIClient:
    def __init__(self, model="gpt-6-luna"):
        self.model = model
        self.calls = []

    def __call__(self, system, user):
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ValueError("OPENAI_API_KEY is required")
        payload = {
            "model": self.model,
            "reasoning_effort": "low",
            "max_completion_tokens": 16384,
            "service_tier": "default",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        request = Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=120) as response:
            data = json.load(response)
        self.calls.append({"model": data.get("model"), "usage": data.get("usage")})
        choice = data["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ValueError("Model response did not finish normally")
        content = choice["message"].get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Model returned no text")
        return content
