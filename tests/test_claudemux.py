"""Tests for claudemux. Standard library only: python3 -m unittest discover -s tests"""

import contextlib
import io
import os
import shutil
import tempfile
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claudemux  # noqa: E402


@contextlib.contextmanager
def mock_env(**overrides):
    """Set or (with None) unset environment variables for the duration."""
    saved = {key: os.environ.get(key) for key in overrides}
    for key, value in overrides.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def pinned_server(name):
    """server_name() is cached, so the cache has to go with the environment."""
    claudemux.server_name.cache_clear()
    try:
        with mock_env(CLAUDEMUX_SERVER=name):
            yield
    finally:
        claudemux.server_name.cache_clear()


class TestNames(unittest.TestCase):
    def test_sanitize_strips_characters_tmux_rejects(self):
        self.assertEqual(claudemux.sanitize("my.project:1"), "my-project-1")
        self.assertEqual(claudemux.sanitize("a/b c"), "a-b-c")

    def test_sanitize_never_returns_empty(self):
        self.assertEqual(claudemux.sanitize(""), "session")
        self.assertEqual(claudemux.sanitize("..."), "session")
        self.assertEqual(claudemux.sanitize("---"), "session")

    def test_full_name_includes_the_server_and_the_owner(self):
        with pinned_server("box1"):
            self.assertEqual(
                claudemux.full_name("site", user="jaime"), "claude-box1-jaime-site"
            )

    def test_full_name_leaves_an_already_qualified_name_alone(self):
        with pinned_server("box1"):
            self.assertEqual(
                claudemux.full_name("claude-jaime-site", user="root"), "claude-jaime-site"
            )

    def test_full_name_sanitizes_every_part(self):
        with pinned_server("web.01"):
            self.assertEqual(
                claudemux.full_name("my.app", user="od.d"), "claude-web-01-od-d-my-app"
            )

    def test_the_same_directory_on_two_servers_gets_two_names(self):
        with pinned_server("alpha"):
            first = claudemux.full_name("root", user="root")
        with pinned_server("beta"):
            second = claudemux.full_name("root", user="root")
        self.assertNotEqual(first, second)


class TestServerName(unittest.TestCase):
    """Resolution order: $CLAUDEMUX_SERVER, the config files, then the hostname."""

    def setUp(self):
        claudemux.server_name.cache_clear()
        self.addCleanup(claudemux.server_name.cache_clear)
        self._files = claudemux.SERVER_FILES
        self.addCleanup(lambda: setattr(claudemux, "SERVER_FILES", self._files))

    def use_files(self, *contents):
        """Point SERVER_FILES at temporary files; None means the file is absent."""
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        paths = []
        for index, text in enumerate(contents):
            path = os.path.join(directory, "server%d" % index)
            if text is not None:
                with open(path, "w") as handle:
                    handle.write(text)
            paths.append(path)
        claudemux.SERVER_FILES = tuple(paths)

    def test_the_environment_wins(self):
        self.use_files("fromfile\n")
        with mock_env(CLAUDEMUX_SERVER="fromenv"):
            self.assertEqual(claudemux.server_name(), "fromenv")

    def test_the_first_readable_file_wins(self):
        self.use_files(None, "  second  \n")
        with mock_env(CLAUDEMUX_SERVER=None):
            self.assertEqual(claudemux.server_name(), "second")

    def test_a_user_file_shadows_the_system_one(self):
        self.use_files("mine\n", "system\n")
        with mock_env(CLAUDEMUX_SERVER=None):
            self.assertEqual(claudemux.server_name(), "mine")

    def test_an_empty_file_falls_through(self):
        self.use_files("\n", "system\n")
        with mock_env(CLAUDEMUX_SERVER=None):
            self.assertEqual(claudemux.server_name(), "system")

    def test_falls_back_to_the_short_hostname(self):
        self.use_files(None, None)
        with mock_env(CLAUDEMUX_SERVER=None):
            self.assertEqual(
                claudemux.server_name(),
                claudemux.sanitize(os.uname().nodename.split(".")[0]),
            )

    def test_the_result_is_sanitized(self):
        self.use_files(None, None)
        with mock_env(CLAUDEMUX_SERVER="srv.example.com"):
            self.assertEqual(claudemux.server_name(), "srv-example-com")


class TestLegacyNames(unittest.TestCase):
    """Renaming the scheme must not strand sessions an older build started."""

    def test_both_older_schemes_are_offered_newest_first(self):
        self.assertEqual(
            claudemux.legacy_names("api", user="jaime"),
            ["claude-jaime-api", "claude-api"],
        )

    def test_an_already_qualified_name_has_no_older_form(self):
        self.assertEqual(claudemux.legacy_names("claude-jaime-api", user="jaime"), [])

    def test_the_new_name_is_not_among_them(self):
        with pinned_server("box1"):
            new = claudemux.full_name("api", user="jaime")
        self.assertNotIn(new, claudemux.legacy_names("api", user="jaime"))


class TestBaseName(unittest.TestCase):
    """What rename prefills, and hands straight back to full_name()."""

    def test_the_whole_current_prefix_comes_off(self):
        with pinned_server("box1"):
            self.assertEqual(claudemux.base_name("claude-box1-root-api", "root"), "api")

    def test_renaming_does_not_double_the_user(self):
        """The bug this exists to prevent: prefill -> full_name -> same name."""
        with pinned_server("box1"):
            name = claudemux.full_name("api", user="root")
            self.assertEqual(
                claudemux.full_name(claudemux.base_name(name, "root"), user="root"), name
            )

    def test_an_older_scheme_is_upgraded_rather_than_kept(self):
        with pinned_server("box1"):
            self.assertEqual(claudemux.base_name("claude-root-api", "root"), "api")
            self.assertEqual(claudemux.base_name("claude-api", "root"), "api")

    def test_a_server_that_matches_the_user_is_not_confusing(self):
        with pinned_server("root"):
            self.assertEqual(claudemux.base_name("claude-root-root-api", "root"), "api")

    def test_another_users_session_uses_that_users_prefix(self):
        with pinned_server("box1"):
            self.assertEqual(
                claudemux.base_name("claude-box1-jaime-site", "jaime"), "site"
            )

    def test_an_unrecognised_name_is_left_whole(self):
        with pinned_server("box1"):
            self.assertEqual(claudemux.base_name("something-else", "root"), "something-else")


