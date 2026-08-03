@echo off
title Local LLM Server (llama_cpp) - 130k context / 1 slot
echo ===================================================
echo Starting Local LLM Server (llama_cpp) - 130k ctx...
echo ===================================================
cd /d "%~dp0"

REM Log to a temp folder; delete any previous run's log before starting
set "LLM_LOG=%TEMP%\llama_server.log"
if exist "%LLM_LOG%" del /f /q "%LLM_LOG%"

REM Execution Arguments Matrix:
REM -c 130000 / --parallel 1 -> 130000 tokens of context in the SINGLE slot.
REM   The earlier setup was -c 32768 --parallel 4 (8192 tokens/slot). A request
REM   carrying the full Data Window JSON + news dossier + chart payload reached
REM   ~8617 tokens and llama-server rejected it with:
REM     "request (8617 tokens) exceeds the available context size (8192 tokens)"
REM   Running 1 slot at 130k removes the per-slot cap entirely, so even very
REM   large triage/deep-research payloads always fit. Cost: no parallelism, so
REM   concurrent LLM calls are serialized. LLM_LOCAL_CONCURRENCY=1 is kept in
REM   sync with --parallel 1.
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

D:\My-Projects\Stock\llama-cpp-server\llama-server.exe -m "D:\My-Projects\Stock\models\Qwen3.5-9B-Q8_0.gguf" --host 127.0.0.1 --port 8000 -c 130000 --parallel 1 -fa on -ctk q8_0 -ctv q8_0 -ngl 999 -a "gpt-4" --jinja --reasoning off -lv 4 --log-file "%LLM_LOG%"

pause
