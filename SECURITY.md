# Security policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.** Write to [hello@meridian.org.in](mailto:hello@meridian.org.in) with "security" in the subject, or report it privately through [GitHub's security advisories](https://github.com/harshareddy-bathala/Meridian/security/advisories/new).

Say what you found, where, and how to reproduce it. A person reads that address, not a queue. We aim to reply within a week, to agree a fix and a date with you before anything is published, and to credit you in the advisory unless you would rather we did not.

## What is in scope

- **The platform:** the public read API under `/api/v1`, the MSP endpoints under `/msp/`, and the metrics endpoint.
- **The station client and the simulator.**
- **The deployment:** the compose files, the front, and the tools in `deploy/`.
- **The dashboard** at `dash.meridian.org.in` and **the site** at `meridian.org.in`.
- **The protocol itself:** a flaw in [MSP](docs/MSP-SPEC.md) that every implementation would inherit.

Of particular interest: a way to register a station without an invite, to act as another station, to read anything the public API is not meant to give (see [`docs/THREAT-MODEL.md`](docs/THREAT-MODEL.md) §7), or to make a simulated record pass for a measured one.

**Out of scope:** findings that need physical access to a station, denial of service by volume, and reports from automated scanners with no demonstrated impact.

## Supported versions

Meridian is pre-1.0. Fixes go to `main` and into the next release; there are no back-ported branches yet.

## How the project defends itself

[`docs/THREAT-MODEL.md`](docs/THREAT-MODEL.md) lists the assets, the trust boundaries, every threat considered and the code or decision that answers it. Dependencies are watched by Dependabot, and CI audits the Python and dashboard dependencies on every pull request.
