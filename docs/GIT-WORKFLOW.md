# Git Workflow and Commit Rules

For the Meridian team. Read once, then use the quick reference at the bottom.

---

## Why this exists

Three reasons, in order of how much they matter to us:

1. **The commit history is our proof of authorship.** We will be asked whether this project is our own work. A public repository with hundreds of small, dated, incremental commits — each one showing something being figured out — is evidence that cannot be manufactured after the fact. A repository with four large commits is not. This is the single strongest thing we can do to answer that question before it is asked.

2. **Three people share one codebase.** A readable history is how we stay out of each other's way and understand what changed while we weren't looking.

3. **We will need to explain decisions months later.** In the viva, in the report, and to ourselves in week 28 when something breaks and nobody remembers why it was built that way.

---

## Rule 1 — Commit early, commit often

**Commit whenever something works, however small.** A passing test. A function that does one thing. A doc paragraph. Do not wait for a feature to be "finished."

Aim for several commits a day when you are working. If you have gone four hours without a commit, you are probably working on too many things at once.

**Push at least once a day**, even on an unfinished branch. Work that exists only on your laptop is work that can be lost, and it is invisible in the history that proves we did this.

---

## Rule 2 — One logical change per commit

A commit should do one thing and be describable in one sentence without using "and."

**Good:** add pass-window computation to the orbit service
**Bad:** add pass windows, fix a typo in the README, bump a dependency, rename a variable

If your commit message needs "and," split the commit. Use `git add -p` to stage parts of a file when you have mixed two changes together.

**Why it matters:** when something breaks in week 20, `git bisect` finds the exact commit that caused it — but only if commits are small enough for that to be useful.

---

## Rule 3 — Message format

We use Conventional Commits. The format is:

```
<type>(<scope>): <subject>

<body — why, not what>

<footer>
```

### Types

| Type | Use for |
|---|---|
| `feat` | New capability |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `test` | Adding or fixing tests |
| `refactor` | Restructuring with no behaviour change |
| `perf` | Performance improvement |
| `chore` | Dependencies, tooling, housekeeping |
| `ci` | CI configuration |
| `spec` | Changes to the MSP specification — **ours, not standard, because spec changes need to stand out** |

### Scopes

Use the module name: `orbit`, `prediction`, `scheduler`, `registry`, `observations`, `reliability`, `store`, `api`, `client`, `simulator`, `dashboard`, `site`, `firmware`, `ingest`, `deploy`, `docs`, `msp`, `repo`.

There is no `platform` scope and no `sim` scope: name the module, or use `repo` for a change that genuinely spans several.

Two of these are easy to confuse:

| Scope | Use for |
|---|---|
| `site` | The static page at **meridian.org.in** — `site/`. Marketing and description, no live data, deployed to Cloudflare Pages. |
| `dashboard` | The live dashboard on `dash.meridian.org.in` — stations, passes, reliability. Reads the observation store, tunnelled from the station. |

They are separate surfaces on purpose; see D-036. A change to the public page is `site`, never `dashboard`.

**This list is enforced.** The `conventions` job in CI checks every commit subject on a PR against it, so a scope that is not here fails the build rather than being quietly accepted.

### Subject line

- Imperative mood: "add", not "added" or "adds"
- No capital letter at the start, no full stop at the end
- **At most 61 characters after `): `** — that is what the `conventions` job measures, and it keeps the whole subject line near 72
- Say what changed, not that you changed something

**Good:**
```
feat(orbit): compute pass windows from element set and station location
fix(registry): reject heartbeats with a stale token instead of 500ing
test(orbit): add TEME to topocentric fixtures from known ISS passes
spec(msp): add held_assignments to heartbeat per D-003
docs(evaluation): record completeness-ratio threshold and rationale
feat(site): draw the orbit ground track on the landing page globe
```

**Bad:**
```
update files
fixed bug
WIP
asdf
Added new feature for computing the pass windows for a station.
```

### Body — write the *why*

The diff already shows what changed. The body explains why, and what you rejected.

```
fix(scheduler): widen assignment window by timing uncertainty

The scheduler was issuing windows at exactly the predicted AOS/LOS.
With a 6-day-old element set the real AOS can be several seconds
early, so the station started recording after the pass had begun.

Windows are now widened by the timing_uncertainty_s the orbit
service reports. Considered a fixed 30s pad instead, but that
over-records on fresh element sets and wastes disk on the Pi.
```

You will thank yourself in month six. Skip the body only when the subject genuinely says everything.

---

## Rule 4 — What never gets committed

| Never | Why |
|---|---|
| `.env`, tokens, the registration invite token | Public repository. Once pushed, assume it is compromised forever — rotate it, don't just delete it. |
| Recorded IQ data, waterfalls, decoded images | Large binaries. They belong in `products/`, which is gitignored. |
| Database dumps, `*.db`, `*.sqlite3` | Same. |
| `__pycache__/`, `.venv/`, `node_modules/` | Generated. Already ignored. |
| Anything you have not read | If you cannot explain a line, it does not go in. |

