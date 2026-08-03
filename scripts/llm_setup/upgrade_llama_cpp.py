import urllib.request
import json
import os
import zipfile
import shutil

SERVER_DIR = r"D:\My-Projects\Stock\llama-cpp-server"

def main():
    print("Fetching latest llama.cpp release info...")
    req = urllib.request.Request("https://api.github.com/repos/ggerganov/llama.cpp/releases/latest")
    with urllib.request.urlopen(req) as res:
        data = json.loads(res.read())
    
    asset_url = None
    for asset in data['assets']:
        name = asset['name']
        # We need the CUDA 12 Windows binary (not just the cudart dlls)
        if 'win-cuda-12' in name and not name.startswith('cudart'):
            asset_url = asset['browser_download_url']
            break
            
    if not asset_url:
        print("Could not find the appropriate CUDA 12 asset in the latest release.")
        return
        
    zip_path = os.path.join(SERVER_DIR, "llama_update.zip")
    print(f"Downloading {asset_url} to {zip_path}...")
    urllib.request.urlretrieve(asset_url, zip_path)
    
    print(f"Extracting {zip_path} over existing installation...")
    # Extract directly into the server directory, overwriting old files
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(SERVER_DIR)
        
    print("Cleaning up zip file...")
    os.remove(zip_path)
    
    print("llama.cpp upgraded successfully!")

if __name__ == "__main__":
    main()