class TestShortName(unittest.TestCase):
    """The browser drops the prefix every local row shares."""

    def test_the_local_prefix_is_dropped(self):
        with pinned_server("box1"):
            self.assertEqual(claudemux.short_name("claude-box1-root-api"), "root-api")

    def test_another_servers_name_is_left_whole(self):
        with pinned_server("box1"):
            self.assertEqual(
                claudemux.short_name("claude-box2-root-api"), "claude-box2-root-api"
            )

    def test_a_name_that_is_only_the_prefix_is_kept(self):
        with pinned_server("box1"):
            self.assertEqual(claudemux.short_name("claude-box1-"), "claude-box1-")

    def test_it_is_the_inverse_of_full_name(self):
        with pinned_server("box1"):
            self.assertEqual(
                claudemux.short_name(claudemux.full_name("api", user="root")), "root-api"
            )


class TestFormatting(unittest.TestCase):
    def test_human_duration(self):
        self.assertEqual(claudemux.human_duration(5), "5s")
        self.assertEqual(claudemux.human_duration(65), "1m05s")
        self.assertEqual(claudemux.human_duration(3700), "1h01m")
        self.assertEqual(claudemux.human_duration(90000), "1d01h")

    def test_human_duration_clamps_negative_clock_skew(self):
        self.assertEqual(claudemux.human_duration(-10), "0s")

    def test_shorten_path_uses_tilde_and_truncates(self):
        home = os.path.expanduser("~")
        self.assertEqual(claudemux.shorten_path(home), "~")
        self.assertEqual(claudemux.shorten_path(os.path.join(home, "x")), "~/x")
        long_path = "/very/long/" + "y" * 60
        self.assertEqual(len(claudemux.shorten_path(long_path, 20)), 20)


class TestFlagInjection(unittest.TestCase):
    """--rc and -n are injected, but never over something the caller passed."""

    def build(self, extra, want_rc=True):
        argv = claudemux.build_claude_argv("/bin/claude", "claude-root-api", extra, want_rc)
        self.assertEqual(argv[0], "/bin/claude")
        return argv[1:]

    def test_both_injected_by_default(self):
        self.assertEqual(self.build([]), ["--rc", "claude-root-api", "-n", "claude-root-api"])

    def test_passthrough_args_are_kept_after_the_injected_ones(self):
        self.assertEqual(
            self.build(["--resume"]),
            ["--rc", "claude-root-api", "-n", "claude-root-api", "--resume"],
        )

    def test_no_rc_skips_remote_control(self):
        self.assertEqual(self.build([], want_rc=False), ["-n", "claude-root-api"])

    def test_caller_rc_wins(self):
        self.assertEqual(self.build(["--rc", "mine"]), ["-n", "claude-root-api", "--rc", "mine"])

    def test_caller_remote_control_alias_also_wins(self):
        self.assertNotIn("--rc", self.build(["--remote-control", "mine"]))

    def test_caller_name_wins(self):
        self.assertEqual(self.build(["-n", "custom"]), ["--rc", "claude-root-api", "-n", "custom"])

    def test_equals_form_is_recognised(self):
        self.assertNotIn("--rc", self.build(["--rc=mine"]))

    def test_has_flag_does_not_match_a_longer_flag(self):
        self.assertFalse(claudemux.has_flag(["--resume"], "--rc"))
        self.assertFalse(claudemux.has_flag(["--name-thing"], "--name"))


class TestPaneCommand(unittest.TestCase):
    def test_arguments_are_quoted(self):
        command = claudemux.pane_command(["/bin/claude", "-n", "a b; rm -rf /"])
        self.assertIn("'a b; rm -rf /'", command)

    def test_holds_the_pane_open_on_failure_or_a_quick_exit(self):
        command = claudemux.pane_command(["/bin/claude"])
        self.assertIn('"$rc" -ne 0', command)
        self.assertIn('"$d" -lt %d' % claudemux.QUICK_EXIT_SECONDS, command)
        self.assertIn("read -r _", command)


class TestClaudeTmpdir(unittest.TestCase):
    """Where a new session's Claude is told to keep its temporary files."""

    def setUp(self):
        self.cache = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache.cleanup)

    def resolve(self, **env):
        base = {"XDG_CACHE_HOME": self.cache.name, "CLAUDEMUX_TMPDIR": None,
                "CLAUDE_CODE_TMPDIR": None}
        base.update(env)
        with mock_env(**base):
            return claudemux.claude_tmpdir()

    def test_defaults_to_the_cache_directory_not_tmp(self):
        self.assertEqual(
            self.resolve(), os.path.join(self.cache.name, "claudemux", "tmp")
        )

    def test_a_tmpdir_already_in_the_environment_is_respected(self):
        self.assertEqual(self.resolve(CLAUDE_CODE_TMPDIR="/srv/scratch"), "/srv/scratch")

    def test_claudemux_tmpdir_moves_it(self):
        self.assertEqual(
            self.resolve(CLAUDEMUX_TMPDIR="/data/claude", CLAUDE_CODE_TMPDIR="/srv/scratch"),
            "/data/claude",
        )

    def test_system_opts_out(self):
        self.assertIsNone(self.resolve(CLAUDEMUX_TMPDIR="system"))
        self.assertIsNone(self.resolve(CLAUDEMUX_TMPDIR=" SYSTEM "))

    def test_a_tilde_is_expanded(self):
        self.assertEqual(
            self.resolve(CLAUDEMUX_TMPDIR="~/scratch"), os.path.expanduser("~/scratch")
        )

    def test_prepare_creates_it_private(self):
        path = os.path.join(self.cache.name, "deep", "er", "tmp")
        self.assertTrue(claudemux.prepare_claude_tmpdir(path))
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o700)
        self.assertTrue(claudemux.prepare_claude_tmpdir(path))   # idempotent

    def test_prepare_reports_an_unusable_path(self):
        blocker = os.path.join(self.cache.name, "file")
        with open(blocker, "w") as handle:
            handle.write("not a directory")
        self.assertFalse(claudemux.prepare_claude_tmpdir(os.path.join(blocker, "tmp")))

    def test_prune_days_parsing(self):
        with mock_env(CLAUDEMUX_TMP_DAYS=None):
            self.assertEqual(claudemux.tmp_days(), claudemux.DEFAULT_TMP_DAYS)
        with mock_env(CLAUDEMUX_TMP_DAYS="0"):
            self.assertEqual(claudemux.tmp_days(), 0)
        with mock_env(CLAUDEMUX_TMP_DAYS="2.5"):
            self.assertEqual(claudemux.tmp_days(), 2.5)
        with mock_env(CLAUDEMUX_TMP_DAYS="-3"):
            self.assertEqual(claudemux.tmp_days(), 0)
        with mock_env(CLAUDEMUX_TMP_DAYS="soon"):
            self.assertEqual(claudemux.tmp_days(), claudemux.DEFAULT_TMP_DAYS)


