from pathlib import Path

target_file = Path("src/ui/services/copilot_context.py")
content = target_file.read_text(encoding="utf-8")

old_snippet = '''    if explicit_ticker:
        exp_u = explicit_ticker.strip().upper()
        if exp_u not in ("GENERAL", "AUTO", "NONE", "ALL", ""):
            has_dollar_ticker = any(matched_by.get(t) == "dollar" for t in detected)
            if not has_dollar_ticker:
                if exp_u in detected:
                    detected.remove(exp_u)
                detected.insert(0, exp_u)
                matched_by[exp_u] = "modal"'''

new_snippet = '''    if explicit_ticker:
        exp_u = explicit_ticker.strip().upper()
        if exp_u not in ("GENERAL", "AUTO", "NONE", "ALL", ""):
            has_prompt_ticker = any(matched_by.get(t) in ("dollar", "keyword") for t in detected)
            if not has_prompt_ticker:
                if exp_u in detected:
                    detected.remove(exp_u)
                detected.insert(0, exp_u)
                matched_by[exp_u] = "modal"'''

if old_snippet in content:
    content = content.replace(old_snippet, new_snippet, 1)
    target_file.write_text(content, encoding="utf-8")
    print("PATCH SUCCESSFUL")
else:
    print("OLD SNIPPET NOT FOUND")
