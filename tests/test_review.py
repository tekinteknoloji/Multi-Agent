import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import development_worker
import team
import web_app


def reply(decision, findings):
    return json.dumps({"decision": decision, "reason": "Denetim gerekçesi", "findings": findings}, ensure_ascii=False)


class ReviewTests(unittest.TestCase):
    def test_close_finding_requires_matching_successful_evidence_and_survives_restart(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            identifier = "f" * 12
            out = root / "runs" / identifier
            out.mkdir(parents=True)
            source = Path(web_app.prepare_project({"repo": str(root / "project")}))
            base = team.git(source, "rev-parse", "HEAD").strip()
            work = out / "repo"
            shutil.copytree(source, work)
            (work / "group.csv").write_text("13,1,100013\n25,1,100025\n", encoding="utf-8")
            (out / "01-base.txt").write_text(base, encoding="utf-8")
            checks = {"05-integration": [sys.executable, "-c", "print('CSV verified')"]}
            with patch.object(development_worker, "run_development", return_value=0), \
                 patch.object(team, "ask", return_value=reply("CHANGES_REQUESTED", ["group.csv same amount"])):
                _, _, diff = team.review_round(out, work, base, "Task", "pilot", checks, [], "", 1,
                                                lambda *_: None, None)
            with patch.object(team, "ROOT", root), web_app.Dashboard(0) as panel:
                panel.job = {"id": identifier, "stage": "CHANGES_REQUESTED", "running": False,
                             "project": str(source), "check": checks, "timeline": []}
                review = web_app.review_findings(out)
                data = {"id": identifier, "revision": review["revision"], "part": 1,
                        "report": "05-integration", "reason": "100013 < 100025"}
                with self.assertRaisesRegex(ValueError, "değişti"):
                    panel.close_review_finding({**data, "revision": "old"})
                with self.assertRaisesRegex(ValueError, "neden"):
                    panel.close_review_finding({**data, "reason": ""})
                report = out / "round-01" / "05-integration.txt"
                original = report.read_bytes()
                report.write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "eşleşmiyor"):
                    panel.close_review_finding(data)
                report.write_bytes(original)
                self.assertEqual(panel.close_review_finding(data), {"ready": True})
                self.assertEqual(panel.candidate[3], diff)
                with web_app.Dashboard(0) as restarted:
                    restarted.checkpoint = panel.checkpoint
                    restarted.restore_job()
                    self.assertEqual(restarted.job["stage"], "HUMAN_REVIEW")
                    self.assertEqual(restarted.job["finding_resolutions"][0]["explanation"], data["reason"])
                    self.assertEqual(restarted.candidate[3], diff)

    def test_review_resumes_with_only_unfinished_parts_for_same_evidence(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            diff = "x" * (team.REVIEW_CHUNK_SIZE + 1)
            valid = reply("PASS", [])
            with patch.object(team, "ask", side_effect=[valid, '{"decision":', '{"decision":']):
                with self.assertRaisesRegex(RuntimeError, "2. bölüm"):
                    team.finish_review(path, 1, "Review", "pilot", diff, 0, "", None, lambda *_: None, None)
            with patch.object(team, "ask", return_value=valid) as ask:
                status, _, _ = team.finish_review(
                    path, 1, "Review", "pilot", diff, 0, "", None, lambda *_: None, None, resumed=True
                )
                self.assertEqual([call.args[2]["diff_part"] for call in ask.call_args_list], [2])
                self.assertEqual(status, "HUMAN_REVIEW")
            with patch.object(team, "ask", return_value=valid) as ask:
                team.finish_review(
                    path, 1, "Review", "pilot", diff, 0, "new evidence", None, lambda *_: None, None, resumed=True
                )
                self.assertEqual([call.args[2]["diff_part"] for call in ask.call_args_list], [1, 2])

    def test_malformed_adjudication_retries_only_current_part(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            diff = "x" * (team.REVIEW_CHUNK_SIZE + 1)
            valid = reply("PASS", [])
            with patch.object(team, "ask", side_effect=[valid, '{"decision":', valid]) as ask:
                status, _, _ = team.finish_review(
                    path, 1, "Review", "pilot", diff, 0, "", None, lambda *_: None, None
                )
            self.assertEqual(status, "HUMAN_REVIEW")
            self.assertEqual([call.args[2]["diff_part"] for call in ask.call_args_list], [1, 2, 2])
            self.assertIn('{"decision":\n\n', (path / "06-review.raw.txt").read_text(encoding="utf-8"))

    def test_supervisor_plan_shows_dependency_and_combined_test(self):
        plan = {"subtasks": [{"title": "Veri", "outcome": "Şema testi", "depends_on": []},
                             {"title": "Tahsilat", "outcome": "Ödeme testi", "depends_on": [1]}],
                "integration": "CSV → tahsilat → dosya kapanışı"}
        with patch.object(team, "ask", return_value=json.dumps(plan)):
            text = team.supervisor_plan({"role": "supervisor"}, {})
        self.assertIn("2. **Tahsilat** · Bağımlılık: 1", text)
        self.assertIn("CSV → tahsilat → dosya kapanışı", text)
        plan["subtasks"][0]["depends_on"] = [2]
        with patch.object(team, "ask", return_value=json.dumps(plan)):
            with self.assertRaisesRegex(ValueError, "bağımlılık"):
                team.supervisor_plan({"role": "supervisor"}, {})

    def test_integration_failure_blocks_approval_and_records_same_diff(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            work = Path(web_app.prepare_project({"repo": str(root / "project")}))
            base = team.git(work, "rev-parse", "HEAD").strip()
            (work / "test input.txt").write_text("value\n", encoding="utf-8")
            checks = {
                "05-check": [sys.executable, "-c", "print('unit passed')"],
                "05-integration": [sys.executable, "-c", "print('integration failed'); raise SystemExit(3)"],
            }
            checkpoints = []
            with patch.object(development_worker, "run_development", return_value=0), \
                 patch.object(team, "ask", return_value=reply("PASS", [])):
                status, feedback, diff = team.review_round(
                    root, work, base, "Task", "pilot", checks, [], "", 1, lambda *_: None, None,
                    on_review_ready=checkpoints.append,
                )
            self.assertEqual(status, "CHANGES_REQUESTED")
            self.assertEqual(feedback["check_exit"], 3)
            folder = root / "round-01"
            coverage = json.loads((folder / "06-file-review.json").read_text(encoding="utf-8"))
            self.assertEqual(coverage["files"][0]["path"], "test input.txt")
            self.assertEqual(coverage["decisions"], [{"part": 1, "decision": "PASS"}])
            for phase, code in [("05-check", 0), ("05-integration", 3)]:
                receipt = json.loads((folder / f"{phase}-evidence.json").read_text(encoding="utf-8"))
                self.assertEqual(receipt["diff_sha256"], coverage["diff_sha256"])
                self.assertEqual(receipt["exit_code"], code)
            saved = {**checkpoints[-1], "number": 1, "base": base, "check": checks, "task": "Task"}
            with patch.object(team, "ask", side_effect=AssertionError("Denetim yinelendi")):
                self.assertEqual(team.resume_review(root, work, saved, "pilot", None, None, None),
                                 (status, feedback, diff))

    def test_repair_after_test_failure_sends_test_evidence_without_pass_review(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = Path(web_app.prepare_project({"repo": str(root / "project")}))
            base = team.git(repo, "rev-parse", "HEAD").strip()
            out = root / "run"
            out.mkdir()
            feedback = {
                "status": "CHANGES_REQUESTED", "check_exit": 1, "check_output": "Arithmetic incorrect",
                "review": {"decision": "PASS", "reason": "Olumlu yorum", "findings": []},
            }
            with patch.object(team, "review_round", side_effect=[
                ("CHANGES_REQUESTED", feedback, "diff"), ("HUMAN_REVIEW", {}, "fixed diff"),
            ]) as rounds:
                result = team.execute_plan(repo, base, "main", "Task", "Plan", "pilot", [],
                                           out, lambda *_: None, None, None)
            self.assertEqual(result, 0)
            repair_prompt = rounds.call_args.args[7]
            self.assertIn("Arithmetic incorrect", repair_prompt)
            self.assertNotIn("Olumlu yorum", repair_prompt)

    def test_large_diff_runs_test_and_all_review_parts_without_requesting_repairs_for_pass(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            work = Path(web_app.prepare_project({"repo": str(root / "project")}))
            base = team.git(work, "rev-parse", "HEAD").strip()
            (work / "a.txt").write_text("x" * 28_000 + "\n", encoding="utf-8")
            (work / "b.txt").write_text("last line\n" * 2000, encoding="utf-8")
            positive = "Tahsilat doğru hesaplanıyor."
            check = [sys.executable, "-c", "print('check passed')"]
            with patch.object(development_worker, "run_development", return_value=0), \
                 patch.object(team, "ask", return_value=reply("PASS", [positive])) as ask:
                status, feedback, diff = team.review_round(
                    root, work, base, "Review the full diff", "pilot", check, [], "", 1, lambda *_: None, None
                )
            self.assertEqual(status, "HUMAN_REVIEW")
            self.assertEqual(feedback["check_exit"], 0)
            self.assertEqual(feedback["review"]["findings"], [])
            sections = [call.args[2] for call in ask.call_args_list]
            self.assertGreater(len(sections), 1)
            self.assertGreater(len(diff), 30_000)
            self.assertEqual("".join(section["diff"] for section in sections), diff)
            self.assertTrue(all(len(section["diff"]) <= team.REVIEW_CHUNK_SIZE for section in sections))
            self.assertEqual([section["diff_part"] for section in sections], list(range(1, len(sections) + 1)))
            self.assertTrue(all(section["diff_part_count"] == len(sections) for section in sections))
            self.assertIn(positive, (root / "round-01" / "06-review.raw.txt").read_text(encoding="utf-8"))

    def test_only_blocking_review_findings_go_to_repair(self):
        with TemporaryDirectory() as directory:
            diff = "x" * (team.REVIEW_CHUNK_SIZE + 1)
            with patch.object(team, "ask", side_effect=[
                reply("PASS", ["Olumlu gözlem"]),
                reply("CHANGES_REQUESTED", ["app.py: Geçersiz ödemeyi reddet."]),
            ]):
                status, feedback, _ = team.finish_review(
                    Path(directory), 1, "Review", "pilot", diff, 0, "", None, lambda *_: None, None
                )
            self.assertEqual(status, "CHANGES_REQUESTED")
            self.assertEqual(feedback["review"]["decision"], "CHANGES_REQUESTED")
            self.assertEqual(feedback["review"]["findings"], ["app.py: Geçersiz ödemeyi reddet."])

    def test_pass_never_overrides_a_failed_or_missing_test(self):
        with TemporaryDirectory() as directory, patch.object(
            team, "ask", return_value=reply("PASS", ["Olumlu gözlem"])
        ):
            for code, expected in [(1, "CHANGES_REQUESTED"), (None, "CHECK_REQUIRED")]:
                with self.subTest(code=code):
                    status, feedback, _ = team.finish_review(
                        Path(directory), 1, "Review", "pilot", "diff", code, "", None, lambda *_: None, None
                    )
                    self.assertEqual(status, expected)
                    self.assertEqual(feedback["review"]["findings"], [])
