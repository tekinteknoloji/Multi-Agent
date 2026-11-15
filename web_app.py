"""Local browser interface for the three-agent workflow."""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request

import team
import discussion
import providers
import agent_roles
from worker_process import round_lock

REVERTED_MESSAGE = "Panelin uyguladığı değişiklik geri alındı."
REAPPLIED_MESSAGE = "Geri alınan değişiklik kaynak projeye yeniden uygulandı."


ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.css": ("app.css", "text/css"),
    "/app.js": ("app.js", "text/javascript"),
    "/board.js": ("board.js", "text/javascript"),
}
ARTIFACTS = {
    "plan": "02-plan.md",
    "code": "03-codex.txt",
    "diff": "04-change.diff",
    "test": "05-check.txt",
    "observers": "07-observers.json",
    "review": "06-review.json",
}
MAX_BODY = 96_000
MAX_PREVIEW = 96_000
LIVE_LIMIT = 12_000
AGENTS = {"plan": "Mimar", "code": "Codex", "review": "Denetçi"}
ACTIVITY = {
    "starting": (("Bekliyor", "Git deposu kontrol ediliyor."), ("Bekliyor", "Mimarın planı bekleniyor."), ("Bekliyor", "Kod değişikliği bekleniyor.")),
    "board_ready": (("Tamamlandı", "Kullanıcının pano taslağı kullanılıyor."), ("Bekliyor", "Git kopyası hazırlanıyor."), ("Bekliyor", "Git farkı ve test sonucu bekleniyor.")),
    "plan": (("Çalışıyor", "Görevi ve proje bilgilerini inceleyip plan hazırlıyor."), ("Bekliyor", "Mimarın planı bekleniyor."), ("Bekliyor", "Kod değişikliği bekleniyor.")),
    "code": (("Tamamlandı", "Planı Codex’e verdi."), ("Çalışıyor", "Planı izole Git kopyasında uyguluyor."), ("Bekliyor", "Git farkı ve test sonucu bekleniyor.")),
    "repair": (("Tamamlandı", "Uygulama planı hazır."), ("Çalışıyor", "Önceki denetim ve test bulgularını ele alıyor."), ("Bekliyor", "Düzeltilen Git farkı ve yeni test sonucu bekleniyor.")),
    "test": (("Tamamlandı", "Uygulama planı hazır."), ("Bekliyor", "Panelin çalıştırdığı testin sonucu bekleniyor."), ("Bekliyor", "Panelin çalıştırdığı testin sonucu bekleniyor.")),
    "observe": (("Tamamlandı", "Uygulama planı hazır."), ("Bekliyor", "Ek ajan gözlemleri bekleniyor."), ("Bekliyor", "Ek ajan gözlemleri bekleniyor.")),
    "review": (("Tamamlandı", "Uygulama planı hazır."), ("Bekliyor", "Yerel denetçinin kararı bekleniyor."), ("Çalışıyor", "Git farkını görev ve test kanıtıyla karşılaştırıyor.")),
    "plan_ready": (("Tamamlandı", "Uygulama planı kullanıcı düzenlemesine hazır."), ("Bekliyor", "Kullanıcının planı göndermesi bekleniyor."), ("Bekliyor", "Kullanıcı isterse planı denetleyecek.")),
    "plan_review": (("Bekliyor", "Denetçinin plan bulguları bekleniyor."), ("Bekliyor", "Kullanıcının son planı göndermesi bekleniyor."), ("Çalışıyor", "Uygulama planını denetliyor.")),
    "plan_revision": (("Çalışıyor", "Denetim bulgularına göre planı gözden geçiriyor."), ("Bekliyor", "Kullanıcının son planı göndermesi bekleniyor."), ("Tamamlandı", "Plan bulgularını mimara iletti.")),
}