class TestPruneTmpdir(unittest.TestCase):
    """Stale session folders go; anything alive, and anything outside, stays."""

    DAYS = 14

    def setUp(self):
        self.base = tempfile.TemporaryDirectory()
        self.addCleanup(self.base.cleanup)
        self.root = os.path.join(self.base.name, "tmp")
        # The layout Claude Code builds: claude-<uid>/<project>/<session>/
        self.project = os.path.join(self.root, "claude-1000", "-home-me-app")
        os.makedirs(self.project)

    def make(self, *parts, age_days=0, files=("scratchpad/notes.txt",)):
        directory = os.path.join(self.project, *parts)
        os.makedirs(directory, exist_ok=True)
        stamp = time.time() - age_days * 86400
        for relative in files:
            path = os.path.join(directory, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write("x")
            os.utime(path, (stamp, stamp))
        os.utime(directory, (stamp, stamp))
        return directory

    def test_a_stale_session_goes_and_a_live_one_stays(self):
        stale = self.make("old-session", age_days=30)
        live = self.make("new-session", age_days=1)
        self.assertGreaterEqual(claudemux.prune_tmpdir(self.root, self.DAYS), 1)
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(os.path.join(live, "scratchpad", "notes.txt")))
        self.assertTrue(os.path.isdir(self.root))

    def test_one_fresh_file_keeps_the_whole_folder(self):
        session = self.make("mixed", age_days=30)
        fresh = os.path.join(session, "fresh.txt")
        with open(fresh, "w") as handle:
            handle.write("y")
        claudemux.prune_tmpdir(self.root, self.DAYS)
        self.assertTrue(os.path.exists(session))
        self.assertTrue(os.path.exists(os.path.join(session, "scratchpad", "notes.txt")))

    def test_nothing_below_a_session_is_removed_on_its_own(self):
        """A session with one fresh file keeps even its oldest sub-folders."""
        session = self.make("mixed", age_days=30, files=("scratchpad/notes.txt", "edits/a/b.diff"))
        with open(os.path.join(session, "fresh.txt"), "w") as handle:
            handle.write("y")
        claudemux.prune_tmpdir(self.root, self.DAYS)
        self.assertTrue(os.path.exists(os.path.join(session, "scratchpad", "notes.txt")))
        self.assertTrue(os.path.exists(os.path.join(session, "edits", "a", "b.diff")))

    def test_the_edit_diff_store_is_pruned_the_same_way(self):
        store = os.path.join(self.root, "claude-1000", "bash-edit-diff")
        stale = os.path.join(store, "1111-aaaa")
        live = os.path.join(store, "2222-bbbb")
        for path, age in ((stale, 30), (live, 1)):
            os.makedirs(os.path.join(path, "objects"))
            target = os.path.join(path, "objects", "blob")
            with open(target, "w") as handle:
                handle.write("z")
            stamp = time.time() - age * 86400
            os.utime(target, (stamp, stamp))
        claudemux.prune_tmpdir(self.root, self.DAYS)
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(live))

    def test_the_parent_folders_go_once_everything_in_them_is_stale(self):
        self.make("only-session", age_days=30)
        claudemux.prune_tmpdir(self.root, self.DAYS)
        self.assertFalse(os.path.exists(os.path.join(self.root, "claude-1000")))
        self.assertTrue(os.path.isdir(self.root))   # never the root itself

    def test_an_empty_folder_is_judged_by_its_own_age(self):
        fresh = self.make("just-started", age_days=0, files=())
        stale = self.make("abandoned", age_days=30, files=())
        claudemux.prune_tmpdir(self.root, self.DAYS)
        self.assertTrue(os.path.isdir(fresh))
        self.assertFalse(os.path.exists(stale))

    def test_zero_days_never_prunes(self):
        stale = self.make("old-session", age_days=400)
        self.assertEqual(claudemux.prune_tmpdir(self.root, 0), 0)
        self.assertTrue(os.path.exists(stale))

    def test_symlinks_are_not_followed_out_of_the_directory(self):
        outside = os.path.join(self.base.name, "elsewhere")
        os.makedirs(outside)
        precious = os.path.join(outside, "keep.txt")
        with open(precious, "w") as handle:
            handle.write("keep")
        old = time.time() - 60 * 86400
        os.utime(precious, (old, old))
        os.utime(outside, (old, old))
        session = self.make("linked", age_days=30, files=())
        link = os.path.join(session, "link")
        os.symlink(outside, link)
        os.utime(link, (old, old), follow_symlinks=False)
        os.utime(session, (old, old))
        claudemux.prune_tmpdir(self.root, self.DAYS)
        self.assertFalse(os.path.exists(session))      # the stale folder went
        self.assertTrue(os.path.exists(precious))      # the target did not

    def test_a_missing_or_symlinked_root_is_left_alone(self):
        self.assertEqual(claudemux.prune_tmpdir(os.path.join(self.base.name, "nope"), 1), 0)
        link = os.path.join(self.base.name, "link")
        os.symlink(self.root, link)
        self.make("old-session", age_days=30)
        self.assertEqual(claudemux.prune_tmpdir(link, self.DAYS), 0)


