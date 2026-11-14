// Every table wider than a phone scrolls inside a frame, and a frame that
// scrolls must be reachable by keyboard and say what it holds (WCAG 2.1.1).
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TableScroll } from "./TableScroll";

describe("a table's scroll frame", () => {
  it("takes focus, and is a region named for what it holds", () => {
    const html = renderToStaticMarkup(
      <TableScroll label="Every pass over the place">
        <table />
      </TableScroll>,
    );
    expect(html).toBe(
      '<div class="table-scroll" tabindex="0" role="region" aria-label="Every pass over the place"><table></table></div>',
    );
  });

  it("keeps the class a view gives it to mark a copy", () => {
    const html = renderToStaticMarkup(
      <TableScroll label="Settled passes" className="table-scroll snapshot">
        <table />
      </TableScroll>,
    );
    expect(html).toContain('class="table-scroll snapshot"');
  });

  it("is the only way a view writes one, so none is left unreachable", () => {
    const sources = import.meta.glob<string>(["./*.tsx", "!./TableScroll.tsx"], {
      query: "?raw",
      import: "default",
      eager: true,
    });
    const bare = Object.entries(sources)
      .filter(([, source]) => /<div className=\{?[^>]*"table-scroll"/.test(source))
      .map(([name]) => name);
    expect(Object.keys(sources).length).toBeGreaterThan(20);
    expect(bare).toEqual([]);
  });
});
