import json
import tempfile
import unittest
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import discussion
import development_worker
import evidence
import team
import test_worker
import web_app


class JobRecoveryTests(unittest.TestCase):
    def test_native_picker_returns_selected_folder_or_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            selected = root / "Yeni Proje"
            selected.mkdir()
            with patch.object(team, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(str(selected)))) as run:
                self.assertEqual(web_app.pick_project_folder({"repo": str(root), "create": True}), str(selected))
                self.assertIn("Yeni Klasör", run.call_args.args[0][-1])
            with patch.object(team, "run", return_value=subprocess.CompletedProcess([], 0, '""')):
                self.assertIsNone(web_app.pick_project_folder({"repo": str(root)}))
            repo = Path(web_app.prepare_project({"repo": str(selected)}))
            self.assertEqual(team.git(repo, "log", "-1", "--format=%s").strip(), "Initial commit")

    def test_reapply_recovery_preserves_event_and_does_not_repeat_git_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = "eeeeeeeeeeee"
            (root / "runs" / run).mkdir(parents=True)
            repo = Path(web_app.prepare_project({"repo": str(root / "project")}))
            base = team.git(repo, "rev-parse", "HEAD").strip()
            branch = team.git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
            diff = ("diff --git a/out.txt b/out.txt\n"
                    "new file mode 100644\n--- /dev/null\n+++ b/out.txt\n@@ -0,0 +1 @@\n+once\n")
            with patch.object(team, "ROOT", root):
                pending = {**web_app.application_record(repo, base, branch, diff, run),
                           "reapply": True, "reapply_event": "pilot-redo"}
                web_app.save_application(pending)
                self.assertEqual(web_app.load_application()["state"], "reverted")
                web_app.save_application(pending)
                web_app.git_bytes(repo, "apply", "-", data=diff.encode())
                with patch.object(web_app, "git_bytes", wraps=web_app.git_bytes) as git:
                    recovered = web_app.load_application()
                    self.assertFalse(any(call.args[1:] == ("apply", "-") for call in git.call_args_list))
                self.assertEqual(recovered["state"], "applied")
                self.assertEqual(recovered["reapply_event"], "pilot-redo")
                self.assertEqual((repo / "out.txt").read_text().splitlines(), ["once"])

    def test_revert_recovers_on_both_sides_of_git_apply(self):
        for reversed_already in (False, True):
            with self.subTest(reversed_already=reversed_already), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                run = "dddddddddddd"
                (root / "runs" / run).mkdir(parents=True)
                repo = Path(web_app.prepare_project({"repo": str(root / "project")}))
                base = team.git(repo, "rev-parse", "HEAD").strip()
                branch = team.git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
                diff = ("diff --git a/out.txt b/out.txt\n"
                        "new file mode 100644\n--- /dev/null\n+++ b/out.txt\n@@ -0,0 +1 @@\n+once\n")
                with patch.object(team, "ROOT", root):
                    receipt = web_app.apply_reviewed_change(repo, base, branch, diff, job_id=run)
                    web_app.save_application({**receipt, "state": "reverting", "revert_event": "pilot-event"})
                    if reversed_already:
                        web_app.git_bytes(repo, "apply", "--reverse", "-", data=diff.encode())
                    with patch.object(web_app, "git_bytes", wraps=web_app.git_bytes) as git:
                        recovered = web_app.load_application()
                        reversals = [call for call in git.call_args_list
                                     if call.args[1:] == ("apply", "--reverse", "-")]
                    self.assertEqual(len(reversals), 0 if reversed_already else 1)
                    self.assertEqual(recovered["state"], "reverted")
                    self.assertEqual(recovered["revert_event"], "pilot-event")
                    self.assertFalse((repo / "out.txt").exists())
                    self.assertEqual(web_app.load_application(), recovered)

    def test_reapply_reuses_patch_once_and_protects_new_source_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = "cccccccccccc"
            (root / "runs" / run).mkdir(parents=True)
            repo = Path(web_app.prepare_project({"repo": str(root / "project")}))
            base = team.git(repo, "rev-parse", "HEAD").strip()
            branch = team.git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
            diff = ("diff --git a/out.txt b/out.txt\n"
                    "new file mode 100644\n--- /dev/null\n+++ b/out.txt\n@@ -0,0 +1 @@\n+once\n")
            with patch.object(team, "ROOT", root), web_app.Dashboard(0) as panel:
                panel.application = web_app.apply_reviewed_change(repo, base, branch, diff, job_id=run)
                panel.revert_application(run)
                pending = repo / "pending.txt"
                pending.write_text("keep", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "bekleyen değişiklikler"):
                    panel.reapply_application(run)
                self.assertEqual(pending.read_text(), "keep")
                pending.unlink()
                panel.reapply_application(run)
                panel.reapply_application(run)
                self.assertEqual((repo / "out.txt").read_text().splitlines(), ["once"])
                panel.revert_application(run)
                self.assertFalse((repo / "out.txt").exists())

    def test_development_reuses_worktree_and_completed_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = Path(web_app.prepare_project({"repo": str(root / "project")}))
            base = team.git(repo, "rev-parse", "HEAD").strip()
            folder = root / "round-01"
            folder.mkdir()
            (repo / "partial.txt").write_text("keep", encoding="utf-8")
            (folder / "03-development-started").touch()
            discussion.write_json(folder / "03-development-request.json",
                                  {"base": base, "prompt": "Bitir.", "developer": {"provider": "ollama"}})

            def finish(agent, work, prompt, emit):
                self.assertEqual((work / "partial.txt").read_text(), "keep")
                self.assertIn("Önceki geliştirme işlemi yarıda kaldı", prompt)
                emit("append", "İş tamamlandı.")
                return subprocess.CompletedProcess([], 0, "ok", "")

            with patch("providers.develop", side_effect=finish) as develop:
                development_worker.worker(folder, repo)
                development_worker.worker(folder, repo)
                develop.assert_called_once()
            messages = []
            self.assertEqual(development_worker.run_development(folder, repo, lambda _, text: messages.append(text)), 0)
            self.assertEqual(messages, ["İş tamamlandı."])

    def test_concurrent_test_reattachment_executes_command_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = Path(web_app.prepare_project({"repo": str(root / "project")}))
            base = team.git(repo, "rev-parse", "HEAD").strip()
            folder = root / "round-01"
            folder.mkdir()
            discussion.atomic_text(folder / "04-change.diff", "")
            marker = root / "attempts.txt"
            script = ("from pathlib import Path; import time; "
                      f"p = Path({str(marker)!r}); "
                      "f = p.open('a'); f.write('test\\n'); f.close(); time.sleep(.5); print('ok')")
            command = [sys.executable, "-c", script]
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(test_worker.run_test, folder, repo, base, "", command)
                second = pool.submit(test_worker.run_test, folder, repo, base, "", command)
                self.assertEqual(first.result(), (0, "ok\n\n"))
                self.assertEqual(second.result(), (0, "ok\n\n"))
            self.assertEqual(marker.read_text().splitlines(), ["test"])

    def test_review_reuses_completed_result_and_rejects_changed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = Path(web_app.prepare_project({"repo": str(root / "project")}))
            base = team.git(repo, "rev-parse", "HEAD").strip()
            (repo / "out.txt").write_text("once\n", encoding="utf-8")
            team.git(repo, "add", "-N", "--", ".")
            diff = team.git(repo, "diff", "--no-ext-diff", "--no-textconv", "--binary", base, "--")
            folder = root / "round-01"
            folder.mkdir()
            discussion.atomic_text(folder / "04-change.diff", diff)
            discussion.atomic_text(folder / "05-check.txt", "test ok\n")
            feedback = {"status": "HUMAN_REVIEW", "review": {"decision": "PASS"}}
            discussion.write_json(folder / "06-review.json", feedback)
            saved = {"number": 1, "base": base, "diff_hash": evidence.digest(diff),
                     "check_exit": 0, "test_hash": evidence.digest("test ok\n")}
            with patch.object(team, "ask", side_effect=AssertionError("Denetçi yeniden çağrıldı.")):
                self.assertEqual(team.resume_review(root, repo, saved, "pilot", None, None, None),
                                 ("HUMAN_REVIEW", feedback, diff))
                discussion.atomic_text(folder / "05-check.txt", "different\n")
                with self.assertRaisesRegex(ValueError, "test çıktısı"):
                    team.resume_review(root, repo, saved, "pilot", None, None, None)

    def test_incomplete_application_is_reconciled_without_reapplying(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = "aaaaaaaaaaaa"
            (root / "runs" / run).mkdir(parents=True)
            repo = Path(web_app.prepare_project({"repo": str(root / "project")}))
            base = team.git(repo, "rev-parse", "HEAD").strip()
            branch = team.git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
            diff = ("diff --git a/out.txt b/out.txt\n"
                    "new file mode 100644\n--- /dev/null\n+++ b/out.txt\n@@ -0,0 +1 @@\n+once\n")
            with patch.object(team, "ROOT", root):
                record = web_app.application_record(repo, base, branch, diff, run)
                web_app.save_application(record)
                self.assertIsNone(web_app.recover_application(run))
                web_app.save_application(record)
                web_app.git_bytes(repo, "apply", "-", data=diff.encode())
                saved = web_app.recover_application(run)
                self.assertEqual(saved["state"], "applied")
                self.assertEqual(web_app.recover_application(run), saved)
                self.assertEqual((repo / "out.txt").read_text().splitlines(), ["once"])

    def test_pending_approval_survives_new_dashboard_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(team, "ROOT", root), web_app.Dashboard(0) as first, web_app.Dashboard(0) as second:
                first.job = {"id": "bbbbbbbbbbbb", "stage": "HUMAN_REVIEW", "running": False}
                first.candidate = (root, "base", "main", "approved diff")
                first.save_job()
                second.checkpoint = first.checkpoint
                second.restore_job()
                self.assertEqual(second.job, first.job)
                self.assertEqual(second.candidate, first.candidate)
