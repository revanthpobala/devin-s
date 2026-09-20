@echo off
title Local LLM Server (Qwen3.8-27B UD-Q6_K + Vision - 256k Context 100%% VRAM)
echo ===================================================================
echo Starting Local LLM Server - Qwen3.8-27B (UD-Q6_K + Vision)
echo Mode: 1 Parallel Slot - 256k Context (262,144 tokens) - 100%% GPU VRAM
echo Hardware: Dual GPU (RTX 5070 Ti + RTX 5060 Ti) - Full Vision + Coding
echo ===================================================================
cd /d "%~dp0"

set "LLM_LOG=%TEMP%\llama_server_qwen38_q6.log"
if exist "%LLM_LOG%" del /f /q "%LLM_LOG%"

REM -m: Qwen3.8-27B UD-Q6_K (20.47 GB) - High Q6 Precision
REM --mmproj: Vision Projector (mmproj-Qwen3.8-27B-F16.gguf)
REM -c 262144: 256k native context limit for 1 parallel thread/slot
REM -fa on: Flash Attention
REM -ctk q4_0 -ctv q4_0: 4-bit Quantized KV Cache (4.3 GB for 256k tokens across 16 attention layers)
REM -b 2048 -ub 2048: Large batch size to ingest 30k+ token Gem/Bible prompts in fast parallel chunks
REM -ngl 999: 100% GPU Offload (66/66 layers offloaded, Zero CPU RAM spillover)
REM --split-mode row: Parallel tensor-level compute distribution across dual GPUs
REM Total Footprint: 20.47 GB (Model) + 0.86 GB (Vision) + 4.30 GB (KV Cache) + 1.60 GB (Compute) = ~27.23 GB (Fits in 32GB VRAM!)
REM
REM Official Unsloth Sampling Settings for Qwen3.8:
REM - Instruct Mode (non-thinking/coding): --temp 0.7 --top-p 0.80 --top-k 20 --min-p 0.0 --presence-penalty 1.5 --repeat-penalty 1.0 --reasoning off
D:\My-Projects\Stock\llama-cpp-server\llama-server.exe -m "D:\My-Projects\Stock\models\Qwen3.8-27B-UD-Q6_K.gguf" --mmproj "D:\My-Projects\Stock\models\mmproj-Qwen3.8-27B-F16.gguf" --host 127.0.0.1 --port 8000 -c 348576 --parallel 1 -fa on -ctk q4_0 -ctv q4_0 -b 1024 -ub 512 -ngl 999 --split-mode layer --temp 0.7 --top-p 0.80 --top-k 20 --min-p 0.0 --presence-penalty 1.5 --repeat-penalty 1.0 -a "qwen,gpt-4,qwen-coder,qwen3.8-27b" --jinja --reasoning off -lv 3 --log-file "%LLM_LOG%"

pause



