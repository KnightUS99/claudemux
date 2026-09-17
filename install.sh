#!/bin/sh
# claudemux installer.
#
#   curl -fsSL https://raw.githubusercontent.com/KnightUS99/claudemux/main/install.sh | sh
#
# Installs a single dependency-free Python file. Run as root to install for
# every account (/usr/local/bin), or as any user to install just for yourself
# (~/.local/bin). Override with INSTALL_DIR=/somewhere.
set -eu

REPO=${CLAUDEMUX_REPO:-KnightUS99/claudemux}
REF=${CLAUDEMUX_REF:-main}
SOURCE_URL="https://raw.githubusercontent.com/${REPO}/${REF}/claudemux.py"

note() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- where does it go -------------------------------------------------------
if [ -n "${INSTALL_DIR:-}" ]; then
    target_dir=$INSTALL_DIR
elif [ "$(id -u)" = 0 ]; then
    target_dir=/usr/local/bin
else
    target_dir=$HOME/.local/bin
fi
mkdir -p "$target_dir" || fail "cannot create $target_dir"
[ -w "$target_dir" ] || fail "$target_dir is not writable (try: sudo sh -c '...' or INSTALL_DIR=~/.local/bin)"
target=$target_dir/claudemux

# --- requirements -----------------------------------------------------------
python=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 7) else 1)' 2>/dev/null; then
            python=$(command -v "$candidate")
            break
        fi
    fi
done
[ -n "$python" ] || fail "python 3.7 or newer is required but was not found"

tmux_command=""
if ! command -v tmux >/dev/null 2>&1; then
    # Name the one command that fits this machine, not a menu of four.
    for pair in "apt-get:apt install tmux" "dnf:dnf install tmux" "yum:yum install tmux" \
                "apk:apk add tmux" "pacman:pacman -S tmux" "zypper:zypper install tmux" \
                "brew:brew install tmux"; do
        if command -v "${pair%%:*}" >/dev/null 2>&1; then
            tmux_command=${pair#*:}
            break
        fi
    done
    [ -n "$tmux_command" ] || tmux_command="install tmux with your package manager"
fi

# --- fetch ------------------------------------------------------------------
tmpfile=$(mktemp "${TMPDIR:-/tmp}/claudemux.XXXXXX") || fail "cannot create a temporary file"
trap 'rm -f "$tmpfile"' EXIT INT TERM

if [ -f "${CLAUDEMUX_LOCAL:-}" ]; then
    cat "$CLAUDEMUX_LOCAL" > "$tmpfile"          # local install, for development
elif command -v curl >/dev/null 2>&1; then
    curl -fsSL "$SOURCE_URL" -o "$tmpfile" || fail "download failed: $SOURCE_URL"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "$tmpfile" "$SOURCE_URL" || fail "download failed: $SOURCE_URL"
else
    fail "neither curl nor wget is available"
fi

head -n 1 "$tmpfile" | grep -q '^#!' || fail "downloaded file does not look like claudemux"
"$python" -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$tmpfile" \
    || fail "downloaded file is not valid python - refusing to install"

# Run under the interpreter we actually verified, not whatever env resolves to.
sed "1s|.*|#!${python}|" "$tmpfile" > "$tmpfile.shebang" && mv "$tmpfile.shebang" "$tmpfile"

chmod 755 "$tmpfile"
mv "$tmpfile" "$target"
trap - EXIT INT TERM

# --- server name ------------------------------------------------------------
# Sessions are claude-<server>-<user>-<directory>. Without a server segment two
# boxes both running claude in /root both report claude-root-root to Remote
# Control, and there is no way to tell which machine you are looking at.
if [ "$(id -u)" = 0 ]; then
    config_file=/etc/claudemux/server      # every account on the box agrees
else
    config_file=$HOME/.config/claudemux/server
fi
default_server=$(uname -n | cut -d. -f1)
[ -n "$default_server" ] || default_server=server

if [ -n "${CLAUDEMUX_SERVER:-}" ]; then
    server=$CLAUDEMUX_SERVER
elif [ -s "$config_file" ]; then
    server=$(cat "$config_file")           # a reinstall keeps the existing name
elif { true >/dev/tty; } 2>/dev/null; then
    # Read the terminal directly: under `curl ... | sh` stdin is the script.
    printf 'Name for this server (appears in every session name) [%s]: ' \
        "$default_server" >/dev/tty
    read -r server </dev/tty || server=""
    [ -n "$server" ] || server=$default_server
else
    server=$default_server                 # CI, or no terminal to ask on
fi
# tmux session names allow a narrow set; match sanitize() in claudemux.py.
server=$(printf '%s' "$server" | tr -c 'A-Za-z0-9_-' '-' | sed 's/^-*//; s/-*$//')
[ -n "$server" ] || server=$default_server

if mkdir -p "$(dirname "$config_file")" 2>/dev/null &&
   printf '%s\n' "$server" > "$config_file" 2>/dev/null; then
    chmod 644 "$config_file" 2>/dev/null || true
else
    warn "could not write $config_file - set CLAUDEMUX_SERVER=$server in your
    environment instead, or sessions will be named after the hostname."
fi

# --- report -----------------------------------------------------------------
if version=$("$target" --version 2>/dev/null); then
    note ""
    note "Installed $version -> $target"
    note "Server name: $server   (sessions are claude-$server-$(id -un)-<directory>)"
else
    warn "installed to $target but it would not run. If $target_dir is on a
    noexec mount, reinstall with INSTALL_DIR pointing somewhere executable."
fi
case ":${PATH}:" in
    *:"$target_dir":*) ;;
    *) warn "$target_dir is not on your PATH; add it with:
    echo 'export PATH=\"$target_dir:\$PATH\"' >> ~/.bashrc" ;;
esac
note ""
if [ -n "$tmux_command" ]; then
    # Last thing on screen, because claudemux cannot do anything without it.
    note "NEXT STEP: claudemux needs tmux, which is not installed yet:"
    note ""
    note "    $tmux_command"
    note ""
else
    note "  claudemux            browse your sessions"
    note "  claudemux -n api     start or reattach claude-$server-$(id -un)-api"
    note "  claudemux --help     everything else"
    note ""
fi