def ollama_json(path, payload=None):
    request = Request(team.OLLAMA_URL + path)
    if payload is not None:
        request = Request(team.OLLAMA_URL + path, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"})
    with team.OLLAMA.open(request, timeout=5) as response:
        return json.load(response)


def installed_model_names():
    models = ollama_json("/api/tags")["models"]
    return sorted({
        model["name"] for model in models
        if model.get("name") and "cloud" not in model["name"].lower()
        and not model.get("remote_model") and not model.get("remote_host")
    }, key=str.casefold)


def supports_text(name):
    return "completion" in ollama_json("/api/show", {"model": name}).get("capabilities", [])


def available_models():
    names = installed_model_names()
    with ThreadPoolExecutor(max_workers=4) as pool:
        return [name for name, supports in zip(names, pool.map(supports_text, names)) if supports]


def check_model_selection(planner, reviewer):
    names = installed_model_names()
    for name in dict.fromkeys((planner, reviewer)):
        if name not in names or not supports_text(name):
            raise ValueError(f"{name} kullanılabilir bir yerel metin modeli değil. Model listesini yenile.")


def artifact_paths(out):
    paths = {"plan": out / ARTIFACTS["plan"]}
    for number in range(1, team.MAX_CORRECTIONS + 2):
        folder = out / f"round-{number:02}"
        for key, name in ARTIFACTS.items():
            if key != "plan":
                paths[f"{key}-{number}"] = folder / name
    return paths


class Dashboard(ThreadingHTTPServer):
    allow_reuse_address = False

    def __init__(self, port, *, discussion_root=None):
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.lock = threading.Lock()
        self.job = None
        self.candidate = None
        self.plan_context = None
        self.review_context = None
        self.application = load_application()
        self.reverting = False
        self.discussions = discussion.Discussions(discussion_root or team.ROOT / "runs" / "discussions")
        self.board_turn = None
        self.agent_path = team.ROOT / "runs" / "agents.json"
        self.extra_agents = (json.loads(self.agent_path.read_text(encoding="utf-8"))
                             if self.agent_path.is_file() else {})
        self.checkpoint = team.ROOT / "runs" / f"dashboard-{self.server_port}.json"
        self.restore_job()
        if self.application and self.application["state"] == "reverted":
            self.post_revert_event(self.application)
        elif self.application and self.application["state"] == "applied":
            self.post_application_event(self.application, "reapply_event", REAPPLIED_MESSAGE)

    def save_job(self):
        def pack(values):
            return [str(values[0]), *values[1:]] if values else None

        discussion.write_json(self.checkpoint, {"job": self.job, "candidate": pack(self.candidate),
                                                "plan": pack(self.plan_context), "review": self.review_context})

    def restore_job(self):
        if not self.checkpoint.is_file():
            return
        saved = json.loads(self.checkpoint.read_text(encoding="utf-8"))
        self.job = job = saved["job"]
        self.review_context = saved.get("review")
        if not job:
            return
        for key, attribute in (("candidate", "candidate"), ("plan", "plan_context")):
            values = saved.get(key)
            if values:
                setattr(self, attribute, (Path(values[0]), *values[1:]))
        if job["running"] and job["stage"] in {"code", "repair", "test", "review"} and self.review_context:
            job["message"] = "Aynı Git kopyasındaki çalışma açılıyor."
            if job["stage"] in {"code", "repair"}:
                job["agents"]["code"]["output"] = ""
            if job["stage"] == "review":
                job["agents"]["review"]["output"] = ""
            self.save_job()
            threading.Thread(target=self.work, args=(job,), kwargs={"resume": self.review_context},
                             daemon=True).start()
            return
        if job["stage"] == "applying":
            try:
                state = recover_application(job["id"])
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                job.update(running=False, stage="error",
                           message=f"Kesilen uygulama doğrulanamadı. Kaynak dosyaları incele: {error}")
            else:
                if state:
                    self.application = state
                    self.candidate = None
                    job.update(running=False, stage=state["state"],
                               message="Kesilen işlem kayıttan doğrulandı. Git farkı tekrar uygulanmadı.")
                else:
                    job.update(running=False, stage="HUMAN_REVIEW",
                               message="Uygulama başlamadan kesildi. Değişiklik yeniden onaylanabilir.")
        elif job["running"] and job["stage"] not in {"HUMAN_REVIEW", "plan_ready"}:
            job.update(running=False, failed_stage=job["stage"], stage="error",
                       message="Panel çalışma sırasında kapandı. Güvenli bir onay noktası bulunamadı; görevi yeniden başlat.")
            for agent in job["agents"].values():
                if agent["state"] in {"Çalışıyor", "Bekliyor"}:
                    agent.update(state="Durdu", activity="Panel kapandı.")
        else:
            job["running"] = False
            if self.application and self.application["id"] == job["id"] and self.application["state"] == "reverted":
                if job["stage"] != "reverted":
                    job["timeline"].append({"time": datetime.now(timezone.utc).isoformat(),
                                             "source": "Panel", "message": REVERTED_MESSAGE})
                job.update(stage="reverted", message=REVERTED_MESSAGE)
            elif (self.application and self.application["id"] == job["id"]
                  and self.application["state"] in {"revert_blocked", "reapply_blocked"}):
                job.update(stage="error", message=self.application["error"])
            elif (self.application and self.application["id"] == job["id"]
                  and self.application["state"] == "applied" and job["stage"] == "reverted"):
                job.update(stage="applied", message=REAPPLIED_MESSAGE)
                job["timeline"].append({"time": datetime.now(timezone.utc).isoformat(),
                                         "source": "Panel", "message": REAPPLIED_MESSAGE})
        self.save_job()

    def active_agents(self):
        with self.lock:
            return {key: dict(agent) for key, agent in self.extra_agents.items() if agent["enabled"]}

    def save_agent(self, data):
        if not isinstance(data, dict):
            raise ValueError("Ajan bilgileri okunamadı.")
        name, duty, model = (data.get(key) for key in ("name", "duty", "model"))
        if not all(isinstance(value, str) and value.strip() and "\0" not in value
                   for value in (name, duty, model)) or len(name) > 64 or len(duty) > 6000 or len(model) > 128:
            raise ValueError("Modeli seç, en fazla 64 karakterlik ad ve 6000 karakterlik görev yaz.")
        watch, enabled = data.get("watch", False), data.get("enabled", True)
        if type(watch) is not bool or type(enabled) is not bool:
            raise ValueError("Ajan ayarları geçersiz.")
        if enabled:
            check_model_selection(model, model)
        with self.lock:
            key = data.get("id")
            if key is not None and (not isinstance(key, str) or key not in self.extra_agents):
                raise ValueError("Düzenlenecek ajan bulunamadı.")
            name = name.strip()
            if discussion.plain(name) in {"mimar", "codex", "denetci", "panel", "kullanici", "herkes"}:
                raise ValueError("Bu ad ekipte ayrılmış. Başka bir ad seç.")
            if any(discussion.plain(agent["name"]) == discussion.plain(name) and other != key
                   for other, agent in self.extra_agents.items()):
                raise ValueError("Bu adla bir ajan zaten var.")
            if key is None:
                key = "custom-" + uuid.uuid4().hex[:10]
                stem = discussion.agent_handle(name)
                handle = stem
                used = {*discussion.ROLE_IDS, "herkes", *(item["handle"] for item in self.extra_agents.values())}
                number = 2
                while handle in used:
                    handle = f"{stem}-{number}"
                    number += 1
            else:
                handle = self.extra_agents[key]["handle"]
            agent = {"id": key, "handle": handle, "name": name, "duty": duty.strip(),
                     "model": model, "watch": watch, "enabled": enabled}
            updated = {**self.extra_agents, key: agent}
            self.agent_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.agent_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.agent_path)
            self.extra_agents = updated
            return agent

    def start_job(self, inputs, board_id=None, approved=None):
        project = Path(team.git(inputs[0].resolve(), "rev-parse", "--show-toplevel").strip()).resolve()
        roster = providers.effective_team(project)
        active = {key: member for key, member in (roster or {}).items() if member["enabled"]}
        slots = {member["slot"]: member for member in active.values() if member["slot"] != "member"}
        required = ({"code", "review"} if approved is not None else {"plan", "code", "review"}) if inputs[4] else {"plan"}
        if roster is not None and not required <= slots.keys():
            raise ValueError("Proje ekibinde bu işlem için gereken akış görevlerine etkin ajan ata.")
        if roster is None:
            raise ValueError("Önce Ekip ekranından çalışacak ajanları seç.")
        with self.lock:
            if self.reverting or self.board_turn or (self.job and self.job["running"]):
                return False
            job = {"id": uuid.uuid4().hex[:12], "running": True,
                   "stage": "starting", "message": "Git deposu kontrol ediliyor."}
            job["agents"] = {
                key: {"state": state, "activity": activity, "output": "", "decision": ""}
                for key, (state, activity) in zip(AGENTS, ACTIVITY["starting"])
            }
            job["timeline"] = []
            job["models"] = {"planner": inputs[2], "reviewer": inputs[3]}
            job["slots"] = slots
            job["project"] = str(project)
            job["check"] = inputs[5]
            for key, member in slots.items():
                job["agents"][key].update(label=member["name"], model=member["model"] or member["provider"],
                                          **agent_roles.assignment(member))
            job["extras"] = {key: dict(agent) for key, agent in (active if roster is not None else self.extra_agents).items()
                             if agent["enabled"] and agent["watch"]}
            for key, agent in job["extras"].items():
                job["agents"][key] = {"state": "Bekliyor", "activity": "Kod turu ve test kanıtı bekleniyor.",
                                      "output": "", "decision": "", "label": agent["name"], "model": agent["model"]}
            if board_id:
                self.discussions.bind(project, board_id)
                heading = ("Geliştiriciye gönderilen görev" if approved is not None else
                           ("Başlatılan görev" if inputs[4] else "Planı istenen görev"))
                self.discussions.post(board_id, "Kullanıcı", f"{heading}:\n\n" + (approved or inputs[1]),
                                      run_id=job["id"])
                job["board_id"] = board_id
            elif roster is not None:
                job["board_id"], _ = self.discussions.post(None, "Kullanıcı", inputs[1],
                                                            project=project, run_id=job["id"])
            self.job = job
            self.candidate = None
            self.plan_context = None
            self.review_context = None
            self.save_job()
        threading.Thread(target=self.work, args=(job, inputs), kwargs={"approved": approved}, daemon=True).start()
        return True

    def start_board_turn(self, topic, models, mode, request_id=None):
        request = discussion.request_message(topic, request_id)
        roster = providers.effective_team(topic.get("project"))
        if roster is None:
            raise ValueError("Ajanlara iletmeden önce Ekip ekranından bir takım seç.")
        extras = {key: member for key, member in roster.items() if member["enabled"]}
        if not extras:
            raise ValueError("Bu ekipte etkin bir ajan seç.")
        with self.lock:
            if self.reverting or self.board_turn or (self.job and self.job["running"]):
                raise ValueError("Devam eden ajan çalışması bitince yeni pano turu başlatabilirsin.")
            self.board_turn = {"id": topic["id"], "role": "İstek değerlendiriliyor"}
        threading.Thread(target=self.board_work, args=(topic, models, mode, request, extras, False), daemon=True).start()

    def board_work(self, topic, models, mode, request, extras, use_base):
        topic_id = topic["id"]
        request_id = request["id"]
        runs_root = team.ROOT / "runs"
        try:
            context = discussion.transcript(topic, request_id=request_id, root=runs_root,
                                            attachment_root=self.discussions.root)
            quote = context.get("reply_to") or {}
            query = request["text"] + " " + quote.get("text", "")[:1000] + " " + (quote.get("run_id") or "")
            project_history = (providers.project_context(topic["project"], query, topic_id=topic_id, structured=True)
                               if topic.get("project") else {})
            context.update(project_history)
            if not use_base and not extras:
                raise ValueError("Bu proje ekibinde etkin ajan yok.")
            planner = next((member for member in extras.values() if member.get("slot") == "plan"), None)
            supervisor = next((member for member in extras.values() if member.get("role") == "supervisor"), None)
            coordinator = supervisor or planner
            router = models["plan"] if use_base else coordinator or next(iter(extras.values()))
            if mode in {"opinions", "team"}:
                turns = [(role, "Kullanıcının fikri hakkında kendi görüşünü yaz.")
                         for role in ((list(discussion.ROLES) + list(extras) if mode == "team" else discussion.ROLES)
                                      if use_base else extras)]
            elif mode == "synthesis":
                if not use_base and coordinator is None:
                    raise ValueError("Projeye bir koordinatör veya planlama ajanı ata.")
                turns = [(coordinator["id"] if coordinator else "plan", "Ekibin görüşlerini ve son kullanıcı notunu değerlendir; uygun yaklaşımı öner.")]
            else:
                targets = discussion.explicit_responders(topic, request, extras, use_base)
                turns = [(role, "") for role in targets] if targets else discussion.route_request(router, context, extras, use_base)
            for role, focus in turns:
                extra = extras.get(role)
                label = extra["name"] if extra else discussion.ROLES[role]
                with self.lock:
                    self.board_turn = {"id": topic["id"], "role": label}
                model = extra["model"] if extra else models.get(role, "Codex")
                try:
                    if mode not in {"opinions", "team"}:
                        fresh = self.discussions.read(topic_id)
                        replies = [message for message in fresh["messages"] if message.get("agent_id")
                                   and message.get("reply_to") == request_id
                                   and (mode != "synthesis" or message["agent_id"] != role)]
                        contributions = [discussion.serialize_message(message, 1800) for message in
                                         list({message["agent_id"]: message for message in replies}.values())[:8]]
                        if mode == "synthesis":
                            if not contributions:
                                raise ValueError("Özetlemek için önce ekip görüşlerini al.")
                            context = {"request": request["text"], "team_responses": contributions}
                        else:
                            context = discussion.transcript(fresh,
                                                        request_id=request_id, root=runs_root,
                                                        attachment_root=self.discussions.root)
                            context.update(project_history)
                            context["team_responses"] = contributions
                    text, proposal = discussion.opinion(role, models, context, self.discussions.root, focus,
                                                         agent=extra, synthesis=mode == "synthesis")
                    if not text.strip():
                        raise RuntimeError("Boş yanıt alındı.")
                except Exception as error:
                    self.discussions.post(topic["id"], "Panel", f"{label} görüşü alınamadı: {error}")
                else:
                    profile = ({key: extra[key] for key in ("name", "provider", "model", "handle", "slot", "duty")}
                               if extra and "provider" in extra else None)
                    if profile:
                        profile.update(agent_roles.assignment(extra))
                    self.discussions.post(topic_id, label, text, model=model, reply_to=request_id,
                                          agent_id=role, agent_profile=profile, proposal=proposal)
        except Exception as error:
            self.discussions.post(topic["id"], "Panel", f"İstek yönlendirilemedi: {error}")
        finally:
            with self.lock:
                self.board_turn = None

    def continue_plan(self, data):
        if not isinstance(data, dict):
            raise ValueError("Plan isteği okunamadı.")
        plan, action = data.get("plan"), data.get("action")
        if action not in ("review", "execute") or not isinstance(plan, str) or not plan.strip() or len(plan) > 24_000:
            raise ValueError("Geçerli bir plan metni gir.")
        if "\0" in plan:
            raise ValueError("Plan metni geçersiz.")
        check = parse_check(data.get("check", ""))
        with self.lock:
            job = self.job
            if (self.reverting or self.board_turn or not job or job["running"] or job["stage"] != "plan_ready"
                    or self.plan_context is None or data.get("id") != job["id"]
                    or data.get("version") != job["plan_version"]):
                raise ValueError("Bu plan değişti veya başka bir işlem sürüyor. Güncel planı kontrol et.")
            if action == "execute" and not {"code", "review"} <= job["slots"].keys():
                raise ValueError("Bu planı oluştururken etkin geliştirme ve denetim ajanları seçilmemişti. Ekip görevlerini düzenleyip yeni plan oluştur.")
            job.update(running=True, stage="starting", plan=plan, check=check,
                       plan_version=job["plan_version"] + 1)
            context = self.plan_context
            self.save_job()
        threading.Thread(target=self.work, args=(job, None, action, context, plan, check), daemon=True).start()

    def work(self, job, inputs=None, action=None, context=None, plan=None, check=None, approved=None, resume=None):
        out = team.ROOT / "runs" / job["id"]
        slots = job.get("slots", {})
        developer = slots.get("code")
        memory = None
        selected_inputs = inputs
        if inputs and slots:
            selected_inputs = (*inputs[:2], slots.get("plan", inputs[2]), slots.get("review", inputs[3]), *inputs[4:])

        def record(source, message):
            job["timeline"].append({"time": datetime.now(timezone.utc).isoformat(),
                                    "source": source, "message": message})
            del job["timeline"][:-24]

        def progress(stage, message):
            with self.lock:
                if not self.review_context or self.review_context.get("stage", "review") != stage:
                    self.review_context = None
                job.update(stage=stage, message=message)
                entries = ACTIVITY.get(stage)
                if entries:
                    for agent, (state, activity) in zip(job["agents"].values(), entries):
                        agent.update(state=state, activity=activity)
                    if job.get("board_id") and stage in {"code", "test", "review", "repair"}:
                        job["agents"]["plan"]["activity"] = "Kullanıcının pano taslağı kullanılıyor."
                else:
                    for key in AGENTS:
                        job["agents"][key].update(state="Tamamlandı", activity="Bu çalışma sona erdi.")
                if stage == "repair":
                    for key in ("code", "review"):
                        agent = job["agents"][key]
                        agent["output"] = (agent["output"] + "\n\n" + message + "\n\n")[-LIVE_LIMIT:]
                key = {"plan": "plan", "plan_revision": "plan", "code": "code", "repair": "code",
                       "review": "review", "plan_review": "review"}.get(stage)
                source = job["agents"][key].get("label", AGENTS[key]) if key else "Panel"
                record(source, message)
                self.save_job()

        def live(key, action, value):
            with self.lock:
                agent = job["agents"][key]
                if action == "append":
                    agent["output"] = (agent["output"] + value)[-LIVE_LIMIT:]
                elif action == "decision":
                    agent["decision"] = value[:2000]
                    record(agent.get("label", AGENTS[key]), agent["decision"])
                elif action == "activity":
                    agent["activity"] = value

        def approve_candidate(repo, base, branch, diff):
            with self.lock:
                self.candidate = (repo, base, branch, diff)
                job["target"] = {"repo": str(repo), "base": base, "branch": branch}

        def on_plan(repo, base, branch, task, original):
            with self.lock:
                self.plan_context = (repo, base, branch, task, selected_inputs[2], selected_inputs[3])
                job.update(plan=original, plan_version=0)

        def on_review(review):
            with self.lock:
                job["plan_review"] = review

        def on_review_ready(checkpoint):
            with self.lock:
                self.review_context = checkpoint
                job.update(stage=checkpoint["stage"], message="Git farkı bu adım için kaydedildi.")
                self.save_job()

        def observe_round(number, evidence):
            notes = []
            report = {"type": "object", "required": ["finding"], "properties": {"finding": {"type": "string"}}}
            progress("observe", f"Tur {number} · Ek ajanlar kod farkını ve test kanıtını inceliyor.")
            for key, definition in job["extras"].items():
                with self.lock:
                    state = job["agents"][key]
                    state.update(state="Çalışıyor", activity=f"Tur {number} · Kod ve test kanıtını inceliyor.")
                    record(definition["name"], state["activity"])
                try:
                    raw = team.ask(definition if definition.get("provider") else definition["model"], (
                        f"Bu ekipte {definition['name']} adlı ajansın. Görevin: {definition['duty']}\n"
                        "Verilen görev, Git farkı ve test kanıtını incele. Somut bir anomali veya "
                        "görevinle ilgili bir bulgu varsa finding alanına yaz; yoksa boş bırak. "
                        "Kapsam dışı gereksinim üretme. Görmediğin dosyaları incelediğini söyleme."
                    ), evidence, report, temperature=0)
                    finding = json.loads(raw).get("finding")
                    if not isinstance(finding, str):
                        raise ValueError("Geçerli bir gözlem alınamadı.")
                    finding = finding.strip()
                    state_label = "Tamamlandı"
                    message = finding or "Bulgu yok."
                except Exception as error:
                    finding = ""
                    state_label = "Hata"
                    message = f"Gözlem alınamadı: {error}"
                notes.append({"agent": definition["name"], "handle": definition["handle"],
                              "model": definition["model"], "status": state_label, "result": message})
                with self.lock:
                    state.update(state=state_label, activity=f"Tur {number} · Gözlem tamamlandı.",
                                 decision=message[:2000], output=(state["output"] + f"Tur {number}: {message}\n")[-LIVE_LIMIT:])
                    record(definition["name"], state["decision"])
                if finding and job.get("board_id"):
                    self.discussions.post(job["board_id"], definition["name"], f"Tur {number} · {finding}",
                                          model=definition["model"], run_id=job["id"], agent_id=key)
            return notes

        observer = observe_round if job.get("extras") else None
        try:
            if resume:
                repo = Path(resume["repo"])
                check_target(repo, resume["base"], resume["branch"])
                team.execute_plan(repo, resume["base"], resume["branch"], resume["task"], "",
                                  resume["reviewer"], resume["check"], out, progress, live, approve_candidate,
                                  observe_round=observer, developer=developer, memory=resume["memory"],
                                  on_review_ready=on_review_ready, resume=resume)
                return
            query = "\n".join(filter(None, [selected_inputs[1] if selected_inputs else context[3],
                                              approved or plan]))
            memory = providers.project_context(job["project"], query)
            if not action:
                team.workflow(*selected_inputs, out=out, progress=progress, live=live,
                              approve_candidate=approve_candidate, on_plan=on_plan, prepared_plan=approved,
                              observe_round=observer, developer=developer, memory=memory, on_review_ready=on_review_ready)
            else:
                repo, base, branch, task, planner, reviewer = context
                check_target(repo, base, branch)
                if action == "review":
                    revised = team.review_plan(task, plan, planner, reviewer, out,
                                               job["plan_version"], progress, live, on_review, memory=memory)
                    with self.lock:
                        job.update(plan=revised, plan_version=job["plan_version"] + 1)
                    progress("plan_ready", "Gözden geçirilen plan hazır. Düzenleyebilir veya Codex’e gönderebilirsin.")
                else:
                    team.execute_plan(repo, base, branch, task, plan, reviewer, check, out,
                                      progress, live, approve_candidate, user_selected=True, observe_round=observer,
                                      developer=developer, memory=memory, on_review_ready=on_review_ready)
        except Exception as error:
            if action == "review" or (action == "execute" and job["stage"] == "plan_ready"):
                progress("plan_ready", f"İşlem durdu: {error}")
            else:
                self.fail_job(job, error, record)
        finally:
            try:
                if job.get("board_id"):
                    write_run_summary(out, job)
                    self.discussions.post(job["board_id"], "Panel", f"Görev sonucu: {job['message']}",
                                          run_id=job["id"])
            except (ValueError, OSError) as error:
                with self.lock:
                    record("Panel", f"Proje hafızasına kayıt yazılamadı: {error}")
            finally:
                with self.lock:
                    job["running"] = False
                    self.save_job()

    def fail_job(self, job, error, record):
        with self.lock:
            job.update(stage="error", failed_stage=job["stage"], message=str(error))
            for agent in job["agents"].values():
                if agent["state"] == "Çalışıyor":
                    agent.update(state="Hata", activity=str(error))
                elif agent["state"] == "Bekliyor":
                    agent.update(state="Durdu", activity="Akış hata nedeniyle durdu.")
            record("Panel", str(error))

    def apply_job(self, job_id):
        with self.lock:
            job = self.job
            if job and job["id"] == job_id and job["stage"] == "applied":
                return
            if (self.reverting or not job or job["id"] != job_id or job["running"]
                    or job["stage"] != "HUMAN_REVIEW" or self.candidate is None):
                raise ValueError("Bu işin uygulanabilir bir denetim sonucu yok.")
            candidate = self.candidate
            job.update(running=True, stage="applying", message="Git farkı kaynak projeye uygulanıyor.")
            self.save_job()
        try:
            receipt = apply_reviewed_change(*candidate, job_id=job_id, board_id=job.get("board_id"))
        except Exception:
            with self.lock:
                job.update(running=False, stage="HUMAN_REVIEW",
                           message="Uygulama durdu. Hedef projeyi kontrol edip tekrar dene.")
                self.save_job()
            raise
        with self.lock:
            message = "Onaylanan değişiklik kaynak projenin çalışma dosyalarına uygulandı."
            job.update(running=False, stage="applied", message=message)
            job["timeline"].append({"time": datetime.now(timezone.utc).isoformat(),
                                    "source": "Panel", "message": message})
            self.candidate = None
            self.application = receipt
            self.save_job()
        if job.get("board_id"):
            self.discussions.post(job["board_id"], "Panel", message, run_id=job_id)

    def revert_application(self, application_id):
        with self.lock:
            receipt = self.application
            if receipt and receipt["id"] == application_id and receipt["state"] == "reverted":
                return
            if (self.reverting or (self.job and self.job["running"]) or not receipt
                    or receipt["id"] != application_id or receipt["state"] != "applied"):
                raise ValueError("Geri alınabilecek son uygulama artık etkin değil.")
            self.reverting = True
        try:
            reverted = revert_reviewed_change(receipt)
            with self.lock:
                self.application = reverted
                if self.job and self.job["id"] == application_id:
                    message = REVERTED_MESSAGE
                    self.job.update(stage="reverted", message=message)
                    self.job["timeline"].append({"time": datetime.now(timezone.utc).isoformat(),
                                                 "source": "Panel", "message": message})
                    self.save_job()
            self.post_revert_event(reverted)
        finally:
            with self.lock:
                self.reverting = False

    def post_revert_event(self, receipt):
        self.post_application_event(receipt, "revert_event", REVERTED_MESSAGE)

    def post_application_event(self, receipt, key, message):
        if receipt.get("board_id") and receipt.get(key):
            try:
                self.discussions.post(receipt["board_id"], "Panel", message,
                                      run_id=receipt["id"], event_id=receipt[key])
            except (OSError, ValueError) as error:
                print(f"Uygulama işleminin pano kaydı yazılamadı: {error}", flush=True)

    def reapply_application(self, application_id):
        with self.lock:
            receipt = self.application
            if receipt and receipt["id"] == application_id and receipt["state"] == "applied":
                return
            if (self.reverting or self.board_turn or (self.job and self.job["running"]) or not receipt
                    or receipt["id"] != application_id or receipt["state"] != "reverted"):
                raise ValueError("Yeniden uygulanabilecek bir geri alma kaydı bulunamadı.")
            self.reverting = True
        message = REAPPLIED_MESSAGE
        try:
            with round_lock(receipt_path(application_id).with_suffix(".lock")):
                reapplied = apply_reviewed_change(Path(receipt["repo"]), receipt["base"], receipt["branch"],
                                                 receipt["diff"], job_id=application_id,
                                                 board_id=receipt.get("board_id"), reapply=True)
            with self.lock:
                self.application = reapplied
                if self.job and self.job["id"] == application_id:
                    self.job.update(stage="applied", message=message)
                    self.job["timeline"].append({"time": datetime.now(timezone.utc).isoformat(),
                                                 "source": "Panel", "message": message})
                    self.save_job()
            self.post_application_event(reapplied, "reapply_event", message)
        finally:
            with self.lock:
                self.reverting = False


