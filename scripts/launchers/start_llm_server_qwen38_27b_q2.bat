@echo off
title Local LLM Server (Qwen3.8-27B UD-Q2_K_XL + Vision - 100% VRAM)
echo ===================================================================
echo Starting Local LLM Server - Qwen3.8-27B (UD-Q2_K_XL + Vision)
echo 10.8 GB Total Footprint - 100% VRAM (Zero RAM Offload)
echo ===================================================================
cd /d "%~dp0"

set "LLM_LOG=%TEMP%\llama_server_qwen38_q2.log"
if exist "%LLM_LOG%" del /f /q "%LLM_LOG%"

REM -m: Qwen3.8-27B UD-Q2_K_XL (9.9 GB)
REM --mmproj: Vision Projector (mmproj-Qwen3.8-27B-F16.gguf)
REM -c 131072: 128k context fits in 16GB VRAM (14.8 GB total footprint with q8_0 KV cache)
REM -fa on: Flash Attention
REM -ctk q8_0 -ctv q8_0: Quantized KV Cache
REM -ngl 999: 100% GPU Offload
D:\My-Projects\Stock\llama-cpp-server\llama-server.exe -m "D:\My-Projects\Stock\models\Qwen3.8-27B-UD-Q2_K_XL.gguf" --mmproj "D:\My-Projects\Stock\models\mmproj-Qwen3.8-27B-F16.gguf" --host 127.0.0.1 --port 8000 -c 131072 --parallel 1 -fa on -ctk q8_0 -ctv q8_0 -ngl 999 -a "gpt-4" --jinja -lv 4 --log-file "%LLM_LOG%"

pause