class TestSessionEnvironment(unittest.TestCase):
    """CLAUDE_CODE_TMPDIR reaches the session by -e, or by the pane command."""

    def pin_tmux(self, version):
        original = claudemux.tmux_version
        claudemux.tmux_version = lambda: version
        self.addCleanup(setattr, claudemux, "tmux_version", original)

    def test_version_parsing(self):
        self.assertEqual(claudemux.parse_tmux_version("tmux 3.2a"), (3, 2))
        self.assertEqual(claudemux.parse_tmux_version("tmux next-3.4"), (3, 4))
        self.assertEqual(claudemux.parse_tmux_version("tmux 2.9"), (2, 9))
        self.assertEqual(claudemux.parse_tmux_version("tmux master"), (0, 0))

    def test_a_recent_tmux_gets_dash_e(self):
        self.pin_tmux((3, 2))
        flags, left = claudemux.environment_args({"CLAUDE_CODE_TMPDIR": "/x/y"})
        self.assertEqual(flags, ["-e", "CLAUDE_CODE_TMPDIR=/x/y"])
        self.assertEqual(left, {})

    def test_an_old_tmux_carries_it_in_the_pane_command(self):
        self.pin_tmux((3, 1))
        flags, left = claudemux.environment_args({"CLAUDE_CODE_TMPDIR": "/x/y"})
        self.assertEqual(flags, [])
        command = claudemux.pane_command(["/bin/claude"], left)
        self.assertIn("env CLAUDE_CODE_TMPDIR=/x/y /bin/claude", command)

    def test_nothing_to_set_means_nothing_added(self):
        self.pin_tmux((3, 2))
        self.assertEqual(claudemux.environment_args({}), ([], {}))
        self.assertNotIn("env ", claudemux.pane_command(["/bin/claude"], {}))

    def test_pane_command_quotes_the_value(self):
        command = claudemux.pane_command(["/bin/claude"], {"CLAUDE_CODE_TMPDIR": "/a b/c"})
        self.assertIn("CLAUDE_CODE_TMPDIR='/a b/c'", command)

    def test_proc_environ_reads_our_own(self):
        self.assertIn("PATH", claudemux.proc_environ(os.getpid()))
        self.assertEqual(claudemux.proc_environ(2 ** 22 + 12345), {})


