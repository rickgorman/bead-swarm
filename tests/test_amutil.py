from __future__ import annotations

import unittest

from beadswarm.amutil import parse_reserve
from beadswarm.contract import parse_contract
from beadswarm.worker import parse_allowed


class ParseReserveTests(unittest.TestCase):
    def test_conflict_json_is_exit_zero(self) -> None:
        stdout = """{"granted": [], "conflicts": [{"path": "files/shared.txt", "holder": "BlueLake"}]}"""
        parsed = parse_reserve(stdout, returncode=0)
        self.assertTrue(parsed["conflicted"])
        self.assertFalse(parsed["ok"])

    def test_granted_is_ok(self) -> None:
        stdout = """{"granted": [{"id": 1, "path": "files/a.txt", "exclusive": true}], "conflicts": []}"""
        parsed = parse_reserve(stdout, returncode=0)
        self.assertTrue(parsed["ok"])
        self.assertFalse(parsed["conflicted"])

    def test_mailbox_busy_is_retryable(self) -> None:
        stderr = "error: Resource is temporarily busy. Wait a moment and try again. (mailbox activity lock is busy"
        parsed = parse_reserve("", stderr, returncode=1)
        self.assertTrue(parsed["busy"])
        self.assertFalse(parsed["ok"])


class ContractTests(unittest.TestCase):
    def test_touch_label_adds_file(self) -> None:
        shown = {
            "description": "```json\n{\"files\": [], \"mode\": \"append\", \"lease\": \"exclusive\"}\n```",
            "labels": ["touch:files/hotspot.txt"],
        }
        contract = parse_contract(shown)
        self.assertEqual(contract["files"], ["files/hotspot.txt"])

    def test_title_fallback(self) -> None:
        contract = parse_contract({"title": "[lab] write files/03.txt", "description": ""})
        self.assertEqual(contract["files"], ["files/03.txt"])
        self.assertEqual(contract["mode"], "write")


class ListScenariosTests(unittest.TestCase):
    def test_list_includes_overlap(self) -> None:
        from beadswarm.scenario import list_scenarios

        names = [path.stem for path in list_scenarios()]
        self.assertIn("01-overlap-exclusive", names)
        self.assertIn("00-width6", names)


class PromptTests(unittest.TestCase):
    def test_parse_allowed(self) -> None:
        prompt = "Allowed bead ids (this wave only):\n- ovx-a\n- ovx-b\n\nWhen you have finished\n"
        self.assertEqual(parse_allowed(prompt), ["ovx-a", "ovx-b"])


if __name__ == "__main__":
    unittest.main()
