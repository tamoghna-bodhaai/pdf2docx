import { describe, expect, it } from "vitest";
import { dressMmdLists, prepareMmd, restoreMmd } from "./mmd";

describe("Mathpix Markdown preparation", () => {
  it("converts document commands while preserving mathematics exactly", () => {
    const prepared = prepareMmd("\\section*{Results}\n\\textbf{Answer}: $a_{1} < b$\\\\next");
    expect(prepared.markdown).toContain("## Results");
    expect(prepared.markdown).toContain("<strong>Answer</strong>");
    expect(prepared.markdown).not.toContain("a_{1}");
    expect(restoreMmd(prepared.markdown, prepared.math)).toContain("$a_{1} &lt; b$");
  });

  it("turns image and list constructs into renderable Markdown", () => {
    const prepared = prepareMmd("\\begin{itemize}\\item One\\item Two\\end{itemize}\n\\includegraphics[width=2in]{figures/a.png}");
    expect(prepared.markdown).toContain("- One");
    expect(prepared.markdown).toContain("- Two");
    expect(prepared.markdown).toContain("![](figures/a.png)");
  });

  it("marks lists that carry Mathpix's own item labels", () => {
    document.body.innerHTML = '<ol><li><span class="mmd-item-label">(a)</span> One</li></ol>';
    dressMmdLists(document.body);
    expect(document.querySelector("ol")).toHaveClass("mmd-labelled");
  });
});
