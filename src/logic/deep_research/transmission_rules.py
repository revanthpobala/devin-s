"""transmission_rules.py — the channel playbook for event -> ticker transmission chains.

A small data table of known transmission channels. Each channel names the policy/macro area,
the groups it hits, and the proxy symbols (drawn only from ``market_events.TAPE``) whose
last-session move can confirm or contradict the chain. These are unmeasured channels —
hypotheses for the writer to consider, not facts and no edge claim.
"""
from __future__ import annotations

from typing import Dict, Tuple

CHANNELS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "POLITICAL_CONTROL": {
        "areas": "election outcomes shifting datacenter and energy permits, EV credits, tariffs, antitrust, drug pricing, defense",
        "sectors": "broad market, policy-sensitive groups",
        "proxies": ("SPY", "QQQ", "TLT"),
    },
    "AI_CAPEX": {
        "areas": "AI and datacenter capex, chip supply, hyperscaler spending",
        "sectors": "semiconductors, AI hardware, Asia tech supply chain",
        "proxies": ("SOXX", "SMH", "NVDA", "TSM", "EWT", "EWY"),
    },
    "RATES": {
        "areas": "central-bank policy, yields, long-duration discount rates",
        "sectors": "growth equities, bonds, rate-sensitive groups",
        "proxies": ("TLT", "QQQ"),
    },
    "CHINA_TARIFF": {
        "areas": "tariffs, export controls, China demand and supply chains",
        "sectors": "multinationals, chips, autos, industrials",
        "proxies": ("FXI", "EWT"),
    },
    "EV_POLICY": {
        "areas": "EV credits, emissions and fuel-economy rules, EV subsidies",
        "sectors": "autos, batteries, charging",
        "proxies": ("QQQ", "FXI"),
    },
    "OIL": {
        "areas": "crude supply, OPEC decisions, energy prices",
        "sectors": "energy, transports, airlines, chemicals",
        "proxies": ("USO",),
    },
    "KEY_PERSON_POLITICAL": {
        "areas": "a founder's or executive's political exposure, government contracts, investigations",
        "sectors": "founder-led and headline-driven names",
        "proxies": ("QQQ", "IWM"),
    },
    "USD_RISK_OFF": {
        "areas": "risk-off flows, dollar strength, safe-haven demand",
        "sectors": "broad market, high-beta names",
        "proxies": ("GLD", "^VIX", "TLT"),
    },
    "REGULATION": {
        "areas": "antitrust, legal rulings, agency action against a company or industry",
        "sectors": "the targeted industry and its peers",
        "proxies": ("SPY", "QQQ"),
    },
    "DEFENSE_GEO": {
        "areas": "geopolitics, conflicts, sanctions, defense spending",
        "sectors": "defense, energy, shippers",
        "proxies": ("^VIX", "GLD", "USO"),
    },
}


def rules_prompt_block() -> str:
    """The channel table injected into the writer prompt — hypotheses to consider, not facts."""
    lines = ["TRANSMISSION CHANNELS (unmeasured — hypotheses to consider, not facts; no edge claim):",
             "channel | policy & macro areas | groups hit | confirm proxies"]
    for name, c in CHANNELS.items():
        lines.append(f"{name} | {c['areas']} | {c['sectors']} | {', '.join(c['proxies'])}")
    return "\n".join(lines)
