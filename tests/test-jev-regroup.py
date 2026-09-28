#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path


PATH = Path(__file__).parents[1] / "scripts" / "jev-regroup.py"
SPEC = importlib.util.spec_from_file_location("jev_regroup", PATH)
JEV = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(JEV)


MANIFEST = """MANIFEST  4 episodes · 21d · [Rn] = lexical group, [--] = ungrouped
Regroup by MEANING. The lexical pass only sees shared words.
[R1 ] aaaaaa 09-20 safe-project         s80  e4/r2/c1  harmless existing prompt
       terms: email, person, lookup
[-- ] bbbbbb 09-21 safe-project         s75  e3 /r1/c0  harmless first prompt
       terms: contact, details, profile
[-- ] cccccc 09-22 safe-project         s70  e2/r1/c0  harmless second prompt
       terms: address, find, profile
[~--] dddddd 09-23 safe-project         s90  e8/r4/c2  declined prompt
       terms: private, declined, terms
"""


class JevRegroupTests(unittest.TestCase):
    def test_terms_only_omits_prompts_and_declined_episode(self):
        episodes = JEV.parse_manifest(MANIFEST)
        payload = JEV.build_request(episodes)
        encoded = str(payload)
        self.assertNotIn("opening_prompt", encoded)
        self.assertNotIn("declined prompt", encoded)
        self.assertNotIn("dddddd", encoded)
        self.assertIn("terms", encoded)

    def test_prompts_require_narrow_non_sensitive_scope(self):
        episodes = JEV.parse_manifest(MANIFEST)
        with self.assertRaises(JEV.ManifestError):
            JEV.validate_prompt_scope(episodes, "all")
        with self.assertRaises(JEV.ManifestError):
            JEV.validate_prompt_scope(episodes, "job-search")
        JEV.validate_prompt_scope(episodes, "safe-project")

    def test_declined_episode_is_never_questioned(self):
        payload = JEV.build_request(JEV.parse_manifest(MANIFEST))
        self.assertTrue(payload["questions"])
        self.assertFalse(any("dddddd" in key for key in payload["questions"]))

    def test_complete_link_avoids_transitive_weak_merge(self):
        episodes = [x for x in JEV.parse_manifest(MANIFEST) if x.group == "--" and not x.declined]
        extra = JEV.Episode("eeeeee", "--", False, "09-24", "safe-project", 70, 1, 1, 0,
                            ("another", "intent"), "prompt")
        answers = {
            "pair_bbbbbb_cccccc": {"noul": 0.9},
            "pair_bbbbbb_eeeeee": {"noul": 0.2},
            "pair_cccccc_eeeeee": {"noul": 0.9},
        }
        groups = JEV.accepted_pairs(sorted(episodes + [extra], key=lambda x: x.episode_id),
                                    answers, 0.8)
        self.assertEqual(groups[0]["members"], ["bbbbbb", "cccccc"])


if __name__ == "__main__":
    unittest.main()
