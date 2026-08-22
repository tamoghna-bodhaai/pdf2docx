/* Mathpix Markdown → Markdown a parser can read.
 *
 * Mathpix returns MMD: Markdown with LaTeX left standing in it. Headings arrive
 * as `\section*{...}`, lists as `\begin{itemize}\item[(a)]...\end{itemize}`,
 * pictures and tables inside float environments. A Markdown parser knows none
 * of that, so every one of those commands used to reach the page as literal
 * backslashes in the middle of a sentence.
 *
 * This pass rewrites them — into Markdown where Markdown can say it, into plain
 * HTML where it cannot (Markdown has no way to write a list whose bullets read
 * "(a)", "(b)", "(c)", and in a question paper those bullets *are* the
 * question numbers). Two rules run through all of it:
 *
 *   * Mathematics is never touched. Every `$…$`, `$$…$$`, `\(…\)` and `\[…\]`
 *     span is lifted out before anything else happens and put back after the
 *     Markdown parser has run, so neither this pass nor Markdown itself can
 *     reinterpret `a_{1}` as emphasis or `\begin{aligned}` as an environment.
 *     KaTeX ends up seeing exactly the characters Mathpix wrote.
 *   * Nothing is deleted for being unrecognised. An unknown command is left on
 *     the page as written, where it is visible and can be reported, rather than
 *     vanishing and leaving a sentence quietly missing a word.
 *
 * The MMD behind a page-by-page preview is routinely malformed, because a page
 * can open an environment that the next page closes. Every construct here
 * therefore degrades to its own contents instead of failing.
 */

