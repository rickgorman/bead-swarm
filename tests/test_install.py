from __future__ import annotations

import os
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from beadswarm.home import swarm_home
from beadswarm import install
from support import tmpdir, write_executable


class HomeTests(unittest.TestCase):
    def test_env_overrides(self) -> None:
        with tmpdir() as name:
            os.environ["BEAD_SWARM_HOME"] = name
            try:
                self.assertEqual(swarm_home(), Path(name).resolve())
            finally:
                del os.environ["BEAD_SWARM_HOME"]

    def test_default_is_checkout(self) -> None:
        os.environ.pop("BEAD_SWARM_HOME", None)
        root = Path(__file__).resolve().parent.parent
        self.assertEqual(swarm_home(), root)


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tmpdir()
        self.root = Path(self._tmp.name)
        self.home = self.root / "checkout"
        self.user = self.root / "user"
        self.bindir = self.root / "bin"
        (self.home / ".claude" / "skills" / "bead-swarm").mkdir(parents=True)
        (self.home / ".claude" / "skills" / "bead-swarm" / "SKILL.md").write_text("---\nname: bead-swarm\n---\n")
        for name in ("bead-swarm", "bead-swarm-lab-setup", "bead-swarm-lab-worker"):
            write_executable(self.home / "bin" / name, "#!/bin/sh\necho $BEAD_SWARM_HOME $0\n")
        self.user.mkdir()
        self._old = {
            "BEAD_SWARM_USER_HOME": os.environ.get("BEAD_SWARM_USER_HOME"),
            "BEAD_SWARM_BIN_DIR": os.environ.get("BEAD_SWARM_BIN_DIR"),
        }
        os.environ["BEAD_SWARM_USER_HOME"] = str(self.user)
        os.environ["BEAD_SWARM_BIN_DIR"] = str(self.bindir)

    def tearDown(self) -> None:
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def test_local_links_vendor_skills(self) -> None:
        notes = install.install_local(self.home, force=True)
        grok = self.home / ".grok" / "skills" / "bead-swarm"
        self.assertTrue(grok.is_symlink())
        self.assertEqual(grok.resolve(), (self.home / ".claude" / "skills" / "bead-swarm").resolve())
        self.assertTrue(any("canonical skill" in line for line in notes))

    def test_global_writes_shims_not_copies(self) -> None:
        install.install_global(self.home, bin_dir=self.bindir, force=True)
        shim = self.bindir / "bead-swarm"
        text = shim.read_text()
        self.assertIn("BEAD_SWARM_HOME", text)
        self.assertIn(str(self.home), text)
        self.assertIn("exec", text)
        self.assertTrue(shim.stat().st_mode & stat.S_IXUSR)
        user_skill = self.user / ".claude" / "skills" / "bead-swarm"
        self.assertTrue(user_skill.is_symlink())

    def test_refuses_to_overwrite_without_force(self) -> None:
        dest = self.home / ".grok" / "skills" / "bead-swarm"
        dest.parent.mkdir(parents=True)
        dest.write_text("nope")
        with self.assertRaises(SystemExit):
            install.install_local(self.home, force=False)


if __name__ == "__main__":
    unittest.main()
