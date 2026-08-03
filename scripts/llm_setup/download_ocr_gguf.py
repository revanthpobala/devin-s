import os
import sys
import urllib.request
import threading

def download(url, dest):
    if os.path.exists(dest):
        print(f"File {dest} already exists.")
        return
    print(f"Downloading {url} to {dest}...")
    urllib.request.urlretrieve(url, dest)
    print(f"Download complete: {dest}")

models_dir = "D:/My-Projects/Stock/models"
os.makedirs(models_dir, exist_ok=True)

files = [
    ("https://huggingface.co/bartowski/Qwen2-VL-7B-Instruct-GGUF/resolve/main/Qwen2-VL-7B-Instruct-Q8_0.gguf", f"{models_dir}/Qwen2-VL-7B-Instruct-Q8_0.gguf"),
    ("https://huggingface.co/bartowski/Qwen2-VL-7B-Instruct-GGUF/resolve/main/mmproj-Qwen2-VL-7B-Instruct-f16.gguf", f"{models_dir}/mmproj-Qwen2-VL-7B-Instruct-f16.gguf")
]

threads = []
for url, dest in files:
    t = threading.Thread(target=download, args=(url, dest))
    t.start()
    threads.append(t)

for t in threads:
    t.join()
print("All downloads finished!")
