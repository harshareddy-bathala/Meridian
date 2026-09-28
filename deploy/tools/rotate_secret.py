"""Create and rotate the platform's secret files. Stdlib only.

    python deploy/tools/rotate_secret.py init
    python deploy/tools/rotate_secret.py rotate token_hash_pepper
    python deploy/tools/rotate_secret.py retire token_hash_pepper
    python deploy/tools/rotate_secret.py set tunnel_token < token.txt

The files live in `deploy/secrets/`, one secret per file, and
`deploy/docker-compose.secrets.yml` mounts each one into the containers that read
it through the `*_FILE` variables (D-114, D-201). This tool writes them so that
nobody has to remember the modes or the order:

- the directory is `0700`, so no other user on the host can reach a file in it;
- each file is `0444`, because each one is bind-mounted into a container whose
  process is not the operator's uid, and a `0600` file would be unreadable there;
- a file is replaced by writing a new one beside it and renaming it into place,
  never edited, so a reader sees the old secret or the new one and never half.

A renamed file is a new inode, and a bind mount keeps the old one, so every
change ends with the containers that read it being recreated. The tool prints
that command rather than running it: which services to restart is the step an
operator should see.

**The tunnel token is issued by Cloudflare, not generated here.** `set` reads it
from standard input, so it never appears in the shell's history or in `ps`.

**Rotating the pepper keeps the old one.** It moves to `token_hash_pepper_previous`,
which the platform accepts for verification only and re-hashes away from as
stations call (D-201). `retire` empties it once the overlap is over.
"""

from __future__ import annotations

import argparse
import os
import secrets
import stat
import sys
from pathlib import Path

SECRETS_DIR = Path(__file__).resolve().parents[1] / "secrets"
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

PLACEHOLDER = "change-me"
DIR_MODE = stat.S_IRWXU
FILE_MODE = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH

PEPPER = "token_hash_pepper"
PREVIOUS_PEPPER = "token_hash_pepper_previous"

GENERATED = {
    PEPPER: "TOKEN_HASH_PEPPER",
    "metrics_token": "METRICS_TOKEN",
    "registration_invite_token": "REGISTRATION_INVITE_TOKEN",
}
"""Secrets this tool can generate, and the variable each replaces."""

TUNNEL_TOKEN = "tunnel_token"
"""Issued by Cloudflare; stored by `set`, read by docker-compose.public.yml."""

READERS = {
    PEPPER: ("api",),
    "metrics_token": ("api", "jobs", "prometheus"),
    "registration_invite_token": ("api",),
    TUNNEL_TOKEN: ("tunnel",),
}
"""The services that read each secret at start and must be recreated."""

COMPOSE = (
    "docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.secrets.yml"
)


class ToolError(Exception):
    """A step was refused; the message is what the operator reads."""


def env_values(env_file: Path) -> dict[str, str]:
    """`KEY=value` pairs from a compose `.env` file, or nothing if it is absent."""
    if not env_file.is_file():
        return {}
    values = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip().strip("'\"")
    return values


def new_secret() -> str:
    """32 random bytes as hex, the form `openssl rand -hex 32` gives."""
    return secrets.token_hex(32)


def write_secret(directory: Path, name: str, value: str) -> Path:
    """Put `value` in `directory/name`, atomically, with `FILE_MODE`."""
    target = directory / name
    staging = directory / f".{name}.new"
    staging.unlink(missing_ok=True)
    with staging.open("x", encoding="utf-8") as handle:
        handle.write(value + "\n" if value else "")
        handle.flush()
        os.fsync(handle.fileno())
    staging.chmod(FILE_MODE)
    staging.replace(target)
    return target


def read_secret(directory: Path, name: str) -> str:
    """The secret in `directory/name`, or refuse if there is none."""
    path = directory / name
    if not path.is_file():
        raise ToolError(f"{path} does not exist; run `init` first")
    return path.read_text(encoding="utf-8").strip()


def init(directory: Path, env: dict[str, str]) -> list[str]:
    """Create the directory and every missing file; return what was written.

    A secret already set in `.env` is carried over rather than regenerated: a
    new pepper would strand every station, and a new metrics token would stop
    every scrape, for a change that was only meant to move the value to a file.
    """
    directory.mkdir(mode=DIR_MODE, exist_ok=True)
    directory.chmod(DIR_MODE)
    written = []
    for name, variable in GENERATED.items():
        if (directory / name).exists():
            continue
        carried = env.get(variable, "")
        value = carried if carried and carried != PLACEHOLDER else new_secret()
        write_secret(directory, name, value)
        written.append(f"{name} ({'from .env' if value == carried else 'generated'})")
    if not (directory / PREVIOUS_PEPPER).exists():
        write_secret(directory, PREVIOUS_PEPPER, "")
        written.append(f"{PREVIOUS_PEPPER} (empty: no rotation in progress)")
    return written


def rotate(directory: Path, name: str) -> None:
    """Replace one secret with a new one; the pepper keeps its predecessor."""
    if name not in GENERATED:
        raise ToolError(f"cannot rotate {name!r}; choose one of {sorted(GENERATED)}")
    current = read_secret(directory, name)
    if name == PEPPER:
        if read_secret(directory, PREVIOUS_PEPPER):
            raise ToolError(
                "a pepper rotation is already in progress; `retire token_hash_pepper` "
                "before starting another, or stations still on the oldest pepper "
                "are stranded"
            )
        write_secret(directory, PREVIOUS_PEPPER, current)
    write_secret(directory, name, new_secret())


def set_secret(directory: Path, name: str, value: str) -> None:
    """Store a secret someone else issued, such as the tunnel token."""
    if name != TUNNEL_TOKEN:
        raise ToolError(f"only {TUNNEL_TOKEN} is set by hand; rotate the others")
    if not value.strip():
        raise ToolError("nothing was given on standard input")
    directory.mkdir(mode=DIR_MODE, exist_ok=True)
    write_secret(directory, name, value.strip())


def retire(directory: Path, name: str) -> None:
    """End a pepper rotation: forget the previous pepper."""
    if name != PEPPER:
        raise ToolError("only token_hash_pepper keeps a previous value to retire")
    write_secret(directory, PREVIOUS_PEPPER, "")


def recreate_command(name: str) -> str:
    """The command that makes the running services read `name` again."""
    services = " ".join(READERS.get(name, ("api",)))
    public = " -f deploy/docker-compose.public.yml" if name == TUNNEL_TOKEN else ""
    return f"{COMPOSE}{public} up -d --force-recreate {services}"


def run(action: str, name: str | None, directory: Path, env_file: Path) -> str:
    """Perform one action; return what to tell the operator."""
    if action == "init":
        lines = [f"wrote {line}" for line in init(directory, env_values(env_file))]
        return "\n".join([*lines, f"then: {COMPOSE} up -d"])
    if not name:
        raise ToolError(f"{action} needs the name of a secret")
    if action == "set":
        set_secret(directory, name, sys.stdin.read())
    else:
        (rotate if action == "rotate" else retire)(directory, name)
    done = {"rotate": "rotated", "retire": "retired", "set": "stored"}[action]
    return f"{done} {name}; then: {recreate_command(name)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("init", "rotate", "retire", "set"))
    parser.add_argument("name", nargs="?", help="the secret, for rotate, retire, set")
    parser.add_argument("--dir", type=Path, default=SECRETS_DIR)
    parser.add_argument("--env-file", type=Path, default=ENV_FILE)
    args = parser.parse_args(argv)
    try:
        print(run(args.action, args.name, args.dir, args.env_file))
    except (ToolError, OSError) as exc:
        print(f"rotate_secret: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
