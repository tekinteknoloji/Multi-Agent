import json
import unittest
from pathlib import Path
from unittest.mock import patch

import discussion


REQUEST = ("Önce UI/UX dalı kısa bir akış önersin. Ardından QA & Review dalı "
           "bu akışı doğrulayacak iki kabul testi yazsın.")


class BoardRoutingTests(unittest.TestCase):
    def test_explicit_branch_order_repairs_reversed_and_missing_model_routes(self):
        extras = {
            "qa": {"name": "Denetçi", "handle": "qa", "duty": "Test", "branch": "quality"},
            "ux": {"name": "UX", "handle": "ux", "duty": "Tasarım", "branch": "design"},
        }
        for responders in (["qa", "ux"], ["qa"]):
            with patch("team.ask", return_value=json.dumps({"responders": responders})):
                turns = discussion.route_request("local", {"request": REQUEST}, extras, use_base=False)
            self.assertEqual([key for key, _ in turns], ["ux", "qa"])
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
