"use strict";
const assert = require("assert");
const { loadDesk } = require("./helpers/desk_dom_harness");

const h = loadDesk({});
const mk = (title, exposed, same_day) => ({ when: "Thu Oct 15 08:30 ET (06:30 MT)", hours: 10, same_day, pre_open: true, playbook: [],
  releases: [{ title, forecast: "0.6%", previous: "0.4%" }], exposed });
const spread = { underlying: "$NDX", expiry: "2026-10-15", type: "CALL", long_strike: 29200, short_strike: 29225, qty: 1, upside: 110, downside: 2390, ratio: 21.7 };

assert.strictEqual(h.AppDesk.renderMacro({ releases: [], spreads: [] }), "");
const exposed = [{ underlying: "$NDX", expiry: "2026-10-15", legs: 2 }];
const html = h.AppDesk.renderMacro({ releases: [mk("PPI m/m", exposed, true), mk("Fed's Speech", [], false)], spreads: [spread] });
assert.ok(html.includes("risk $2,390 to make $110"), "spread flag missing");
const prompts = h.AppDesk._macroPrompts;
assert.strictEqual(prompts.length, 3, "one button per flagged spread and one per release");
assert.ok(prompts[0].includes("$NDX 2026-10-15 29200/29225 call spread") && prompts[0].includes("$2,390") && prompts[0].includes("PPI m/m"), "spread prompt must carry the flagged row");
assert.ok(prompts[1].includes("$NDX 2026-10-15 (2 legs), expiring that day"), "release prompt must carry the exposed options");
assert.ok(prompts[2].includes("None of my open options expire near it") && !prompts[2].includes("hold or close"), "an unexposed release must not ask about closing");
assert.strictEqual((html.match(/AppChat\.askPortfolioCopilot\(AppDesk\._macroPrompts\[\d\]\)/g) || []).length, 3);
assert.ok(![...html.matchAll(/onclick="([^"]*)"/g)].some(m => m[1].includes("Fed")), "titles never go into an onclick");
console.log("macro card ok");
