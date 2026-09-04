/* Render Mathpix Markdown exactly as the browser does, for the tests.
 *
 * Reads MMD on stdin and writes HTML on stdout, through the same two files the
 * page loads: the converter and the vendored Markdown parser. KaTeX is not run —
 * these tests are about what reaches it, and it is handed the mathematics
 * verbatim by construction.
 */

const fs = require('fs');
const path = require('path');

const { pathToFileURL } = require('url');
const frontendDir = path.join(__dirname, '..', 'frontend');
const mmd = require(path.join(frontendDir, 'features', 'viewer', 'mmd-runtime.js'));

async function run() {
  const markedPath = path.join(frontendDir, 'node_modules', 'marked', 'lib', 'marked.esm.js');
  const { marked } = await import(pathToFileURL(markedPath).href);
  const source = fs.readFileSync(0, 'utf8');
  const converted = mmd.prepare(source);
  const html = marked.parse(converted.markdown, { gfm: true, breaks: true });
  process.stdout.write(
    process.argv[2] === '--markdown'
      ? converted.markdown
      : mmd.restore(html, converted.math)
  );
}

run().catch(error => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
