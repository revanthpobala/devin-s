import os
import sys
import time
from openai import OpenAI
import base64

# Force UTF-8 stdout
sys.stdout.reconfigure(encoding='utf-8')

API_URL = "http://127.0.0.1:8000/v1"
CHART_PATH = r"D:\My-Projects\Stock\data\raw\2026-07-13\AAPL_chart.png"

def encode_image(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode("utf-8")

def main():
    if not os.path.exists(CHART_PATH):
        print(f"Chart path not found: {CHART_PATH}")
        return

    print(f"=== Testing Qwen3.8-27B Vision & Reasoning (Streaming) ===")
    print(f"Image: {CHART_PATH}")
    print(f"Target Server: {API_URL}\n")

    base64_img = encode_image(CHART_PATH)

    client = OpenAI(base_url=API_URL, api_key="sk-no-key-required", timeout=600)

    prompt = (
        "Carefully examine this TradingView stock chart image and answer the following questions clearly:\n"
        "1. Identify the ticker symbol, exchange, timeframe, and the latest visible price/date.\n"
        "2. Read visible indicators, moving averages, price levels, and overlays.\n"
        "3. Describe the overall price action, trend structure, key support/resistance levels.\n"
        "4. Provide your technical assessment / trading bias (Bullish/Bearish/Neutral)."
    )

    messages = [
        {
            "role": "system",
            "content": "You are an expert technical analyst. Extract exact prices, dates, and technical structures from the chart."
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
            max_tokens=2048,
            stream=True,
        )

        in_thinking = False
        first_token_time = None
        full_content = []
        full_thinking = []

        for chunk in response_stream:
            delta = chunk.choices[0].delta
            
            # Check for reasoning chunk
            reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if reasoning:
                if not first_token_time:
                    first_token_time = time.time()
                    print(f"\n[Prompt Ingested in {first_token_time - start_time:.1f}s - Generating Reasoning...]\n")
                if not in_thinking:
                    print("--- [THINKING TRACE] ---")
                    in_thinking = True
                print(reasoning, end="", flush=True)
                full_thinking.append(reasoning)

            # Check for content chunk
            content = delta.content
            if content:
                if not first_token_time:
                    first_token_time = time.time()
                    print(f"\n[Prompt Ingested in {first_token_time - start_time:.1f}s - Generating Content...]\n")
                if in_thinking:
                    print("\n--- [FINAL RESPONSE] ---")
                    in_thinking = False
                print(content, end="", flush=True)
                full_content.append(content)

        elapsed = time.time() - start_time
        print(f"\n\n=== Completed in {elapsed:.1f}s ===")

    except Exception as e:
        print(f"\nError during inference: {e}")

if __name__ == "__main__":
    main()
