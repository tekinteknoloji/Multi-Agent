import json
import unittest
from pathlib import Path
from unittest.mock import patch

import discussion


REQUEST = ("Önce UI/UX dalı kısa bir akış önersin. Ardından QA & Review dalı "
           "bu akışı doğrulayacak iki kabul testi yazsın.")


class BoardRoutingTests(unittest.TestCase):
    def test_current_decision_and_criterion_answer_keeps_both_active_decisions(self):
        decisions = [
            {"id": "warning", "topic": "one", "time": "2026-11-15", "active": True,
             "text": "Oturum kapanmadan 3 dakika önce uyarı gösterilir."},
            {"id": "expiry", "topic": "one", "time": "2026-11-15", "active": True,
             "text": "Oturum 30 dakikada zaman aşımına uğrar."},
            {"id": "old", "topic": "one", "time": "2026-11-15", "active": False,
             "text": "Oturum kapanmadan 5 dakika önce uyarı gösterilir."},
        ]
        context = {"request": "Güncel oturum kapanış uyarısı kararını ve kabul kriterlerini yaz.",
                   "decision_records": decisions,
                   "criterion_records": [{"title": "Uyarı görünür", "status": "Doğrulandı",
                                          "report_available": True, "report_run": "warning-run",
                                          "report": "round-01/05-check.txt", "check": "test_warning"}]}
        payload = {"answer": "Tek geçerli karar 3 dakikalık uyarıdır.", "new_decision": ""}
        with patch("team.ask", return_value=json.dumps(payload)):
            answer, proposal = discussion.opinion("review", {"review": "local"}, context, Path("."))
        self.assertIn("3 dakika önce", answer)
        self.assertIn("30 dakikada", answer)
        self.assertIn("warning-run/round-01/05-check.txt", answer)
        self.assertNotIn("5 dakika", answer)
        self.assertNotIn("Tek geçerli", answer)
        self.assertEqual(proposal, "")

    def test_exclusive_branch_removes_unrequested_agents_from_router_choices(self):
        roster = {
            "qa": {"name": "Denetçi", "handle": "qa", "duty": "Test", "branch": "quality"},
            "ux": {"name": "UX", "handle": "ux", "duty": "Tasarım", "branch": "design"},
        }
        context = {"request": "QA & Review dalı test raporlarını incelesin. Yalnız bu daldan görüş istiyorum."}
        with patch("team.ask", return_value='{"responders":["qa"]}') as ask:
            self.assertEqual(discussion.route_request("local", context, roster, use_base=False), [("qa", "")])
        self.assertEqual(list(ask.call_args.args[2]["team"]), ["qa"])

    def test_source_trace_only_lists_records_in_supplied_context(self):
        message = {"id": "first", "author": "Kullanıcı", "text": "İstek"}
        decision = {"id": "approved", "topic": "old", "text": "Uyarı 3 dakika", "time": "2026-11-15",
                    "active": True}
        project = {"decisions": [decision], "reports": [{"run": "run", "path": "round-01/05-check.txt"}]}
        context = {"project_memory": "Onaylı karar ve rapor", "decision_records": [decision],
                   "test_evidence": project["reports"], "idea": message}
        trace = discussion.board_context_sources(context, "topic", message, project)
        self.assertEqual(trace["decisions"], [decision])
        self.assertEqual(trace["reports"], project["reports"])
        self.assertEqual(len(trace["messages"]), 1)
        synthesis = discussion.board_context_sources({"team_responses": [message]}, "topic", message, project)
        self.assertEqual(synthesis["decisions"], [])
        self.assertEqual(synthesis["reports"], [])

    def test_explicit_branch_order_repairs_reversed_and_missing_model_routes(self):
        extras = {
            "qa": {"name": "Denetçi", "handle": "qa", "duty": "Test", "branch": "quality"},
            "ux": {"name": "UX", "handle": "ux", "duty": "Tasarım", "branch": "design"},
        }
        for responders in (["qa", "ux"], ["qa"]):
            selected_by_model = set()
            with patch("team.ask", return_value=json.dumps({"responders": responders})):
                turns = discussion.route_request("local", {"request": REQUEST}, extras, use_base=False,
                                                 selected_by_model=selected_by_model)
            self.assertEqual([key for key, _ in turns], ["ux", "qa"])
            self.assertEqual(selected_by_model, set(responders))
            self.assertIn("kabul testi yazsın", turns[1][1])

    def test_new_tests_and_synthesis_use_normal_answers_even_with_reports(self):
        payload = {"answer": "Tasarım ve kabul testi özeti", "new_decision": "",
                   "report_lookup": True, "test_evidence_ids": ["stale"]}
        context = {"request": REQUEST, "test_evidence": [{"id": "stale"}]}
        with patch("team.ask", return_value=json.dumps(payload)) as ask:
            answer, _ = discussion.opinion("review", {"review": "local"}, context, Path("."))
            self.assertEqual(answer, payload["answer"])
            self.assertIs(ask.call_args.args[3], discussion.OPINION_SCHEMA)
            context["request"] = "Test raporlarını bul"
            answer, _ = discussion.opinion("plan", {"plan": "local"}, context, Path("."), synthesis=True)
            self.assertEqual(answer, payload["answer"])
            self.assertIs(ask.call_args.args[3], discussion.OPINION_SCHEMA)
