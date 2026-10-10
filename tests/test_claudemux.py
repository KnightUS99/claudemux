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
