# claudemux

Named, reattachable [tmux](https://github.com/tmux/tmux) sessions for [Claude Code](https://claude.com/claude-code).

Sessions are named `claude-<user>-<directory>`, so coming back to a project and re-running
the command reattaches instead of starting over. Run it with no arguments and you get a
session browser; run it as root and the browser shows every user's sessions on the box.

One file, standard library only, no runtime dependencies beyond `tmux` and `python3`.

```
 claudemux 1.2.0   root@vmi3465112   [all users]                       4 sessions
  SESSION                      OWNER       W STATE     UPTIME   IDLE    DIRECTORY
> claude-root-api              root        1 attached  2h14m    3s      ~
  claude-root-configs          root        1 detached  17s      17s     /etc
  claude-jaime-website         jaime       2 detached  16s      16s     /home/jaime
  claude-ztasks-v2-elcielo-dev ztasks      1 detached  2m32s    45s     ~/public_html

  ENTER attach  n new  x kill  r rename  d details  / filter  ? help  q quit
```

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

## Use

```sh
claudemux                  # browse sessions, attach with ENTER
claudemux -n api           # start or reattach claude-<you>-api
claudemux --resume         # any claude flag passes straight through
claudemux -l               # list          -l --json for scripting
claudemux -a NAME          # attach        -k NAME   kill
```

| flag | what it does |
| --- | --- |
| `-n, --name NAME` | session name (default: the current directory) |
| `--detach` | create the session but don't attach |
| `--no-rc` | start without Remote Control |
| `-l, --list` | list sessions (`--json` for machine-readable output) |
| `-a, --attach NAME` | attach to a session |
| `-k, --kill NAME` | kill a session |
| `--all` / `--mine` | include every user's sessions (root only), or just yours |
| `-h`, `-V` | help, version |

Anything claudemux doesn't recognise goes to `claude` untouched, so `--resume`, `-c` and
`--model opus` work directly. Use `--` for arguments that would otherwise be read as
claudemux flags: `claudemux -- -n "display name"`.

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
 │  Session        claude-root-api                                            │
 │  Owner          root (uid 0)                                               │
 │  Server         /tmp/tmux-0/default                                        │
 │  Created        2026-09-15 20:50:12  (3s ago)                              │
 │  Last activity  2026-09-15 20:50:12  (idle 3s)                             │
 │  State          detached (0 clients)                                       │
 │  Directory      /root                                                      │
 │  Windows        1                                                          │
 │  Pane 1.1       node  (pid 532170)                                         │
 │  Command        claude --rc claude-root-api -n claude-root-api             │
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

## Scrolling

Coming from GNU `screen`, tmux's default scrollback is the thing worth fixing first. There
is a [`tmux.conf.example`](tmux.conf.example) in this repo — the important part being:

```tmux
set -g mouse on          # wheel scrolling
set -g history-limit 50000
setw -g mode-keys vi
```

Drop it in `~/.tmux.conf`, or in `/etc/tmux.conf` to set it for every account. With mouse
mode on, hold **Shift** while dragging for your terminal's own copy/paste.

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
