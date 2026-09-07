"""Behavior checks using isolated, disposable libraries."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import gardener


class GardenerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gardener-test-")
        self.root = Path(self.temp.name)
        self.library = self.root / "library.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def run_cmd(self, *args):
        return gardener.run(["--library", str(self.library), *args])

    def add(self, name="deck-maker", description="制作演示文稿 PPT slides", **extra):
        path = self.root / (name + ".json")
        path.write_text(json.dumps({"name": name, "description": description,
                                    "source_url": "https://author.test/skills/" + name,
                                    "categories": ["presentations"], **extra}), encoding="utf-8")
        return self.run_cmd("upsert", str(path))

    def set_old(self, *ids):
        with sqlite3.connect(self.library) as db:
            for ident in ids:
                row = json.loads(db.execute("SELECT data FROM skills WHERE id=?", (ident,)).fetchone()[0])
                row["created_at"] = "2020-01-01T00:00:00+00:00"
                db.execute("UPDATE skills SET data=? WHERE id=?", (json.dumps(row), ident))

    def test_chinese_query_recalls_english_skill(self):
        self.add(description="Create slide decks and presentations")
        result = self.run_cmd("recommend", "请帮我制作一份演示文稿")
        self.assertEqual(result["results"][0]["name"], "deck-maker")

    def test_favorite_never_introduces_unrelated_candidate(self):
        first = self.add()
        other = self.add("calendar", "Arrange meeting room calendars", categories=["productivity"])
        self.run_cmd("favorite", other["id"], "--reason", "test user choice")
        results = self.run_cmd("recommend", "演示文稿")
        self.assertEqual([r["id"] for r in results["results"]], [first["id"]])

    def test_generic_word_skill_does_not_route_to_skill_management(self):
        self.add("skill-creator", "Create skills", categories=["skill-management"])
        expected = self.add()
        results = self.run_cmd("recommend", "帮我找做PPT的skill")
        self.assertEqual([r["id"] for r in results["results"]], [expected["id"]])

    def test_recruitment_stays_in_candidate_area_until_promoted(self):
        path = self.root / "candidate.json"
        path.write_text(json.dumps({"name": "new-ppt", "description": "Create presentations",
                                    "source_url": "https://author.test/new-ppt", "categories": ["presentations"]}))
        recruited = self.run_cmd("recruit", str(path), "--need", "No active presentation skill supports this output")
        candidate_id = recruited["candidate_ids"][0]
        self.assertEqual(self.run_cmd("show", candidate_id)["state"], "candidate")
        self.assertEqual(self.run_cmd("recommend", "presentation")["results"], [])
        self.run_cmd("promote", candidate_id, "--reason", "Trial passed and fills the need")
        self.assertEqual(self.run_cmd("recommend", "presentation")["results"][0]["id"], candidate_id)

    def test_compare_proposes_review_without_changing_either_skill(self):
        active = self.add("ppt-a", "Create product presentations", tags=["slides"])
        path = self.root / "candidate.json"
        path.write_text(json.dumps({"name": "ppt-b", "description": "Create product presentations", "tags": ["slides"],
                                    "source_url": "https://author.test/ppt-b", "categories": ["presentations"]}))
        candidate = self.run_cmd("recruit", str(path), "--need", "Evaluate a visual alternative")["candidate_ids"][0]
        pairs = self.run_cmd("compare", "--threshold", "0.1")["pairs"]
        self.assertTrue(any({row["left"]["id"], row["right"]["id"]} == {active["id"], candidate} for row in pairs))
        self.assertEqual(self.run_cmd("show", active["id"])["state"], "active")
        self.assertEqual(self.run_cmd("show", candidate)["state"], "candidate")

    def test_stale_candidate_can_be_archived_and_restored(self):
        path = self.root / "candidate.json"
        path.write_text(json.dumps({"name": "candidate", "description": "Create presentations",
                                    "source_url": "https://author.test/candidate", "categories": ["presentations"]}))
        candidate = self.run_cmd("recruit", str(path), "--need", "Testing") ["candidate_ids"][0]
        db = sqlite3.connect(self.library)
        try:
            row = json.loads(db.execute("SELECT data FROM skills WHERE id=?", (candidate,)).fetchone()[0])
            row["candidate_since"] = "2020-01-01T00:00:00+00:00"
            db.execute("UPDATE skills SET data=? WHERE id=?", (json.dumps(row), candidate))
            db.commit()
        finally:
            db.close()
        report = self.run_cmd("maintain", "--archive-stale-candidates", "--reason", "User requested candidate cleanup")
        self.assertIn(candidate, report["archived"])
        self.run_cmd("restore", candidate, "--reason", "User wants another trial")
        self.assertEqual(self.run_cmd("show", candidate)["state"], "candidate")

    def test_ask_answers_detail_and_latest_with_freshness(self):
        item = self.add("new-ppt", release_published_at="2026-09-07T08:00:00+00:00",
                        release_url="https://author.test/releases/1", discovered_at="2026-09-07T08:10:00+00:00")
        detail = self.run_cmd("ask", "new-ppt 的详细信息", "--skill", item["id"])
        self.assertEqual(detail["kind"], "skill_detail")
        latest = self.run_cmd("ask", "最新发布的 Skill")
        self.assertEqual(latest["items"][0]["id"], item["id"])
        self.assertFalse(latest["requires_online_refresh"])

    def test_doctor_surfaces_candidates_overlap_and_preference_gaps(self):
        self.add("ppt-a", "Create product presentation slides", tags=["slides"])
        path = self.root / "candidate.json"
        path.write_text(json.dumps({"name": "ppt-b", "description": "Create product presentation slides",
                                    "source_url": "https://author.test/ppt-b", "categories": ["presentations"],
                                    "tags": ["slides"]}))
        self.run_cmd("recruit", str(path), "--need", "visual alternative")
        self.run_cmd("preferences", "set", "preferred_categories", '["presentations","audio"]',
                     "--reason", "user preference")
        report = self.run_cmd("doctor", "--threshold", "0.1")
        self.assertEqual(report["counts"]["candidate"], 1)
        self.assertIn("audio", report["preferred_category_gaps"])
        self.assertTrue(report["overlap_review"])
        self.assertTrue(any(action["action"] == "review_candidates" for action in report["next_actions"]))

    def test_passport_uses_only_recorded_successes_as_strengths(self):
        item = self.add("passport-skill")
        empty = self.run_cmd("passport", item["id"])
        self.assertEqual(empty["verified_strengths"], [])
        trial = self.run_cmd("trial-start", item["id"], "--task", "make a short deck",
                             "--criterion", "three valid slides", "--platform", "Codex")
        self.run_cmd("trial-finish", trial["id"], "--outcome", "success",
                     "--evidence", "fixture:three-slides", "--note", "verified")
        card = self.run_cmd("passport", item["id"])
        self.assertEqual(card["verified_strengths"], ["make a short deck"])

    def test_duel_reports_evidence_leader_without_universal_winner_claim(self):
        first = self.add("duel-a")
        second = self.add("duel-b")
        self.run_cmd("record-use", first["id"], "--outcome", "success", "--platform", "Codex",
                     "--evidence", "fixture:passed", "--note", "verified")
        result = self.run_cmd("duel", first["id"], second["id"])
        self.assertEqual(result["evidence_leader"]["id"], first["id"])
        self.assertIn("does not declare", result["note"])

    def test_explore_surfaces_relevant_undertried_skill(self):
        familiar = self.add("familiar-ppt")
        fresh = self.add("fresh-ppt")
        self.run_cmd("record-use", familiar["id"], "--outcome", "success", "--platform", "Codex",
                     "--evidence", "fixture:passed", "--note", "verified")
        result = self.run_cmd("explore", "制作演示文稿")
        self.assertEqual(result["selected"]["id"], fresh["id"])

    def test_invalid_annotation_does_not_corrupt_description(self):
        item = self.add()
        patch = self.root / "invalid.json"
        patch.write_text('{"description":null}')
        with self.assertRaises(ValueError):
            self.run_cmd("annotate", item["id"], str(patch), "--reason", "invalid input")
        self.assertIsInstance(self.run_cmd("show", item["id"])["description"], str)

    def test_hard_filters_reject_unknown_and_override_saved_preference(self):
        self.add()
        paid = self.add("paid-slides", cost="paid", offline="yes", platforms={"Codex": "supported"})
        self.assertEqual(self.run_cmd("recommend", "PPT", "--cost", "free")["results"], [])
        self.run_cmd("preferences", "set", "cost", '"free"', "--reason", "user requested free")
        result = self.run_cmd("recommend", "PPT", "--cost", "paid", "--offline", "yes", "--platform", "Codex", "--strict-platform")
        self.assertEqual([r["id"] for r in result["results"]], [paid["id"]])

    def test_unknown_platform_disclosed_or_filtered(self):
        self.add()
        self.assertEqual(self.run_cmd("recommend", "PPT", "--platform", "Codex")["results"][0]["platform_status"], "unknown")
        self.assertEqual(self.run_cmd("recommend", "PPT", "--platform", "Codex", "--strict-platform")["results"], [])

    def test_scan_preserves_archive_favorite_and_annotations(self):
        entry = self.root / "skill" / "SKILL.md"
        entry.parent.mkdir()
        entry.write_text('---\nname: local-ppt\ndescription: >\n  制作演示文稿\n  PPT slides\n---\n', encoding="utf-8")
        self.assertEqual(self.run_cmd("scan", "--root", str(self.root))["scanned"], 1)
        item = self.run_cmd("show", "local-ppt")
        patch = self.root / "patch.json"
        patch.write_text('{"tags":["minimal"],"cost":"free"}')
        self.run_cmd("annotate", item["id"], str(patch), "--reason", "verified fixture")
        self.run_cmd("favorite", item["id"], "--reason", "test")
        self.run_cmd("archive", item["id"], "--reason", "test")
        self.run_cmd("scan", "--root", str(self.root))
        item2 = self.run_cmd("show", item["id"])
        self.assertTrue(item2["favorite"])
        self.assertEqual(item2["state"], "archived")
        self.assertEqual(item2["tags"], ["minimal"])
        self.assertEqual(item2["successes"], 0)
        self.assertEqual(self.run_cmd("status")["total"], 1)

    def test_same_name_different_source_requires_id(self):
        self.add()
        self.add(source_url="https://different.test/skill")
        with self.assertRaises(ValueError):
            self.run_cmd("show", "deck-maker")
        self.assertEqual(self.run_cmd("status")["total"], 2)

    def test_trial_evidence_and_no_double_count(self):
        item = self.add()
        trial = self.run_cmd("trial-start", item["id"], "--task", "short deck", "--criterion", "3 pages", "--platform", "Codex")
        with self.assertRaises(ValueError):
            self.run_cmd("trial-finish", trial["id"], "--outcome", "success", "--note", "no artifact")
        self.run_cmd("trial-finish", trial["id"], "--outcome", "success", "--evidence", "fixture:verified-three-pages", "--note", "verified")
        with self.assertRaises(ValueError):
            self.run_cmd("trial-finish", trial["id"], "--outcome", "success", "--evidence", "same", "--note", "duplicate")
        fresh = self.run_cmd("show", item["id"])
        self.assertEqual(fresh["successes"], 1)
        self.assertEqual(fresh["ratings"], [])

    def test_blocked_is_not_failure_or_success(self):
        item = self.add()
        self.run_cmd("record-use", item["id"], "--outcome", "blocked", "--platform", "Codex", "--note", "dependency absent")
        fresh = self.run_cmd("show", item["id"])
        self.assertEqual((fresh["successes"], fresh["failures"], fresh["last_used_at"]), (0, 0, None))

    def test_success_and_rating_affect_only_related_recommendations(self):
        first = self.add("ppt-first")
        second = self.add("ppt-second")
        self.run_cmd("record-use", second["id"], "--outcome", "success", "--platform", "Codex", "--evidence", "fixture:checked", "--note", "pass", "--rating", "5")
        self.assertEqual(self.run_cmd("recommend", "演示文稿")["results"][0]["id"], second["id"])
        self.assertEqual(self.run_cmd("show", first["id"])["successes"], 0)

    def test_maintenance_preview_protection_archive_restore(self):
        unused = self.add("old")
        favorite = self.add("favorite")
        new = self.add("new", maintenance="archived")
        self.set_old(unused["id"], favorite["id"])
        self.run_cmd("favorite", favorite["id"], "--reason", "keep")
        self.assertEqual(self.run_cmd("maintain")["archived"], [])
        result = self.run_cmd("maintain", "--archive-unused", "--reason", "authorized old cleanup")
        self.assertEqual(result["archived"], [unused["id"]])
        self.assertEqual(self.run_cmd("show", new["id"])["state"], "active")
        self.assertEqual(self.run_cmd("show", favorite["id"])["state"], "active")
        self.run_cmd("restore", unused["id"], "--reason", "undo")
        self.assertEqual(self.run_cmd("show", unused["id"])["state"], "active")
        self.assertTrue(any(e["action"] == "archive" for e in self.run_cmd("history", "--skill", unused["id"])))

    def test_bulk_upsert_rolls_back_on_invalid_record(self):
        path = self.root / "bulk.json"
        path.write_text(json.dumps([{"name": "good", "description": "PPT", "source_url": "https://test.org/good"},
                                    {"name": "bad", "description": "PPT", "source_url": "javascript:alert(1)"}]))
        with self.assertRaises(ValueError):
            self.run_cmd("upsert", str(path))
        self.assertEqual(self.run_cmd("status")["total"], 0)

    def test_catalog_preserves_source_without_claiming_personal_use(self):
        path = self.root / "catalog.json"
        record = {"name": "remote", "type": "agent_skill", "summary": "PPT", "categories": ["content-creation"],
                  "source": {"canonical_url": "https://github.com/author/repo", "commit": "abc123", "checked_at": "2026-09-03"},
                  "file_evidence": {"upstream_path": "skills/remote/SKILL.md"},
                  "platforms": [{"name": "Codex", "claim_status": "publisher_explicit", "runtime_status": "task_pass"}]}
        path.write_text(json.dumps([record, {"type": "tool_tip", "name": "tip"}]))
        result = self.run_cmd("import-catalog", str(path))
        self.assertEqual(result["imported"], 1)
        self.run_cmd("import-catalog", str(path))
        item = self.run_cmd("show", "remote")
        self.assertFalse(item["installed"])
        self.assertEqual(item["successes"], 0)
        self.assertEqual(item["platforms"]["Codex"], "supported")
        self.assertEqual(self.run_cmd("status")["total"], 1)

    def test_missing_source_is_reported_without_deletion(self):
        entry = self.root / "SKILL.md"
        entry.write_text("---\nname: temp-ppt\ndescription: PPT\n---\n")
        self.run_cmd("scan", "--root", str(self.root))
        entry.unlink()
        self.assertIn("local_entry_missing", self.run_cmd("maintain")["findings"][0]["reasons"])
        self.assertEqual(self.run_cmd("status")["total"], 1)

    def test_export_is_complete_and_refuses_overwrite(self):
        item = self.add()
        self.run_cmd("favorite", item["id"], "--reason", "test")
        output = self.root / "export.json"
        self.run_cmd("export", str(output))
        data = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(data["skills"][0]["favorite"])
        self.assertTrue(data["events"])
        with self.assertRaises(FileExistsError):
            self.run_cmd("export", str(output))

    def test_concurrent_usage_does_not_lose_updates(self):
        item = self.add()
        command = [sys.executable, str(Path(gardener.__file__)), "--library", str(self.library), "record-use", item["id"],
                   "--outcome", "success", "--platform", "Codex", "--evidence", "fixture:checked", "--note", "parallel check"]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr.decode())
        self.assertEqual(self.run_cmd("show", item["id"])["successes"], 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
