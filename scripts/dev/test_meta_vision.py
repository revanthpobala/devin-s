import os
import sys
import time
from openai import OpenAI
import base64

# Force UTF-8 stdout
sys.stdout.reconfigure(encoding='utf-8')

API_URL = "http://127.0.0.1:8000/v1"
CHART_PATH = r"D:\My-Projects\Stock\data\raw\2026-08-14\META_chart_zoom.png"

def encode_image(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode("utf-8")

def main():
    if not os.path.exists(CHART_PATH):
        print(f"Chart path not found: {CHART_PATH}")
        return

    print(f"=== Testing Qwen3.8-27B on Fresh META Zoomed Chart ===")
    print(f"Image: {CHART_PATH}")
    print(f"Target Server: {API_URL}\n")

    base64_img = encode_image(CHART_PATH)

    client = OpenAI(base_url=API_URL, api_key="sk-no-key-required", timeout=600)

    prompt = (
        "Analyze this zoomed 3-month TradingView stock chart for META:\n"
        "1. Identify the ticker, latest price, and visible moving averages.\n"
        "2. Identify key support floor, resistance ceiling, and current trend/breakout structure.\n"
        "3. State a concise trade verdict (Bullish/Bearish/Neutral) with rationale."
    )

    messages = [
        {
            "role": "system",
            "content": "You are a quantitative swing trader. Extract exact price levels and provide concise technical analysis."
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{base64_img}"}
                }
            ]
        }
    ]

    print("Sending request to Qwen3.8-27B (streaming response)...")
    start_time = time.time()
    try:
        response_stream = client.chat.completions.create(
            model="gpt-4",
            messages=messages,
            temperature=0.7,
            max_tokens=1024,
            stream=True,
        )

        first_token = False
        for chunk in response_stream:
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if reasoning:
                if not first_token:
                    print(f"\n[First token in {time.time() - start_time:.1f}s]\n--- [THINKING] ---")
                    first_token = True
                print(reasoning, end="", flush=True)

            content = delta.content
            if content:
                if not first_token:
                    print(f"\n[First token in {time.time() - start_time:.1f}s]\n--- [RESPONSE] ---")
                    first_token = True
                print(content, end="", flush=True)

        elapsed = time.time() - start_time
        print(f"\n\n=== Completed in {elapsed:.1f}s ===")

    except Exception as e:
        print(f"\nError during inference: {e}")

if __name__ == "__main__":
    main()
