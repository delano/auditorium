import base64
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "test.sqlite"
        core.create(self.db, self.root, "example/docs")
        self.ws = core.state(self.db)["workspace"]
        self.uid = self.ws["id"]
        with core.connection(self.db, True) as c:
            for fid in ("A", "B", "C"):
                c.execute(
                    "INSERT INTO findings(id,title,quotation,path,page_url,target,severity,evidence) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        fid,
                        "Problem " + fid,
                        "Original text",
                        fid + ".md",
                        "https://docs.onetimesecret.com/en/" + fid,
                        "main",
                        "medium",
                        "source revision",
                    ),
                )
            c.execute(
                "INSERT INTO batches(id,title,target,submitted_at) VALUES(1,'Batch','main',?)",
                (core.now(),),
            )
            c.executemany(
                "INSERT INTO batch_findings(batch_id,finding_id) VALUES(1,?)",
                [(i,) for i in ("A", "B")],
            )
            self.pid = c.execute(
                "INSERT INTO pull_requests(batch_id,url,metadata,tree,observed_at) VALUES(1,'https://github.com/example/docs/pull/1',?,?,?)",
                (
                    json.dumps({"head_sha": "a" * 40}),
                    json.dumps({"A.md": "blob-a", "B.md": "blob-b"}),
                    core.now(),
                ),
            ).lastrowid
            self.hid = core.make_handoff(c, self.db, "validation", 1, ["A", "B"])

    def action(self, name, **kw):
        return core.action(self.db, {"database_id": self.uid, "action": name, **kw})

    def report(self, fid="A", outcome="pass", rec="accept", handoff=None):
        return {
            "contract": "brenda",
            "database_id": self.uid,
            "handoff_id": handoff or self.hid,
            "checks": [
                {
                    "finding_id": fid,
                    "pr_id": self.pid,
                    "revision": "a" * 40,
                    "source_paths": [fid + ".md"],
                    "proposed": "Proposed text",
                    "outcome": outcome,
                    "recommendation": rec,
                    "summary": "The original issue is corrected.",
                    "target_url": "https://docs.onetimesecret.com/en/" + fid,
                    "screenshots": [],
                }
            ],
        }

    def record(self, *args, **kw):
        with patch("core.tree_at", return_value={"A.md": "blob-a", "B.md": "blob-b"}):
            core.record(self.db, self.report(*args, **kw))

    def item(self, fid="A"):
        return next(i for i in core.state(self.db)["queue"] if i["id"] == fid)

    def entry(self, fid):
        i = self.item(fid)
        return {
            "finding_id": fid,
            "check_id": i["check"]["id"] if i["check"] else None,
            "decision_id": i["decision"]["id"] if i["decision"] else None,
        }

    def observe(self, head="b" * 40, tree=None, time=None):
        with core.connection(self.db, True) as c:
            c.execute(
                "UPDATE pull_requests SET metadata=?,tree=?,observed_at=? WHERE id=?",
                (
                    json.dumps({"head_sha": head}),
                    json.dumps(tree or {"A.md": "blob-a", "B.md": "blob-b"}),
                    time or core.now(),
                    self.pid,
                ),
            )


