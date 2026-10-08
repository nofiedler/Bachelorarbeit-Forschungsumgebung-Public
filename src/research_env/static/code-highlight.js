/* Local, presentation-only code highlighting. Source text is never interpreted as HTML. */
(function () {
  'use strict';
  const LIMITS = { characters: 240000, lines: 5000, tokens: 12000, lineTokens: 1000, lineCharacters: 20000 };
  const TYPES = new Set(['comment', 'string', 'keyword', 'number', 'variable', 'property', 'tag', 'punctuation', 'function']);
  const WORDS = {
    php: 'abstract and array as break callable case catch class clone const continue declare default do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile enum eval exit extends final finally fn for foreach function global goto if implements include include_once instanceof insteadof interface isset list match namespace new or print private protected public readonly require require_once return static switch throw trait try unset use var while xor yield true false null self parent',
    javascript: 'as async await break case catch class const continue debugger default delete do else export extends false finally for from function get if import in instanceof let new null of return set static super switch this throw true try typeof undefined var void while with yield',
    sql: 'all alter and as asc between by case check column constraint create cross database default delete desc distinct drop else end exists false foreign from full group having in index inner insert into is join key left like limit not null offset on or order outer primary references right select set table then true union unique update using values view when where with',
    css: 'important inherit initial none revert unset',
    json: 'true false null', yaml: 'true false null yes no on off',
  };
  const keywords = Object.fromEntries(Object.entries(WORDS).map(([language, words]) => [language, new Set(words.split(' '))]));
  const aliases = { js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript', ts: 'javascript', tsx: 'javascript', htm: 'html', yml: 'yaml', jsonl: 'json' };
  const supported = new Set(['php', 'blade', 'html', 'json', 'javascript', 'css', 'sql', 'yaml']);
  const numberPattern = /(?:0[xX][\da-fA-F]+|0[bB][01]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)/y;
  const identifierPattern = /[A-Za-z_$][A-Za-z0-9_$\\-]*/y;
  const tagPattern = /<\/?[A-Za-z][A-Za-z0-9:._-]*/y;
  const attributePattern = /[A-Za-z_:][A-Za-z0-9:._-]*(?=\s*=)/y;
  const directivePattern = /@[A-Za-z_][A-Za-z0-9_]*/y;
  const processed = new WeakMap();
  const queued = new WeakSet();

  function languageForPath(path) {
    const clean = String(path || '').toLowerCase();
    if (clean.endsWith('.blade.php')) return 'blade';
    const extension = clean.split(/[\\/]/).pop().split('.').pop();
    const language = aliases[extension] || extension;
    return supported.has(language) ? language : null;
  }
  function match(pattern, source, index) {
    pattern.lastIndex = index;
    const found = pattern.exec(source);
    return found ? found[0] : '';
  }
  function tokenize(source, language) {
    if (!supported.has(language) || source.length > LIMITS.characters) return [{ type: null, text: source }];
    const tokens = [];
    let index = 0;
    let mode = language === 'html' || language === 'blade' || (language === 'php' && source.includes('<?')) ? 'html' : 'code';
    let codeLanguage = language === 'blade' ? 'php' : language;
    let closing = null;
    let tagName = '';
    let closingTag = false;
    let directiveDepth = 0;
    function emit(type, end) {
      const text = source.slice(index, end);
      const previous = tokens[tokens.length - 1];
      if (previous && previous.type === type) previous.text += text;
      else tokens.push({ type, text });
      index = end;
    }
    function through(marker, from) {
      const end = source.indexOf(marker, from);
      return end < 0 ? source.length : end + marker.length;
    }
    function quoted(quote) {
      let end = index + 1;
      while (end < source.length) {
        if (source[end] === '\\') { end += 2; continue; }
        if (source[end] === quote) {
          if ((codeLanguage === 'sql' || codeLanguage === 'yaml') && source[end + 1] === quote) { end += 2; continue; }
          return end + 1;
        }
        end += 1;
      }
      return source.length;
    }
    while (index < source.length) {
      if (tokens.length >= LIMITS.tokens) { emit(null, source.length); break; }
      const ch = source[index];
      if (source.startsWith('{{--', index) && language === 'blade') { emit('comment', through('--}}', index + 4)); continue; }
      if (mode === 'html') {
        if (source.startsWith('<!--', index)) { emit('comment', through('-->', index + 4)); continue; }
        if (source.startsWith('<?', index)) {
          const tag = source.slice(index).match(/^<\?(?:php\b|=)?/)[0];
          emit('tag', index + tag.length); mode = 'code'; codeLanguage = 'php'; closing = '?>'; continue;
        }
        if (language === 'blade' && (source.startsWith('{{', index) || source.startsWith('{!!', index))) {
          const raw = source.startsWith('{!!', index);
          emit('punctuation', index + (raw ? 3 : 2)); mode = 'code'; codeLanguage = 'php'; closing = raw ? '!!}' : '}}'; continue;
        }
        const directive = language === 'blade' ? match(directivePattern, source, index) : '';
        if (directive) {
          emit('keyword', index + directive.length);
          if (directive === '@php') { mode = 'code'; codeLanguage = 'php'; closing = '@endphp'; }
          else if (/^\s*\(/.test(source.slice(index))) { mode = 'code'; codeLanguage = 'php'; closing = 'directive'; directiveDepth = 0; }
          continue;
        }
        const tag = match(tagPattern, source, index);
        if (tag) {
          tagName = tag.replace(/^<\/?/, '').toLowerCase(); closingTag = tag.startsWith('</');
          emit('tag', index + tag.length); mode = 'tag'; continue;
        }
        emit(null, index + 1); continue;
      }
      if (mode === 'tag') {
        if (ch === '"' || ch === "'") { emit('string', quoted(ch)); continue; }
        if (source.startsWith('/>', index) || ch === '>') {
          const selfClosing = source.startsWith('/>', index);
          emit('tag', index + (selfClosing ? 2 : 1));
          if (!closingTag && !selfClosing && (tagName === 'script' || tagName === 'style')) {
            mode = 'code'; codeLanguage = tagName === 'script' ? 'javascript' : 'css'; closing = '</' + tagName;
          } else mode = 'html';
          continue;
        }
        const attribute = match(attributePattern, source, index);
        if (attribute) { emit('property', index + attribute.length); continue; }
        emit(ch === '=' ? 'punctuation' : null, index + 1); continue;
      }
      if (closing && closing !== 'directive' && source.slice(index, index + closing.length).toLowerCase() === closing) {
        if (closing.startsWith('</')) { mode = 'html'; closing = null; continue; }
        emit(closing === '?>' ? 'tag' : 'punctuation', index + closing.length); mode = 'html'; closing = null; continue;
      }
      if (closing === 'directive') {
        if (ch === '(') directiveDepth += 1;
        if (ch === ')') {
          directiveDepth -= 1; emit('punctuation', index + 1);
          if (directiveDepth === 0) { mode = 'html'; closing = null; }
          continue;
        }
      }
      if (source.startsWith('/*', index) && ['php', 'javascript', 'css', 'sql'].includes(codeLanguage)) {
        emit('comment', through('*/', index + 2)); continue;
      }
      if ((source.startsWith('//', index) && ['php', 'javascript'].includes(codeLanguage)) ||
          (source.startsWith('--', index) && codeLanguage === 'sql') ||
          (ch === '#' && ['php', 'yaml'].includes(codeLanguage))) {
        const end = source.indexOf('\n', index); emit('comment', end < 0 ? source.length : end); continue;
      }
      if (codeLanguage === 'php' && source.startsWith('<<<', index)) {
        const start = source.slice(index).match(/^<<<[ \t]*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\r\n]*\r?\n/);
        if (start) {
          const tail = source.slice(index + start[0].length);
          const end = new RegExp('^[ \\t]*' + start[2] + '(?=[;,\\r\\n]|$)', 'm').exec(tail);
          emit('string', end ? index + start[0].length + end.index + end[0].length : source.length); continue;
        }
      }
      if (codeLanguage === 'yaml' && (ch === '|' || ch === '>')) {
        const header = source.slice(index).match(/^[|>][+-]?[1-9]?(?:[ \t]*(?:#[^\n]*)?)?\r?\n/);
        if (header) {
          const lineStart = source.lastIndexOf('\n', index - 1) + 1;
          const indent = source.slice(lineStart, index).match(/^[ \t]*/)[0].length;
          let end = index + header[0].length;
          while (end < source.length) {
            const newline = source.indexOf('\n', end);
            const next = newline < 0 ? source.length : newline + 1;
            const line = source.slice(end, next);
            if (line.trim() && line.match(/^[ \t]*/)[0].length <= indent) break;
            end = next;
          }
          emit('string', end); continue;
        }
      }
      if (ch === '"' || ch === "'" || (ch === '`' && ['php', 'javascript'].includes(codeLanguage))) {
        const end = quoted(ch);
        const property = ['json', 'yaml'].includes(codeLanguage) && /^\s*:/.test(source.slice(end));
        emit(property ? 'property' : 'string', end); continue;
      }
      const number = match(numberPattern, source, index);
      if (number) { emit('number', index + number.length); continue; }
      const word = match(identifierPattern, source, index);
      if (word) {
        let type = null;
        if (codeLanguage === 'php' && word.startsWith('$')) type = 'variable';
        else if ((keywords[codeLanguage] || new Set()).has(codeLanguage === 'sql' ? word.toLowerCase() : word)) type = 'keyword';
        else if (['css', 'yaml'].includes(codeLanguage) && /^\s*:/.test(source.slice(index + word.length))) type = 'property';
        else if (['php', 'javascript'].includes(codeLanguage) && /^\s*\(/.test(source.slice(index + word.length))) type = 'function';
        emit(type, index + word.length); continue;
      }
      emit(/[{}()[\];,.?:=]/.test(ch) ? 'punctuation' : null, index + 1);
    }
    return tokens;
  }

  function fragmentForTokens(document, tokens) {
    const fragment = document.createDocumentFragment();
    for (const token of tokens) {
      if (token.type && TYPES.has(token.type)) {
        const span = document.createElement('span');
        span.className = 'code-token code-token-' + token.type;
        span.textContent = token.text;
        fragment.appendChild(span);
      } else fragment.appendChild(document.createTextNode(token.text));
    }
    return fragment;
  }
  function schedule(callback) {
    if (typeof requestIdleCallback === 'function') requestIdleCallback(callback, { timeout: 250 });
    else setTimeout(callback, 0);
  }
  function highlight(root) {
    const explicit = root.dataset.codeLanguage;
    const language = explicit ? (aliases[explicit] || explicit) : languageForPath(root.dataset.codePath);
    if (!supported.has(language)) return;
    const lines = root.matches('.code-lines') ? Array.from(root.querySelectorAll('.code-line > code')) : [root];
    if (!lines.length || lines.length > LIMITS.lines || lines.every(line => processed.has(line) && processed.get(line) === line.firstChild)) return;
    const texts = lines.map(line => line.textContent);
    const source = texts.join('\n');
    if (source.length > LIMITS.characters) return;
    const tokens = tokenize(source, language);
    let lineIndex = 0, tokenIndex = 0, tokenOffset = 0;
    function renderBatch() {
      if (!root.isConnected) return;
      const stop = Math.min(lineIndex + 64, lines.length);
      for (; lineIndex < stop; lineIndex += 1) {
        const line = lines[lineIndex];
        const original = texts[lineIndex];
        let remaining = original.length;
        const pieces = [];
        let plainLine = original.length > LIMITS.lineCharacters;
        while (remaining > 0 && tokenIndex < tokens.length) {
          const token = tokens[tokenIndex];
          const length = Math.min(remaining, token.text.length - tokenOffset);
          if (pieces.length >= LIMITS.lineTokens) plainLine = true;
          if (!plainLine) pieces.push({ type: token.type, text: token.text.slice(tokenOffset, tokenOffset + length) });
          remaining -= length; tokenOffset += length;
          if (tokenOffset === token.text.length) { tokenIndex += 1; tokenOffset = 0; }
        }
        // The newline joins code-line elements for lexing only; it is never inserted into a line.
        if (lineIndex < lines.length - 1 && tokenIndex < tokens.length) {
          tokenOffset += 1;
          if (tokenOffset === tokens[tokenIndex].text.length) { tokenIndex += 1; tokenOffset = 0; }
        }
        if (line.textContent !== original) return;
        const fragment = fragmentForTokens(line.ownerDocument, plainLine ? [{ type: null, text: original }] : pieces);
        if (fragment.textContent !== original) return;
        line.replaceChildren(fragment);
        processed.set(line, line.firstChild);
      }
      if (lineIndex < lines.length) schedule(renderBatch);
    }
    renderBatch();
  }
  function enhance(scope) {
    if (!scope || !scope.querySelectorAll) return;
    const nodes = Array.from(scope.querySelectorAll('[data-code-path], [data-code-language]'));
    if (scope.matches && scope.matches('[data-code-path], [data-code-language]')) nodes.unshift(scope);
    for (const root of nodes) {
      if (queued.has(root)) continue;
      queued.add(root);
      schedule(function () { queued.delete(root); highlight(root); });
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { tokenize, languageForPath, fragmentForTokens, enhance, highlight, LIMITS };
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { enhance(document); }, { once: true });
    else enhance(document);
    document.addEventListener('htmx:afterSwap', function (event) { enhance(event.detail && event.detail.target ? event.detail.target : event.target); });
  }
}());
