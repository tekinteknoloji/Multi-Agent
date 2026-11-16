import json
import io
from urllib.error import HTTPError
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import discussion
import providers
import team
import web_app


REQUEST = ("Önce UI/UX dalı kısa bir akış önersin. Ardından QA & Review dalı "
           "bu akışı doğrulayacak iki kabul testi yazsın.")


class BoardRoutingTests(unittest.TestCase):
    def test_task_draft_reads_selected_topic_without_starting_job(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project, other = root / "project", root / "other"
            project.mkdir()
            other.mkdir()
            members = {
                "planner": {"name": "Mimar", "slot": "plan", "enabled": True},
                "supervisor": {"name": "Supervisor", "role": "supervisor", "enabled": True},
            }
            with patch.object(team, "ROOT", root), web_app.Dashboard(0, discussion_root=root / "index") as panel:
                topic, _ = panel.discussions.post(None, "Kullanıcı", "Oturum 30 dakikada kapansın.", project=project)
                panel.discussions.post(topic, "Mimar", "Uyarıyı üç dakika kala gösterelim.")
                handler = object.__new__(web_app.Handler)
                handler.server = panel
                payload = {"action": "draft_task", "id": topic, "repo": str(project)}
                with self.assertRaisesRegex(ValueError, "bağlı olduğu projeyi"):
                    handler.board_action({**payload, "repo": str(other)})
                with patch.object(providers, "effective_team", return_value=members), \
                     patch.object(providers, "ask", return_value="Düzenlenebilir görev taslağı") as ask:
                    result = handler.board_action(payload)
                self.assertEqual(result, {"id": topic, "draft": "Düzenlenebilir görev taslağı"})
                author, _, context = ask.call_args.args
                self.assertEqual(author, members["supervisor"])
                self.assertIn("30 dakika", context["conversation"]["request"])
                self.assertIn("üç dakika", context["conversation"]["messages"][0]["text"])
                self.assertIn("decision_records", context["project_records"])
                self.assertEqual(len(panel.discussions.read(topic)["messages"]), 2)
                self.assertIsNone(panel.job)
                self.assertIsNone(panel.board_turn)

    def test_local_api_requests_structured_output_and_falls_back_if_unsupported(self):
        agent = {"provider": "openai", "endpoint": "http://127.0.0.1:1235/v1", "model": "test", "key_env": ""}
        schema = discussion.OPINION_SCHEMA
        reply = json.dumps({"answer": "Yanıt", "new_decision": ""})
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": reply}}]}).encode())
        unsupported = HTTPError(agent["endpoint"], 400, "Unsupported format", None, io.BytesIO())
        with patch.object(providers, "local_endpoints", return_value=(None, [providers.endpoint_key(agent)])), \
             patch.object(providers, "build_opener") as opener:
            opener.return_value.open.side_effect = [unsupported, response]
            self.assertEqual(providers.api_answer(agent, "instruction", "prompt", schema), reply)
            first, second = [json.loads(call.args[0].data) for call in opener.return_value.open.call_args_list]
            self.assertEqual(first["response_format"]["json_schema"]["schema"], schema)
            self.assertNotIn("response_format", second)

    def test_board_delivery_records_queue_wait_reply_and_failure(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            extras = {name: {"id": name, "name": name, "handle": name, "model": "test",
                             "duty": "test", "enabled": True} for name in ("mimar", "denetci")}
            with patch.object(team, "ROOT", root), web_app.Dashboard(0, discussion_root=root / "index") as panel:
                board = panel.discussions
                topic_id, message_id = board.post(None, "Kullanıcı", "@Herkes Test", extras=extras, use_base=False)
                topic = board.read(topic_id)
                request = discussion.request_message(topic, message_id)

                def respond(role, models, context, *_args, on_context, **_kwargs):
                    current = board.read(topic_id)["messages"][0]["delivery"]
                    expected = ["preparing", "queued"] if role == "mimar" else ["answered", "preparing"]
                    self.assertEqual([item["state"] for item in current["agents"]], expected)
                    on_context(context)
                    state = board.read(topic_id)["messages"][0]["delivery"]["agents"]
                    self.assertEqual(next(item["state"] for item in state if item["id"] == role), "responding")
                    if role == "denetci":
                        raise RuntimeError("Test API hatası")
                    return "Mimar yanıtı", ""

                with patch.object(discussion, "opinion", side_effect=respond):
                    panel.board_work(topic, {}, "directed", request, extras, False)
                stored = board.read(topic_id)
                receipt = stored["messages"][0]["delivery"]
                self.assertEqual(receipt["state"], "finished")
                self.assertEqual(receipt["agents"][0]["response"], stored["messages"][1]["id"])
                self.assertEqual(receipt["agents"][1]["reason"], "Test API hatası")

    def test_topic_titles_are_read_from_selected_project_and_removed_from_legacy_index(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "e", root / "Yeni klasör"
            first.mkdir()
            second.mkdir()
            index = root / "index"
            board = discussion.Discussions(index)
            first_id, _ = board.post(None, "Kullanıcı", "e test", project=first)
            board.post(None, "Kullanıcı", "Su analiz", project=second)
            board.post(None, "Kullanıcı", "Projeye bağlanmamış eski konu")
            pointer = index / f"{first_id}.json"
            discussion.write_json(pointer, {"id": first_id, "project": str(first), "title": "Eski başlık"})
            board = discussion.Discussions(index)
            self.assertEqual(board.list(), [])
            self.assertEqual([item["title"] for item in board.list(first)], ["e test"])
            self.assertEqual([item["title"] for item in board.list(second)], ["Su analiz"])
            self.assertNotIn("title", json.loads(pointer.read_text(encoding="utf-8")))
            self.assertEqual(board.read(first_id)["title"], "e test")

    def test_project_post_rejects_unbound_and_other_project_topics(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first", root / "second"
            first.mkdir()
            second.mkdir()
            board = discussion.Discussions(root / "index")
            unbound, _ = board.post(None, "Kullanıcı", "Unbound")
            bound, _ = board.post(None, "Kullanıcı", "First project", project=first)
            for topic in (unbound, bound):
                with self.assertRaisesRegex(ValueError, "seçili projeye bağlı değil"):
                    board.post(topic, "Kullanıcı", "Wrong project", project=second)
                self.assertEqual(len(board.read(topic)["messages"]), 1)
            board.post(bound, "Kullanıcı", "Correct project", project=first)
            self.assertEqual(len(board.read(bound)["messages"]), 2)

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
