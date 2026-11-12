import changelog from "../../CHANGELOG.md?raw";

import { parseReleases, type Block, type Inline } from "./releaseNotes";

// Every release, from the CHANGELOG.md this build shipped with.

const RELEASES = parseReleases(changelog);

function Parts({ parts }: { parts: Inline[] }) {
  return (
    <>
      {parts.map((one, index) => {
        const key = `${String(index)}:${one.text}`;
        switch (one.kind) {
          case "strong":
            return <strong key={key}>{one.text}</strong>;
          case "em":
            return <em key={key}>{one.text}</em>;
          case "code":
            return <code key={key}>{one.text}</code>;
          case "link":
            return (
              <a key={key} href={one.href}>
                {one.code ? <code>{one.text}</code> : one.text}
              </a>
            );
          case "text":
            return <span key={key}>{one.text}</span>;
        }
      })}
    </>
  );
}

/** Runs of bullets become one list; headings and paragraphs stand alone. */
function grouped(blocks: Block[]): (Block | Block[])[] {
  const out: (Block | Block[])[] = [];
  for (const one of blocks) {
    const last = out[out.length - 1];
    if (one.kind === "item" && Array.isArray(last)) last.push(one);
    else out.push(one.kind === "item" ? [one] : one);
  }
  return out;
}

export function ReleaseNotes() {
  if (RELEASES.length === 0) {
    return <p className="dim">No release has been made yet.</p>;
  }
  return (
    <div className="release-notes">
      {RELEASES.map((release) => (
        <article key={release.title}>
          <h4>{release.title}</h4>
          {grouped(release.blocks).map((one, index) =>
            Array.isArray(one) ? (
              <ul key={index}>
                {one.map((item, at) => (
                  <li key={at}>
                    <Parts parts={item.parts} />
                  </li>
                ))}
              </ul>
            ) : one.kind === "heading" ? (
              <h5 key={index}>
                <Parts parts={one.parts} />
              </h5>
            ) : (
              <p key={index}>
                <Parts parts={one.parts} />
              </p>
            ),
          )}
        </article>
      ))}
    </div>
  );
}