class WorkflowTests(Fixture):
    def test_create_is_empty_and_does_not_overwrite(self):
        with self.assertRaises(ValueError):
            core.create(self.db, self.root, "example/docs")
        blank = self.root / "empty.sqlite"
        core.create(blank, self.root, "example/docs")
        self.assertEqual(core.state(blank)["findings"], [])

    def test_failed_creation_leaves_no_partial_database(self):
        path = self.root / "failed.sqlite"
        with (
            patch("core.digest", side_effect=ValueError("Failed schema validation")),
            self.assertRaises(ValueError),
        ):
            core.create(path, self.root, "example/docs")
        self.assertFalse(path.exists())

    def test_old_database_rejected_without_writes(self):
        old = self.root / "old.sqlite"
        with sqlite3.connect(old) as c:
            c.execute("CREATE TABLE findings(id TEXT)")
        before = old.read_bytes()
        with self.assertRaises(ValueError):
            core.state(old)
        self.assertEqual(before, old.read_bytes())

    def test_uuid_pins_actions_and_reports(self):
        with self.assertRaises(ValueError):
            core.action(
                self.db,
                {"action": "new_batch", "database_id": "wrong", "finding_ids": ["C"]},
            )
        report = self.report()
        report["database_id"] = "wrong"
        with self.assertRaises(ValueError):
            core.record(self.db, report)

    def test_newer_report_does_not_assert_stale_evidence(self):
        self.record()
        self.observe(time="2000-01-01T00:00:00+00:00")
        item = self.item()
        self.assertEqual(item["freshness"], "unknown")
        self.assertIn("predates", item["freshness_note"])

    def test_unrelated_commit_preserves_acceptance_and_scope_change_reopens(self):
        self.record()
        self.action(
            "decide", batch_id=1, entries=[self.entry("A")], disposition="accept"
        )
        self.observe()
        self.assertTrue(self.item()["resolved"])
        self.assertIn("unchanged", self.item()["freshness_note"])
        self.observe(tree={"A.md": "new-blob", "B.md": "blob-b"})
        self.assertFalse(self.item()["resolved"])
        self.assertEqual(self.item()["freshness"], "changed")

    def test_declared_supporting_dependency_changes_require_check(self):
        report = self.report()
        report["checks"][0]["source_paths"].append("B.md")
        with patch("core.tree_at", return_value={"A.md": "blob-a", "B.md": "blob-b"}):
            core.record(self.db, report)
        self.observe(tree={"A.md": "blob-a", "B.md": "changed"})
        self.assertEqual(self.item()["freshness"], "changed")

    def test_group_acceptance_is_atomic_and_has_no_network_call(self):
        self.record()
        self.record("B", outcome="needs_work", rec="needs_work")
        with (
            patch("core.run", side_effect=AssertionError("Network called")),
            self.assertRaises(ValueError),
        ):
            self.action(
                "decide",
                batch_id=1,
                entries=[self.entry("A"), self.entry("B")],
                disposition="accept",
            )
        self.assertEqual(core.state(self.db)["decisions"], [])
        self.record("B")
        with patch("core.run", side_effect=AssertionError("Network called")):
            self.action(
                "decide",
                batch_id=1,
                entries=[self.entry("A"), self.entry("B")],
                disposition="accept",
            )
        self.assertTrue(all(i["resolved"] for i in core.state(self.db)["queue"]))

    def test_optimistic_decision_cannot_accept_new_unseen_report(self):
        self.record()
        old = self.entry("A")
        self.record()
        with self.assertRaises(ValueError):
            self.action("decide", batch_id=1, entries=[old], disposition="accept")

    def test_agent_never_records_operator_decisions(self):
        self.record()
        self.assertEqual(core.state(self.db)["decisions"], [])

    def test_requested_correction_stays_explicit_after_new_agent_report(self):
        self.record(outcome="needs_work", rec="needs_work")
        self.action(
            "decide",
            batch_id=1,
            entries=[self.entry("A")],
            disposition="needs_work",
            notes="Revise the cross-page policy.",
        )
        self.record()
        self.assertEqual(self.item()["category"], "decisions")
        self.assertIn("cross-page", self.item()["decision"]["notes"])
        self.assertFalse(self.item()["can_group_accept"])
        self.assertTrue(self.item()["can_accept"])

    def test_scoped_handoff_rejects_other_member_and_followup_excludes_waiting(self):
        hid = self.action("handoff", kind="validation", batch_id=1, finding_ids=["A"])
        with self.assertRaises(ValueError):
            core.record(self.db, self.report("B", handoff=hid))
        with self.assertRaises(ValueError):
            self.action("handoff", kind="follow_up", batch_id=1, finding_ids=["A"])
        self.record()
        self.action(
            "decide",
            batch_id=1,
            entries=[self.entry("A")],
            disposition="needs_work",
            notes="Fix this.",
        )
        hid = self.action("handoff", kind="follow_up", batch_id=1, finding_ids=["A"])
        h = next(h for h in core.state(self.db)["handoffs"] if h["id"] == hid)
        self.assertIn("manual", h["prompt"])
        self.assertIn('"A"', h["prompt"])
        self.assertNotIn('"B"', h["prompt"])

    def test_deferred_returns_available_with_history_and_other_terminals_resolve(self):
        self.action(
            "decide",
            batch_id=1,
            entries=[self.entry("A")],
            disposition="deferred",
            notes="Separate policy work.",
        )
        self.assertIn("A", core.state(self.db)["available"])
        self.assertTrue(self.item()["resolved"])
        self.action(
            "decide",
            batch_id=1,
            entries=[self.entry("B")],
            disposition="resolved_by_revert",
            notes="Unwanted change removed; original issue remains.",
        )
        self.assertTrue(self.item("B")["resolved"])

    def test_override_requires_reason_and_observed_heads(self):
        self.record(outcome="needs_work", rec="needs_work")
        with self.assertRaises(ValueError):
            self.action(
                "decide", batch_id=1, entries=[self.entry("A")], disposition="accept"
            )
        self.action(
            "decide",
            batch_id=1,
            entries=[self.entry("A")],
            disposition="accept",
            notes="Checked the source; the reported concern does not apply.",
        )
        self.assertTrue(self.item()["resolved"])

    def test_discovery_saves_received_prompt_even_for_zero_findings(self):
        hid = self.action("handoff", kind="discovery", focus="Unreviewed sources")
        prompt = next(
            h["prompt"] for h in core.state(self.db)["handoffs"] if h["id"] == hid
        )
        core.record(
            self.db,
            {
                "contract": "brenda",
                "database_id": self.uid,
                "handoff_id": hid,
                "received_prompt": prompt,
                "findings": [],
                "coverage": [],
            },
        )
        self.assertEqual(core.state(self.db)["discovery_prompts"][0]["prompt"], prompt)

    def test_mixed_targets_prepare_separate_batches_without_git(self):
        with core.connection(self.db, True) as c:
            c.execute(
                "INSERT INTO findings(id,title,quotation,path,page_url,target,severity,evidence) VALUES('D','D','q','D.md','https://docs.onetimesecret.dev/en/D','develop','low','source')"
            )
        with patch("core.run", side_effect=AssertionError("Git called")):
            self.action("new_batch", finding_ids=["C", "D"])
        self.assertEqual(
            [b["target"] for b in core.state(self.db)["batches"][1:]],
            ["main", "develop"],
        )

    def test_authorization_syncs_once_and_rejects_unresolved(self):
        with (
            patch("core.sync_prs", return_value=[]) as sync,
            self.assertRaises(ValueError),
        ):
            self.action("authorize", batch_id=1)
            sync.assert_called_once()
        self.record()
        self.record("B")
        self.action(
            "decide",
            batch_id=1,
            entries=[self.entry("A"), self.entry("B")],
            disposition="accept",
        )
        with patch("core.sync_prs", return_value=[]) as sync:
            self.action("authorize", batch_id=1)
            sync.assert_called_once()
        self.assertEqual(len(core.state(self.db)["publication_authorizations"]), 1)

    def test_bad_evidence_rolls_back_entire_report(self):
        report = self.report()
        report["checks"][0]["screenshots"] = [
            {"content": base64.b64encode(b"not an image").decode(), "label": "Fake"}
        ]
        with (
            patch("core.tree_at", return_value={"A.md": "blob-a"}),
            self.assertRaises(ValueError),
        ):
            core.record(self.db, report)
        self.assertEqual(core.state(self.db)["checks"], [])

    def test_prepare_real_worktree_preserves_original_checkout(self):
        seed = self.root / "seed"
        seed.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=seed, check=True)
        (seed / "C.md").write_text("Fixture source")
        subprocess.run(["git", "add", "C.md"], cwd=seed, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "Fixture",
            ],
            cwd=seed,
            check=True,
        )
        origin = self.root / "origin.git"
        subprocess.run(
            ["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True
        )
        repo = self.root / "checkout"
        subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
        before = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        with core.connection(self.db, True) as c:
            c.execute("UPDATE workspace SET repository=?", (str(repo),))
        self.action("new_batch", finding_ids=["C"])
        bid = core.state(self.db)["batches"][-1]["id"]
        hid = self.action("submit", batch_id=bid)
        data = core.state(self.db)
        batch = next(b for b in data["batches"] if b["id"] == bid)
        self.assertTrue(Path(batch["worktree"]).is_dir())
        self.assertEqual(batch["base_sha"], before)
        self.assertEqual(
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True
            ).strip(),
            before,
        )
        self.assertIn(
            "manually", next(h["prompt"] for h in data["handoffs"] if h["id"] == hid)
        )
        self.assertEqual(data["pull_requests"][0]["id"], self.pid)

    def test_sync_rejects_wrong_pr_base(self):
        with patch(
            "core.run",
            return_value=json.dumps(
                {"head": {"sha": "a" * 40}, "base": {"ref": "develop"}}
            ),
        ):
            errors = core.sync_prs(self.db, 1)
        self.assertIn("target", errors[0])
        self.assertTrue(core.state(self.db)["pull_requests"][0]["sync_error"])

    def test_real_git_blob_scope(self):
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "test.md").write_text("One passage")
        (repo / "tést note.md").write_text("Unicode path")
        subprocess.run(["git", "add", "test.md", "tést note.md"], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "Fixture",
            ],
            cwd=repo,
            check=True,
        )
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        tree = core.tree_at({"repository": str(repo)}, sha)
        self.assertEqual(len(tree["test.md"]), 40)
        self.assertEqual(len(tree["tést note.md"]), 40)


if __name__ == "__main__":
    unittest.main()
