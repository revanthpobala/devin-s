"""
scripts/dev/download_latest_qwen.py

Downloads or updates the latest Qwen3.8-27B GGUF weights and vision projector
from Hugging Face (unsloth/Qwen3.8-27B-GGUF).
"""

import os
import sys
import argparse
from pathlib import Path
from huggingface_hub import hf_hub_download, list_repo_files


REPO_ID = "unsloth/Qwen3.8-27B-GGUF"
MODELS_DIR = Path("D:/My-Projects/Stock/models")


def check_and_download(quant: str = "Q6_K_XL", include_mmproj: bool = True):
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"=== Inspecting {REPO_ID} on Hugging Face ===")
    
    files = list_repo_files(repo_id=REPO_ID)
    matching_models = [f for f in files if quant in f and f.endswith(".gguf")]
    
    if not matching_models:
        print(f"No files matching quant '{quant}' found. Available GGUFs:")
        for f in sorted([f for f in files if f.endswith(".gguf")]):
            print(f"  • {f}")
        return

    target_model_file = matching_models[0]
    print(f"\n[Target Model]: {target_model_file}")
    
    # Download Model
    local_model_path = MODELS_DIR / os.path.basename(target_model_file)
    print(f"Downloading/verifying model to: {local_model_path} ...")
    downloaded_path = hf_hub_download(
        repo_id=REPO_ID,
        filename=target_model_file,
        local_dir=str(MODELS_DIR),
        local_dir_use_symlinks=False,
        resume_download=True,
    )
    print(f"✓ Model ready at: {downloaded_path}")

    # Download mmproj if requested
    if include_mmproj:
        mmproj_files = [f for f in files if "mmproj-F16" in f or "mmproj-Qwen3.8-27B-F16" in f]
        target_mmproj = mmproj_files[0] if mmproj_files else "mmproj-F16.gguf"
        print(f"\n[Target Vision Projector]: {target_mmproj}")
        local_mmproj_path = MODELS_DIR / os.path.basename(target_mmproj)
        downloaded_mmproj = hf_hub_download(
            repo_id=REPO_ID,
            filename=target_mmproj,
            local_dir=str(MODELS_DIR),
            local_dir_use_symlinks=False,
            resume_download=True,
        )
        print(f"✓ Vision Projector ready at: {downloaded_mmproj}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quant", default="Q6_K_XL", choices=["Q4_K_XL", "Q4_K_M", "Q5_K_M", "Q6_K_XL", "Q8_0", "Q8_K_XL"])
    parser.add_argument("--no-mmproj", action="store_true")
    args = parser.parse_args()

    check_and_download(quant=args.quant, include_mmproj=not args.no_mmproj)
