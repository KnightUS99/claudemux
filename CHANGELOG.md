# Changelog

## 1.6.0

- `claudemux --skip-perms` starts claude with `--dangerously-skip-permissions`.
  As root it also sets `IS_SANDBOX=1`, which claude requires before it accepts
  that flag from root - so the whole of
  `IS_SANDBOX=1 claudemux --dangerously-skip-permissions` is now two words.
  Like every launch option, it applies to a session being created.
- `IS_SANDBOX=1 claudemux --dangerously-skip-permissions` itself now works. A pane is
  started by the tmux server with the server's environment, so a variable set
  for claudemux never reached claude unless the tmux server happened to have
  been started with it; as root, claude then refused to start. `IS_SANDBOX`,
  `ANTHROPIC_*`, `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_OAUTH_TOKEN`,
  `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` and
  `CLAUDE_CODE_NO_FLICKER` are now carried into a new session, and
  `CLAUDEMUX_ENV=NAME,NAME` adds others.
- The values travel in a file only you can read, removed as the session starts,
  not on tmux's command line where every account on the machine could see an
  API key go by.
- Reattaching to a running session with extra arguments (`claudemux --resume`)
  now says that they were not passed on, instead of silently ignoring them.

## 1.5.0

- Starting a session shows a progress bar, and attaches once Claude has drawn
  its first frame rather than straight away. Claude can take several seconds
  to load, and until now that time was spent looking at an empty pane that was
  easy to mistake for a hang. The bar paces itself on how long Claude took on
  recent launches; Ctrl-C attaches at once, `CLAUDEMUX_START_WAIT` sets the
  longest wait (20 seconds by default, `0` never waits) and
  `CLAUDEMUX_PROGRESS=off` hides the bar. Nothing is drawn unless stderr is a
  terminal, so scripts and `--detach` output are unchanged.
- The mouse wheel and PageUp now scroll in every claudemux session with no
  `tmux.conf` at all. claudemux turns the mouse on for its own sessions - the
  session, not the whole tmux server - so Claude no longer answers a dead wheel
  with "add 'set -g mouse on' to ~/.tmux.conf". It also turns on focus events,
  which Claude asks for the same way, and binds PageUp to scroll back when
  nothing is bound there. Sessions that are already running pick all of this
  up the next time claudemux attaches to them.
- `prefix + m` (Ctrl-b m) turns the mouse off and on again for the current
  session, for selecting text with the terminal's own selection. It replaces
  tmux's stock "mark pane" binding only; a key you have bound yourself is left
  alone, and so is a session whose mouse you switched off.
- `CLAUDEMUX_SCROLL=off` leaves every one of those tmux settings untouched.
  Another user's tmux server is never changed, even by root.
- The update check no longer discards the rest of the cache file when it
  records its result.

## 1.4.0

- Each session's Claude now keeps its temporary files (scratchpads, edit
  diffs, sockets) under `~/.cache/claudemux/tmp` instead of `/tmp`, by
  passing `CLAUDE_CODE_TMPDIR` into the session. On a host where `/tmp` is a
  small ramdisk or loop file, a few weeks of sessions used to fill it and take
  down whatever else needed it. A `CLAUDE_CODE_TMPDIR` already in your
  environment is respected; `CLAUDEMUX_TMPDIR=/path` moves it, and
  `CLAUDEMUX_TMPDIR=system` leaves Claude's own default alone.
- Session folders in that directory that nothing has touched for 14 days are
  pruned at launch. `CLAUDEMUX_TMP_DAYS` changes the age; `0` never prunes.
  Nothing outside the directory is ever touched and symlinks are not followed.
- The variable goes in with `new-session -e`, so a window opened later in the
  session inherits it too. On a tmux older than 3.2 the pane command carries
  it instead, and only claude itself sees it.
- The details pane shows the session's «Claude temp dir». Sessions started
  by an older claudemux keep running with whatever they had; it changes when
  they are next started.

## 1.3.0

- Sessions are now named `claude-<server>-<user>-<directory>`. Two machines
  both running claude in `/root` no longer both report `claude-root-root` to
  Remote Control, where the names from every server appear side by side.
- The server name comes from `$CLAUDEMUX_SERVER`, then
  `~/.config/claudemux/server`, then `/etc/claudemux/server`, and finally the
  short hostname - so it keeps working with no configuration at all, and
  renaming the host does not silently rename every session.
- The installer asks for the server name, offering the hostname as the
  default, and writes it to `/etc/claudemux/server` when run as root so every
  account on the box agrees. It does not ask when `$CLAUDEMUX_SERVER` is set,
  when a name is already configured, or when there is no terminal to ask on.
- The browser leaves the `claude-<server>-` prefix out of the session column,
  where it is the same on every row, and names the server once in the header
  instead. `claudemux -l` still prints the full name.
- Sessions started by 1.2 and earlier still reattach: `claudemux`, `-a` and
  `-k` fall back to the `claude-<user>-<directory>` and `claude-<directory>`
  schemes before giving up.

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
