# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
python3 -m unittest discover -s tests            # all tests
python3 -m unittest tests.test_claudemux.TestFlagInjection.test_caller_rc_wins   # one test
python3 -m py_compile claudemux.py               # syntax check
shellcheck install.sh                            # CI runs this and it must stay clean
INSTALL_DIR=~/bin CLAUDEMUX_LOCAL=./claudemux.py sh install.sh   # install a local build
```

No build step, no third-party dependencies, no linter config. Target is Python 3.7+
and the standard library only; CI runs 3.9, 3.11 and 3.13.

## Releasing

Three files carry the version and CI fails if they disagree: `__version__` in
`claudemux.py`, `VERSION`, and a matching `## x.y.z` heading in `CHANGELOG.md`.
Then tag and publish, because `--update` resolves `v<version>` and only falls back
to `main` if that tag is missing:

```sh
git tag -a v1.2.3 -m "claudemux 1.2.3 - summary" && git push origin --tags
gh release create v1.2.3 --title "claudemux 1.2.3" --notes "..."
```

## Architecture

One file, `claudemux.py`, in sections: helpers, tmux plumbing, updates, launching,
analytics, the curses browser, then the hand-rolled CLI. `main()` parses arguments
itself rather than with argparse, because unrecognised arguments must pass through
to `claude` untouched.

**tmux is the only state.** Sessions are discovered by querying tmux, never tracked
in a file. The sole persisted state is the update-check cache in `~/.cache/claudemux/`.

**One name, three places.** `full_name()` produces `claude-<user>-<directory>` and
that string is given to tmux, to Claude's Remote Control (`--rc`) and to Claude's
`-n` display name, so a session is recognisable wherever it is seen. Changing the
naming scheme means changing what reattaches: `launch()` also looks for a session
under the older `claude-<directory>` scheme before creating a new one.

**One tmux server per user.** `discover_servers()` returns this user's default
server, plus - for root only - every other user's socket under
`$TMUX_TMPDIR/tmux-<uid>/`. Anything acting on a session must pass `session.socket`
through to `tmux -S`; a command that forgets it silently operates on the caller's
own server instead.

**The browser does not act.** `Browser.run()` returns an action tuple and
`run_browser()` performs it after curses has exited, because attaching replaces the
process via `os.execvp`. Anything that needs the terminal back must follow that
pattern rather than shelling out mid-loop.

## Invariants that are easy to break

- **tmux 3.3+ strips control characters from `-F` output.** A separator like `\x1f`
  collapses every field into one and silently hides all sessions. Build formats with
  `field()` and read them with `split_fields()`, which use `#{q:...}` plus `shlex`.
  A leading marker character keeps an empty field from vanishing and shifting the rest.
- **`die()` must raise `SystemExit` carrying the message.** Inside curses, anything
  written to stderr is wiped by the teardown and the user sees a silent exit.
- **Never write the last cell of the last line** in curses; it wraps the cursor and
  errors. All drawing goes through `Browser.write()`, which clips.
- **`cached_update()` is on the launch path and must never do network I/O.**
  `update_available()` may, and only when the day-old cache is stale.
- **Explicit update checks use the releases API, not the raw file.**
  raw.githubusercontent serves `VERSION` with `max-age=300` and expires each path
  separately, so just after a release it still reports the previous version.
- **`--rc` is real but undocumented.** It is a hidden alias for `--remote-control`
  and is absent from `claude --help`; it is not a typo to be corrected.
- **Injected flags never override the caller's.** `build_claude_argv()` adds `--rc`
  and `-n` only when absent, including the `--flag=value` form.
- **The pane wrapper is deliberate.** `pane_command()` holds a pane open when claude
  exits non-zero or in under `QUICK_EXIT_SECONDS`, so a fast failure stays readable
  instead of the session vanishing.

## Testing notes

The test suite is pure unit tests over the parsing, naming, flag-injection and
update logic; nothing in it starts tmux. Two things it cannot cover:

- **Your tmux may be older than CI's.** Format-string behaviour differs between
  versions and has caused a release-blocking bug before. Anything touching `-F`
  output needs a CI run, not just local success.
- **The curses UI** is verified by rendering it into tmux and screenshotting:

```sh
tmux new-session -d -s ui && tmux resize-window -t ui -x 100 -y 20
tmux send-keys -t ui 'python3 claudemux.py' Enter && sleep 2
tmux capture-pane -p -t ui          # add -e to inspect colour attributes
```

Send keys to that pane to drive it, and remember a running browser consumes
keystrokes - a `send-keys` meant for a shell will be read as commands by the TUI.

For end-to-end checks, point `CLAUDE_BIN` at a stub (`#!/bin/sh` + `sleep 60`)
rather than starting a real Claude session, which would register Remote Control.
Note that `/tmp` is mounted `noexec` on some hosts, so a stub there will not run.
