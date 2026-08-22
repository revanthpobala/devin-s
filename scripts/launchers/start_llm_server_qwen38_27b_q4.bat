@echo off
title Local LLM Server (Qwen3.8-27B UD-Q4_K_XL + Vision)
echo ===================================================================
echo Starting Local LLM Server - Qwen3.8-27B (UD-Q4_K_XL + Vision)
echo ===================================================================
cd /d "%~dp0"

set "LLM_LOG=%TEMP%\llama_server_qwen38_q4.log"
if exist "%LLM_LOG%" del /f /q "%LLM_LOG%"

REM -m: Qwen3.8-27B UD-Q4_K_XL
REM --mmproj: Vision Projector (mmproj-Qwen3.8-27B-F16.gguf)
REM -c 262144: 262k total context (divided by 2 slots = 131k context per slot)
REM -fa on: Flash Attention
REM -ctk q8_0 -ctv q8_0: High-Quality Quantized KV Cache
REM -ngl 999: Offload layers to GPU
REM NOTE: Q4 model (17.9GB) + 2x 131k q8_0 KV Cache (9.8GB) = 27.7GB. Safely fits 32GB VRAM!
D:\My-Projects\Stock\llama-cpp-server\llama-server.exe -m "D:\My-Projects\Stock\models\Qwen3.8-27B-UD-Q4_K_XL.gguf" --mmproj "D:\My-Projects\Stock\models\mmproj-Qwen3.8-27B-F16.gguf" --host 127.0.0.1 --port 8000 -c 262144 --parallel 2 -fa on -ctk q8_0 -ctv q8_0 -ngl 999 --split-mode layer -a "qwen" --jinja --reasoning off -lv 4 --log-file "%LLM_LOG%"

pause