(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.mmd = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // Private-use characters: no Markdown meaning, nothing the parser escapes,
  // and nothing that can occur in a document.
  const HOLD_OPEN = '\uE000';
  const HOLD_CLOSE = '\uE001';
  const HOLD_RE = /\uE000(\d+)\uE001/g;

  function escapeHtml(text) {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // --------------------------------------------------------------- masking --

  function findClosingDollar(source, from, fence) {
    for (let i = from; i < source.length; i += 1) {
      if (source[i] === '\\') { i += 1; continue; }
      // Inline mathematics never spans a blank line. Without this an unpaired
      // `$` — a price, a variable named in prose — would swallow the document.
      if (fence === '$' && source.startsWith('\n\n', i)) return -1;
      if (source.startsWith(fence, i)) return i;
    }
    return -1;
  }

  /** Replace every mathematics span with a placeholder, returning both. */
  function maskMath(source) {
    const spans = [];
    const hold = (raw) => HOLD_OPEN + (spans.push(raw) - 1) + HOLD_CLOSE;
    let out = '';
    let i = 0;

    while (i < source.length) {
      const ch = source[i];
      if (ch === '\\') {
        const next = source[i + 1];
        if (next === '(' || next === '[') {
          const close = next === '(' ? '\\)' : '\\]';
          const end = source.indexOf(close, i + 2);
          if (end !== -1) {
            out += hold(source.slice(i, end + 2));
            i = end + 2;
            continue;
          }
        }
        // Every other escape — `\$`, `\%`, `\\` — passes through whole, so that
        // the scanner below still sees it as an escape rather than as a command.
        out += source.slice(i, i + 2);
        i += 2;
        continue;
      }
      if (ch === '$') {
        const fence = source[i + 1] === '$' ? '$$' : '$';
        const end = findClosingDollar(source, i + fence.length, fence);
        if (end !== -1) {
          out += hold(source.slice(i, end + fence.length));
          i = end + fence.length;
          continue;
        }
      }
      out += ch;
      i += 1;
    }
    return { text: out, spans };
  }

  // ---------------------------------------------------------------- reading --

  /** Read `{...}` starting at or just after `from`, honouring nesting. */
  function readGroup(source, from) {
    let i = from;
    while (i < source.length && /\s/.test(source[i])) i += 1;
    if (source[i] !== '{') return null;
    let depth = 0;
    for (let j = i; j < source.length; j += 1) {
      const ch = source[j];
      if (ch === '\\') { j += 1; continue; }
      if (ch === '{') depth += 1;
      else if (ch === '}') {
        depth -= 1;
        if (depth === 0) return { body: source.slice(i + 1, j), end: j + 1 };
      }
    }
    return null;
  }

  /** Read `[...]` starting at or just after `from`, on this line only. */
  function readOptional(source, from) {
    let i = from;
    while (source[i] === ' ' || source[i] === '\t') i += 1;
    if (source[i] !== '[') return null;
    let depth = 0;
    for (let j = i; j < source.length; j += 1) {
      const ch = source[j];
      if (ch === '\\') { j += 1; continue; }
      if (ch === '\n') return null;
      if (ch === '[') depth += 1;
      else if (ch === ']') {
        depth -= 1;
        if (depth === 0) return { body: source.slice(i + 1, j), end: j + 1 };
      }
    }
    return null;
  }

  /** The body of `\begin{name}…\end{name}` opened at `start`, nesting included. */
  function matchEnvironment(source, start, name) {
    const marker = new RegExp('\\\\(begin|end)\\{' + name.replace(/\*/g, '\\*') + '\\}', 'g');
    const bodyStart = start + '\\begin{}'.length + name.length;
    marker.lastIndex = start;
    let depth = 0;
    let match;
    while ((match = marker.exec(source))) {
      depth += match[1] === 'begin' ? 1 : -1;
      if (depth === 0) return { body: source.slice(bodyStart, match.index), end: marker.lastIndex };
    }
    // A page can inherit an environment that the page before it opened and the
    // page after it closes. Reading the rest as the body keeps that content on
    // screen; refusing it would blank the page.
    return { body: source.slice(bodyStart), end: source.length };
  }

  // --------------------------------------------------------------- commands --

  function inlineHtml(text) {
    return convertText(escapeHtml(text)).replace(/\s*\n\s*/g, ' ').trim();
  }

  function heading(level, body) {
    const text = inlineHtml(body);
    return text ? '\n\n' + '#'.repeat(level) + ' ' + text + '\n\n' : '\n\n';
  }

  function aside(className, body) {
    const text = inlineHtml(body);
    return text ? '\n\n<p class="' + className + '">' + text + '</p>\n\n' : '\n\n';
  }

  // Commands whose single argument becomes a block of its own.
  const BLOCK_COMMANDS = {
    title: (body) => heading(1, body),
    section: (body) => heading(2, body),
    subsection: (body) => heading(3, body),
    subsubsection: (body) => heading(4, body),
    paragraph: (body) => heading(5, body),
    subparagraph: (body) => heading(6, body),
    caption: (body) => aside('mmd-caption', body),
    author: (body) => aside('mmd-byline', body),
    date: (body) => aside('mmd-byline', body),
    footnotetext: (body) => aside('mmd-footnote', body),
  };

  // Commands whose single argument stays in the run of text around it.
  const INLINE_COMMANDS = {
    textbf: (body) => '<strong>' + inlineHtml(body) + '</strong>',
    textit: (body) => '<em>' + inlineHtml(body) + '</em>',
    textsl: (body) => '<em>' + inlineHtml(body) + '</em>',
    emph: (body) => '<em>' + inlineHtml(body) + '</em>',
    underline: (body) => '<u>' + inlineHtml(body) + '</u>',
    texttt: (body) => '<code>' + inlineHtml(body) + '</code>',
    textrm: (body) => inlineHtml(body),
    textnormal: (body) => inlineHtml(body),
    textsc: (body) => inlineHtml(body),
    mbox: (body) => inlineHtml(body),
    footnote: (body) => ' <span class="mmd-footnote">(' + inlineHtml(body) + ')</span>',
  };

  // Presentation with no argument and no equivalent here.
  const DROPPED = new Set([
    'noindent', 'centering', 'raggedright', 'raggedleft', 'hfill', 'vfill',
    'hline', 'toprule', 'midrule', 'bottomrule', 'protect', 'maketitle',
    'normalsize', 'small', 'footnotesize', 'scriptsize', 'tiny', 'large',
    'Large', 'LARGE', 'huge', 'Huge', 'bfseries', 'itshape', 'rmfamily',
    'ttfamily', 'sffamily', 'boldmath', 'unboldmath',
  ]);

  // Spacing that separates one block from the next.
  const DROPPED_BLOCK = new Set([
    'pagebreak', 'newpage', 'clearpage', 'cleardoublepage', 'par',
    'smallskip', 'medskip', 'bigskip', 'tableofcontents',
  ]);

  const BREAKS = new Set(['newline', 'linebreak']);

  // Bookkeeping: the command and its argument both go.
  const DROPPED_WITH_ARGUMENT = new Set([
    'captionsetup', 'label', 'ref', 'pageref', 'cite', 'index', 'vspace',
    'hspace', 'setlength', 'renewcommand', 'newcommand', 'graphicspath',
    'addcontentsline', 'markboth', 'markright', 'thispagestyle', 'pagestyle',
  ]);

  const COMMAND_RE = /\\([a-zA-Z]+)(\*?)/y;

  /** Convert everything that is not an environment. Math is already masked. */
  function convertText(source) {
    let out = '';
    let i = 0;

    while (i < source.length) {
      if (source[i] !== '\\') { out += source[i]; i += 1; continue; }

      // `\\` outside mathematics is a line break, optionally with a length.
      if (source[i + 1] === '\\') {
        const option = readOptional(source, i + 2);
        out += '<br>';
        i = option ? option.end : i + 2;
        continue;
      }

      COMMAND_RE.lastIndex = i;
      const command = COMMAND_RE.exec(source);
      if (!command) {
        // `\%`, `\&`, `\_`, `\#`, `\{`, `\}`, `\$`: the character alone.
        out += source[i + 1] === undefined ? '\\' : source[i + 1];
        i += 2;
        continue;
      }

      const name = command[1];
      const after = i + command[0].length;

      if (name === 'begin' || name === 'end') {
        // Only ever an orphan: a page whose partner marker is on another page.
        const group = readGroup(source, after);
        i = group ? group.end : after;
        continue;
      }

      if (name === 'includegraphics') {
        const option = readOptional(source, after);
        const group = readGroup(source, option ? option.end : after);
        if (group) {
          // Written as Markdown rather than as an `<img>` so that the caller's
          // own rewrite of relative image paths still recognises it.
          out += '\n\n![](' + group.body.trim() + ')\n\n';
          i = group.end;
          continue;
        }
      }

      const handler = BLOCK_COMMANDS[name] || INLINE_COMMANDS[name];
      if (handler) {
        const group = readGroup(source, after);
        if (group) { out += handler(group.body); i = group.end; continue; }
      }

      if (DROPPED_WITH_ARGUMENT.has(name)) {
        const group = readGroup(source, after);
        i = group ? group.end : after;
        continue;
      }

      if (BREAKS.has(name)) { out += '<br>'; i = after; continue; }
      if (DROPPED_BLOCK.has(name)) { out += '\n\n'; i = after; continue; }
      if (DROPPED.has(name)) { i = after; continue; }

      out += command[0];
      i = after;
    }
    return out;
  }

  // ----------------------------------------------------------- environments --

  const ENVIRONMENT_RE = /\\begin\{([a-zA-Z*]+)\}/;

  function convertBlocks(source, depth) {
    let out = '';
    let rest = source;
    for (;;) {
      const match = ENVIRONMENT_RE.exec(rest);
      if (!match) return out + convertText(rest);
      out += convertText(rest.slice(0, match.index));
      const name = match[1];
      const found = matchEnvironment(rest, match.index, name);
      const block = convertEnvironment(name, found.body, depth);
      out += depth === 0 ? '\n\n' + block + '\n\n' : block;
      rest = rest.slice(found.end);
    }
  }

  function convertEnvironment(name, body, depth) {
    switch (name) {
      case 'itemize':
      case 'enumerate':
      case 'description':
        return convertList(body, depth);
      case 'tabular':
      case 'tabular*':
      case 'longtable':
        return convertTabular(body);
      case 'verbatim':
        return '```\n' + body.trim() + '\n```';
      default:
        // figure, table, center, abstract, quote, and anything unforeseen: the
        // wrapper says nothing this renderer can show, but its contents do.
        return convertBlocks(body, depth);
    }
  }

  /** Newlines inside a list item, which has to stay on one line for Markdown. */
  function collapse(text) {
    return text.replace(/\s*\n\s*/g, '<br>').replace(/^(?:<br>)+|(?:<br>)+$/g, '').trim();
  }

  const ITEM_RE = /\\item(?![a-zA-Z])|\\begin\{([a-zA-Z*]+)\}/g;

  /** Everything an item carries beyond its first line, indented to stay in it. */
  function indentBlock(block) {
    return block.split('\n').map((line) => (line.trim() ? '  ' + line : '')).join('\n');
  }

  function convertList(body, depth) {
    const items = [];
    let lead = '';
    let current = null;
    let cursor = 0;
    let match;

    const take = (piece) => {
      if (current) current.parts.push(piece);
      else if (!piece.block) lead += piece.value;
      else lead += '\n\n' + piece.value + '\n\n';
    };

    ITEM_RE.lastIndex = 0;
    while ((match = ITEM_RE.exec(body))) {
      take({ block: false, value: convertText(body.slice(cursor, match.index)) });

      if (match[1]) {
        // A list nested inside an item, or a figure standing in one.
        const found = matchEnvironment(body, match.index, match[1]);
        take({ block: true, value: convertEnvironment(match[1], found.body, depth + 1) });
        cursor = found.end;
      } else {
        const option = readOptional(body, match.index + '\\item'.length);
        current = { label: option ? option.body : '', parts: [] };
        items.push(current);
        cursor = option ? option.end : match.index + '\\item'.length;
      }
      ITEM_RE.lastIndex = cursor;
    }
    take({ block: false, value: convertText(body.slice(cursor)) });

    const lines = [];
    for (const item of items) {
      const head = [];
      const blocks = [];
      for (const part of item.parts) {
        if (part.block) {
          if (part.value.trim()) blocks.push(part.value.replace(/^\n+|\n+$/g, ''));
        } else if (blocks.length) {
          if (part.value.trim()) blocks.push(collapse(part.value));
        } else {
          head.push(part.value);
        }
      }
      const text = collapse(head.join(' '));
      const label = item.label.trim()
        ? '<span class="mmd-item-label">' + inlineHtml(item.label) + '</span> '
        : '';
      if (!label && !text && !blocks.length) continue;
      lines.push('- ' + label + text);
      // Anything after the item's own line — a nested list, a figure, a second
      // paragraph — has to clear the bullet, or Markdown reads it as the end of
      // the list rather than as part of this item. A block that is not itself a
      // list also needs a blank line after it: raw HTML runs on to the next
      // blank line, so without one a caption swallows the list beneath it.
      let listed = true;
      for (const block of blocks) {
        const isList = /^\s*- /.test(block);
        if (!isList || !listed) lines.push('');
        lines.push(indentBlock(block));
        listed = isList;
      }
    }

    // A list that declared no items at all — Mathpix emits these around a page
    // break — is nothing, but whatever sat loose inside it is still content.
    const list = lines.length ? '\n' + lines.join('\n') + '\n' : '';
    return lead.trim() ? lead.trim() + '\n' + list : list;
  }

  // ---------------------------------------------------------------- tabular --

  function splitRows(text) {
    const rows = [];
    let current = '';
    let i = 0;
    while (i < text.length) {
      if (text[i] === '\\' && text[i + 1] === '\\') {
        const option = readOptional(text, i + 2);
        rows.push(current);
        current = '';
        i = option ? option.end : i + 2;
        continue;
      }
      if (text[i] === '\\') { current += text.slice(i, i + 2); i += 2; continue; }
      current += text[i];
      i += 1;
    }
    rows.push(current);
    return rows.filter((row) => row.trim() !== '');
  }

  function splitCells(row) {
    const cells = [];
    let current = '';
    let depth = 0;
    for (let i = 0; i < row.length; i += 1) {
      const ch = row[i];
      if (ch === '\\') { current += row.slice(i, i + 2); i += 1; continue; }
      if (ch === '{') depth += 1;
      else if (ch === '}') depth -= 1;
      else if (ch === '&' && depth === 0) { cells.push(current); current = ''; continue; }
      current += ch;
    }
    cells.push(current);
    return cells;
  }

  function cellHtml(cell) {
    const text = cell.trim();
    const multi = /^\\multicolumn\s*/.exec(text);
    if (multi) {
      const count = readGroup(text, multi[0].length);
      const spec = count && readGroup(text, count.end);
      const content = spec && readGroup(text, spec.end);
      if (content) {
        const span = Math.max(1, parseInt(count.body.trim(), 10) || 1);
        return { span, html: inlineHtml(content.body) };
      }
    }
    return { span: 1, html: inlineHtml(text) };
  }

  function convertTabular(body) {
    let rest = body;
    const option = readOptional(rest, 0);
    if (option) rest = rest.slice(option.end);
    const spec = readGroup(rest, 0);
    if (spec) rest = rest.slice(spec.end);
    rest = rest.replace(/\\cline\s*\{[^}]*\}/g, '');

    const rows = splitRows(rest)
      .map((row) => splitCells(row).map(cellHtml))
      .filter((cells) => cells.some((cell) => cell.html !== ''));
    if (!rows.length) return '';

    const html = rows.map((cells) => {
      const tds = cells.map((cell) => (
        cell.span > 1
          ? '<td colspan="' + cell.span + '">' + cell.html + '</td>'
          : '<td>' + cell.html + '</td>'
      ));
      return '<tr>' + tds.join('') + '</tr>';
    });
    // The wrapper scrolls: a table wider than a phone must not widen the page.
    return '<div class="mmd-table"><table><tbody>' + html.join('') + '</tbody></table></div>';
  }

  // ------------------------------------------------------------------- api --

  function tidy(text) {
    return text.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  }

  /**
   * Turn Mathpix Markdown into Markdown, with its mathematics set aside.
   * Feed `markdown` to the Markdown parser, then hand the result and `math`
   * back to `restore` before putting it on the page.
   */
  function prepare(source) {
    const masked = maskMath(source == null ? '' : String(source));
    return { markdown: tidy(convertBlocks(masked.text, 0)), math: masked.spans };
  }

  /** Put the mathematics back into rendered HTML, ready for KaTeX. */
  function restore(html, math) {
    return String(html).replace(HOLD_RE, (whole, index) => {
      const raw = math[Number(index)];
      return raw === undefined ? whole : escapeHtml(raw);
    });
  }

  return { prepare, restore };
});
