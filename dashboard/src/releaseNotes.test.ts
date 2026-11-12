// The release notes, read from the changelog this build ships with (D-366).

import { describe, expect, it } from "vitest";

import changelog from "../../CHANGELOG.md?raw";
import { parseInline, parseReleases, REPOSITORY } from "./releaseNotes";

describe("the release notes", () => {
  it("read every release in the changelog, newest first", () => {
    const releases = parseReleases(changelog);
    expect(releases.length).toBeGreaterThan(0);
    expect(releases[0]?.title).toMatch(/^\d+\.\d+\.\d+ — \d{4}-\d{2}-\d{2}$/);
    expect(releases[0]?.blocks.some((one) => one.kind === "item")).toBe(true);
  });

  it("keep bold, code and links as parts, and point a relative link at the repository", () => {
    expect(parseInline("**Predicts** with `uv`, see [the log](docs/DECISIONS.md) or [site](https://meridian.org.in/).")).toEqual([
      { kind: "strong", text: "Predicts" },
      { kind: "text", text: " with " },
      { kind: "code", text: "uv" },
      { kind: "text", text: ", see " },
      { kind: "link", text: "the log", href: `${REPOSITORY}docs/DECISIONS.md`, code: false },
      { kind: "text", text: " or " },
      { kind: "link", text: "site", href: "https://meridian.org.in/", code: false },
      { kind: "text", text: "." },
    ]);
  });

  it("read emphasis, and a link whose text is code", () => {
    expect(parseInline("a cause, or *undetermined*, in [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md)")).toEqual([
      { kind: "text", text: "a cause, or " },
      { kind: "em", text: "undetermined" },
      { kind: "text", text: ", in " },
      { kind: "link", text: "docs/ACCEPTANCE.md", href: `${REPOSITORY}docs/ACCEPTANCE.md`, code: true },
    ]);
  });

  it("never turn the file's text into markup", () => {
    const [release] = parseReleases("## 9.9.9 — 2099-01-01\n\n<script>alert(1)</script>\n");
    expect(release?.blocks).toEqual([{ kind: "paragraph", parts: [{ kind: "text", text: "<script>alert(1)</script>" }] }]);
  });
});
