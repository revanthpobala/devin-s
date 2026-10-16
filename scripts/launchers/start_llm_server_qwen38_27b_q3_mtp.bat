@echo off
title Local LLM Server (Qwen3.8-27B UD-Q3_K_XL + MTP + Vision - 262k Unified Context)
echo ===================================================================
echo Starting Local LLM Server - Qwen3.8-27B (UD-Q3_K_XL + MTP + Vision)
echo Mode: 3 Parallel Slots - Unified 262k Context (262,144 tokens) - KV Unified
echo Speculative Decoding: Multi-Token Prediction (MTP Q4_0, 2 tokens max)
echo Hardware: Dual GPU (RTX 5070 Ti + RTX 5060 Ti) - Layer Split + Flash Attention
echo ===================================================================
cd /d "%~dp0"

set "LLM_LOG=D:\My-Projects\Stock\logs\llama_server.log"
if exist "%LLM_LOG%" del /f /q "%LLM_LOG%"

REM -m: Qwen3.8-27B UD-Q3_K_XL (12.24 GB) - Fast Unsloth Dynamic Quant
REM -md: MTP Draft Model (mtp-Qwen3.8-27B-Q4_0.gguf, 1.28 GB) for speculative decoding
REM --spec-type draft-mtp --spec-draft-n-max 2: 2x speculation step
REM --mmproj: Vision Projector (mmproj-Qwen3.8-27B-F16.gguf)
REM -c 262144: Full 256k native context limit dynamically shared across slots
REM -kvu: Single unified KV buffer dynamically allocated across all 3 slots
REM --parallel 3: 3 concurrency slots (2 for Deep Research, 1 for interactive Chat/Copilot)
REM -fa on: Flash Attention for fast prefill
REM -ctk q4_0 -ctv q4_0: 4-bit Quantized KV Cache
REM -b 2048 -ub 512: Batch size for fast prompt ingestion
REM -ngl 999: 100% GPU Offload across both GPUs
REM --split-mode layer: Parallel tensor distribution across dual GPUs
REM Total Footprint: 12.24 GB (Base) + 1.28 GB (MTP) + 0.86 GB (Vision) + 10 GB (Base+MTP KV) = ~24.5 GB (Fits comfortably in 32GB VRAM with ~7.5GB free!)

D:\My-Projects\Stock\llama-cpp-server\llama-server.exe -m "D:\My-Projects\Stock\models\Qwen3.8-27B-UD-Q3_K_XL.gguf" --mmproj "D:\My-Projects\Stock\models\mmproj-Qwen3.8-27B-F16.gguf" -md "D:\My-Projects\Stock\models\MTP\mtp-Qwen3.8-27B-Q4_0.gguf" --spec-type draft-mtp --spec-draft-n-max 2 --host 127.0.0.1 --port 8000 -c 262144 --parallel 3 -kvu -fa on -ctk q4_0 -ctv q4_0 -b 2048 -ub 512 -ngl 999 --split-mode layer --temp 0.7 --top-p 0.80 --top-k 20 --min-p 0.0 --presence-penalty 1.5 --repeat-penalty 1.0 -a "qwen,gpt-4,qwen-coder,qwen3.8-27b" --jinja --reasoning off -lv 3 --log-file "%LLM_LOG%"

pause
