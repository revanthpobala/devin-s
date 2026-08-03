import glob
import json
import shutil
from pathlib import Path

from src import config


def build_finetune_dataset():
    """
    Crawls historical outputs and builds ShareGPT-formatted JSONL files
    for fine-tuning the local Qwen model. Organizes by date and includes Gemini outputs.
    """
    base_dataset_dir = config.BASE_DIR / "finetune_dataset"
    base_dataset_dir.mkdir(exist_ok=True)

    # Load system prompts
    triage_prompt_path = config.BASE_DIR / "gems" / "revanth-gem-local.md"
    gemini_prompt_path = config.BASE_DIR / "gems" / "revanth-gem.md"

    with open(triage_prompt_path, "r", encoding="utf-8") as f:
        triage_system_prompt = f.read()

    gemini_system_prompt = ""
    if gemini_prompt_path.exists():
        with open(gemini_prompt_path, "r", encoding="utf-8") as f:
            gemini_system_prompt = f.read()

    # Canonical screenshots, data windows and triage theses live under
    # data/raw/<date>/ (produced by tv_scraper + run_local_research). Only tickers
    # flagged for deep research are MOVED into data/triage/<date>/_DEEP_RESEARCH
    # (with their charts + data windows). Crawl data/raw for date dirs; also pull
    # deep-research theses from the _DEEP_RESEARCH folder.
    date_dirs = glob.glob(str(config.BASE_DIR / "data" / "raw" / "*"))

    total_success = 0

    for date_dir in date_dirs:
        date_path = Path(date_dir)
        if not date_path.is_dir() or date_path.name.startswith("_"):
            continue

        date_str = date_path.name
        date_dataset_dir = base_dataset_dir / date_str
        images_dir = date_dataset_dir / "images"

        date_dataset_dir.mkdir(exist_ok=True)
        images_dir.mkdir(exist_ok=True)

        output_file = date_dataset_dir / f"training_data_{date_str}.jsonl"
        success_count = 0

        # Local theses live in data/raw; deep-research theses are moved into
        # data/triage/<date>/_DEEP_RESEARCH. Scan both so all examples are found.
        triage_files = glob.glob(str(date_path / "*_thesis.json"))
        triage_root = config.BASE_DIR / "data" / "triage" / date_str
        deep_dir = triage_root / "_DEEP_RESEARCH"
        if deep_dir.exists():
            triage_files += glob.glob(str(deep_dir / "*_thesis.json"))

        with open(output_file, "w", encoding="utf-8") as out_f:
            # 1. Triage Data
            for thesis_path in triage_files:
                try:
                    ticker = Path(thesis_path).name.replace("_thesis.json", "")
                    folder = Path(thesis_path).parent

                    dw_path = folder / f"{ticker}_datawindow.json"
                    news_path = folder / f"{ticker}_news.json"

                    if not dw_path.exists():
                        continue

                    with open(dw_path, "r") as f:
                        llm_input = json.load(f)

                    if news_path.exists():
                        with open(news_path, "r") as f:
                            news_data = json.load(f)
                        headlines = []
                        if "feed" in news_data:
                            for item in news_data.get("feed", [])[:5]:
                                date = item.get("time_published", "")[:8]
                                if date:
                                    date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
                                title = item.get("title", "")
                                if title:
                                    headlines.append(f"[{date}] {title}")
                        llm_input["headlines"] = headlines
                    else:
                        llm_input["headlines"] = []

                    with open(thesis_path, "r") as f:
                        assistant_output = json.load(f)

                    if "llm_data" in assistant_output:
                        assistant_output = assistant_output["llm_data"]

                    if "triage" not in assistant_output:
                        continue

                    chart_src = folder / f"{ticker}_chart.png"
                    image_ref = ""
                    if chart_src.exists():
                        img_dest = images_dir / f"{ticker}_{date_str}_triage_chart.png"
                        shutil.copy2(chart_src, img_dest)
                        image_ref = f"images/{img_dest.name}"

                    user_msg = json.dumps(llm_input, indent=2)
                    assistant_msg = json.dumps(assistant_output, indent=2)

                    record = {
                        "task": "triage",
                        "messages": [
                            {"role": "system", "content": triage_system_prompt},
                            {"role": "user", "content": user_msg},
                            {"role": "assistant", "content": assistant_msg},
                        ],
                    }

                    if image_ref:
                        record["image"] = image_ref

                    out_f.write(json.dumps(record) + "\n")
                    success_count += 1

                except Exception as e:
                    print(f"Skipping {thesis_path} due to error: {e}")

            # 2. Gemini Deep Research Data
            if gemini_system_prompt:
                # Deep-research theses + their charts/data windows live in
                # data/triage/<date>/_DEEP_RESEARCH (moved there by local research).
                deep_dir = config.BASE_DIR / "data" / "triage" / date_str / "_DEEP_RESEARCH"
                if deep_dir.exists():
                    gemini_files = glob.glob(str(deep_dir / "*_gemini_thesis.md"))
                    for gem_path in gemini_files:
                        ticker = Path(gem_path).name.replace("_gemini_thesis.md", "")
                        # Chart + data window were moved into the deep-research
                        # folder during local-research segregation, alongside the
                        # Gemini thesis.
                        folder = deep_dir

                        chart_src = folder / f"{ticker}_chart.png"
                        dw_path = folder / f"{ticker}_datawindow.json"

                        if not dw_path.exists() or not chart_src.exists():
                            continue

                        try:
                            with open(gem_path, "r", encoding="utf-8") as f:
                                assistant_msg = f.read()

                            with open(dw_path, "r") as f:
                                context_data = {"data_window": json.load(f)}

                            user_msg = f"I am requesting a Deep Research Validation for the ticker: {ticker}.\n\nRAW JSON CONTEXT:\n{json.dumps(context_data, indent=2)}"

                            img_dest = images_dir / f"{ticker}_{date_str}_deep_chart.png"
                            shutil.copy2(chart_src, img_dest)

                            record = {
                                "task": "deep_research",
                                "image": f"images/{img_dest.name}",
                                "messages": [
                                    {"role": "system", "content": gemini_system_prompt},
                                    {"role": "user", "content": user_msg},
                                    {"role": "assistant", "content": assistant_msg},
                                ],
                            }
                            out_f.write(json.dumps(record) + "\n")
                            success_count += 1

                        except Exception as e:
                            print(f"Skipping Gemini file {gem_path} due to error: {e}")

        print(
            f"[{date_str}] Generated {success_count} training examples in {date_dataset_dir.name}"
        )
        total_success += success_count

    print(f"Finished! Total generated training examples: {total_success}")


if __name__ == "__main__":
    build_finetune_dataset()
