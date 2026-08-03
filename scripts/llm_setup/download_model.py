import os
import sys
import urllib.request

url = "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-UD-Q4_K_XL.gguf"
dest = "D:/My-Projects/Stock/models/Qwen3.5-9B-UD-Q4_K_XL.gguf"

if os.path.exists(dest):
    print(f"File {dest} already exists.")
    sys.exit(0)

print(f"Downloading {url} to {dest}...")
urllib.request.urlretrieve(url, dest)
print("Download complete!")