def write_run_summary(out, job):
    out.mkdir(parents=True, exist_ok=True)
    base_file = out / "01-base.txt"
    base = base_file.read_text(encoding="utf-8").strip() if base_file.is_file() else "Yok"
    task_path = out / "02-plan-sent.md"
    if not task_path.is_file():
        task_path = out / "01-task.md"
    task = task_path.read_text(encoding="utf-8") if task_path.is_file() else ""
    people = [providers.describe(member) for member in job.get("slots", {}).values()]
    if not people:
        people = [job["models"]["planner"], "Codex", job["models"]["reviewer"]]
    members = {member["id"]: member for member in [*job.get("slots", {}).values(), *job.get("extras", {}).values()]}
    if members:
        snapshot = [{key: member[key] for key in ("id", "name", "handle", "provider", "model", "duty", "slot")
                     if key in member} for member in members.values()]
        for item, member in zip(snapshot, members.values()):
            item.update(agent_roles.assignment(member))
        discussion.write_json(out / "00-team.json", snapshot)
    lines = ["# Kapsam ve çalışma sonucu", "", f"Kaynak proje: {job['project']}",
             f"Başlangıç commit’i: {base}",
             "Ekip: " + " | ".join(people),
             f"Sonuç: {job['message']}", "", "## Onaylanan kapsam", "", task, "",
             "## Test ve denetim", ""]
    for number in range(1, team.MAX_CORRECTIONS + 2):
        path = out / f"round-{number:02}" / "06-review.json"
        if path.is_file():
            result = json.loads(path.read_text(encoding="utf-8"))
            check = result.get("check_exit")
            lines.append(f"- Tur {number}: test {('çalıştırılmadı' if check is None else 'çıkış kodu ' + str(check))}, "
                         f"denetim {result['review']['decision']}.")
    (out / "00-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_target(repo, base, branch):
    current_root = Path(team.git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    if current_root != repo or team.git(repo, "rev-parse", "HEAD").strip() != base:
        raise ValueError("Kaynak projenin başlangıç commit’i değişti. Görevi yeniden çalıştır.")
    if team.git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() != branch:
        raise ValueError("Kaynak projenin dalı değişti. Görevin başladığı dala dön.")


def git_bytes(repo, *args, data=None, env=None):
    result = subprocess.run(["git", *args], cwd=repo, input=data, capture_output=True, timeout=30, env=env)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip() or "Git işlemi başarısız.")
    return result.stdout


def patch_state(repo, base, patch=None):
    """Hash tracked and untracked changes without updating the real index."""
    with tempfile.TemporaryDirectory(dir=team.ROOT / "runs") as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        git_bytes(repo, "read-tree", base, env=env)
        args = ("diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv")
        if patch is None:
            git_bytes(repo, "add", "-N", "--", ".", env=env)
            diff = git_bytes(repo, *args, base, "--", env=env)
            paths = []
        else:
            git_bytes(repo, "apply", "--cached", "-", data=patch, env=env)
            diff = git_bytes(repo, *args, "--cached", base, "--", env=env)
            names = git_bytes(repo, "diff", "--cached", "--name-only", "--no-renames", "-z", base, "--", env=env)
            paths = [name.decode("utf-8") for name in names.split(b"\0") if name]
    return hashlib.sha256(diff).hexdigest(), paths


def snapshot(repo, base):
    return patch_state(repo, base)[0]


def tracked_file(repo, name):
    path = repo / name
    if not path.parent.resolve().is_relative_to(repo.resolve()):
        raise ValueError("Git farkında proje dışına çıkan bir yol var.")
    return path


def raw_file(repo, name):
    path = tracked_file(repo, name)
    return path.read_bytes() if not path.is_symlink() and path.is_file() else None


def raw_hash(repo, name):
    contents = raw_file(repo, name)
    return hashlib.sha256(contents).hexdigest() if contents is not None else None


def receipt_path(job_id):
    if not re.fullmatch(r"[a-f0-9]{12}", job_id):
        raise ValueError("Geçersiz uygulama kaydı.")
    return team.ROOT / "runs" / job_id / "application.json"


def save_application(receipt):
    path = receipt_path(receipt["id"])
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def load_application():
    records = (team.ROOT / "runs").glob("*/application.json")
    for path in sorted(records, key=lambda file: file.stat().st_mtime, reverse=True):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if receipt_path(receipt["id"]) == path and receipt["state"] == "reverting":
                try:
                    return revert_reviewed_change(receipt, recover=True)
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                    return {**receipt, "state": "revert_blocked",
                            "error": f"Kesilen geri alma doğrulanamadı. Kaynak dosyaları incele: {error}"}
            if (receipt_path(receipt["id"]) == path and receipt.get("reapply") and receipt["state"] == "applying"):
                try:
                    with round_lock(path.with_suffix(".lock")):
                        return recover_application(receipt["id"])
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                    return {**receipt, "state": "reapply_blocked",
                            "error": f"Kesilen yeniden uygulama doğrulanamadı. Kaynak dosyaları incele: {error}"}
            if (receipt_path(receipt["id"]) == path and receipt["state"] in {"applied", "reverted"}
                    and all(isinstance(receipt[key], str)
                            for key in ("repo", "base", "branch", "diff", "expected"))):
                return receipt
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.TimeoutExpired):
            continue
    return None


