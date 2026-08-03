import os
import random
import glob
import json
from pathlib import Path
import logging

# Ensure project root is in sys.path so we can import src modules
import sys
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.clients.llm_client import query_local_llm, _extract_json_response

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a strict Data QA Auditor.
You are given a TradingView data window screenshot and a JSON object representing the OCR extraction of that image.
Your job is to independently verify that EVERY key-value pair in the JSON exactly matches the visible pixels in the image.

Output your audit strictly as JSON using the following schema:
{
    "match": boolean, // true if the entire JSON perfectly matches the image, false otherwise
    "mismatches": [ // leave empty if perfectly matched
        {
            "key": "The key name",
            "json_value": "The value in the provided JSON",
            "image_value": "The actual value visible in the image"
        }
    ]
}

DO NOT include markdown code fences in your output, just the raw JSON.
"""

def get_random_frame_and_json(base_dir: str, ticker: str = None):
    """Recursively finds all frames, picks a random one, and maps it to the extracted JSON."""
    if ticker:
        search_path = os.path.join(base_dir, "backtesting", "data-windows", "**", ticker.upper(), "frames", "frame_*.png")
    else:
        search_path = os.path.join(base_dir, "backtesting", "data-windows", "**", "frames", "frame_*.png")
    
    frames = glob.glob(search_path, recursive=True)
    
    if not frames:
        logger.error("No frame_*.png files found in data-windows directory.")
        return None, None
        
    random.shuffle(frames)
    
    for frame_path in frames:
        # frame_path looks like: backtesting/data-windows/2026-07-27/LLY/frames/frame_0001.png
        ticker_dir = os.path.dirname(os.path.dirname(frame_path))
        
        # We need the corresponding JSON. Because the JSON filename is like "LLY_Tue 04 Nov 23.json"
        # and we don't know the date strictly from the frame filename, we can read frame_0001.txt,
        # get the date, and build the filename, OR we can just pick a random JSON from the ticker_dir.
        # But for exact matching, we must map them.
        
        # The easiest way: process_backtest_media writes frame_0001.txt
        txt_path = frame_path.replace(".png", ".txt")
        if not os.path.exists(txt_path):
            continue
            
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
            # The text contains the raw JSON. We can extract it just like ocr_to_datawindow does.
            start = raw_text.find("{")
            end = raw_text.rfind("}")
            if start != -1 and end != -1:
                parsed = json.loads(raw_text[start:end+1])
                return frame_path, parsed
        except Exception as e:
            logger.debug(f"Could not parse {txt_path}: {e}")
            pass
            
        # Fallback for old Baidu extractions which are saved as TICKER_X.json
        frame_idx_str = os.path.basename(frame_path).replace("frame_", "").replace(".png", "")
        try:
            frame_idx = int(frame_idx_str)
            ticker_name = os.path.basename(ticker_dir)
            json_fallback_path = os.path.join(ticker_dir, f"{ticker_name}_{frame_idx}.json")
            if os.path.exists(json_fallback_path):
                with open(json_fallback_path, "r", encoding="utf-8") as f:
                    parsed = json.load(f)
                return frame_path, parsed
        except Exception as e:
            logger.debug(f"Could not parse fallback JSON: {e}")
            continue

    logger.error("Could not find any frames with valid .txt or sequentially named JSON files.")
    return None, None


def verify_extraction(ticker: str = None):
    frame_path, json_data = get_random_frame_and_json(project_root, ticker)
    if not frame_path or not json_data:
        return
        
    logger.info(f"Randomly selected frame: {frame_path}")
    logger.info("JSON extracted from frame:")
    print(json.dumps(json_data, indent=2))
    
    user_prompt = f"Here is the JSON extracted from the image:\n```json\n{json.dumps(json_data, indent=2)}\n```\nVerify it against the image."
    
    logger.info("Sending to OpenRouter (Minimax) for verification...")
    
    schema = {
        "type": "object",
        "properties": {
            "match": {"type": "boolean"},
            "mismatches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "json_value": {"type": "string"},
                        "image_value": {"type": "string"}
                    },
                    "required": ["key", "json_value", "image_value"]
                }
            }
        },
        "required": ["match", "mismatches"]
    }
    
    response_text = query_local_llm(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        image_paths=[frame_path],
        use_openrouter=True,
        json_mode=True,
        json_schema=schema,
        use_tools=False,
        max_tokens=2048
    )
    
    result = _extract_json_response(response_text)
    
    print("\n" + "="*50)
    print("                AUDIT RESULT")
    print("="*50)
    if result.get("match"):
        print("[PASS] PERFECT MATCH! OpenRouter verified every key-value pair.")
    else:
        print("[FAIL] MISMATCHES DETECTED:")
        mismatches = result.get("mismatches", [])
        real_mismatches = [m for m in mismatches if str(m.get('json_value')).strip() != str(m.get('image_value')).strip()]
        
        if not real_mismatches:
            print("[PASS] OpenRouter output mismatches array, but all values actually matched perfectly.")
        else:
            for m in real_mismatches:
                print(f"  - Key: '{m.get('key')}'")
                print(f"    - Extracted JSON had: '{m.get('json_value')}'")
                print(f"    - Image actually shows: '{m.get('image_value')}'")
                print("-" * 30)
            
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, default=None, help="Specific ticker to test (e.g. AAPL)")
    args = parser.parse_args()
    verify_extraction(args.ticker)
