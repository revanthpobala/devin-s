@echo off
title Local LLM Server (Qwen3.8-27B UD-Q6_K_XL + Vision)
echo ===================================================================
echo Starting Local LLM Server - Qwen3.8-27B (UD-Q6_K_XL + Vision)
echo ===================================================================
cd /d "%~dp0"

set "LLM_LOG=%TEMP%\llama_server_qwen38_q6.log"
if exist "%LLM_LOG%" del /f /q "%LLM_LOG%"

REM -m: Qwen3.8-27B UD-Q6_K_XL
REM --mmproj: Vision Projector (mmproj-Qwen3.8-27B-F16.gguf)
REM -c 262144: 256k context (Full context limit for 1 slot)
REM -fa on: Flash Attention
REM -ctk q4_0 -ctv q4_0: Aggressively Quantized KV Cache
REM -ngl 999: Offload all layers across GPUs
REM --split-mode layer: Split evenly across GPUs
REM NOTE: Q6 model (25.3GB) + 1x 256k q4_0 KV Cache (4.9GB) = 30.2GB. Fits in 32GB VRAM!
D:\My-Projects\Stock\llama-cpp-server\llama-server.exe -m "D:\My-Projects\Stock\models\Qwen3.8-27B-UD-Q6_K_XL.gguf" --mmproj "D:\My-Projects\Stock\models\mmproj-Qwen3.8-27B-F16.gguf" --host 127.0.0.1 --port 8000 -c 262144 --parallel 1 -fa on -ctk q4_0 -ctv q4_0 -ngl 999 --split-mode layer -a "qwen" --jinja --reasoning off -lv 4 --log-file "%LLM_LOG%"

pause