def recover_application(job_id):
    path = receipt_path(job_id)
    if not path.is_file():
        return None
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt["id"] != job_id:
        raise ValueError("Uygulama kaydı görevle eşleşmiyor.")
    if receipt["state"] in {"applied", "reverted"}:
        return receipt
    if receipt["state"] != "applying":
        raise ValueError("Uygulama kaydının durumu tanınmıyor.")
    repo = Path(receipt["repo"])
    base = receipt["base"]
    check_target(repo, base, receipt["branch"])
    if git_bytes(repo, "diff", "--cached", base, "--"):
        raise ValueError("Git hazırlama alanında değişiklik var.")
    current = snapshot(repo, base)
    if current == receipt["expected"]:
        receipt.update(after={name: raw_hash(repo, name) for name in receipt["paths"]}, state="applied")
        save_application(receipt)
        return receipt
    if not team.git(repo, "status", "--porcelain", "--untracked-files=all").strip():
        if receipt.get("reapply"):
            receipt["state"] = "reverted"
            save_application(receipt)
            return receipt
        path.unlink()
        return None
    raise ValueError("Çalışma ağacı başlangıç durumuyla veya onaylanan Git farkıyla eşleşmiyor.")


def application_record(repo, base, branch, diff, job_id):
    expected, paths = patch_state(repo, base, diff.encode("utf-8"))
    originals = {}
    for name in paths:
        contents = raw_file(repo, name)
        if contents is not None:
            originals[name] = base64.b64encode(contents).decode("ascii")
    return {"id": job_id, "repo": str(repo), "base": base, "branch": branch,
            "diff": diff, "expected": expected, "paths": paths,
            "originals": originals, "state": "applying"}


