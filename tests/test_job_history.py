import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import discussion
import evidence
import job_history
import providers


class JobHistoryTests(unittest.TestCase):
    def test_criteria_report_binding_distinguishes_pass_fail_and_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / "project"
            project.mkdir()
            board = discussion.Discussions(root / "topics")
            run = "aaaaaaaaaaaa"
            topic, _ = board.post(None, "Kullanıcı", "Uyarı", run_id=run, project=project)
            output = ("test_threshold (suite.Warning.test_threshold) ... ok\n"
                      "test_visual (suite.Warning.test_visual) ... FAIL\n"
                      "test_save (suite.Warning.test_save) ... skipped 'manual'\n")
            with patch("team.ROOT", root):
                folder = root / "runs" / run / "round-01"
                report = "round-01/05-check.txt"
                discussion.atomic_text(folder / "05-check.txt", output)
                discussion.write_json(folder / "05-check-evidence.json", evidence.test_record(output, exit_code=1))
                board.post(topic, "Panel", "Test tamamlandı.", run_id=run)
                for title, check in [("Eşik", "test_threshold"), ("Görsel", "test_visual"),
                                     ("Kaydetme", "test_save")]:
                    job_history.save_criterion(project, run, {"title": title, "report": report, "check": check})
                detail = job_history.task_detail(project, run)
                self.assertEqual([item["status"] for item in detail["criteria"]],
                                 ["Doğrulandı", "Başarısız", "Kanıt yok"])
                self.assertTrue(all(item["report_available"] for item in detail["criteria"]))
                with self.assertRaises(ValueError):
                    job_history.save_criterion(project, run, {"title": "Uydurma", "report": report, "check": "test_fake"})
                archive = job_history.task_detail(project, run, report)
                discussion.atomic_text(archive, "yeni çıktı")
                statuses = [item["status"] for item in job_history.task_detail(project, run)["criteria"]]
                self.assertEqual(statuses, ["Kanıt yok"] * 3)

                followup = "cccccccccccc"
                folder = root / "runs" / followup / "round-01"
                discussion.atomic_text(folder / "05-check.txt", "test_visual (suite.Warning.test_visual) ... ok\n")
                board.post(topic, "Panel", "Takip testi tamamlandı.", run_id=followup)
                old_criterion = detail["criteria"][1]
                updated = job_history.save_criterion(project, run, {"id": old_criterion["id"],
                    "title": old_criterion["title"], "report_run": followup, "report": report, "check": "test_visual"})
                self.assertEqual(updated["criteria"][1]["status"], "Doğrulandı")
                self.assertEqual(updated["criteria"][1]["report_run"], followup)
                self.assertTrue(job_history.task_detail(project, followup, report).is_file())
                copied = root / "copied-with-criteria"
                shutil.copytree(project, copied)
                context = providers.project_context(copied, "Uyarı kabul kriterleri", structured=True)
                matching = {item["title"]: item for item in context["criterion_records"]}
                self.assertEqual(matching["Görsel"]["task_run"], run)
                self.assertEqual(matching["Görsel"]["report_run"], followup)
                self.assertEqual(matching["Görsel"]["status"], "Doğrulandı")
                self.assertEqual(matching["Eşik"]["status"], "Doğrulandı")
                self.assertEqual(matching["Kaydetme"]["status"], "Kanıt yok")
                self.assertEqual(providers.project_context(copied, "Fatura dipnotu", structured=True)["criterion_records"], [])
                metadata = job_history.task_detail(copied, run, "round-01/05-check-evidence.json")
                discussion.atomic_text(metadata, "{broken")
                after_damage = job_history.task_detail(copied, run)["criteria"]
                self.assertEqual([item["status"] for item in after_damage],
                                 ["Kanıt yok", "Doğrulandı", "Kanıt yok"])

    def test_copied_project_exposes_run_reports_and_its_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / "project"
            project.mkdir()
            board = discussion.Discussions(root / "topics")
            run = "aaaaaaaaaaaa"
            second = "bbbbbbbbbbbb"
            topic, _ = board.post(None, "Kullanıcı", "Çarpma ekle", run_id=run, project=project)
            board.post(topic, "Kullanıcı", "Başka bir görev", run_id=second)
            with patch("team.ROOT", root):
                out = root / "runs" / run
                folder = out / "round-01"
                discussion.atomic_text(out / "01-task.md", "Çarpma ekle")
                discussion.write_json(out / "00-team.json", [{"name": "Codex", "slot": "code"}])
                discussion.atomic_text(folder / "04-change.diff", "+def carp(a, b): return a * b")
                discussion.atomic_text(folder / "05-check.txt", "4 kontrol geçti")
                discussion.atomic_text(folder / "06-review.json", '{"review":{"decision":"PASS"}}')
                for message in job_history.APPLICATION_MESSAGES:
                    board.post(topic, "Panel", message, run_id=run)
                copied = root / "copied"
                shutil.copytree(project, copied)

                entries = job_history.task_history(copied)
                self.assertEqual({entry["id"] for entry in entries}, {run, second})
                detail = job_history.task_detail(copied, run)
                self.assertEqual(detail["task"], "Çarpma ekle")
                self.assertEqual(detail["team"][0]["name"], "Codex")
                self.assertIn("def carp", detail["rounds"][0]["diff"])
                self.assertEqual(detail["rounds"][0]["test"], "4 kontrol geçti")
                self.assertIn("PASS", detail["rounds"][0]["review"])
                self.assertEqual([event["text"] for event in detail["operations"]],
                                 list(job_history.APPLICATION_MESSAGES))
                self.assertTrue(job_history.task_detail(copied, run, "round-01/05-check.txt").is_file())
                self.assertEqual(job_history.task_detail(copied, second)["operations"], [])
                with self.assertRaises(ValueError):
                    job_history.task_detail(copied, run, "../01-task.md")
