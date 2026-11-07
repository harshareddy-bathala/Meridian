// Every page says which way in a reader wants (Stage 37): reading the data
// needs nothing; running a station signs in; wanting to run one leads to the
// site's page on what a station needs. A way not built yet says so, and links
// nowhere.

/** The site's page for someone deciding whether to build a station. */
export const JOIN_URL = "https://meridian.org.in/stations/";

export function WaysIn() {
  return (
    <aside className="ways-in" aria-label="Ways in">
      <p>
        <strong>Exploring the data?</strong> Every page here is open to read, with no sign-in.
      </p>
      <p>
        <strong>Run a station?</strong> Owners will sign in with an owner key. That is still being built.
      </p>
      <p>
        <strong>Want to run one?</strong> <a href={JOIN_URL}>What a station needs, and how it joins</a>.
      </p>
    </aside>
  );
}