def apply_reviewed_change(repo, base, branch, diff, job_id=None, board_id=None, reapply=False):
    check_target(repo, base, branch)
    if team.git(repo, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("Kaynak projede bekleyen değişiklikler var. Bunları koruyarak çalışma ağacını temizle ve yeniden dene.")
    if not diff.strip():
        raise ValueError("Uygulanacak Git farkı boş.")
    patch = diff.encode("utf-8")
    git_bytes(repo, "apply", "--check", "-", data=patch)
    receipt = application_record(repo, base, branch, diff, job_id) if job_id else None
    if receipt:
        if reapply:
            receipt["reapply"] = True
            receipt["reapply_event"] = uuid.uuid4().hex
        if board_id:
            receipt["board_id"] = board_id
        save_application(receipt)
    try:
        git_bytes(repo, "apply", "-", data=patch)
    except Exception:
        if receipt:
            if reapply:
                recover_application(job_id)
            else:
                receipt_path(job_id).unlink(missing_ok=True)
        raise
    if receipt:
        receipt.update(after={name: raw_hash(repo, name) for name in receipt["paths"]}, state="applied")
        save_application(receipt)
    return receipt


def revert_reviewed_change(receipt, *, recover=False):
    path = receipt_path(receipt["id"])
    with round_lock(path.with_suffix(".lock")):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt["state"] == "reverted":
            return receipt
        if receipt["state"] == "reverting":
            recover = True
        elif receipt["state"] != "applied" or recover:
            raise ValueError("Geri alma kaydı artık geçerli değil.")
        return finish_revert(receipt, recover)


def finish_revert(receipt, recover):
    repo = Path(receipt["repo"])
    base = receipt["base"]
    check_target(repo, base, receipt["branch"])
    clean = not team.git(repo, "status", "--porcelain", "--untracked-files=all").strip()
    if not (recover and clean):
        if (git_bytes(repo, "diff", "--cached", base, "--")
                or snapshot(repo, base) != receipt["expected"]
                or any(raw_hash(repo, name) != digest for name, digest in receipt["after"].items())):
            raise ValueError("Uygulamadan sonra kaynak projede yeni değişiklikler var. Dosyalarına dokunulmadı.")
        patch = receipt["diff"].encode("utf-8")
        git_bytes(repo, "apply", "--reverse", "--check", "-", data=patch)
        if not recover:
            receipt = {**receipt, "state": "reverting", "revert_event": uuid.uuid4().hex}
            save_application(receipt)
        git_bytes(repo, "apply", "--reverse", "-", data=patch)
    for name, encoded in receipt["originals"].items():
        original = base64.b64decode(encoded, validate=True)
        if raw_file(repo, name) != original:
            path = tracked_file(repo, name)
            if path.is_symlink() or not path.is_file():
                raise ValueError("Geri alınan dosyanın türü beklenenle eşleşmiyor.")
            path.write_bytes(original)
    if team.git(repo, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("Geri alma sonunda kaynak proje başlangıç durumuyla eşleşmiyor.")
    reverted = {**receipt, "state": "reverted"}
    save_application(reverted)
    return reverted


def prepare_project(data):
    value = data.get("repo") if isinstance(data, dict) else None
    if not isinstance(value, str) or not value.strip() or len(value) > 2048 or "\0" in value:
        raise ValueError("Yeni proje klasörünün tam yolunu gir.")
    repo = Path(value.strip().strip('"'))
    if not repo.is_absolute():
        raise ValueError("Yeni proje klasörünün tam yolunu gir.")
    if repo.exists() and (not repo.is_dir() or any(repo.iterdir())):
        raise ValueError("Boş bir klasör veya henüz oluşturulmamış bir klasör yolu seç.")
    repo.mkdir(exist_ok=True)
    repo = repo.resolve()
    team.git(repo, "init", "--quiet", "--template=")
    team.git(repo, "-c", "user.name=Multi-Agent", "-c", "user.email=multi-agent@localhost",
             "-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", "Initial commit")
    return str(repo)


def parse_check(value):
    if not isinstance(value, str) or len(value) > 4096 or "\0" in value:
        raise ValueError("Test komutu geçersiz veya çok uzun.")
    check = [arg.strip() for arg in value.splitlines() if arg.strip()]
    if os.name == "nt" and check and Path(check[0]).suffix.lower() in {".bat", ".cmd"}:
        raise ValueError("Test için doğrudan bir çalıştırılabilir dosya seç. Örnek: python veya node.")
    return check


def parse_job(data):
    if not isinstance(data, dict):
        raise ValueError("İş bilgileri okunamadı.")

    def field(name, max_size, default=""):
        value = data.get(name, default)
        if not isinstance(value, str) or len(value) > max_size or "\0" in value:
            raise ValueError("Bir alan geçersiz veya çok uzun.")
        return value.strip()

    repo = Path(field("repo", 2048).strip('"'))
    task = field("task", 16_000)
    planner = field("planner", 128, team.PLANNER)
    reviewer = field("reviewer", 128, team.REVIEWER)
    execute = data.get("execute")
    if not repo.is_absolute() or not repo.is_dir():
        raise ValueError("Bilgisayarındaki bir Git proje klasörünün tam yolunu gir.")
    if not task:
        raise ValueError("Görev açıklamasını gir.")
    if type(execute) is not bool:
        raise ValueError("Çalıştırma türü geçersiz.")
    if any(not name or "cloud" in name.lower() for name in (planner, reviewer)):
        raise ValueError("İki rol için de yerel Ollama modeli seç.")
    check = parse_check(data.get("check", ""))
    return repo, task, planner, reviewer, execute, check if execute else None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, body, content_type, status=200, attachment=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; form-action 'none'")
        if attachment:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(attachment, safe='')}")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value, status=200):
        self.reply(json.dumps(value, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", status)

    def valid_host(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self.valid_host():
            self.send_json({"error": "Yerel adres bekleniyor."}, 403)
            return
        url = urlsplit(self.path)
        if url.path in ASSETS:
            name, media_type = ASSETS[url.path]
            self.reply((team.ROOT / "web" / name).read_bytes(), f"{media_type}; charset=utf-8")
            return
        if url.path == "/api/models":
            try:
                names = available_models()
            except (OSError, ValueError, KeyError, TypeError):
                self.send_json({"error": "Ollama model listesi alınamadı. Ollama’yı açıp listeyi yenile."}, 503)
            else:
                self.send_json({"models": names, "defaults": {"planner": team.PLANNER,
                                                               "reviewer": team.REVIEWER}})
            return
        if url.path == "/api/agents":
            with self.server.lock:
                agents = list(self.server.extra_agents.values())
            self.send_json({"agents": agents})
            return
        if url.path == "/api/project-team":
            try:
                raw = parse_qs(url.query).get("repo", [""])[0]
                project = memory_project_path(raw) if raw else None
                self.send_json(team_payload(project))
            except (ValueError, OSError) as error:
                self.send_json({"error": str(error)}, 400)
            return
        if url.path == "/api/board":
            query = parse_qs(url.query)
            try:
                topic_id = query.get("topic", [None])[0]
                topic = self.server.discussions.read(topic_id) if topic_id else None
                topics = self.server.discussions.list() if not topic_id or "list" in query else None
            except (ValueError, OSError) as error:
                self.send_json({"error": str(error)}, 400)
                return
            with self.server.lock:
                turn = self.server.board_turn
                job = self.server.job
                busy = bool(turn or self.server.reverting or (job and job["running"]))
            files = discussion.reports(topic, team.ROOT / "runs") if topic else []
            selected = query.get("repo", [None])[0]
            try:
                project = topic.get("project") if topic else memory_project_path(selected) if selected else None
                roster = providers.effective_team(project)
                decisions = discussion.project_decisions(project) if project else []
            except (ValueError, OSError) as error:
                self.send_json({"error": str(error)}, 400)
                return
            self.send_json({"topic": topic, "topics": topics, "turn": turn, "busy": busy, "reports": files,
                            "decisions": decisions,
                            "configured": roster is not None,
                            "extra_agents": [{**member, **agent_roles.assignment(member)}
                                             for member in (roster or {}).values() if member["enabled"]]})
            return
        memory = re.fullmatch(r"/api/board-memory/([a-f0-9]{12})(?:/([a-f0-9]{12})/(.+))?", url.path)
        if memory:
            try:
                topic = self.server.discussions.read(memory[1])
                root = team.ROOT / "runs"
                if not memory[2]:
                    self.reply(discussion.memory_archive(topic, root, self.server.discussions.root), "application/zip",
                               attachment=f"proje-hafizasi-{memory[1]}.zip")
                    return
                entry = next((r for r in discussion.reports(topic, root)
                              if r["run"] == memory[2] and r["path"] == memory[3]), None)
                if not entry:
                    raise ValueError("Bu pano konusunda böyle bir rapor yok.")
                path = discussion.report_root(topic, root) / entry["run"] / entry["path"]
                self.reply(path.read_bytes(), "text/plain; charset=utf-8")
            except (ValueError, OSError) as error:
                self.send_json({"error": str(error)}, 404)
            return
        attachment = re.fullmatch(r"/api/board-attachment/([a-f0-9]{12})/([a-f0-9]{32})", url.path)
        if attachment:
            try:
                topic = self.server.discussions.read(attachment[1])
                path = discussion.attachment_file(topic, self.server.discussions.root, attachment[2])
                entry = next(item for item in discussion.topic_attachments(topic) if item["id"] == attachment[2])
                self.reply(path.read_bytes(), "application/octet-stream", attachment=entry["name"])
            except (ValueError, OSError) as error:
                self.send_json({"error": str(error)}, 404)
            return
        with self.server.lock:
            job = deepcopy(self.server.job)
            candidate = self.server.candidate
            receipt = self.server.application
            application = ({key: receipt[key] for key in ("id", "repo", "branch", "state")}
                           if receipt else None)
            if application:
                application["busy"] = self.server.reverting or bool(job and job["running"])
        out = team.ROOT / "runs" / job["id"] if job else None
        if url.path == "/api/state":
            if job:
                job["folder"] = str(out)
                job["artifacts"] = [key for key, path in artifact_paths(out).items() if path.is_file()]
            self.send_json({"job": job, "application": application})
            return
        if job and job["stage"] == "HUMAN_REVIEW" and not job["running"] and candidate:
            if url.path == f"/api/runs/{job['id']}/approved-diff":
                self.reply(candidate[3].encode("utf-8"), "text/plain; charset=utf-8")
                return
        match = re.fullmatch(r"/api/runs/([a-f0-9]{12})/([a-z]+(?:-[0-9]+)?)", url.path)
        target = artifact_paths(out).get(match[2]) if match and job and match[1] == job["id"] else None
        if target:
            try:
                if target.resolve() != out.resolve() / target.relative_to(out):
                    raise FileNotFoundError
                with target.open("rb") as file:
                    body = file.read() if url.query == "download=1" else file.read(MAX_PREVIEW + 1)
                if len(body) > MAX_PREVIEW and url.query != "download=1":
                    body = body[:MAX_PREVIEW] + "\n\nÖnizleme kısaltıldı. Tam dosyayı indir.\n".encode("utf-8")
                self.reply(body, "text/plain; charset=utf-8",
                           attachment=target.name if url.query == "download=1" else None)
                return
            except OSError:
                pass
        self.send_json({"error": "Dosya bulunamadı."}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if (
            self.path not in {"/api/run", "/api/apply", "/api/revert", "/api/reapply", "/api/project", "/api/plan", "/api/board", "/api/agents", "/api/project-team"}
            or not self.valid_host()
            or self.headers.get("Origin") != self.server.origin
        ):
            self.send_json({"error": "İstek bu yerel arayüzden gelmeli."}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            limit = 2_800_000 if self.path == "/api/board" else MAX_BODY
            if not 0 < length <= limit or self.headers.get_content_type() != "application/json":
                raise ValueError("İstek biçimi veya boyutu geçersiz.")
            data = json.loads(self.rfile.read(length))
            if self.path == "/api/run":
                inputs = parse_job(data)
            elif self.path in {"/api/apply", "/api/revert", "/api/reapply"} and (
                not isinstance(data, dict) or not isinstance(data.get("id"), str)
            ):
                raise ValueError("Uygulanacak iş seçilmedi.")
        except (ValueError, OSError) as error:
            self.send_json({"error": str(error)}, 400)
            return
        if self.path == "/api/board":
            try:
                result = self.board_action(data)
            except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
                self.send_json({"error": str(error)}, 400)
            else:
                self.send_json(result, 202)
            return
        if self.path == "/api/agents":
            try:
                agent = self.server.save_agent(data)
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.send_json({"error": str(error)}, 400)
            else:
                self.send_json({"agent": agent}, 200)
            return
        if self.path == "/api/project-team":
            try:
                project = memory_project_path(data["repo"]) if data.get("repo") else None
                with self.server.lock:
                    if data.get("action") == "remove":
                        members = providers.effective_team(project)
                        key = data.get("id")
                        if not isinstance(key, str) or members is None or key not in members:
                            raise ValueError("Ajan bulunamadı.")
                        del members[key]
                        if project:
                            discussion.project_memory(project, create=True)
                        discussion.write_json(providers.team_file(project), members)
                    else:
                        if data.get("provider") == "ollama" and data.get("enabled", True):
                            name = data.get("model")
                            if not isinstance(name, str):
                                raise ValueError("Yerel model seç.")
                            check_model_selection(name, name)
                        providers.save_member(project, data)
                    payload = team_payload(project)
                self.send_json(payload)
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.send_json({"error": str(error)}, 400)
            return
        if self.path == "/api/plan":
            try:
                self.server.continue_plan(data)
            except ValueError as error:
                self.send_json({"error": str(error)}, 409)
            else:
                self.send_json({"ok": True}, 202)
            return
        if self.path == "/api/project":
            try:
                with self.server.lock:
                    repo = prepare_project(data)
            except FileNotFoundError:
                self.send_json({"error": "Üst klasörü ve Git’in bilgisayarda kurulu olduğunu kontrol et."}, 400)
            except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                self.send_json({"error": str(error)}, 400)
            else:
                self.send_json({"repo": repo}, 201)
            return
        if self.path in {"/api/apply", "/api/revert", "/api/reapply"}:
            try:
                if self.path == "/api/revert":
                    self.server.revert_application(data["id"])
                elif self.path == "/api/reapply":
                    self.server.reapply_application(data["id"])
                else:
                    self.server.apply_job(data["id"])
            except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                self.send_json({"error": str(error)}, 409)
            else:
                self.send_json({"ok": True})
            return
        try:
            board_id = data.get("board_id")
            if board_id is not None and not isinstance(board_id, str):
                raise ValueError("Geçerli bir pano konusu seç.")
            direct = data.get("direct", False)
            if type(direct) is not bool or (direct and not inputs[4]):
                raise ValueError("Görev başlatma seçeneği geçersiz.")
            started = self.server.start_job(inputs, board_id=board_id, approved=inputs[1] if direct else None)
        except (ValueError, RuntimeError) as error:
            self.send_json({"error": str(error)}, 400)
            return
        except (OSError, KeyError, TypeError):
            self.send_json({"error": "Ollama’ya ulaşılamadı. Ollama’yı açıp yeniden dene."}, 503)
            return
        if not started:
            self.send_json({"error": "Devam eden iş tamamlanınca yeni bir iş başlatabilirsin."}, 409)
            return
        with self.server.lock:
            topic = self.server.job.get("board_id")
        self.send_json({"ok": True, "topic": topic}, 202)

    def board_action(self, data):
        if not isinstance(data, dict):
            raise ValueError("Pano isteği okunamadı.")
        action, topic_id = data.get("action"), data.get("id")
        if action == "approve_decision":
            proposal, replaces = data.get("proposal"), data.get("replaces")
            if not isinstance(proposal, str) or (replaces is not None and not isinstance(replaces, str)):
                raise ValueError("Geçerli bir karar önerisi seç.")
            return {"id": topic_id, "message": self.server.discussions.approve_decision(topic_id, proposal, replaces)}
        if action in {"bind_project", "open_project"}:
            project = memory_project_path(data.get("repo"))
            with self.server.lock:
                job = self.server.job
                if self.server.board_turn or self.server.reverting or (job and job["running"]):
                    raise ValueError("Projeyi bağlamadan önce çalışan ajanların bitmesini bekle.")
                identifier = self.server.discussions.bind(project, topic_id if action == "bind_project" else None,
                                                          import_only=action == "open_project")
            return {"id": identifier, "project": str(project)}
        if action == "post":
            text = data.get("text")
            uploads = discussion.parse_uploads(data.get("attachments", []))
            if not isinstance(text, str) or (not text.strip() and not uploads) or len(text) > 8000 or "\0" in text:
                raise ValueError("Bir dosya ekle veya en fazla 8000 karakterlik mesaj yaz.")
            project = memory_project_path(data["repo"]) if not topic_id and data.get("repo") else None
            roster = providers.effective_team(project or (self.server.discussions.read(topic_id).get("project") if topic_id else None))
            topic_id, message_id = self.server.discussions.post(topic_id, "Kullanıcı", text.strip(),
                                                                reply_to=data.get("reply_to"),
                                                                extras=({key: member for key, member in roster.items() if member["enabled"]}
                                                                        if roster is not None else self.server.active_agents()),
                                                                project=project, uploads=uploads, use_base=roster is None)
            return {"id": topic_id, "message": message_id}
        if action not in ("opinions", "team", "synthesis", "directed", "execute"):
            raise ValueError("Geçerli bir pano işlemi seç.")
        topic = self.server.discussions.read(topic_id)
        planner, reviewer = data.get("planner") or team.PLANNER, data.get("reviewer") or team.REVIEWER
        if action == "execute":
            approved = data.get("draft")
            if not isinstance(approved, str) or not approved.strip() or len(approved) > 24_000 or "\0" in approved:
                raise ValueError("Codex’e gönderilecek görev taslağını doldur.")
            inputs = parse_job({**data, "task": topic["messages"][0]["text"] or topic["title"],
                                "planner": planner, "reviewer": reviewer, "execute": True})
            if not self.server.start_job(inputs, board_id=topic_id, approved=approved):
                raise ValueError("Devam eden ajan çalışması bitince kodlamayı başlat.")
        else:
            self.server.start_board_turn(topic, {"plan": planner, "review": reviewer}, action, data.get("message"))
        return {"id": topic_id}


def memory_project_path(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048 or "\0" in value:
        raise ValueError("Proje klasörünün tam yolunu gir.")
    path = Path(value.strip().strip('"'))
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("Var olan bir proje klasörünü seç.")
    return path.resolve()


def team_payload(project):
    selected = providers.read_team(project)
    available = providers.effective_team(project)
    return {"configured": available is not None, "inherited": bool(project and selected is None and available is not None),
            "repo": str(project) if project else "", "catalog": agent_roles.catalog(),
            "members": [{**member, **agent_roles.assignment(member)} for member in (available or {}).values()]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    with Dashboard(args.port) as server:
        print(f"Multi-Agent: {server.origin}", flush=True)
        if args.open:
            threading.Thread(target=webbrowser.open, args=(server.origin,), daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
