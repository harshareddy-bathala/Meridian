// The release notes, from CHANGELOG.md as it was when the dashboard was built.
//
// The file is bundled at build time (Vite's `?raw`), so the notes are always
// the ones that shipped with this build and fetch nothing. Only the handful of
// Markdown forms the changelog uses are read: a release heading, a section
// heading, a bullet, a paragraph, and inline bold, code and links. Rendering
// builds React elements from these parts, never HTML from text, so nothing in
// the file can become markup.

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "strong"; text: string }
  | { kind: "em"; text: string }
  | { kind: "code"; text: string }
  | { kind: "link"; text: string; href: string; code: boolean };

export type Block =
  | { kind: "heading"; parts: Inline[] }
  | { kind: "item"; parts: Inline[] }
  | { kind: "paragraph"; parts: Inline[] };

export interface Release {
  title: string;
  blocks: Block[];
}

/** Where a relative link in the changelog points: the repository it lives in. */
export const REPOSITORY = "https://github.com/harshareddy-bathala/Meridian/blob/main/";

const INLINE = /\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;

function href(target: string): string {
  return /^https?:\/\//.test(target) ? target : `${REPOSITORY}${target.replace(/^\.\//, "")}`;
}

function token(match: RegExpExecArray): Inline {
  const [, strong, em, code, text = "", target = ""] = match;
  if (strong !== undefined) return { kind: "strong", text: strong };
  if (em !== undefined) return { kind: "em", text: em };
  if (code !== undefined) return { kind: "code", text: code };
  // A link whose text is code, such as [`docs/ACCEPTANCE.md`](...).
  const quoted = /^`(.*)`$/.exec(text);
  return { kind: "link", text: quoted?.[1] ?? text, href: href(target), code: quoted !== null };
}

/** A line's bold, code and links, and the plain text between them. */
export function parseInline(line: string): Inline[] {
  const parts: Inline[] = [];
  let at = 0;
  for (const match of line.matchAll(INLINE)) {
    if (match.index > at) parts.push({ kind: "text", text: line.slice(at, match.index) });
    parts.push(token(match));
    at = match.index + match[0].length;
  }
  if (at < line.length) parts.push({ kind: "text", text: line.slice(at) });
  return parts;
}

function block(line: string): Block | null {
  if (line.startsWith("### ")) return { kind: "heading", parts: parseInline(line.slice(4)) };
  if (line.startsWith("- ")) return { kind: "item", parts: parseInline(line.slice(2)) };
  return line.trim() === "" ? null : { kind: "paragraph", parts: parseInline(line.trim()) };
}

/** Every release in the changelog, newest first as the file lists them. */
export function parseReleases(markdown: string): Release[] {
  const releases: Release[] = [];
  for (const line of markdown.split("\n")) {
    if (line.startsWith("## ")) {
      releases.push({ title: line.slice(3).trim(), blocks: [] });
      continue;
    }
    const current = releases[releases.length - 1];
    const parsed = block(line);
    if (current !== undefined && parsed !== null) current.blocks.push(parsed);
  }
  return releases;
}
