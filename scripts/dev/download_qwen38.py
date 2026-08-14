import os
import shutil
import sys
import time

# Enable high-speed Rust-based hf-transfer engine
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

from huggingface_hub import hf_hub_download

REPO_ID = "unsloth/Qwen3.8-27B-GGUF"
DEST_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "models"))

FILES_TO_DOWNLOAD = [
    "mmproj-F16.gguf",
    "Qwen3.8-27B-UD-Q4_K_XL.gguf",
    "Qwen3.8-27B-UD-Q6_K_XL.gguf",
]

def main():
    os.makedirs(DEST_DIR, exist_ok=True)
    print(f"=== Starting Fast Model Download from {REPO_ID} ===")
    print(f"Destination directory: {DEST_DIR}\n")

    for filename in FILES_TO_DOWNLOAD:
        dest_file = os.path.join(DEST_DIR, filename)
        if os.path.exists(dest_file):
            size_gb = os.path.getsize(dest_file) / (1024 ** 3)
            print(f"[EXISTS] {filename} already exists ({size_gb:.2f} GB). Skipping...")
            continue

        print(f"[DOWNLOADING] {filename}...")
        start_time = time.time()
        try:
            downloaded_path = hf_hub_download(
                repo_id=REPO_ID,
                filename=filename,
                local_dir=DEST_DIR,
                local_dir_use_symlinks=False,
            )
            elapsed = time.time() - start_time
            size_gb = os.path.getsize(downloaded_path) / (1024 ** 3)
            speed = size_gb * 1024 / elapsed if elapsed > 0 else 0
            print(f"[SUCCESS] Downloaded {filename} ({size_gb:.2f} GB) in {elapsed:.1f}s (~{speed:.1f} MB/s)\n")
        except Exception as e:
            print(f"[ERROR] Failed to download {filename}: {e}\n")
            sys.exit(1)

    # Create convenient alias for the mmproj
    src_mmproj = os.path.join(DEST_DIR, "mmproj-F16.gguf")
    alias_mmproj = os.path.join(DEST_DIR, "mmproj-Qwen3.8-27B-F16.gguf")
    if os.path.exists(src_mmproj) and not os.path.exists(alias_mmproj):
        print(f"Creating alias: mmproj-Qwen3.8-27B-F16.gguf -> {src_mmproj}")
        shutil.copyfile(src_mmproj, alias_mmproj)

    print("\n=== All requested files downloaded successfully! ===")

if __name__ == "__main__":
    main()
