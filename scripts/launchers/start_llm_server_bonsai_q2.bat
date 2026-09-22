@echo off
title Local LLM Server (Ternary Bonsai 2 27B PQ2_0 + Vision - PrismML Fork)
echo ===================================================================
echo Starting Local LLM Server - Ternary Bonsai 2 27B (PQ2_0 + Vision)
echo PrismML Fork (prism-b10709) - Required for ternary quant type 142
echo 3 Parallel Slots x 200k Context Each - Dual GPU Layer Split
echo ===================================================================
cd /d "%~dp0"

set "LLM_LOG=%TEMP%\llama_server_bonsai_q2.log"
if exist "%LLM_LOG%" del /f /q "%LLM_LOG%"

REM -m: Ternary Bonsai 2 27B PQ2_0 (~5.9 GB) - requires PrismML fork (ggml type 142)
REM --mmproj: Vision Projector (mmproj-Qwen3.8-27B-F16.gguf, ~1.2 GB)
REM -c 614400: 3 slots x 200k context each (3 x 204800)
REM -fa on: Flash Attention (O(n) memory scaling)
REM -ctk q4_0 -ctv q4_0: 4-bit KV Cache
REM -b 2048 -ub 512: Large batch for fast prompt ingestion
REM -ngl 999: 100%% GPU Offload
REM --split-mode layer: Distribute layers across RTX 5070 Ti + RTX 5060 Ti
REM Total est: ~5.9 GB (model) + 1.2 GB (vision) + ~19.6 GB (KV 3x200k q4_0) + ~1.6 GB compute = ~28.3 GB
REM NOTE: llama-server.exe here is the PrismML fork (prism-b10709-9a9394a, CUDA 12.4)
D:\My-Projects\Stock\llama-cpp-prism\llama-server.exe -m "D:\My-Projects\Stock\models\Ternary-Bonsai-2-27B-PQ2_0.gguf" --mmproj "D:\My-Projects\Stock\models\mmproj-Qwen3.8-27B-F16.gguf" --host 127.0.0.1 --port 8000 -c 614400 --parallel 3 -fa on -ctk q4_0 -ctv q4_0 -b 2048 -ub 512 -ngl 999 --split-mode layer -a "gpt-4" --jinja -lv 4 --log-file "%LLM_LOG%"

pause
