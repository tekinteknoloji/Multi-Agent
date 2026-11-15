import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import discussion
import evidence
import providers


class ProjectEvidenceTests(unittest.TestCase):
    def test_report_lookup_renders_only_matching_record_scope(self):
        records = [
            {"id": "expiry/05-check.txt", "run": "expiry", "path": "05-check.txt",
             "observations": "1.800. saniyede oturumun süresi doldu."},
            {"id": "warning/05-check.txt", "run": "warning", "path": "05-check.txt",
             "observations": "Uyarı koşulu 1.620. saniyede açıldı.",
             "outside_scope": "Oturumun sona ermesi ve otomatik kaydetme."},
        ]
        model_reply = {"answer": "Uyarı testi zaman aşımını da doğruladı.",
                       "new_decision": "Bu sonucu yeni karar say.",
                       "report_lookup": True,
                       "test_evidence_ids": ["warning/05-check.txt", "uydurma", "expiry/05-check.txt"]}
        with patch("team.ask", return_value=json.dumps(model_reply)):
            answer, proposal = discussion.opinion("review", {"review": "pilot"}, {
                "request": "Hangi test neyi doğruladı?", "test_evidence": records,
            }, Path("."))
        self.assertEqual(proposal, "")
        self.assertIn("expiry · 05-check.txt\n  Doğrulanan davranış: 1.800.", answer)
        self.assertIn("warning · 05-check.txt\n  Doğrulanan davranış: Uyarı koşulu", answer)
        self.assertIn("Test kapsamı dışında: Oturumun sona ermesi ve otomatik kaydetme.", answer)
        self.assertNotIn("uydurma", answer)
        self.assertNotIn(model_reply["answer"], answer)

    def test_unknown_reference_does_not_create_evidence(self):
        self.assertEqual(evidence.report_answer(["geçersiz"], []),
                         "Bu soruyu doğrulayan bir test raporu seçilemedi.")

    def test_copied_project_keeps_replacement_and_evidence_with_distinct_types(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            project = root / "project"
            project.mkdir()
            board = discussion.Discussions(root / "index")
            topic, _ = board.post(None, "Kullanıcı", "Oturum kapanış uyarısı testi", project=project)
            _, first = board.post(topic, "Mimar", "İlk öneri", agent_id="plan", proposal="Uyarı 5 dakika önce")
            old = board.approve_decision(topic, first)
            _, last = board.post(topic, "Mimar", "Yeni öneri", agent_id="plan", proposal="Uyarı 3 dakika önce")
            new = board.approve_decision(topic, last, old)
            run = "111111111111"
            with patch("team.ROOT", root):
                directory = root / "runs" / run / "round-01"
                directory.mkdir(parents=True)
                output = "test_warning_starts (test_warning.Case.test_warning_starts) ... ok\n"
                (directory / "05-check.txt").write_text(output, encoding="utf-8")
                discussion.write_json(directory / "05-check-evidence.json",
                                      evidence.test_record(output, exit_code=0, base="pilot-base"))
                board.post(topic, "Panel", "Uyarı testi bitti.", run_id=run)
                (directory / "05-check.txt").unlink()
                copied = root / "copied-project"
                shutil.copytree(project, copied)
                context = providers.project_context(copied, "Oturum uyarı testi", structured=True)
            decisions = {item["id"]: item for item in context["decision_records"]}
            self.assertFalse(decisions[old]["active"])
            self.assertEqual(decisions[new]["replaces"], old)
            self.assertEqual(decisions[new]["kind"], "approved_decision")
            report, = context["test_evidence"]
            self.assertEqual(report["base_commit"], "pilot-base")
            self.assertEqual(report["kind"], "test_execution")
            self.assertEqual(report["checks"], [{"name": "test_warning_starts", "result": "ok"}])
