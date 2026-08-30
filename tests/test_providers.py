from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from beadswarm import providers, settings
from support import tmpdir, write_executable


class ParseSpecTests(unittest.TestCase):
    def test_harness_only(self) -> None:
        spec = providers.parse_spec("grok")
        self.assertEqual(spec.harness, "grok")
        self.assertEqual(spec.model, "")
        self.assertEqual(spec.effort, "")

    def test_harness_model_effort(self) -> None:
        spec = providers.parse_spec("claude/fable-5/xhigh")
        self.assertEqual(spec.harness, "claude")
        self.assertEqual(spec.model, "fable-5")
        self.assertEqual(spec.effort, "xhigh")
        self.assertEqual(spec.label, "claude/fable-5/xhigh")

    def test_cursor_alias(self) -> None:
        spec = providers.parse_spec("cursor-agent/composer-2.5")
        self.assertEqual(spec.harness, "cursor")
        self.assertEqual(providers.short_id(spec), "composer")

    def test_unknown_harness(self) -> None:
        with self.assertRaises(ValueError):
            providers.parse_spec("nope/model")


class ParseListTests(unittest.TestCase):
    def test_json_array(self) -> None:
        self.assertEqual(
            providers.parse_model_list('["claude/fable-5/xhigh", "codex/gpt-5.6-sol/xhigh"]'),
            ["claude/fable-5/xhigh", "codex/gpt-5.6-sol/xhigh"],
        )

    def test_csv(self) -> None:
        self.assertEqual(
            providers.parse_model_list("grok, cursor/composer-2.5"),
            ["grok", "cursor/composer-2.5"],
        )


class DetectCliTests(unittest.TestCase):
    def test_detects_configured_bins(self) -> None:
        tmp = tmpdir()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        write_executable(root / "claude", "#!/bin/sh\nexit 0\n")
        env = {
            "BEAD_SWARM_CLAUDE_BIN": str(root / "claude"),
            "BEAD_SWARM_CODEX_BIN": str(root / "no-codex"),
            "BEAD_SWARM_GROK_BIN": str(root / "no-grok"),
            "BEAD_SWARM_CURSOR_BIN": str(root / "no-cursor"),
        }
        clis = providers.detect_clis(env)
        self.assertTrue(clis["claude"])
        self.assertIsNone(clis["codex"])
        self.assertIn("claude=", providers.format_clis(clis))
        self.assertIn("codex=missing", providers.format_clis(clis))


class LadderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            key: os.environ.get(key)
            for key in (
                "PLANNING_MODELS",
                "BUILDING_MODELS",
                "BEAD_SWARM_PLANNING_MODELS",
                "BEAD_SWARM_BUILDING_MODELS",
                "BEAD_SWARM_CLAUDE_BIN",
            )
        }

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_planning_models_reorders_seats(self) -> None:
        os.environ.pop("BEAD_SWARM_PLANNING_MODELS", None)
        os.environ["PLANNING_MODELS"] = '["grok", "claude/opus-5/xhigh"]'
        seats = providers.planning_seats()
        self.assertEqual([seat.id for seat in seats], ["grok", "opus5"])
        self.assertIn("--effort", seats[1].ping)
        self.assertIn("xhigh", seats[1].ping)

    def test_namespaced_planning_wins(self) -> None:
        os.environ["PLANNING_MODELS"] = '["grok"]'
        os.environ["BEAD_SWARM_PLANNING_MODELS"] = '["codex/gpt-5.6-sol"]'
        seats = providers.planning_seats()
        self.assertEqual([seat.id for seat in seats], ["sol"])

    def test_building_defaults_and_override(self) -> None:
        os.environ.pop("BUILDING_MODELS", None)
        os.environ.pop("BEAD_SWARM_BUILDING_MODELS", None)
        labels = [spec.label for spec in providers.building_specs()]
        self.assertIn("cursor/composer-2.5", labels)
        os.environ["BUILDING_MODELS"] = "grok,claude/opus/xhigh"
        labels = [spec.label for spec in providers.building_specs()]
        self.assertEqual(labels, ["grok", "claude/opus/xhigh"])

    def test_available_building_skips_missing_cli(self) -> None:
        os.environ["BUILDING_MODELS"] = "grok,claude/opus"
        available = providers.available_building({"grok": None, "claude": "/bin/claude", "codex": None, "cursor": None})
        self.assertEqual([spec.label for spec in available], ["claude/opus"])

    def test_resolve_pin_accepts_full_spec(self) -> None:
        os.environ["PLANNING_MODELS"] = '["claude/fable"]'
        seats = providers.planning_seats()
        pinned = providers.resolve_pin("grok", seats)
        self.assertEqual(pinned.harness, "grok")


class SettingsTests(unittest.TestCase):
    def test_int_override(self) -> None:
        old = os.environ.get("BEAD_SWARM_WAVE_SIZE")
        os.environ["BEAD_SWARM_WAVE_SIZE"] = "3"
        try:
            self.assertEqual(settings.wave_size(), 3)
        finally:
            if old is None:
                os.environ.pop("BEAD_SWARM_WAVE_SIZE", None)
            else:
                os.environ["BEAD_SWARM_WAVE_SIZE"] = old


if __name__ == "__main__":
    unittest.main()
