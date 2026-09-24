from pathlib import Path

target_file = Path("src/ui/routes/research.py")
content = target_file.read_text(encoding="utf-8")

# Increase cache TTL from 15s to 60s
content = content.replace("_REPORT_CACHE_TTL = 15", "_REPORT_CACHE_TTL = 60")

old_loading_code = '''    rep_dir = rep_root / target_date
    raw_dir = raw_root / target_date / ticker_u

    summary_file = rep_dir / f"{ticker_u}_summary.md"
    if not summary_file.exists() and raw_dir.exists():
        for cand_name in (f"{ticker_u}_gemini_thesis.md", f"{ticker_u}_thesis.md"):
            cand = raw_dir / cand_name
            if cand.exists():
                summary_file = cand
                break

    ind_file = rep_dir / f"{ticker_u}_independent.md"
    if not ind_file.exists() and raw_dir.exists():
        cand = raw_dir / f"{ticker_u}_independent_thesis.md"
        if cand.exists():
            ind_file = cand

    arb_file = rep_dir / f"{ticker_u}_arbitration.md"
    if not arb_file.exists() and raw_dir.exists():
        cand = raw_dir / f"{ticker_u}_arbitration.md"
        if cand.exists():
            arb_file = cand

    summary_md = summary_file.read_text(encoding="utf-8") if summary_file.exists() else None
    independent_md = ind_file.read_text(encoding="utf-8") if ind_file.exists() else None
    arbitration_md = arb_file.read_text(encoding="utf-8") if arb_file.exists() else None

    if not arbitration_md and (summary_md or independent_md):
        arbitration_md = f"# {ticker_u} | ARBITRATION & EXECUTIVE RESEARCH DOSSIER ({target_date})\\n\\n*(Displaying primary research report for {target_date})*\\n\\n" + (independent_md or summary_md)'''

new_loading_code = '''    def _read_dossier_files(d_str: str):
        r_d = rep_root / d_str
        w_d = raw_root / d_str / ticker_u
        s_md, i_md, a_md = None, None, None

        # 1. Summary / Model A
        s_cand = r_d / f"{ticker_u}_summary.md"
        if not s_cand.exists() and w_d.exists():
            for c_name in (f"{ticker_u}_gemini_thesis.md", f"{ticker_u}_summary.md", f"{ticker_u}_thesis.md"):
                c_p = w_d / c_name
                if c_p.exists():
                    s_cand = c_p
                    break
        if s_cand and s_cand.exists():
            try:
                s_md = s_cand.read_text(encoding="utf-8")
            except Exception:
                pass

        # 2. Independent / Model B
        i_cand = r_d / f"{ticker_u}_independent.md"
        if not i_cand.exists() and w_d.exists():
            c_p = w_d / f"{ticker_u}_independent_thesis.md"
            if c_p.exists():
                i_cand = c_p
        if i_cand and i_cand.exists():
            try:
                i_md = i_cand.read_text(encoding="utf-8")
            except Exception:
                pass

        # 3. Senior-PM Arbitration
        a_cand = r_d / f"{ticker_u}_arbitration.md"
        if not a_cand.exists() and w_d.exists():
            c_p = w_d / f"{ticker_u}_arbitration.md"
            if c_p.exists():
                a_cand = c_p
        if a_cand and a_cand.exists():
            try:
                a_md = a_cand.read_text(encoding="utf-8")
            except Exception:
                pass

        # 4. Triage fallback if no reports in rep or raw
        if not s_md and not i_md and not a_md:
            triage_cands = [
                config.BASE_DIR / "data" / "triage" / d_str / "_DEEP_RESEARCH" / f"{ticker_u}_gemini_thesis.md",
                config.BASE_DIR / "data" / "triage" / d_str / "force" / f"{ticker_u}_gemini_thesis.md",
                config.BASE_DIR / "data" / "triage" / d_str / f"{ticker_u}_gemini_thesis.md",
            ]
            for tc in triage_cands:
                if tc.exists():
                    try:
                        s_md = tc.read_text(encoding="utf-8")
                        break
                    except Exception:
                        pass

        return s_md, i_md, a_md

    summary_md, independent_md, arbitration_md = _read_dossier_files(target_date)

    # If requested date has no reports, fall back to the most recent date that has reports
    if not summary_md and not independent_md and not arbitration_md:
        for alt_d in available_dates:
            if alt_d == target_date:
                continue
            alt_s, alt_i, alt_a = _read_dossier_files(alt_d)
            if alt_s or alt_i or alt_a:
                summary_md, independent_md, arbitration_md = alt_s, alt_i, alt_a
                target_date = alt_d
                break

    if not arbitration_md and (summary_md or independent_md):
        arbitration_md = f"# {ticker_u} | ARBITRATION & EXECUTIVE RESEARCH DOSSIER ({target_date})\\n\\n*(Displaying primary research report for {target_date})*\\n\\n" + (independent_md or summary_md)'''

if old_loading_code in content:
    content = content.replace(old_loading_code, new_loading_code, 1)
    target_file.write_text(content, encoding="utf-8")
    print("RESEARCH BUNDLE PATCH APPLIED")
else:
    print("TARGET NOT FOUND IN RESEARCH.PY")
