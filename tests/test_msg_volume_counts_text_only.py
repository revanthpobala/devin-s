from src.clients.llm_client import _IMAGE_CHAR_EQUIV, _message_text_chars


def test_image_counts_as_fixed_equivalent_not_base64_length():
    msgs = [
        {"role": "system", "content": "s" * 1000},
        {"role": "user", "content": [
            {"type": "text", "text": "u" * 500},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 200_000}},
        ]},
        {"role": "tool", "content": "t" * 250},
    ]
    assert _message_text_chars(msgs) == 1750 + _IMAGE_CHAR_EQUIV


def test_local_final_round_keeps_tools_so_prompt_prefix_stays_cacheable():
    from src.clients.llm_client import _stop_tool_calls

    kw = {"tools": [{"type": "function"}], "tool_choice": "auto", "parallel_tool_calls": True}
    _stop_tool_calls(kw, "local")
    assert kw["tools"] and kw["tool_choice"] == "none"
    remote = {"tools": [{"type": "function"}], "tool_choice": "auto"}
    _stop_tool_calls(remote, "openrouter")
    assert "tools" not in remote and "tool_choice" not in remote
