import type { Inline } from "./releaseNotes";

// A line's inline parts as elements: never HTML from text, so nothing a bundled
// Markdown file says can become markup. The release notes and the data page's
// credits draw their text through it.

export function Parts({ parts }: { parts: Inline[] }) {
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