**If a secret gets committed:** tell the team immediately, rotate the secret, then deal with the history. Removing it from the working tree is not enough — it stays in the history and the repository is public.

---

## Rule 5 — Attribution goes in the same commit

If you read another project's source or documentation to understand an approach, **the `ATTRIBUTION.md` entry lands in the same commit as the code it describes.**

Not the next commit. Not at the end of the week. The same commit.

Retrospective attribution is exactly what the file exists to prevent, and a log where every entry was added weeks later is worth nothing as evidence.

---

## Rule 6 — Branches

```
main                    always working, always deployable
feat/orbit-pass-windows work in progress
fix/registry-token-expiry
spec/msp-0.1-freeze
docs/glossary
```

Branch naming: `<type>/<short-description>`, lowercase, hyphens.

**Nothing is committed directly to `main`.** Everything goes through a pull request, including documentation, including your own module, including one-line fixes. No exceptions — the exceptions are how discipline erodes.

Branch from current `main`, and rebase on `main` before opening a PR so the history stays readable.

---

## Rule 7 — Pull requests

**Keep them under 400 lines of diff.** Beyond that, reviewers stop reading and start nodding. If a change is bigger, split it — usually into interface first, implementation second.

A PR description covers:
- What this does
- Why, and what you considered instead
- How you tested it
- Anything you are unsure about — **say so explicitly, that is what review is for**

**Every PR needs one approving review from someone else.** Yes, even in a team of three. Yes, even for your own module. Especially for your own module — a reviewer who does not know the module is the one who catches the thing you assumed was obvious.

**Review outside your area.** You will be examined on the whole system, not your third of it. Reviewing member 2's orbit code is how you learn enough to defend it.

**Tests pass and CI is green before merge.** Never merge red.

---

## Rule 8 — Review standards

As a reviewer, you are asking:

- Can I explain what this does? If not, ask — the answer is either informative or it catches a problem.
- Is there a test? Does the test actually test the thing?
- Does this cross a module boundary it should not? Only `platform/orbit` imports `sgp4`. Only `platform/reliability` decides what counts as a miss. The scheduler does not read the observation store.
- Does anything handling observation data preserve the `simulated` flag?
- Any secrets, any large files, any `shuffle=True` on time-series data?

Approving a PR means you understood it. If you did not, say so instead of approving.

---

## Rule 9 — Special cases

**MSP spec changes** use `spec(msp):` and always reference the decision: `spec(msp): cap heartbeat assignments at 8 per D-007`. Spec and implementation change in separate PRs — spec first, merged, then implementation against the merged spec.

**Migrations are never edited once merged.** A migration that has run somewhere is history. Fix forward with a new migration.

**Decisions get recorded before the code that depends on them.** If you are about to implement something the docs do not settle, stop and add a `D-` entry to `docs/DECISIONS.md` first. A decision made in code and never written down is a decision nobody can defend in a viva.

---

## Rule 10 — On AI assistance

We use AI coding assistants and we are not hiding it. Two things follow:

**Never commit code you cannot explain.** This is the only rule that matters here. If you cannot walk a teammate through it line by line, it does not get committed, no matter how well it works. You will be asked about arbitrary lines in the viva, and "the AI wrote that part" is the one answer that cannot be recovered from.

**AI-assisted commits carry a `Co-Authored-By` trailer.** Decided in `docs/DECISIONS.md` D-043, which records the argument both ways — trailers are transparent but appear on nearly every commit and add little signal — and why the answer went the way it did. It applies from D-043 forward; commits already on `main` are left as they are rather than rewritten.

This is provenance, not comprehension. Marking a commit does not license committing something you cannot walk a reviewer through, and `ATTRIBUTION.md` — which covers ideas taken from other projects — is a separate obligation kept scrupulously either way.

---

## Quick reference

```
# start work
git checkout main && git pull
git checkout -b feat/orbit-pass-windows

# work in small pieces
git add -p
git commit -m "feat(orbit): compute pass windows from element set"
git push -u origin feat/orbit-pass-windows

# before opening a PR
git fetch origin && git rebase origin/main
# run tests, run ruff, run mypy

# open the PR, get one review, merge, delete the branch
```

**Types:** `feat` `fix` `docs` `test` `refactor` `perf` `chore` `ci` `spec`
**Subject:** imperative, lowercase, no full stop, ≤61 chars after `): `
**Scope:** required — a subject with no `(scope)` fails CI
**Body:** why, not what
**PR:** under 400 lines, one review, green CI
**Never:** secrets, data files, code you cannot explain
