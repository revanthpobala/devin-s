// planLineStyle: rejected plans draw grey + SparseDotted titled "REJECTED …".
// Run: node --test tests/research_rejected_lines.test.js
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'web', 'static', 'js', 'research_report.js'), 'utf8');

// Extract the pure helper and evaluate it with a stubbed LightweightCharts.
const fn = src.match(/function planLineStyle[\s\S]*?\n  \}/)[0];
const LS = { Dashed: 2, SparseDotted: 4 };
const planLineStyle = eval(`(window => { ${fn}; return planLineStyle; })`)({ LightweightCharts: { LineStyle: LS } });

test('accepted plan keeps its color and title', () => {
  assert.deepStrictEqual(planLineStyle(false, '#06b6d4', 'ZONE LOW', null),
    { color: '#06b6d4', title: 'ZONE LOW', style: LS.Dashed });
  assert.deepStrictEqual(planLineStyle(false, '#f43f5e', 'STOP', LS.SparseDotted),
    { color: '#f43f5e', title: 'STOP', style: LS.SparseDotted });
});

test('rejected plan is grey, sparse-dotted, titled REJECTED', () => {
  assert.deepStrictEqual(planLineStyle(true, '#06b6d4', 'ZONE LOW', null),
    { color: '#475569', title: 'REJECTED ZONE LOW', style: LS.SparseDotted });
  assert.deepStrictEqual(planLineStyle(true, '#10b981', 'T1', LS.Dashed),
    { color: '#475569', title: 'REJECTED T1', style: LS.SparseDotted });
});
