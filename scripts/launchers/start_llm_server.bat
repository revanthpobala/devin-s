@echo off
title Local LLM Server (llama_cpp)
echo ===================================================
echo Starting Local LLM Server (llama_cpp)...
echo ===================================================
cd /d "%~dp0"

REM Log to a temp folder; delete any previous run's log before starting
set "LLM_LOG=%TEMP%\llama_server.log"
if exist "%LLM_LOG%" del /f /q "%LLM_LOG%"

REM Execution Arguments Matrix:
REM -c 32768 / --parallel 4 -> 8192 tokens of context PER SLOT (32768/4). The triage
REM   prompt (gem-local + few-shot + Data Window + schema) is ~6218 tokens; at the
REM   old -c 24000/--parallel 4 (6000/slot) that EXCEEDED the per-slot budget and
REM   llama-server returned HTTP 400 -> 7 tickers fell back to deterministic with no
REM   LLM screen. 8192/slot gives ~1900 tokens of headroom so the prompt always fits.
REM   NOTE: 32k Q8 KV cache uses MORE VRAM than the old 24k (the safe no-VRAM-change
REM   alternative is --parallel 3 -> 8000/slot). We take the larger context because
REM   the root-cause fix is per-slot headroom; LLM_LOCAL_CONCURRENCY=4 is kept in
REM   sync with --parallel 4.
REM -fa on                   -> Enables Flash Attention for speed.
REM -ctk q8_0 / -ctv q8_0    -> Quantizes the KV Cache keys/values to Q8 to save VRAM.
REM --jinja                  -> Restores custom template control for per-request kwargs.
REM --reasoning off           -> Disables thinking server-side. Qwen3.5 via llama-server
REM                            cannot emit BOTH clean JSON content AND a thinking trace:
REM                            with deepseek format the JSON lands in reasoning_content
REM                            (content empty); with none format the <think> block pollutes
REM                            content and breaks JSON parsing / trips the GBNF grammar.
REM                            With thinking OFF we can now ALSO enforce a strict GBNF
REM                            json_schema on local calls (no <think> to conflict) -> near
REM                            zero parse failures. The deterministic filter (not the LLM)
REM                            owns the actual trading verdict.

D:\My-Projects\Stock\llama-cpp-server\llama-server.exe -m "D:\My-Projects\Stock\models\Qwen3.5-9B-Q8_0.gguf" --host 127.0.0.1 --port 8000 -c 32768 --parallel 4 -fa on -ctk q8_0 -ctv q8_0 -ngl 999 -a "gpt-4" --jinja --reasoning off -lv 4 --log-file "%LLM_LOG%"

pause