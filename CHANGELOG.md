# Changelog

## 1.2.3

- Errors raised while the browser is on screen are no longer swallowed. Running
  `claudemux` with no arguments on a machine without tmux exited silently,
  because the message was written to stderr while curses held the screen and
  was wiped by the teardown.
- The missing-tmux message now names the one install command that fits the
  machine (`apt install tmux`, `dnf install tmux`, ...) instead of a menu, and
  the installer ends with it as a NEXT STEP rather than a warning scrolled off
  the top.

## 1.2.2

- `--update` and `--check-update` ask the releases API, which is not behind
  the five-minute CDN cache on raw.githubusercontent - running `--update`
  just after a release no longer reports "up to date". The daily background
  check still uses the cheap raw file.

## 1.2.1

- The update notice in the header no longer runs off the edge of a narrow
  terminal: it drops the session count, then shortens itself, rather than
  truncating mid-word.

## 1.2.0

- `--update` updates claudemux in place, and `--check-update` just looks.
- The browser checks for a new version in the background and offers `u` to take
  it; launching prints a one-line notice. Checks are cached for a day, never
  block startup, and can be turned off with `CLAUDEMUX_NO_UPDATE_CHECK=1`.
- Downloads are verified (parses as Python, declares the expected version) and
  swapped in atomically, keeping the interpreter the installer pinned.

## 1.1.0

- Colour in the browser: colour-blocked bars, cyan headings, green attached
  sessions, yellow owners on other users' rows. Terminals without colour keep
  the previous bold/reverse/dim rendering.
- Session names get width priority; the directory column shrinks and drops off
  a narrow terminal instead of every name being truncated.
- The header shows a short hostname and a session count.

## 1.0.0

- Named, reattachable tmux sessions for Claude Code: `claude-<user>-<directory>`,
  with the same name given to tmux, Remote Control (`--rc`) and Claude's display
  name.
- Interactive browser when run with no arguments: attach, create, kill, rename,
  filter, per-session analytics.
- Root sees every account's sessions through the per-user tmux sockets.
- Unrecognised arguments pass through to claude; `--rc` and `-n` are injected
  only when absent.
- A pane that exits quickly or non-zero is held open so the error stays readable.
