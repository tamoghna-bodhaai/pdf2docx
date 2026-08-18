# Vendored assets

The rendered preview typesets Markdown and mathematics in the browser. Both
libraries are kept here rather than loaded from a CDN so the page works with no
network at all — the same promise the conversion itself makes.

| Path | Library | Version | Licence | Upstream |
|---|---|---|---|---|
| `marked.min.js` | marked | 18.0.5 | MIT | <https://github.com/markedjs/marked> |
| `katex/katex.min.js`, `katex/katex.min.css` | KaTeX | 0.16.47 | MIT | <https://github.com/KaTeX/KaTeX> |
| `katex/contrib/auto-render.min.js` | KaTeX auto-render | 0.16.47 | MIT | as above |
| `katex/fonts/*.woff2` | KaTeX fonts | 0.16.47 | OFL 1.1 | as above |

Only KaTeX's `.woff2` fonts are kept. `katex.min.css` lists `woff2`, `woff` and
`ttf` for each face in that order, and every browser that can run this page
takes the first — so the other two formats are dead weight (they are most of
KaTeX's size).

To update, replace the files from the matching `npm` package's `dist/`
directory and change the versions in the table above. Nothing here is edited.