class TestForwardedEnvironment(unittest.TestCase):
    def test_sandbox_flag_and_anthropic_settings_are_carried(self):
        env = claudemux.forwarded_environment({
            "IS_SANDBOX": "1", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "u",
            "HOME": "/root", "PATH": "/bin",
        })
        self.assertEqual(env, {"IS_SANDBOX": "1", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "u"})

    def test_a_claude_sessions_own_plumbing_is_not_carried(self):
        env = claudemux.forwarded_environment({
            "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "x", "CLAUDE_CODE_ENTRYPOINT": "cli",
            "CLAUDE_CODE_MESSAGING_TOKEN": "t", "CLAUDE_PID": "1", "CLAUDE_CODE_TMPDIR": "/t",
        })
        self.assertEqual(env, {})

    def test_extra_names_can_be_listed(self):
        env = claudemux.forwarded_environment({
            "CLAUDEMUX_ENV": "HTTPS_PROXY, NO_PROXY  MISSING bad-name", "HTTPS_PROXY": "p",
            "NO_PROXY": "n", "bad-name": "x", "OTHER": "o",
        })
        self.assertEqual(env, {"HTTPS_PROXY": "p", "NO_PROXY": "n"})

    def test_env_file_is_private_and_quotes_values(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock_env(XDG_CACHE_HOME=tmp):
            path = claudemux.write_env_file("claude-b-u-x", {"A": "it's $HOME; rm -rf", "B": "1"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(os.path.dirname(path)).st_mode & 0o777, 0o700)
            import subprocess
            out = subprocess.run(
                ["sh", "-c", '. "$1"; printf "%s|%s" "$A" "$B"', "sh", path],
                stdout=subprocess.PIPE, universal_newlines=True,
            ).stdout
            self.assertEqual(out, "it's $HOME; rm -rf|1")
            self.assertIsNone(claudemux.write_env_file("claude-b-u-x", {}))

    def test_env_file_replaces_a_symlink_instead_of_following_it(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock_env(XDG_CACHE_HOME=tmp):
            first = claudemux.write_env_file("s", {"A": "1"})
            victim = os.path.join(tmp, "victim")
            open(victim, "w").write("untouched")
            os.unlink(first)
            os.symlink(victim, first)
            claudemux.write_env_file("s", {"A": "2"})
            self.assertFalse(os.path.islink(first))
            self.assertEqual(open(victim).read(), "untouched")

    def test_pane_command_sources_then_removes_the_file(self):
        command = claudemux.pane_command(["claude"], None, "/c/my env/s.env")
        self.assertIn(". '/c/my env/s.env'; rm -f '/c/my env/s.env'", command)
        self.assertLess(command.index("rm -f"), command.index("claude;"))
        self.assertNotIn(".env", claudemux.pane_command(["claude"]))


class TestSkipPerms(unittest.TestCase):
    def test_adds_the_flag_ahead_of_the_callers_arguments(self):
        self.assertEqual(
            claudemux.with_skip_permissions(["--resume"], {}),
            ["--dangerously-skip-permissions", "--resume"],
        )

    def test_does_not_repeat_a_flag_the_caller_gave(self):
        extra = ["--resume", "--dangerously-skip-permissions"]
        self.assertEqual(claudemux.with_skip_permissions(extra, {}), extra)

    def test_root_gets_the_sandbox_marker_and_nobody_else_does(self):
        original = os.getuid
        self.addCleanup(setattr, os, "getuid", original)
        os.getuid = lambda: 0
        environ = {}
        claudemux.with_skip_permissions([], environ)
        self.assertEqual(environ, {"IS_SANDBOX": "1"})
        environ = {"IS_SANDBOX": "0"}          # an explicit choice is not overridden
        claudemux.with_skip_permissions([], environ)
        self.assertEqual(environ, {"IS_SANDBOX": "0"})
        os.getuid = lambda: 1000
        environ = {}
        claudemux.with_skip_permissions([], environ)
        self.assertEqual(environ, {})

    def test_cli_flag_reaches_launch_in_both_spellings(self):
        seen = []
        original = claudemux.launch
        self.addCleanup(setattr, claudemux, "launch", original)
        claudemux.launch = lambda name, extra, *rest: seen.append(list(extra)) or 0
        with mock_env(IS_SANDBOX=None):
            for spelling in ("--skip-perms", "-skip-perms"):
                claudemux.main([spelling, "--resume"])
        self.assertEqual(seen, [["--dangerously-skip-permissions", "--resume"]] * 2)


class TestOptionsGetTheirOwnSession(unittest.TestCase):
    """`claudemux --skip-perms` beside a session that was started without it."""

    def setUp(self):
        self.args = {}          # session name -> the arguments it was started with
        for name in ("collect_sessions", "session_args", "server_name"):
            self.addCleanup(setattr, claudemux, name, getattr(claudemux, name))
        claudemux.server_name = lambda: "box1"
        claudemux.collect_sessions = lambda **_: [self.session(n) for n in self.args]
        claudemux.session_args = lambda session: self.args[session.name]

    def session(self, name):
        return claudemux.Session(
            name=name, uid=os.getuid(), socket=None, windows=1, attached=0,
            created=0, activity=0, path="/", command="", pane_pid=0,
        )

    def name(self, base):
        return claudemux.full_name(base)

    def choose(self, extra):
        chosen, reuse = claudemux.choose_session("api", extra, self.session(self.name("api")))
        return chosen, reuse is not None

    def test_a_session_without_the_option_is_left_alone(self):
        self.args = {self.name("api"): []}
        self.assertEqual(self.choose(["--dangerously-skip-permissions"]),
                         (self.name("api-2"), False))

    def test_a_session_that_has_the_option_is_reattached(self):
        self.args = {self.name("api"): ["--dangerously-skip-permissions"]}
        self.assertEqual(self.choose(["--dangerously-skip-permissions"]),
                         (self.name("api"), True))

    def test_running_it_again_finds_the_sibling_it_made(self):
        self.args = {self.name("api"): [],
                     self.name("api-2"): ["--dangerously-skip-permissions"]}
        self.assertEqual(self.choose(["--dangerously-skip-permissions"]),
                         (self.name("api-2"), True))

    def test_a_sibling_with_other_options_is_skipped(self):
        self.args = {self.name("api"): [], self.name("api-2"): ["--model", "opus"]}
        self.assertEqual(self.choose(["--dangerously-skip-permissions"]),
                         (self.name("api-3"), False))

    def test_a_later_sibling_wins_over_an_earlier_gap(self):
        self.args = {self.name("api"): [],
                     self.name("api-3"): ["--dangerously-skip-permissions"]}
        self.assertEqual(self.choose(["--dangerously-skip-permissions"]),
                         (self.name("api-3"), True))

    def test_continue_alone_still_reattaches(self):
        self.args = {self.name("api"): []}
        for flag in ("-c", "--continue"):
            self.assertEqual(self.choose([flag]), (self.name("api"), True))
        self.assertEqual(self.choose(["-c", "--model", "opus"]), (self.name("api-2"), False))

    def test_new_takes_the_first_free_number(self):
        self.args = {self.name("api"): [], self.name("api-2"): [], self.name("api-4"): []}
        self.assertEqual(claudemux.free_sibling("api"), self.name("api-3"))

    def test_new_reaches_launch(self):
        seen = []
        self.addCleanup(setattr, claudemux, "launch", claudemux.launch)
        claudemux.launch = lambda name, extra, want_rc, detach, fresh: seen.append(fresh) or 0
        claudemux.main(["--new", "--model", "opus"])
        claudemux.main(["--model", "opus"])
        self.assertEqual(seen, [True, False])

    def test_free_base_keeps_a_name_nobody_has(self):
        self.args = {self.name("api"): []}
        self.assertEqual(claudemux.free_base("web"), "web")
        self.assertEqual(claudemux.free_base("api"), "api-2")

    def routed(self, argv, tty=True, here=True):
        """Where main() sends a command line: the browser or a straight launch."""
        seen = []
        for name in ("launch", "run_browser", "running_here"):
            self.addCleanup(setattr, claudemux, name, getattr(claudemux, name))
        claudemux.launch = lambda *a: seen.append(("launch", list(a[1]))) or 0
        claudemux.run_browser = (
            lambda all_users, extra=(), want_rc=True, prefer=None:
            seen.append(("browser", list(extra), prefer)) or 0
        )
        claudemux.running_here = lambda name=None: self.session("here") if here else None
        self.addCleanup(setattr, sys, "stdout", sys.stdout)
        sys.stdout = EncodedStream()
        sys.stdout.isatty = lambda: tty
        claudemux.main(argv)
        return seen

    def test_options_beside_a_running_session_open_the_browser(self):
        self.assertEqual(self.routed(["--model", "opus"]),
                         [("browser", ["--model", "opus"], "here")])

    def test_nothing_running_here_just_starts(self):
        self.assertEqual(self.routed(["--model", "opus"], here=False),
                         [("launch", ["--model", "opus"])])

    def test_a_name_new_detach_or_no_terminal_skip_the_browser(self):
        for argv in (["-n", "api", "--model", "opus"], ["--new", "--model", "opus"],
                     ["--detach", "--model", "opus"]):
            self.assertEqual(self.routed(argv), [("launch", ["--model", "opus"])])
        self.assertEqual(self.routed(["--model", "opus"], tty=False),
                         [("launch", ["--model", "opus"])])

    def test_missing_args_names_only_what_the_session_lacks(self):
        self.assertEqual(
            claudemux.missing_args(["--model", "opus", "-c"], ["--rc", "x", "--model", "opus"]),
            ["-c"],
        )


class TestSessionArgs(unittest.TestCase):
    def session(self):
        return claudemux.Session(
            name="claude-box1-root-api", uid=os.getuid(), socket=None, windows=1,
            attached=0, created=0, activity=0, path="/", command="", pane_pid=0,
        )

    def with_tmux(self, fake):
        self.addCleanup(setattr, claudemux, "tmux_run", claudemux.tmux_run)
        claudemux.tmux_run = fake
        return fake

    def test_reads_what_create_session_recorded(self):
        self.with_tmux(FakeTmux({("show-options",): (0, '["--model", "opus"]')}))
        self.assertEqual(claudemux.session_args(self.session()), ["--model", "opus"])

    def test_a_recorded_empty_list_is_not_mistaken_for_no_record(self):
        self.with_tmux(FakeTmux({("show-options",): (0, "[]")}))
        self.addCleanup(setattr, claudemux, "proc_argv", claudemux.proc_argv)
        claudemux.proc_argv = lambda pid: ["claude", "--from-proc"]
        self.assertEqual(claudemux.session_args(self.session()), [])

    def test_an_older_session_falls_back_to_its_command_line(self):
        self.with_tmux(FakeTmux({("show-options",): (0, "")}))
        for name in ("proc_argv", "child_pids"):
            self.addCleanup(setattr, claudemux, name, getattr(claudemux, name))
        claudemux.child_pids = lambda pid: [7]
        claudemux.proc_argv = lambda pid: (
            ["/root/.local/bin/claude", "--rc", "x", "--dangerously-skip-permissions"]
            if pid == 7 else ["sh", "-c", "t0=$(date +%s); claude --rc x"]
        )
        self.assertEqual(
            claudemux.session_args(self.session()),
            ["--rc", "x", "--dangerously-skip-permissions"],
        )


class FakeTmux:
    """Stands in for tmux_run: answers from a table and records every call."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, socket, *args):
        self.calls.append(args)
        for prefix, answer in self.answers.items():
            if args[:len(prefix)] == prefix:
                return answer(args) if callable(answer) else answer
        return 0, ""

    def ran(self, *prefix):
        return [call for call in self.calls if call[:len(prefix)] == prefix]


class EncodedStream(io.StringIO):
    """A StringIO whose encoding can be chosen, like a terminal's."""
    encoding = "utf-8"


class TestProgress(unittest.TestCase):
    def make(self, encoding="utf-8", enabled=True):
        stream = EncodedStream()
        stream.encoding = encoding
        return claudemux.Progress(stream=stream, enabled=enabled), stream

    def test_bar_is_always_the_same_width(self):
        progress, _ = self.make()
        for fraction in (-1, 0, 0.26, 0.5, 0.999, 1, 7):
            bar = progress.render(fraction, "x").split("[")[1].split("]")[0]
            self.assertEqual(len(bar), claudemux.PROGRESS_WIDTH)

    def test_render_shows_percentage_label_and_elapsed(self):
        progress, _ = self.make()
        text = progress.render(0.5, "waiting for Claude to start", 2.34)
        self.assertIn(" 50%", text)
        self.assertIn("waiting for Claude to start", text)
        self.assertTrue(text.endswith("2.3s"))

    def test_falls_back_to_ascii_without_a_utf8_terminal(self):
        progress, _ = self.make(encoding="ANSI_X3.4-1968")
        text = progress.render(0.5, "x")
        text.encode("ascii")   # raises if anything non-ASCII crept in
        self.assertIn("#", text)

    def test_disabled_progress_writes_nothing(self):
        progress, stream = self.make(enabled=False)
        progress.update(0.5, "x")
        progress.clear()
        self.assertEqual(stream.getvalue(), "")

    def test_not_a_terminal_means_disabled(self):
        self.assertFalse(claudemux.Progress(stream=io.StringIO()).enabled)

    def test_clear_erases_the_line_and_only_once(self):
        progress, stream = self.make()
        progress.update(0.5, "x")
        progress.clear()
        progress.clear()
        self.assertTrue(stream.getvalue().endswith("\r\x1b[K"))
        self.assertEqual(stream.getvalue().count("\r\x1b[K"), 2)   # one draw, one erase

    def test_a_line_never_reaches_the_terminal_edge(self):
        progress, stream = self.make()
        original = shutil.get_terminal_size
        shutil.get_terminal_size = lambda fallback=None: os.terminal_size((30, 24))
        self.addCleanup(setattr, shutil, "get_terminal_size", original)
        progress.update(0.5, "a label much longer than the terminal is wide", 1.0)
        self.assertEqual(len(stream.getvalue()) - len("\r\x1b[K"), 29)

    def test_wait_fraction_rises_but_never_completes(self):
        values = [claudemux.wait_fraction(t, 3.0) for t in (0, 1, 3, 10, 600)]
        self.assertEqual(values[0], 0.0)
        self.assertEqual(values, sorted(values))
        self.assertAlmostEqual(values[2], 0.9)
        self.assertLessEqual(values[-1], 1.0)
        self.assertEqual(claudemux.wait_fraction(5, 0), 0.0)

    def test_start_wait_setting(self):
        with mock_env(CLAUDEMUX_START_WAIT=None):
            self.assertEqual(claudemux.start_wait(), claudemux.DEFAULT_START_WAIT)
        with mock_env(CLAUDEMUX_START_WAIT="0"):
            self.assertEqual(claudemux.start_wait(), 0.0)
        with mock_env(CLAUDEMUX_START_WAIT="-4"):
            self.assertEqual(claudemux.start_wait(), 0.0)
        with mock_env(CLAUDEMUX_START_WAIT="soon"):
            self.assertEqual(claudemux.start_wait(), claudemux.DEFAULT_START_WAIT)


class TestWaitForClaude(unittest.TestCase):
    def setUp(self):
        self.progress = claudemux.Progress(stream=io.StringIO(), enabled=False)
        for name, value in (("POLL_SECONDS", 0.0),):
            self.addCleanup(setattr, claudemux, name, getattr(claudemux, name))
            setattr(claudemux, name, value)
        self.addCleanup(setattr, claudemux, "tmux_run", claudemux.tmux_run)

    def test_returns_once_the_pane_has_output(self):
        screens = iter(["", "", "", "Welcome"])
        claudemux.tmux_run = FakeTmux({("capture-pane",): lambda args: (0, next(screens))})
        took = claudemux.wait_for_claude("s", self.progress, timeout=30, expected=1)
        self.assertIsNotNone(took)
        self.assertEqual(len(claudemux.tmux_run.calls), 4)
        self.assertEqual(claudemux.tmux_run.calls[0][-1], "=s:")

    def test_gives_up_at_the_timeout(self):
        claudemux.tmux_run = FakeTmux()   # the pane stays blank
        self.assertIsNone(claudemux.wait_for_claude("s", self.progress, timeout=0, expected=1))

    def test_stops_when_the_session_is_gone(self):
        claudemux.tmux_run = FakeTmux({("capture-pane",): (1, "can't find session")})
        self.assertIsNone(claudemux.wait_for_claude("s", self.progress, timeout=30, expected=1))
        self.assertEqual(len(claudemux.tmux_run.calls), 1)

    def test_ctrl_c_attaches_instead_of_aborting(self):
        def interrupt(args):
            raise KeyboardInterrupt
        claudemux.tmux_run = FakeTmux({("capture-pane",): interrupt})
        self.assertIsNone(claudemux.wait_for_claude("s", self.progress, timeout=30, expected=1))


class TestStartupMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patch = mock_env(XDG_CACHE_HOME=self.tmp)
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)

    def test_default_until_a_launch_has_been_timed(self):
        self.assertEqual(claudemux.expected_startup(), claudemux.DEFAULT_STARTUP_SECONDS)

    def test_remembers_and_averages(self):
        claudemux.remember_startup(2.0)
        self.assertEqual(claudemux.expected_startup(), 2.0)
        claudemux.remember_startup(6.0)
        self.assertEqual(claudemux.expected_startup(), 4.0)

    def test_stays_within_bounds_and_survives_junk(self):
        claudemux.remember_startup(900)
        self.assertEqual(claudemux.expected_startup(), claudemux.STARTUP_BOUNDS[1])
        claudemux.write_cache({"startup": "fast"})
        self.assertEqual(claudemux.expected_startup(), claudemux.DEFAULT_STARTUP_SECONDS)

    def test_update_check_and_startup_time_share_the_cache(self):
        claudemux.write_cache({"checked": 1, "latest": "9.9.9"})
        claudemux.remember_startup(2.0)
        self.assertEqual(claudemux.read_cache()["latest"], "9.9.9")
        original = claudemux.fetch_latest_version
        claudemux.fetch_latest_version = lambda timeout=3.0, authoritative=False: "9.9.9"
        self.addCleanup(setattr, claudemux, "fetch_latest_version", original)
        with mock_env(CLAUDEMUX_NO_UPDATE_CHECK=None):
            claudemux.update_available(force=True)
        self.assertEqual(claudemux.read_cache()["startup"], 2.0)


class TestScrolling(unittest.TestCase):
    UNBOUND = (1, "unknown key: PPage")

    def session(self, uid=None):
        return claudemux.Session(
            name="claude-box-me-api", uid=os.getuid() if uid is None else uid,
            socket=None, windows=1, attached=0, created=0, activity=0,
            path="/", command="", pane_pid=0,
        )

    def prepare(self, answers=None, session=None, **env):
        fake = FakeTmux(answers)
        self.addCleanup(setattr, claudemux, "tmux_run", claudemux.tmux_run)
        claudemux.tmux_run = fake
        env.setdefault("CLAUDEMUX_SCROLL", None)
        with mock_env(**env):
            claudemux.prepare_scrolling(session or self.session())
        return fake

    def test_bare_tmux_gets_mouse_focus_and_both_keys(self):
        fake = self.prepare({
            ("list-keys", "-T", "root"): self.UNBOUND,
            ("list-keys", "-T", "prefix"): (0, "bind-key    -T prefix m    select-pane -m"),
        })
        self.assertEqual(
            fake.ran("set-option", "-t"),
            [("set-option", "-t", "=claude-box-me-api:", "-q", "mouse", "on")],
        )
        self.assertEqual(len(fake.ran("set-option", "-s", "-q", "focus-events", "on")), 1)
        self.assertEqual(len(fake.ran("bind-key", "-n", "PPage")), 1)
        self.assertEqual(len(fake.ran("bind-key", "m")), 1)

    def test_mouse_is_set_on_the_session_never_globally(self):
        fake = self.prepare()
        for call in fake.ran("set-option"):
            if "mouse" in call:
                self.assertNotIn("-g", call)

    def test_a_mouse_choice_already_on_the_session_is_kept(self):
        for choice in ("off", "on"):
            fake = self.prepare({("show-options",): (0, choice)})
            self.assertEqual(fake.ran("set-option", "-t"), [])

    def test_keys_the_user_bound_are_left_alone(self):
        fake = self.prepare({
            ("list-keys", "-T", "root"): (0, 'bind-key -T root PPage copy-mode -u'),
            ("list-keys", "-T", "prefix"): (0, "bind-key -T prefix m new-window"),
        })
        self.assertEqual(fake.ran("bind-key"), [])

    def test_a_key_tmux_will_not_describe_is_left_alone(self):
        fake = self.prepare({("list-keys",): (1, "usage: list-keys [-T key-table]")})
        self.assertEqual(fake.ran("bind-key"), [])

    def test_opting_out_touches_nothing(self):
        for word in ("off", "0", "OFF", " no "):
            self.assertEqual(self.prepare(CLAUDEMUX_SCROLL=word).calls, [])

    def test_another_users_server_is_never_changed(self):
        self.assertEqual(self.prepare(session=self.session(uid=os.getuid() + 1)).calls, [])

    def test_binding_state(self):
        self.addCleanup(setattr, claudemux, "tmux_run", claudemux.tmux_run)
        cases = [
            ((0, ""), ""),
            ((1, "unknown key: m"), ""),
            ((1, "usage: list-keys"), None),
            ((0, "bind-key -T prefix m select-pane -m"), "select-pane -m"),
            ((0, "bind-key -r -T prefix m select-pane -m"), "select-pane -m"),
            ((0, 'bind-key -T root PPage if-shell -F "#{alternate_on}" "send-keys PPage" "copy-mode -eu"'),
             'if-shell -F "#{alternate_on}" "send-keys PPage" "copy-mode -eu"'),
        ]
        for answer, expected in cases:
            claudemux.tmux_run = FakeTmux({("list-keys",): answer})
            self.assertEqual(claudemux.binding_state(None, "prefix", "m"), expected, answer)

    def test_session_option_falls_back_to_the_plain_name_on_old_tmux(self):
        self.addCleanup(setattr, claudemux, "tmux_run", claudemux.tmux_run)
        claudemux.tmux_run = FakeTmux({("set-option", "-t", "=s:"): (1, "no such session")})
        self.assertTrue(claudemux.set_session_option(None, "s", "mouse", "on"))
        self.assertEqual(claudemux.tmux_run.calls[-1], ("set-option", "-t", "s", "-q", "mouse", "on"))

    def test_toggle_binding_keeps_its_command_separator_escaped(self):
        # A bare ";" would end the bind-key command and run the rest at once.
        self.assertIn("\;", claudemux.MOUSE_TOGGLE_BINDING)
        self.assertNotIn(";", claudemux.MOUSE_TOGGLE_BINDING)


class TestSessionParsing(unittest.TestCase):
    def line(self, *fields):
        """Build a line the way tmux -F would, escaping spaces as #{q:} does."""
        return " ".join(
            claudemux.MARK + str(f).replace("\\", "\\\\").replace(" ", "\\ ")
            for f in fields
        )

    def test_parses_a_full_line(self):
        now = int(time.time())
        session = claudemux.parse_session_line(
            self.line("claude-root-api", 2, 1, now - 60, now - 5, "/root", "node", 4242),
            uid=0, socket=None,
        )
        self.assertIsNotNone(session)
        self.assertEqual(session.name, "claude-root-api")
        self.assertEqual(session.windows, 2)
        self.assertEqual(session.state, "attached")
        self.assertEqual(session.pane_pid, 4242)
        self.assertGreaterEqual(session.uptime, 60)
        self.assertGreaterEqual(session.idle, 5)

    def test_detached_state(self):
        session = claudemux.parse_session_line(
            self.line("s", 1, 0, 1, 1, "/tmp", "bash", 1), uid=0, socket=None
        )
        self.assertEqual(session.state, "detached")

    def test_handles_a_path_containing_spaces(self):
        session = claudemux.parse_session_line(
            self.line("s", 1, 0, 1, 1, "/root/dir with space", "bash", 7),
            uid=0, socket=None,
        )
        self.assertEqual(session.path, "/root/dir with space")

    def test_an_empty_field_does_not_shift_the_others(self):
        session = claudemux.parse_session_line(
            self.line("s", 1, 0, 1, 1, "", "", 7), uid=0, socket=None
        )
        self.assertEqual(session.path, "")
        self.assertEqual(session.pane_pid, 7)

    def test_rejects_a_short_line(self):
        self.assertIsNone(claudemux.parse_session_line("claude-root-api", 0, None))

    def test_survives_non_numeric_fields(self):
        session = claudemux.parse_session_line(
            self.line("s", "x", "y", "z", "w", "/tmp", "bash", "q"), uid=0, socket=None
        )
        self.assertEqual(session.windows, 0)


class TestServerDiscovery(unittest.TestCase):
    def test_non_root_only_ever_sees_itself(self):
        if os.getuid() == 0:
            self.skipTest("running as root")
        self.assertEqual(claudemux.discover_servers(all_users=True), [(os.getuid(), None)])

    def test_own_server_is_always_first(self):
        servers = claudemux.discover_servers(all_users=False)
        self.assertEqual(servers[0], (os.getuid(), None))


class TestErrorReporting(unittest.TestCase):
    def test_die_carries_its_message_on_the_exception(self):
        """Inside curses, stderr is wiped by the teardown; the message has to
        ride out on SystemExit or the user sees a silent exit."""
        with self.assertRaises(SystemExit) as caught:
            claudemux.die("something went wrong")
        self.assertIn("something went wrong", str(caught.exception))

    def test_a_non_default_exit_code_is_preserved(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                claudemux.die("nope", 3)
        self.assertEqual(caught.exception.code, 3)

    def test_install_hint_always_says_something(self):
        self.assertTrue(claudemux.tmux_install_hint())
        self.assertIn("tmux", claudemux.tmux_install_hint())


class TestVersioning(unittest.TestCase):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_version_file_matches_the_module(self):
        """The update check compares against VERSION, so drift would either
        hide a release or offer an update that is already installed."""
        with open(os.path.join(self.root, "VERSION")) as handle:
            self.assertEqual(handle.read().strip(), claudemux.__version__)

    def test_changelog_documents_the_current_version(self):
        with open(os.path.join(self.root, "CHANGELOG.md")) as handle:
            self.assertIn("## %s" % claudemux.__version__, handle.read())

    def test_version_ordering(self):
        self.assertTrue(claudemux.is_newer("1.2.0", "1.1.9"))
        self.assertTrue(claudemux.is_newer("1.10.0", "1.9.0"))
        self.assertFalse(claudemux.is_newer("1.2.0", "1.2.0"))
        self.assertFalse(claudemux.is_newer("1.1.0", "1.2.0"))

    def test_version_tuple_tolerates_junk(self):
        self.assertEqual(claudemux.version_tuple("1.2.3"), (1, 2, 3))
        self.assertEqual(claudemux.version_tuple("1.2.0rc1"), (1, 2, 0))
        self.assertEqual(claudemux.version_tuple("weird"), (0,))


class TestReleaseParsing(unittest.TestCase):
    def test_reads_the_tag(self):
        self.assertEqual(claudemux.parse_release_tag(b'{"tag_name": "v1.2.2"}'), "1.2.2")

    def test_tolerates_a_missing_v(self):
        self.assertEqual(claudemux.parse_release_tag(b'{"tag_name": "1.2.2"}'), "1.2.2")

    def test_rejects_junk(self):
        self.assertIsNone(claudemux.parse_release_tag(b"not json"))
        self.assertIsNone(claudemux.parse_release_tag(b"{}"))
        self.assertIsNone(claudemux.parse_release_tag(b'{"tag_name": "nightly"}'))
        self.assertIsNone(claudemux.parse_release_tag(b'{"message": "rate limited"}'))


class TestUpdateCache(unittest.TestCase):
    """cached_update() sits on the launch path, so it must never do i/o."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = self.directory.name
        os.environ.pop("CLAUDEMUX_NO_UPDATE_CHECK", None)

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = self.previous
        self.directory.cleanup()

    def test_no_cache_means_no_notice(self):
        self.assertIsNone(claudemux.cached_update())

    def test_a_newer_cached_version_is_reported(self):
        claudemux.write_cache({"checked": time.time(), "latest": "99.0.0"})
        self.assertEqual(claudemux.cached_update(), "99.0.0")

    def test_an_older_cached_version_is_ignored(self):
        claudemux.write_cache({"checked": time.time(), "latest": "0.0.1"})
        self.assertIsNone(claudemux.cached_update())

    def test_the_env_switch_silences_it(self):
        claudemux.write_cache({"checked": time.time(), "latest": "99.0.0"})
        os.environ["CLAUDEMUX_NO_UPDATE_CHECK"] = "1"
        self.assertIsNone(claudemux.cached_update())

    def test_a_corrupt_cache_is_survivable(self):
        os.makedirs(os.path.dirname(claudemux.cache_file()), exist_ok=True)
        with open(claudemux.cache_file(), "w") as handle:
            handle.write("not json at all")
        self.assertEqual(claudemux.read_cache(), {})
        self.assertIsNone(claudemux.cached_update())


class TestCli(unittest.TestCase):
    @staticmethod
    @contextlib.contextmanager
    def quiet():
        """Keep help text and error messages out of the test output."""
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield

    def test_help_and_version_exit_cleanly(self):
        with self.quiet():
            self.assertEqual(claudemux.main(["--help"]), 0)
            self.assertEqual(claudemux.main(["--version"]), 0)

    def test_usage_documents_the_passthrough(self):
        self.assertIn("--resume", claudemux.USAGE)
        self.assertIn("claude-<server>-<user>-<directory>", claudemux.USAGE)

    def test_missing_argument_is_an_error(self):
        with self.quiet(), self.assertRaises(SystemExit):
            claudemux.main(["-n"])


if __name__ == "__main__":
    unittest.main()
