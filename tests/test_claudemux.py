"""Tests for claudemux. Standard library only: python3 -m unittest discover -s tests"""

import contextlib
import io
import os
import tempfile
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claudemux  # noqa: E402


class TestNames(unittest.TestCase):
    def test_sanitize_strips_characters_tmux_rejects(self):
        self.assertEqual(claudemux.sanitize("my.project:1"), "my-project-1")
        self.assertEqual(claudemux.sanitize("a/b c"), "a-b-c")

    def test_sanitize_never_returns_empty(self):
        self.assertEqual(claudemux.sanitize(""), "session")
        self.assertEqual(claudemux.sanitize("..."), "session")
        self.assertEqual(claudemux.sanitize("---"), "session")

    def test_full_name_includes_the_owner(self):
        self.assertEqual(claudemux.full_name("site", user="jaime"), "claude-jaime-site")

    def test_full_name_leaves_an_already_qualified_name_alone(self):
        self.assertEqual(
            claudemux.full_name("claude-jaime-site", user="root"), "claude-jaime-site"
        )

    def test_full_name_sanitizes_both_halves(self):
        self.assertEqual(claudemux.full_name("my.app", user="od.d"), "claude-od-d-my-app")


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
        self.assertIn("claude-<user>-<directory>", claudemux.USAGE)

    def test_missing_argument_is_an_error(self):
        with self.quiet(), self.assertRaises(SystemExit):
            claudemux.main(["-n"])


if __name__ == "__main__":
    unittest.main()
