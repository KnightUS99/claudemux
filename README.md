# claudemux

Named, reattachable [tmux](https://github.com/tmux/tmux) sessions for [Claude Code](https://claude.com/claude-code).

Sessions are named `claude-<server>-<user>-<directory>`, so coming back to a project and
re-running the command reattaches instead of starting over - and a session from one
machine is still telling apart from another's when they sit side by side in Remote
Control. Run it with no arguments and you get a session browser; run it as root and the
browser shows every user's sessions on the box.

One file, standard library only, no runtime dependencies beyond `tmux` and `python3`.

```
 claudemux 1.4.0   root@contabo   [all users]                          4 sessions
  SESSION             OWNER       W STATE     UPTIME   IDLE    DIRECTORY
> root-api            root        1 attached  2h14m    3s      ~
  root-configs        root        1 detached  17s      17s     /etc
  jaime-website       jaime       2 detached  16s      16s     /home/jaime
  ztasks-v2-elcielo-dev ztasks    1 detached  2m32s    45s     ~/public_html

  ENTER attach  n new  x kill  r rename  d details  / filter  ? help  q quit
```

Every row here is on the same machine, so the browser names the server once in the
header and leaves the `claude-<server>-` prefix off each row. `claudemux -l` prints the
full name, and so does the details pane.

Attached sessions are green, other users' are marked in yellow, and the bars are
colour-blocked; on a terminal without colour it falls back to bold, reverse and dim.
Session names get the width they need - the directory column shrinks, and drops off a
narrow terminal, rather than truncating every name.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/KnightUS99/claudemux/main/install.sh | sh
```

As root that installs to `/usr/local/bin` for every account on the machine; as anyone else
it goes to `~/.local/bin`. Override with `INSTALL_DIR=/somewhere`. Claude Code itself is
per-account — each user needs their own `claude` on `$PATH` (or `CLAUDE_BIN` pointing at one).

The installer asks what to call this server, offering the hostname as the default. It
skips the question when `$CLAUDEMUX_SERVER` is set, when a name is already configured, or
when there is no terminal to ask on — so an unattended install still works and simply
takes the hostname.

### Naming the server

The `<server>` part of a session name is resolved in this order:

| source | for |
| --- | --- |
| `$CLAUDEMUX_SERVER` | one-off overrides, and unattended installs |
| `~/.config/claudemux/server` | a per-user name, written by a non-root install |
| `/etc/claudemux/server` | the whole box, written by a root install |
| the short hostname | the fallback, so it works unconfigured |

It is read from a file rather than taken from the hostname every time, so renaming the
host does not silently rename every session. Change it by editing the file:

```sh
echo contabo | sudo tee /etc/claudemux/server
```

Existing sessions keep the name they were created with; new ones pick up the change.
Sessions created before 1.3 are still found — `claudemux`, `-a` and `-k` fall back to the
older `claude-<user>-<directory>` and `claude-<directory>` schemes before giving up.

## Use

```sh
claudemux                  # browse sessions, attach with ENTER
claudemux -n api           # start or reattach claude-<server>-<you>-api
claudemux --resume         # any claude flag passes straight through
claudemux -l               # list          -l --json for scripting
claudemux -a NAME          # attach        -k NAME   kill
```

| flag | what it does |
| --- | --- |
| `-n, --name NAME` | session name (default: the current directory) |
| `--detach` | create the session but don't attach |
| `--no-rc` | start without Remote Control |
| `--skip-perms` | start claude with `--dangerously-skip-permissions`; as root also sets `IS_SANDBOX=1`, which claude requires |
| `-l, --list` | list sessions (`--json` for machine-readable output) |
| `-a, --attach NAME` | attach to a session |
| `-k, --kill NAME` | kill a session |
| `--all` / `--mine` | include every user's sessions (root only), or just yours |
| `-h`, `-V` | help, version |

Anything claudemux doesn't recognise goes to `claude` untouched, so `--resume`, `-c` and
`--model opus` work directly. Use `--` for arguments that would otherwise be read as
claudemux flags: `claudemux -- -n "display name"`.

### Environment variables

A session's Claude is started by the tmux server, not by your shell, so it does not see
your shell's environment. claudemux carries these across when it creates a session:
`IS_SANDBOX`, anything starting `ANTHROPIC_`, `CLAUDE_CONFIG_DIR`,
`CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` and
`CLAUDE_CODE_NO_FLICKER`. Add others with `CLAUDEMUX_ENV`:

```sh
IS_SANDBOX=1 claudemux --dangerously-skip-permissions    # or just: claudemux --skip-perms
CLAUDEMUX_ENV=HTTPS_PROXY,NO_PROXY claudemux
```

They only apply to a session being created. Reattaching to one that is already running
changes nothing about it, and claudemux says so if you passed arguments.

### The browser

| key | action |
| --- | --- |
| `ENTER` | attach to the selected session |
| `n` | new session, starting in the current directory |
| `x` | kill (asks first) |
| `r` | rename |
| `d` | details and analytics |
| `/` | filter by name, owner or directory |
| `j`/`k`, `g`/`G` | move, jump to first/last |
| `a` | toggle all users / just me (root only) |
| `R`, `q` | refresh, quit |

`d` shows who owns a session, when it was created, how long it has been idle, which
clients are attached, the panes and their pids, and the actual `claude` command behind it:

```
 ┌─ details ──────────────────────────────────────────────────────────────────┐
 │  Session        claude-contabo-root-api                                    │
 │  Owner          root (uid 0)                                               │
 │  tmux socket    /tmp/tmux-0/default                                        │
 │  Created        2026-09-15 20:50:12  (3s ago)                              │
 │  Last activity  2026-09-15 20:50:12  (idle 3s)                             │
 │  State          detached (0 clients)                                       │
 │  Directory      /root                                                      │
 │  Windows        1                                                          │
 │  Pane 1.1       node  (pid 532170)                                         │
 │  Command        claude --rc claude-contabo-root-api -n claude-contabo-ro…  │
 └────────────────────────────────────────────────────────────────────────────┘
```

## Multiple accounts

tmux runs a **separate server per user**, so sessions never collide between accounts and
an ordinary user only ever sees their own. Root is the exception: other users' servers
live at `$TMUX_TMPDIR/tmux-<uid>/`, which root can read, so `claudemux --all` (and the
browser's `a` key) lists, attaches to and kills any account's sessions.

Attaching to another user's session from inside your own tmux nests one client inside the
other — claudemux clears `$TMUX` and tells you so when it happens.

## Remote Control

Sessions start with `--rc <session-name>`, an alias for `--remote-control`, so you can pick
the session up from claude.ai. It is accepted by the CLI but **not listed in
`claude --help`**; `claudemux --no-rc` opts out per launch.

## Starting up

A new session is attached once Claude has drawn its first frame, and until then
claudemux shows what it is doing:

```
claudemux [███████████░░░░░░░░░]  58%  waiting for Claude to start  1.4s
```

Claude can take several seconds to load, and attaching straight away used to mean
looking at an empty pane. The bar paces itself on how long Claude took on your recent
launches, so it is an estimate, not a measurement. **Ctrl-C** attaches at once.

| variable | effect |
| --- | --- |
| `CLAUDEMUX_PROGRESS=off` | no progress bar |
| `CLAUDEMUX_START_WAIT=SECONDS` | longest wait for the first frame (default 20; `0` attaches immediately) |

## Scrolling

The mouse wheel and **PageUp** scroll back in every claudemux session, with nothing to
add to a `tmux.conf`. Each time it attaches, claudemux fills in whatever is missing:

- **mouse on, for that session only** — your other tmux sessions keep their own setting.
  With the mouse off, Claude answers a dead wheel by asking you to edit `~/.tmux.conf`;
  this is what that message was asking for.
- **focus events on**, which Claude asks for in the same way.
- **PageUp** enters scrollback (and is passed through to full-screen programs), unless
  you have bound PageUp yourself.
- **`prefix + m`** (Ctrl-b, then m) turns the mouse off and on again, with a message
  saying which. Off, a drag selects text with your terminal's own selection instead of
  tmux's — or hold **Shift** while dragging, which most terminals treat the same way.
  This takes the place of tmux's stock "mark pane" key; a binding of your own is left alone.

Scrolling down to the bottom, or pressing `q`, leaves scrollback. A session whose mouse you
switched off stays off when you reattach. `CLAUDEMUX_SCROLL=off` stops claudemux changing
any tmux setting, and it never changes another user's tmux server, even as root.

If the wheel still does nothing, the terminal is not sending wheel events at all: check
that mouse reporting is enabled in the terminal app, and use PageUp meanwhile.

The [`tmux.conf.example`](tmux.conf.example) in this repo is still worth having for the
rest — a 50,000-line history for every pane, vi keys in scrollback, a status bar that
names the account. Drop it in `~/.tmux.conf`, or in `/etc/tmux.conf` for every account.

## Updating

```sh
claudemux --update          # update in place
claudemux --check-update    # just look
```

The browser checks once a day in the background and offers `u` when there is
something newer; launching a session prints a one-line notice. The check is cached,
never blocks startup, and `CLAUDEMUX_NO_UPDATE_CHECK=1` turns it off entirely.
Updates are verified before they are swapped in atomically, and the interpreter the
installer pinned is preserved.

To install a specific version instead, point the installer at that tag:

```sh
CLAUDEMUX_REF=v1.1.0 sh -c "$(curl -fsSL https://raw.githubusercontent.com/KnightUS99/claudemux/main/install.sh)"
```

## Temporary files

Claude Code keeps its scratch files - per-session scratchpads, edit diffs, sockets -
under `/tmp` by default. On a host where `/tmp` is a small ramdisk or a cPanel-style
loop file, a few weeks of sessions fill it, and the next thing that needs a temporary
file (MariaDB, PHP uploads) breaks. claudemux therefore hands every session a
`CLAUDE_CODE_TMPDIR` on the home disk:

| setting | effect |
| --- | --- |
| *(nothing)* | `~/.cache/claudemux/tmp` (under `$XDG_CACHE_HOME` if set), created `0700` |
| `CLAUDE_CODE_TMPDIR` already in your environment | respected as is |
| `CLAUDEMUX_TMPDIR=/some/path` | that directory instead |
| `CLAUDEMUX_TMPDIR=system` | set nothing; Claude uses its own default |
| `CLAUDEMUX_TMP_DAYS=30` | prune session folders idle longer than this (default 14; `0` never) |

Pruning runs at launch, only inside that directory, and only removes a folder when
every file in it is older than the cutoff. Symlinks are never followed. The details
pane (`d` in the browser) shows the directory a running session was given. Sessions
started before 1.4 keep running where they were; they pick this up when next started.

## Requirements

- `tmux` (any reasonably recent version; developed against 3.2a)
- `python3` 3.7 or newer, standard library only
- `claude` on `$PATH`, or `CLAUDE_BIN=/path/to/claude`

## Uninstall

```sh
rm -f "$(command -v claudemux)"
```

## Development

```sh
python3 -m unittest discover -s tests -v
INSTALL_DIR=~/bin CLAUDEMUX_LOCAL=./claudemux.py sh install.sh   # install a local build
```

## License

MIT
