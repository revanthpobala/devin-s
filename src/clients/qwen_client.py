from __future__ import annotations

from openai import OpenAI
import os
from base64 import b64encode

_DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
_client_instance = None

def _get_client():
    global _client_instance
    if _client_instance is None:
        _client_instance = OpenAI(
            api_key=os.getenv("DASHSCOPE_API_KEY") or "EMPTY",
            base_url=_DASHSCOPE_BASE_URL,
        )
    return _client_instance


def query_qwen(
    prompt: str,
    image_paths: list[str] | None = None,
    max_tokens: int = 4096,
    model: str = "qwen3.7-flash",
) -> str:
    content = [{"type": "text", "text": prompt or "Analyze the provided chart and context."}]
    if image_paths:
        for path in image_paths:
            with open(path, "rb") as f:
                img_b64 = b64encode(f.read()).decode("utf-8")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                }
            )

    client = _get_client()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=0.0,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""
