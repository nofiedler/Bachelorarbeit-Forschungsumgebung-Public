'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { tokenize, languageForPath, fragmentForTokens, highlight, LIMITS } = require('../src/research_env/static/code-highlight.js');

function fakeDocument() {
  const created = [];
  let document;
  class Node {
    constructor(type, text = '') { this.nodeType = type; this.text = text; this.children = []; this.ownerDocument = document; this.isConnected = true; this.replacements = 0; }
    get textContent() { return this.nodeType === 3 ? this.text : this.children.map(n => n.textContent).join(''); }
    set textContent(value) { this.children = [new Node(3, value)]; }
    get firstChild() { return this.children[0] || null; }
    appendChild(node) { this.children.push(node); return node; }
    replaceChildren(...nodes) { this.replacements++; this.children = nodes.flatMap(n => n.nodeType === 11 ? n.children : [n]); }
    matches(selector) { return selector === '.code-lines' && !!this.lines; }
    querySelectorAll() { return this.lines || []; }
  }
  document = {
    createDocumentFragment() { return new Node(11); },
    createTextNode(text) { return new Node(3, text); },
    createElement(tag) { created.push(tag); return new Node(1); },
  };
  return { document, created, Node };
}

const samples = [
  ['php', '<?php\n/* comment\n second line */\n$secret = "multi\nline";\necho htmlspecialchars($secret);\n?>'],
  ['php', "$sql = <<<'SQL'\nSELECT 'safe';\nSQL;\nreturn $sql;"],
  ['blade', '<!-- HTML\ncomment -->\n@foreach ($items as $item)\n<div class="card">{{ $item->name }}</div>\n@endforeach\n{{-- private\n note --}}'],
  ['html', '<script>const message = `<img src=x onerror=alert(1)>\n${value}`; /* multi\n comment */</script>\n<style>.card { color: red; }</style>'],
  ['javascript', '// comment\r\nconst a = "\\\"<script>alert(1)</script>";\nlet b = `first\nsecond`;'],
  ['json', '{\n  "html": "<img src=x onerror=alert(1)>",\n  "ok": true, "value": 1.5e-4\n}'],
  ['css', '/* multiline\ncomment */\n.card::before { content: "<img>"; width: 10.5rem; }'],
  ['sql', "SELECT 'it''s\n<script>alert(1)</script>' AS value; -- comment\n/* longer\ncomment */"],
  ['yaml', 'message: |\n  class return\n  <img src=x onerror=alert(1)>\nnext: true\nquoted: "multi\nline"\n'],
];
for (const [language, source] of samples) {
  test(language + ' preserves exact source and renders only safe spans: ' + source.slice(0, 24), () => {
    const tokens = tokenize(source, language);
    assert.equal(tokens.map(t => t.text).join(''), source);
    const { document, created } = fakeDocument();
    assert.equal(fragmentForTokens(document, tokens).textContent, source);
    assert.ok(created.every(name => name === 'span'));
  });
}

test('multiline comments, strings, heredoc and YAML scalars preserve lexical state', () => {
  const php = tokenize(samples[0][1], 'php');
  assert.ok(php.some(t => t.type === 'comment' && t.text.includes('second line')));
  assert.ok(php.some(t => t.type === 'string' && t.text === '"multi\nline"'));
  const heredoc = tokenize(samples[1][1], 'php');
  assert.ok(heredoc.some(t => t.type === 'string' && t.text.includes("SELECT 'safe';\nSQL")));
  const yaml = tokenize(samples[8][1], 'yaml');
  assert.ok(yaml.some(t => t.type === 'string' && t.text.includes('class return\n  <img')));
  assert.ok(yaml.some(t => t.type === 'property' && t.text === 'next'));
  const html = tokenize(samples[3][1], 'html');
  assert.ok(html.some(t => t.type === 'comment' && t.text === '/* multi\n comment */'));
  assert.ok(html.some(t => t.type === 'string' && t.text.includes('${value}')));
});

test('filename detection is explicit; unrecognized logs receive no lexical coloring', () => {
  assert.equal(languageForPath('resources/views/INDEX.blade.php'), 'blade');
  assert.equal(languageForPath('C:\\project\\result.JSON'), 'json');
  assert.equal(languageForPath('client.mjs'), 'javascript');
  assert.equal(languageForPath('pipeline.log'), null);
  assert.equal(languageForPath('plain-report.txt'), null);
  const source = 'class null return true\nERROR: <script>unexpected</script>';
  assert.deepEqual(tokenize(source, null), [{ type: null, text: source }]);
});

test('line rendering preserves empty lines, tabs, redactions, anchors and re-entry', () => {
  const { Node } = fakeDocument();
  const values = ['<?php', '/* comment', '', '\tstill comment */', '$key = "[REDACTED]";', 'echo "<script>not executable</script>";'];
  const root = new Node(1); root.dataset = { codePath: 'app/Test.php' };
  root.lines = values.map((text, index) => { const node = new Node(1); node.textContent = text; node.id = 'L' + (index + 1); return node; });
  highlight(root);
  assert.deepEqual(root.lines.map(n => n.textContent), values);
  assert.deepEqual(root.lines.map(n => n.id), values.map((_, i) => 'L' + (i + 1)));
  assert.ok(root.lines[3].children.some(n => n.className === 'code-token code-token-comment'));
  const replacements = root.lines.map(n => n.replacements);
  highlight(root);
  assert.deepEqual(root.lines.map(n => n.replacements), replacements, 'unchanged DOM is not repeatedly rewritten');
  root.lines[5].textContent = 'echo "updated";';
  highlight(root);
  assert.equal(root.lines[5].textContent, 'echo "updated";', 'fresh swapped content is enhanced');
});

test('plain pre text including newlines and malicious tags is never normalized', () => {
  const { Node, created } = fakeDocument();
  const source = '\t{ "tag": "</pre><img src=x onerror=alert(1)>" }\r\n\n';
  const root = new Node(1); root.dataset = { codeLanguage: 'json' }; root.textContent = source;
  highlight(root);
  assert.equal(root.textContent, source);
  assert.ok(created.every(name => name === 'span'));
});

test('large or unknown views remain readable and unmodified', () => {
  const { Node } = fakeDocument();
  const root = new Node(1); root.dataset = { codePath: 'large.php' };
  const source = 'x'.repeat(LIMITS.characters + 1); root.textContent = source;
  highlight(root);
  assert.equal(root.replacements, 0);
  assert.equal(root.textContent, source);
  assert.deepEqual(tokenize(source, 'php'), [{ type: null, text: source }]);
});

test('a long minified line never creates thousands of syntax elements', () => {
  const { Node, created } = fakeDocument();
  const root = new Node(1); root.dataset = { codeLanguage: 'json' };
  const source = '[' + 'true,false,'.repeat(3000) + 'null]'; root.textContent = source;
  highlight(root);
  assert.equal(root.textContent, source);
  assert.equal(created.length, 0, 'long minified input uses a single unchanged text node');
});
