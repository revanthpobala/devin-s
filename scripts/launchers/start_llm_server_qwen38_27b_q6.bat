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
REM -c 32768: 32k context
REM -fa on: Flash Attention
REM -ctk q8_0 -ctv q8_0: Quantized KV Cache
REM -ngl 999: Offload all layers across GPUs
D:\My-Projects\Stock\llama-cpp-server\llama-server.exe -m "D:\My-Projects\Stock\models\Qwen3.8-27B-UD-Q6_K_XL.gguf" --mmproj "D:\My-Projects\Stock\models\mmproj-Qwen3.8-27B-F16.gguf" --host 127.0.0.1 --port 8000 -c 32768 --parallel 2 -fa on -ctk q8_0 -ctv q8_0 -ngl 999 -a "gpt-4" --jinja -lv 4 --log-file "%LLM_LOG%"

pause
