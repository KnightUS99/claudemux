#!/usr/bin/env python3
"""claudemux - named, reattachable tmux sessions for Claude Code.

Sessions are named claude-<user>-<directory>, so returning to a project and
re-running the command reattaches instead of starting over. The same name is
given to tmux, to Claude's Remote Control (--rc) and to its session display
name, so a session is recognisable from wherever you look at it.

Run with no arguments for an interactive session browser. Run as root and the
browser shows every user's sessions, not just your own.

Single file, standard library only, Python 3.9+.
"""

from __future__ import annotations

import ast
import curses
import glob
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

__version__ = "1.2.0"

REPO = os.environ.get("CLAUDEMUX_REPO", "KnightUS99/claudemux")
VERSION_URL = "https://raw.githubusercontent.com/%s/main/VERSION" % REPO
SCRIPT_URL = "https://raw.githubusercontent.com/%s/%%s/claudemux.py" % REPO
UPDATE_CHECK_INTERVAL = 24 * 3600

PREFIX = "claude"
MARK = "v"  # see field(): keeps an empty value from vanishing when we split
HISTORY_LIMIT = 50000
QUICK_EXIT_SECONDS = 5  # below this, hold the pane open even on a clean exit

# Where a claude binary might live, tried in order after $CLAUDE_BIN and $PATH.
CLAUDE_CANDIDATES = (
    "~/.local/bin/claude",
    "/usr/local/bin/claude",
    "/opt/claude/bin/claude",
)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def die(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[valid-type]
    sys.stderr.write("claudemux: %s\n" % msg)
    raise SystemExit(code)


def sanitize(text: str) -> str:
    """tmux session names cannot contain '.' or ':'; keep to a safe set."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", text).strip("-")
    return cleaned or "session"


def username(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return "uid:%d" % uid


def full_name(name: str, user: Optional[str] = None) -> str:
    """claude-<user>-<name>, unless the caller already passed a full name."""
    if name.startswith(PREFIX + "-"):
        return sanitize(name)
    if user is None:
        user = username(os.getuid())
    return "%s-%s-%s" % (PREFIX, sanitize(user), sanitize(name))


def human_duration(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm%02ds" % (seconds // 60, seconds % 60)
    if seconds < 86400:
        return "%dh%02dm" % (seconds // 3600, (seconds % 3600) // 60)
    return "%dd%02dh" % (seconds // 86400, (seconds % 86400) // 3600)


def shorten_path(path: str, limit: int = 28) -> str:
    home = os.path.expanduser("~")
    if path == home:
        path = "~"
    elif path.startswith(home + "/"):
        path = "~" + path[len(home):]
    if len(path) <= limit:
        return path
    return "..." + path[-(limit - 3):]


def proc_cmdline(pid: int) -> str:
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as handle:
            raw = handle.read()
    except (OSError, ValueError):
        return ""
    return " ".join(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)


# --------------------------------------------------------------------------
# tmux plumbing
# --------------------------------------------------------------------------

def tmux_argv(socket: Optional[str], *args: str) -> List[str]:
    cmd = ["tmux"]
    if socket:
        cmd += ["-S", socket]
    return cmd + list(args)


def tmux_run(socket: Optional[str], *args: str) -> Tuple[int, str]:
    """Run a tmux command, returning (returncode, stdout+stderr stripped)."""
    try:
        proc = subprocess.run(
            tmux_argv(socket, *args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )
    except FileNotFoundError:
        die("tmux is not installed")
    return proc.returncode, proc.stdout.strip()


def tmux_tmpdir() -> str:
    return os.environ.get("TMUX_TMPDIR") or "/tmp"


def discover_servers(all_users: bool) -> List[Tuple[int, Optional[str]]]:
    """[(uid, socket)] to query. socket None means 'our own default server'.

    Other users' servers live at $TMUX_TMPDIR/tmux-<uid>/<name>. Only root can
    read into those directories, so for anyone else this is just themselves.
    """
    me = os.getuid()
    servers: List[Tuple[int, Optional[str]]] = [(me, None)]
    if not all_users or me != 0:
        return servers
    for directory in sorted(glob.glob(os.path.join(tmux_tmpdir(), "tmux-*"))):
        suffix = os.path.basename(directory)[len("tmux-"):]
        if not suffix.isdigit():
            continue
        uid = int(suffix)
        if uid == me:
            continue
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            continue
        for entry in entries:
            path = os.path.join(directory, entry)
            try:
                import stat as _stat
                if _stat.S_ISSOCK(os.stat(path).st_mode):
                    servers.append((uid, path))
            except OSError:
                continue
    return servers


def field(name: str) -> str:
    """One field of a -F format string, safe to split back apart.

    tmux from 3.3 onwards strips control characters out of format output, so a
    separator like \\x1f silently collapses every field into one. #{q:...} has
    tmux escape anything shell-special instead (spaces in paths, quotes), which
    shlex then undoes. The leading marker keeps an empty value from
    disappearing entirely and taking the field count with it.
    """
    return "%s#{q:%s}" % (MARK, name)


def split_fields(line: str) -> List[str]:
    try:
        return [token[len(MARK):] for token in shlex.split(line)]
    except ValueError:
        return []


LIST_FORMAT = " ".join(
    field(name) for name in (
        "session_name",
        "session_windows",
        "session_attached",
        "session_created",
        "session_activity",
        "pane_current_path",
        "pane_current_command",
        "pane_pid",
    )
)


@dataclass
class Session:
    name: str
    uid: int
    socket: Optional[str]
    windows: int
    attached: int
    created: int
    activity: int
    path: str
    command: str
    pane_pid: int

    @property
    def owner(self) -> str:
        return username(self.uid)

    @property
    def mine(self) -> bool:
        return self.uid == os.getuid()

    @property
    def uptime(self) -> int:
        return int(time.time()) - self.created

    @property
    def idle(self) -> int:
        return int(time.time()) - self.activity

    @property
    def state(self) -> str:
        return "attached" if self.attached else "detached"


def parse_session_line(line: str, uid: int, socket: Optional[str]) -> Optional[Session]:
    parts = split_fields(line)
    if len(parts) < 8:
        return None
    parts = parts[:8]

    def as_int(value: str) -> int:
        try:
            return int(value)
        except ValueError:
            return 0

    return Session(
        name=parts[0],
        uid=uid,
        socket=socket,
        windows=as_int(parts[1]),
        attached=as_int(parts[2]),
        created=as_int(parts[3]),
        activity=as_int(parts[4]),
        path=parts[5],
        command=parts[6],
        pane_pid=as_int(parts[7]),
    )


def collect_sessions(all_users: bool = False, claude_only: bool = True) -> List[Session]:
    sessions: List[Session] = []
    for uid, socket in discover_servers(all_users):
        code, out = tmux_run(socket, "list-sessions", "-F", LIST_FORMAT)
        if code != 0 or not out:
            continue  # no server running for that user
        for line in out.splitlines():
            session = parse_session_line(line, uid, socket)
            if session is None:
                continue
            if claude_only and not session.name.startswith(PREFIX + "-"):
                continue
            sessions.append(session)
    sessions.sort(key=lambda s: (s.uid != os.getuid(), s.owner, s.name))
    return sessions


def find_session(name: str, all_users: bool = False) -> Optional[Session]:
    for session in collect_sessions(all_users=all_users, claude_only=False):
        if session.name == name:
            return session
    return None


# --------------------------------------------------------------------------
# updates
# --------------------------------------------------------------------------

def version_tuple(version: str) -> Tuple[int, ...]:
    parts = []
    for chunk in version.split("."):
        digits = re.match(r"\d+", chunk)
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    return version_tuple(candidate) > version_tuple(current)


def cache_file() -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "claudemux", "update.json")


def read_cache() -> dict:
    try:
        with open(cache_file()) as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_cache(data: dict) -> None:
    path = cache_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            json.dump(data, handle)
    except OSError:
        pass  # a read-only home is not a reason to fail


def fetch_url(url: str, timeout: float, limit: int = 4 * 1024 * 1024) -> Optional[bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read(limit)
    except Exception:
        return None  # offline, blocked, rate-limited: never a hard failure


def fetch_latest_version(timeout: float = 3.0) -> Optional[str]:
    raw = fetch_url(VERSION_URL, timeout, limit=64)
    if raw is None:
        return None
    text = raw.decode("utf-8", "replace").strip()
    return text if re.match(r"^\d+(\.\d+)*$", text) else None


def update_available(force: bool = False, timeout: float = 3.0) -> Optional[str]:
    """Latest version if it is newer than us, else None.

    Answers from a day-old cache so startup never waits on the network; only
    a stale cache triggers a fetch. Set CLAUDEMUX_NO_UPDATE_CHECK=1 to disable.
    """
    if os.environ.get("CLAUDEMUX_NO_UPDATE_CHECK"):
        return None
    cache = read_cache()
    if force or time.time() - cache.get("checked", 0) > UPDATE_CHECK_INTERVAL:
        latest = fetch_latest_version(timeout)
        # Record the attempt either way, so an offline box retries tomorrow
        # rather than on every single launch.
        cache = {"checked": time.time(), "latest": latest or cache.get("latest")}
        write_cache(cache)
    latest = cache.get("latest")
    return latest if latest and is_newer(latest, __version__) else None


def cached_update() -> Optional[str]:
    """Cache-only check: no network, safe on the hot path."""
    if os.environ.get("CLAUDEMUX_NO_UPDATE_CHECK"):
        return None
    latest = read_cache().get("latest")
    return latest if latest and is_newer(latest, __version__) else None


def cmd_update(check_only: bool = False) -> int:
    target = os.path.realpath(sys.argv[0])
    latest = fetch_latest_version(timeout=10.0)
    if latest is None:
        die("could not reach github to check for updates")
    write_cache({"checked": time.time(), "latest": latest})

    if not is_newer(latest, __version__):
        print("claudemux %s is up to date" % __version__)
        return 0
    print("claudemux %s is available (you have %s)" % (latest, __version__))
    if check_only:
        print("update with: claudemux --update")
        return 0

    if not os.access(os.path.dirname(target) or ".", os.W_OK):
        die("cannot write to %s - try: sudo claudemux --update" % target)

    payload = None
    for ref in ("v%s" % latest, "main"):   # prefer the tag, fall back to main
        payload = fetch_url(SCRIPT_URL % ref, timeout=30.0)
        if payload:
            break
    if not payload:
        die("could not download claudemux %s" % latest)

    text = payload.decode("utf-8", "replace")
    try:
        ast.parse(text)
    except SyntaxError:
        die("downloaded file is not valid python - refusing to install it")
    if '__version__ = "%s"' % latest not in text:
        die("downloaded file does not declare version %s - refusing to install it" % latest)

    # Keep the interpreter the installer pinned, rather than whatever the
    # published file happens to say.
    current_shebang = ""
    try:
        with open(target) as handle:
            first = handle.readline().rstrip("\n")
        if first.startswith("#!"):
            current_shebang = first
    except OSError:
        pass
    if current_shebang:
        lines = text.split("\n")
        lines[0] = current_shebang
        text = "\n".join(lines)

    temporary = target + ".new"
    try:
        with open(temporary, "w") as handle:
            handle.write(text)
        os.chmod(temporary, os.stat(target).st_mode & 0o7777)
        os.replace(temporary, target)   # atomic: never a half-written binary
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        die("could not replace %s: %s" % (target, error))

    print("updated %s -> %s  (%s)" % (__version__, latest, target))
    return 0


# --------------------------------------------------------------------------
# launching claude
# --------------------------------------------------------------------------

def find_claude() -> str:
    override = os.environ.get("CLAUDE_BIN")
    if override:
        if not os.access(override, os.X_OK):
            die("CLAUDE_BIN is set to '%s' but that is not executable" % override)
        return override
    found = shutil.which("claude")
    if found:
        return found
    for candidate in CLAUDE_CANDIDATES:
        expanded = os.path.expanduser(candidate)
        if os.access(expanded, os.X_OK):
            return expanded
    die(
        "claude not found for user '%s'.\n"
        "Install it with:  curl -fsSL https://claude.ai/install.sh | bash\n"
        "Or point claudemux at an existing binary:  CLAUDE_BIN=/path/to/claude claudemux"
        % username(os.getuid())
    )


def has_flag(args: Sequence[str], *names: str) -> bool:
    for arg in args:
        for name in names:
            if arg == name or arg.startswith(name + "="):
                return True
    return False


def build_claude_argv(
    claude_bin: str, session: str, extra: Sequence[str], want_rc: bool
) -> List[str]:
    """Inject --rc and -n, but never override what the caller passed.

    --rc is an undocumented alias for --remote-control and takes an optional
    name; -n/--name sets Claude's own session display name.
    """
    prefix: List[str] = []
    if want_rc and not has_flag(extra, "--rc", "--remote-control"):
        prefix += ["--rc", session]
    if not has_flag(extra, "-n", "--name"):
        prefix += ["-n", session]
    return [claude_bin] + prefix + list(extra)


def pane_command(argv: Sequence[str]) -> str:
    """Wrap claude so a fast or failing exit leaves its output on screen."""
    inner = " ".join(shlex.quote(a) for a in argv)
    return (
        "t0=$(date +%s); " + inner + "; rc=$?; d=$(( $(date +%s) - t0 )); "
        'if [ "$rc" -ne 0 ] || [ "$d" -lt ' + str(QUICK_EXIT_SECONDS) + " ]; then "
        "printf '\\n[claude exited with status %s after %ss - press Enter to close]' "
        '"$rc" "$d"; read -r _; fi'
    )


def create_session(session: str, cwd: str, extra: Sequence[str], want_rc: bool) -> None:
    argv = build_claude_argv(find_claude(), session, extra, want_rc)
    code, out = tmux_run(
        None, "new-session", "-d", "-s", session, "-c", cwd, pane_command(argv)
    )
    if code != 0:
        die("could not create session: %s" % (out or "tmux failed"))
    tmux_run(None, "set-option", "-t", session, "-q", "history-limit", str(HISTORY_LIMIT))


def attach(session: Session) -> "NoReturn":  # type: ignore[valid-type]
    """Hand the terminal over to tmux; this replaces the current process."""
    inside = bool(os.environ.get("TMUX"))
    same_server = session.socket is None
    if inside and same_server:
        os.execvp("tmux", tmux_argv(None, "switch-client", "-t", "=" + session.name))
    env_note = ""
    if inside:
        # Attaching to another server from inside tmux nests one inside the
        # other; tmux refuses unless TMUX is cleared.
        os.environ.pop("TMUX", None)
        env_note = " (nested inside your current tmux; detach with the inner prefix)"
    if env_note:
        sys.stderr.write("claudemux: attaching to %s%s\n" % (session.name, env_note))
    os.execvp("tmux", tmux_argv(session.socket, "attach-session", "-t", "=" + session.name))


def launch(name: Optional[str], extra: Sequence[str], want_rc: bool, detach: bool) -> int:
    cwd = os.getcwd()
    session_name = full_name(name or os.path.basename(cwd) or "root")

    existing = find_session(session_name)
    if existing is None:
        # A session from the older claude-<dir> naming is still worth reusing.
        legacy = sanitize("%s-%s" % (PREFIX, name or os.path.basename(cwd)))
        found = find_session(legacy)
        if found is not None:
            existing, session_name = found, legacy

    if existing is None:
        create_session(session_name, cwd, extra, want_rc)
        print(
            "Started session: %s  (in %s)%s"
            % (session_name, cwd, ", Remote Control on" if want_rc else "")
        )
        existing = find_session(session_name) or Session(
            name=session_name, uid=os.getuid(), socket=None, windows=1,
            attached=0, created=int(time.time()), activity=int(time.time()),
            path=cwd, command="", pane_pid=0,
        )
    else:
        print("Reattaching to existing session: %s" % session_name)

    pending = cached_update()
    if pending:
        print("claudemux %s is available - update with: claudemux --update" % pending)

    if detach:
        print("Attach with: claudemux -a %s   (or: tmux a -t %s)" % (session_name, session_name))
        return 0
    attach(existing)


# --------------------------------------------------------------------------
# analytics
# --------------------------------------------------------------------------

def child_pids(pid: int) -> List[int]:
    try:
        with open("/proc/%d/task/%d/children" % (pid, pid)) as handle:
            return [int(p) for p in handle.read().split()]
    except (OSError, ValueError):
        return []


def claude_invocation(session: Session) -> str:
    """The real claude command line behind a pane, for the details view."""
    for pid in [session.pane_pid] + child_pids(session.pane_pid):
        line = proc_cmdline(pid)
        if "claude" in line and "date +%s" not in line:
            return line
    return ""


def session_details(session: Session) -> List[Tuple[str, str]]:
    created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(session.created))
    active = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(session.activity))
    rows = [
        ("Session", session.name),
        ("Owner", "%s (uid %d)%s" % (session.owner, session.uid, "" if session.mine else "  [other user]")),
        ("Server", session.socket or "%s/tmux-%d/default" % (tmux_tmpdir(), session.uid)),
        ("Created", "%s  (%s ago)" % (created, human_duration(session.uptime))),
        ("Last activity", "%s  (idle %s)" % (active, human_duration(session.idle))),
        ("State", "%s (%d client%s)" % (session.state, session.attached, "" if session.attached == 1 else "s")),
        ("Directory", session.path),
        ("Windows", str(session.windows)),
    ]

    code, out = tmux_run(
        session.socket, "list-clients", "-t", "=" + session.name,
        "-F", " ".join(field(f) for f in ("client_tty", "client_activity")),
    )
    if code == 0 and out:
        for line in out.splitlines():
            parts = split_fields(line)
            if len(parts) < 2:
                continue
            try:
                idle = human_duration(int(time.time()) - int(parts[1]))
            except ValueError:
                idle = "?"
            rows.append(("Client", "%s  (idle %s)" % (parts[0], idle)))

    code, out = tmux_run(
        session.socket, "list-panes", "-s", "-t", "=" + session.name,
        "-F", " ".join(
            [MARK + "#{window_index}.#{pane_index}"]
            + [field(f) for f in ("pane_current_command", "pane_pid")]
        ),
    )
    if code == 0 and out:
        for line in out.splitlines():
            parts = split_fields(line)
            if len(parts) >= 3:
                rows.append(("Pane %s" % parts[0], "%s  (pid %s)" % (parts[1], parts[2])))

    invocation = claude_invocation(session)
    if invocation:
        rows.append(("Command", invocation))
    return rows


# --------------------------------------------------------------------------
# interactive browser
# --------------------------------------------------------------------------

class Theme:
    """Screen attributes, resolved once curses is up.

    Falls back to bold/reverse/dim on a terminal without colour, so the
    layout still reads the same over a plain serial console or TERM=vt100.
    """

    def __init__(self) -> None:
        self.header = curses.A_REVERSE | curses.A_BOLD
        self.footer = curses.A_REVERSE
        self.column = curses.A_BOLD
        self.selected = curses.A_REVERSE | curses.A_BOLD
        self.name = curses.A_NORMAL
        self.owner_other = curses.A_DIM
        self.attached = curses.A_BOLD
        self.detached = curses.A_DIM
        self.path = curses.A_DIM
        self.message = curses.A_BOLD
        self.title = curses.A_BOLD

    def setup(self) -> None:
        if not curses.has_colors():
            return
        curses.start_color()
        try:
            curses.use_default_colors()   # keep the terminal's own background
        except curses.error:
            pass
        definitions = (
            (1, curses.COLOR_WHITE, curses.COLOR_BLUE),    # bars
            (2, curses.COLOR_CYAN, -1),                    # column headings
            (3, curses.COLOR_GREEN, -1),                   # attached
            (4, curses.COLOR_YELLOW, -1),                  # another user
            (5, curses.COLOR_MAGENTA, -1),                 # messages
        )
        for index, foreground, background in definitions:
            try:
                curses.init_pair(index, foreground, background)
            except curses.error:
                return
        self.header = curses.color_pair(1) | curses.A_BOLD
        self.footer = curses.color_pair(1)
        self.column = curses.color_pair(2) | curses.A_BOLD
        self.selected = curses.A_REVERSE | curses.A_BOLD
        self.name = curses.A_NORMAL
        self.owner_other = curses.color_pair(4)
        self.attached = curses.color_pair(3) | curses.A_BOLD
        self.detached = curses.A_DIM
        self.path = curses.A_DIM
        self.message = curses.color_pair(5) | curses.A_BOLD
        self.title = curses.color_pair(2) | curses.A_BOLD


HELP_LINES = [
    "  ENTER   attach to the selected session",
    "  n       new session (asks for a name, starts in the current directory)",
    "  x       kill the selected session",
    "  r       rename the selected session",
    "  d       details and analytics for the selected session",
    "  /       filter by name, owner or directory     ESC clears",
    "  j / k   move down / up            g / G   first / last",
    "  a       toggle all users / just me            (root only)",
    "  u       update claudemux when a newer version is available",
    "  R       refresh now               q       quit",
]


class Browser:
    """Full-screen session picker. Returns an action for main() to perform."""

    REFRESH_SECONDS = 2.0

    def __init__(self, screen: "curses._CursesWindow", all_users: bool) -> None:
        self.screen = screen
        self.all_users = all_users
        self.sessions: List[Session] = []
        self.index = 0
        self.filter = ""
        self.message = ""
        self.show_help = False
        self.detail_of: Optional[Session] = None
        self.action: Optional[Tuple[str, object]] = None
        self.last_load = 0.0
        self.theme = Theme()
        self.update: Optional[str] = None

    # -- data ------------------------------------------------------------
    def load(self) -> None:
        self.sessions = collect_sessions(all_users=self.all_users)
        self.last_load = time.time()
        if self.index >= len(self.visible):
            self.index = max(0, len(self.visible) - 1)

    @property
    def visible(self) -> List[Session]:
        if not self.filter:
            return self.sessions
        needle = self.filter.lower()
        return [
            s for s in self.sessions
            if needle in s.name.lower() or needle in s.owner.lower() or needle in s.path.lower()
        ]

    @property
    def selected(self) -> Optional[Session]:
        rows = self.visible
        return rows[self.index] if 0 <= self.index < len(rows) else None

    # -- drawing ---------------------------------------------------------
    def write(self, y: int, x: int, text: str, attr: int = curses.A_NORMAL) -> None:
        """Clipped write. Touching the last cell of the last line wraps the
        cursor off-screen, which curses reports as an error, so leave it."""
        height, width = self.screen.getmaxyx()
        if not 0 <= y < height or x >= width:
            return
        room = width - x - (1 if y == height - 1 else 0)
        if room <= 0:
            return
        try:
            self.screen.addnstr(y, x, text, room, attr)
        except curses.error:
            pass

    def draw(self) -> None:
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        if height < 6 or width < 40:
            self.write(0, 0, "terminal too small")
            self.screen.refresh()
            return

        scope = "all users" if self.all_users and os.getuid() == 0 else "mine"
        host = os.uname().nodename.split(".")[0]   # the fqdn crowds out the rest
        left = " claudemux %s   %s@%s   [%s]" % (
            __version__, username(os.getuid()), host, scope,
        )
        right = "%d session%s " % (len(self.sessions), "" if len(self.sessions) == 1 else "s")
        if self.update:
            right = "update %s available - press u   %s" % (self.update, right)
        gap = max(1, width - len(left) - len(right))
        self.write(0, 0, (left + " " * gap + right)[:width], self.theme.header)

        rows = self.visible
        show_owner = self.all_users and os.getuid() == 0
        # Session names carry the most meaning, so they get the space first and
        # the directory column takes what is left - or goes away on a narrow
        # terminal rather than truncating every name.
        fixed = 2 + (11 if show_owner else 0) + 3 + 10 + 9 + 8
        longest = max([len(s.name) for s in rows] + [len("SESSION")])
        room = max(10, width - fixed)
        name_width = min(longest, room)
        path_width = room - name_width
        show_path = path_width >= 12

        columns = "  %-*s " % (name_width, "SESSION")
        if show_owner:
            columns += "%-10s " % "OWNER"
        columns += "%2s %-9s %-8s %-7s" % ("W", "STATE", "UPTIME", "IDLE")
        if show_path:
            columns += " DIRECTORY"
        self.write(1, 0, columns.ljust(width), self.theme.column)

        body_top, body_bottom = 2, height - 3
        capacity = max(1, body_bottom - body_top)
        start = max(0, min(self.index - capacity // 2, len(rows) - capacity))

        if not rows:
            empty = "nothing matches %r" % self.filter if self.filter else \
                "no sessions yet - press n to start one"
            self.write(body_top + 1, 2, empty, self.theme.detached)

        for offset, session in enumerate(rows[start:start + capacity]):
            y = body_top + offset
            selected = (start + offset) == self.index
            theme = self.theme

            segments = [("%s %-*s " % (">" if selected else " ", name_width,
                                       session.name[:name_width]), theme.name)]
            if show_owner:
                segments.append(("%-10s " % session.owner[:10],
                                 theme.name if session.mine else theme.owner_other))
            segments.append(("%2d " % session.windows, theme.detached))
            segments.append(("%-9s " % session.state,
                             theme.attached if session.attached else theme.detached))
            segments.append(("%-8s %-7s " % (human_duration(session.uptime),
                                             human_duration(session.idle)), theme.name))
            if show_path:
                segments.append((shorten_path(session.path, max(10, path_width - 1)), theme.path))

            if selected:
                whole = "".join(text for text, _ in segments)
                self.write(y, 0, whole.ljust(width), theme.selected)
            else:
                column = 0
                for text, attr in segments:
                    self.write(y, column, text, attr)
                    column += len(text)

        footer = "  ENTER attach  n new  x kill  r rename  d details  / filter  ? help  q quit"
        if self.filter:
            footer = "  filter: %s   (ESC clears)  |%s" % (self.filter, footer)
        self.write(height - 2, 0, self.message.ljust(width),
                   self.theme.message if self.message else curses.A_NORMAL)
        self.write(height - 1, 0, footer.ljust(width), self.theme.footer)

        if self.show_help:
            self.overlay("keys", HELP_LINES)
        elif self.detail_of is not None:
            detail = session_details(self.detail_of)
            label_width = max(len(label) for label, _ in detail)
            self.overlay("details", ["  %-*s  %s" % (label_width, label, value)
                                     for label, value in detail])
        self.screen.refresh()

    def overlay(self, title: str, lines: Sequence[str]) -> None:
        height, width = self.screen.getmaxyx()
        box_height = min(len(lines) + 4, height - 2)
        box_width = min(max(len(line) for line in lines) + 4, width - 2)
        if box_height < 4 or box_width < 10:
            return
        top, left = (height - box_height) // 2, (width - box_width) // 2
        window = self.screen.subwin(box_height, box_width, top, left)
        window.erase()
        window.box()
        try:
            window.addnstr(0, 2, " %s " % title, box_width - 4, self.theme.title)
            for offset, line in enumerate(lines[:box_height - 4]):
                window.addnstr(2 + offset, 1, line, box_width - 2)
            window.addnstr(box_height - 1, 2, " any key to close ", box_width - 4,
                           self.theme.detached)
        except curses.error:
            pass
        window.refresh()

    # -- input -----------------------------------------------------------
    def ask(self, label: str, default: str = "") -> Optional[str]:
        height, width = self.screen.getmaxyx()
        buffer = list(default)
        while True:
            text = "%s%s" % (label, "".join(buffer))
            self.screen.addnstr(height - 2, 0, text.ljust(width)[:width - 1], width - 1)
            self.screen.move(height - 2, min(len(text), width - 2))
            curses.curs_set(1)
            key = self.screen.getch()
            if key in (27,):                      # ESC
                curses.curs_set(0)
                return None
            if key in (10, 13, curses.KEY_ENTER):
                curses.curs_set(0)
                return "".join(buffer).strip()
            if key in (curses.KEY_BACKSPACE, 127, 8):
                if buffer:
                    buffer.pop()
            elif 32 <= key < 127:
                buffer.append(chr(key))

    def confirm(self, question: str) -> bool:
        height, width = self.screen.getmaxyx()
        self.screen.addnstr(height - 2, 0, ("%s [y/N] " % question).ljust(width)[:width - 1],
                            width - 1, curses.A_BOLD)
        return self.screen.getch() in (ord("y"), ord("Y"))

    # -- actions ---------------------------------------------------------
    def do_kill(self) -> None:
        session = self.selected
        if session is None:
            return
        owner = "" if session.mine else " (owned by %s)" % session.owner
        if not self.confirm("kill %s%s?" % (session.name, owner)):
            self.message = "not killed"
            return
        code, out = tmux_run(session.socket, "kill-session", "-t", "=" + session.name)
        self.message = "killed %s" % session.name if code == 0 else "could not kill: %s" % out
        self.load()

    def do_rename(self) -> None:
        session = self.selected
        if session is None:
            return
        new = self.ask("rename to: ", session.name)
        if not new or new == session.name:
            self.message = "rename cancelled"
            return
        target = full_name(new, session.owner)
        code, out = tmux_run(session.socket, "rename-session", "-t", "=" + session.name, target)
        self.message = "renamed to %s" % target if code == 0 else "could not rename: %s" % out
        self.load()

    def do_new(self) -> None:
        default = os.path.basename(os.getcwd()) or "root"
        name = self.ask("new session name: ", default)
        if name is None:
            self.message = "cancelled"
            return
        self.action = ("new", name or default)

    # -- main loop -------------------------------------------------------
    def check_for_update(self) -> None:
        """Off the main loop: the browser redraws every couple of seconds, so
        the notice appears on its own once the answer arrives."""
        def worker() -> None:
            try:
                self.update = update_available()
            except Exception:
                self.update = None
        threading.Thread(target=worker, daemon=True).start()

    def run(self) -> Optional[Tuple[str, object]]:
        self.theme.setup()
        self.check_for_update()
        curses.curs_set(0)
        self.screen.timeout(int(self.REFRESH_SECONDS * 1000))
        self.load()
        while True:
            self.draw()
            key = self.screen.getch()

            if key == -1:                                   # timeout: refresh
                self.load()
                continue
            if self.show_help or self.detail_of is not None:
                self.show_help = False
                self.detail_of = None
                continue

            self.message = ""
            rows = self.visible
            if key in (ord("q"), 27):
                return None
            if key in (ord("j"), curses.KEY_DOWN):
                self.index = min(self.index + 1, max(0, len(rows) - 1))
            elif key in (ord("k"), curses.KEY_UP):
                self.index = max(self.index - 1, 0)
            elif key == ord("g"):
                self.index = 0
            elif key == ord("G"):
                self.index = max(0, len(rows) - 1)
            elif key in (10, 13, curses.KEY_ENTER):
                if self.selected is not None:
                    return ("attach", self.selected)
                self.message = "nothing selected - press n to start a session"
            elif key == ord("n"):
                self.do_new()
                if self.action:
                    return self.action
            elif key == ord("x"):
                self.do_kill()
            elif key == ord("r"):
                self.do_rename()
            elif key == ord("d"):
                self.detail_of = self.selected
            elif key == ord("?"):
                self.show_help = True
            elif key == ord("u"):
                if self.update:
                    return ("update", self.update)
                self.message = "claudemux %s is the latest version" % __version__
            elif key == ord("R"):
                self.load()
                self.message = "refreshed"
            elif key == ord("a"):
                if os.getuid() == 0:
                    self.all_users = not self.all_users
                    self.load()
                else:
                    self.message = "only root can see other users' sessions"
            elif key == ord("/"):
                entered = self.ask("filter: ", self.filter)
                self.filter = entered or ""
                self.index = 0


def run_browser(all_users: bool) -> int:
    if not sys.stdout.isatty():
        die("no terminal available - use --list instead")
    action = curses.wrapper(lambda screen: Browser(screen, all_users).run())
    if action is None:
        return 0
    kind, payload = action
    if kind == "attach":
        attach(payload)  # type: ignore[arg-type]
    if kind == "new":
        return launch(str(payload), [], want_rc=True, detach=False)
    if kind == "update":
        return cmd_update()
    return 0


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------

USAGE = """\
Usage: claudemux                         interactive session browser
       claudemux [-n NAME] [CLAUDE_ARGS...]   start or reattach, then attach
       claudemux -l | -a NAME | -k NAME

Named, reattachable tmux sessions for Claude Code, with Remote Control (--rc)
enabled by default. Sessions are named claude-<user>-<directory>.

  -n, --name NAME   session name (default: the current directory)
      --detach      create the session but do not attach
      --no-rc       start without Remote Control
  -l, --list        list sessions            --json   machine-readable
  -a, --attach NAME attach to a session
  -k, --kill NAME   kill a session
      --all         include every user's sessions (root only)
      --mine        only your own sessions
      --update      update claudemux in place   --check-update  just look
  -h, --help        this help               -V, --version
                    (claudemux -- --help shows Claude's own help)

Any other argument is passed straight through to claude, so --resume, -c and
--model work directly. Use -- for anything that would otherwise be read as a
claudemux flag, e.g. claudemux -- -n "display name".

Examples:
  claudemux                     browse and attach
  claudemux -n review           start/reattach "claude-<user>-review"
  claudemux --resume            pick a past conversation to resume
  claudemux -l --all            every user's sessions (as root)
"""


def cmd_list(all_users: bool, as_json: bool) -> int:
    sessions = collect_sessions(all_users=all_users)
    if as_json:
        import json
        print(json.dumps([
            {
                "name": s.name, "owner": s.owner, "uid": s.uid,
                "windows": s.windows, "attached": bool(s.attached),
                "created": s.created, "activity": s.activity,
                "uptime_seconds": s.uptime, "idle_seconds": s.idle,
                "path": s.path, "command": s.command,
                "socket": s.socket, "mine": s.mine,
            }
            for s in sessions
        ], indent=2))
        return 0
    if not sessions:
        print("No Claude sessions running.")
        return 0
    show_owner = all_users and os.getuid() == 0
    width = max(len(s.name) for s in sessions)
    for s in sessions:
        owner = "%-10s " % s.owner if show_owner else ""
        print("%-*s  %s%2d win  %-8s  up %-8s idle %-8s %s" % (
            width, s.name, owner, s.windows, s.state,
            human_duration(s.uptime), human_duration(s.idle), s.path,
        ))
    return 0


def resolve(name: str, all_users: bool) -> Session:
    session = find_session(name, all_users=all_users)
    if session is None:
        session = find_session(full_name(name), all_users=all_users)
    if session is None:
        die("no such session: %s  (try: claudemux -l)" % name)
    return session


def cmd_kill(name: str, all_users: bool) -> int:
    session = resolve(name, all_users)
    code, out = tmux_run(session.socket, "kill-session", "-t", "=" + session.name)
    if code != 0:
        die("could not kill %s: %s" % (session.name, out))
    print("Killed %s" % session.name)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = os.getuid() == 0

    if not args:
        return run_browser(all_users=root)

    name: Optional[str] = None
    detach = False
    want_rc = True
    all_users = root
    as_json = False
    extra: List[str] = []

    while args:
        arg = args.pop(0)
        if arg in ("-n", "--name", "-s", "--session"):
            if not args:
                die("%s requires a name" % arg)
            name = args.pop(0)
        elif arg == "--detach":
            detach = True
        elif arg == "--no-rc":
            want_rc = False
        elif arg == "--all":
            all_users = True
        elif arg == "--mine":
            all_users = False
        elif arg == "--json":
            as_json = True
        elif arg in ("-l", "--list"):
            while args and args[0] in ("--all", "--mine", "--json"):
                flag = args.pop(0)
                all_users = flag == "--all" if flag != "--json" else all_users
                as_json = as_json or flag == "--json"
            return cmd_list(all_users, as_json)
        elif arg in ("-k", "--kill"):
            if not args:
                die("%s requires a session name" % arg)
            return cmd_kill(args.pop(0), all_users)
        elif arg in ("-a", "--attach"):
            if not args:
                die("%s requires a session name" % arg)
            attach(resolve(args.pop(0), all_users))
        elif arg in ("-h", "--help"):
            sys.stdout.write(USAGE)
            return 0
        elif arg in ("-V", "--version"):
            print("claudemux %s" % __version__)
            return 0
        elif arg == "--update":
            return cmd_update()
        elif arg == "--check-update":
            return cmd_update(check_only=True)
        elif arg == "--":
            extra.extend(args)
            break
        else:
            extra.append(arg)

    return launch(name, extra, want_rc, detach)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
