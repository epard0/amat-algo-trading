"""Exercise real Git working/index/history boundaries with disposable state."""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "publication", Path(__file__).parents[1] / "check_publication.py"
)
publication = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publication)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True
        )

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def test_ignored_private_state_and_blank_templates(self):
        self.write(".gitignore", ".KEYS/\n.env\n")
        self.write(".KEYS/private", "local private material")
        self.write(".env", "private environment")
        self.write(".env.live.example", "API_KEY=\n")
        self.assertEqual(
            publication.check(self.root, staged=False, history=False)[0], []
        )

    def test_remote_identity_files_are_rejected_but_example_is_allowed(self):
        for name in (
            "remote.toml",
            "private/ssh.toml",
            "custom.local.toml",
            "known_hosts",
            "id_ecdsa",
            "operator.pub",
        ):
            self.assertIn(
                "private configuration/key path", publication.path_findings(name)
            )
        self.assertEqual(
            publication.path_findings("frontend/config/remote.example.toml"), []
        )

    def test_non_loopback_endpoint_is_reported_without_value(self):
        address = b".".join([b"8"] * 4)
        issues = publication.content_findings(b'host = "' + address + b'"')
        self.assertIn(
            "embedded non-loopback IPv4 address; use private configuration", issues
        )
        self.assertNotIn(address.decode(), str(issues))
        self.assertEqual(publication.content_findings(b"http://127.0.0.1:8765/"), [])
        self.assertEqual(
            publication.content_findings(b'host = "vps.example.invalid"'), []
        )

    def test_staged_secret_is_checked_after_worktree_cleanup(self):
        token = "sk-" + "x" * 32
        self.write("config.txt", token)
        self.git("add", "config.txt")
        self.write("config.txt", "now blank")
        self.assertEqual(
            publication.check(self.root, staged=False, history=False)[0], []
        )
        self.assertIn(
            ("config.txt", "provider token"),
            publication.check(self.root, staged=True, history=False)[0],
        )

    def test_prose_anchor_is_not_a_provider_token(self):
        self.assertEqual(
            publication.content_findings(
                b"#review-the-task-and-world-skill-with-the-human"
            ),
            [],
        )

    def test_export_omits_git_and_ignored_private_files(self):
        self.write(".gitignore", ".KEYS/\n")
        self.write(".KEYS/private", "private material")
        self.write("source.py", "print('example')\n")
        self.git("add", "source.py")
        with tempfile.TemporaryDirectory() as parent:
            destination = Path(parent) / "export"
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parents[1] / "check_publication.py"),
                    "--export",
                    str(destination),
                ],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("Exported", result.stdout)
            self.assertTrue((destination / "source.py").exists())
            self.assertFalse((destination / ".KEYS").exists())
            self.assertFalse((destination / ".git").exists())
            second = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parents[1] / "check_publication.py"),
                    "--export",
                    str(destination),
                ],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(
                (destination / "source.py").read_text(), "print('example')\n"
            )

    def test_history_retains_deleted_secret(self):
        self.write("old.txt", "ghp_" + "x" * 36)
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.git("rm", "old.txt")
        self.git("commit", "-qm", "remove")
        findings, _ = publication.check(self.root, staged=False, history=True)
        self.assertTrue(
            any(
                name.startswith("history:") and issue == "provider token"
                for name, issue in findings
            )
        )

    def test_tracked_private_path_remains_a_finding_when_ignored(self):
        self.write(".env", "KEY=\n")
        self.git("add", ".env")
        self.write(".gitignore", ".env\n")
        findings, _ = publication.check(self.root, staged=False, history=False)
        self.assertIn((".env", "private configuration/key path"), findings)

    def test_external_symlink_is_not_followed(self):
        (self.root / "outside").symlink_to("../unrelated-private-file")
        findings, _ = publication.check(self.root, staged=False, history=False)
        self.assertIn(("outside", "symlink escapes repository"), findings)


if __name__ == "__main__":
    unittest.main()
