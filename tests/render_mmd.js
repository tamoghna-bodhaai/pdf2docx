/* Render Mathpix Markdown exactly as the browser does, for the tests.
 *
 * Reads MMD on stdin and writes HTML on stdout, through the same two files the
 * page loads: the converter and the vendored Markdown parser. KaTeX is not run —
 * these tests are about what reaches it, and it is handed the mathematics
 * verbatim by construction.
 */

const fs = require('fs');
const path = require('path');

const staticDir = path.join(__dirname, '..', 'app', 'static');
const mmd = require(path.join(staticDir, 'mmd.js'));
const marked = require(path.join(staticDir, 'vendor', 'marked.min.js'));

const source = fs.readFileSync(0, 'utf8');
const converted = mmd.prepare(source);
const html = marked.parse(converted.markdown, { gfm: true, breaks: true });
process.stdout.write(
  process.argv[2] === '--markdown'
    ? converted.markdown
    : mmd.restore(html, converted.math)
);
