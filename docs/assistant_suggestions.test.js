// docs/assistant_suggestions.test.js
// Tests JS sans framework, exécutables via `node docs/assistant_suggestions.test.js`.
const assert = require('assert');
const { PLACEHOLDER_EXAMPLES, nextPlaceholderIndex } = require('./assistant_suggestions.js');

function test_exemples_du_placeholder() {
  assert.strictEqual(PLACEHOLDER_EXAMPLES.length, 6);
  assert.strictEqual(nextPlaceholderIndex(5, 6), 0);
  assert.strictEqual(nextPlaceholderIndex(2, 6), 3);
}

test_exemples_du_placeholder();
console.log('OK');
