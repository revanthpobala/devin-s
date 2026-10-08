@echo off
title Local LLM Server (Qwen3.8-27B Uncensored Q4_0 + Vision - 393k Context Turbo Mode)
echo ===================================================================
echo Starting Local LLM Server - Qwen3.8-27B Uncensored (Q4_0 + Vision)
echo Mode: 3 Parallel Slots (131k/slot) - Unified 393k Context (393,216 tokens) - YaRN RoPE 1.5x
echo Hardware: Dual GPU (RTX 5070 Ti + RTX 5060 Ti) - Layer Split + Flash Attention + KV Unified
echo ===================================================================
cd /d "%~dp0"

set "LLM_LOG=%TEMP%\llama_server_qwen38_q4_uncensored.log"
if exist "%LLM_LOG%" del /f /q "%LLM_LOG%"

REM -m: Qwen3.8-27B Uncensored Q4_0 (16.69 GB) - Fast Unsloth Dynamic Quant
REM --mmproj: Vision Projector (mmproj-Qwen3.8-27B-F16.gguf)
REM -c 393216: 393k context limit (131,072 tokens / slot for 3 slots)
REM --rope-scaling yarn --rope-scale 1.5: YaRN context extension beyond 262k native training context
REM -kvu: Single unified KV buffer dynamically shared across all 3 slots
REM -fa on: Flash Attention for O(n) memory and fast prefill
REM -ctk q4_0 -ctv q4_0: 4-bit Quantized KV Cache (7.5 GB for full 393k tokens)
REM -b 2048 -ub 512: Large batch size to ingest 30k+ token Gem/Bible prompts in fast parallel chunks
REM -ngl 999: 100% GPU Offload across both GPUs
REM --split-mode layer: Parallel tensor-level compute distribution across dual GPUs
REM Total Footprint: 16.69 GB (Model) + 0.86 GB (Vision) + 7.50 GB (KV Cache) + 1.85 GB (Compute 3-slot) = ~26.90 GB (Fits in 32GB VRAM with ~5.7GB free!)

D:\My-Projects\Stock\llama-cpp-server\llama-server.exe -m "C:\Users\Revanth\Downloads\orcarouter_Qwen3.8-27B-Uncensored-Q4_0.gguf" --mmproj "D:\My-Projects\Stock\models\mmproj-Qwen3.8-27B-F16.gguf" --host 127.0.0.1 --port 8000 -c 393216 --parallel 3 -kvu --rope-scaling yarn --rope-scale 1.5 -fa on -ctk q4_0 -ctv q4_0 -b 2048 -ub 512 -ngl 999 --split-mode layer --temp 0.7 --top-p 0.80 --top-k 20 --min-p 0.0 --presence-penalty 1.5 --repeat-penalty 1.0 -a "qwen,gpt-4,qwen-coder,qwen3.8-27b" --jinja --reasoning off -lv 3 --log-file "%LLM_LOG%"

pause
