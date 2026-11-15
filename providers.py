"""Model adapters and project team configuration."""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
import agent_roles
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


KINDS = {"ollama", "codex", "claude", "openai", "anthropic"}
SLOTS = {"member", "plan", "code", "review"}


def team_file(project):
    import discussion
    if not project:
        import team
        return team.ROOT / "runs" / "default-team.json"
    return discussion.memory_path(discussion.project_memory(project), "ekip.json")


def read_team(project):
    path = team_file(Path(project) if project else None)
    if not path.is_file():
        return None
    members = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(members, dict) or any(not isinstance(value, dict) for value in members.values()):
        raise ValueError("Projenin ekip dosyası okunamadı.")
    return members


def effective_team(project):
    members = read_team(project) if project else None
    return members if members is not None else read_team(None)


def endpoint_key(agent):
    value = [agent["provider"], agent.get("endpoint", ""), agent.get("key_env", "")]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def local_endpoints():
    import team
    path = team.ROOT / "runs" / "approved-endpoints.json"
    return path, json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def save_member(project, data):
    import discussion
    if not isinstance(data, dict):
        raise ValueError("Ajan bilgileri geçersiz.")
    if not all(isinstance(data.get(key), str) for key in ("name", "duty", "model", "provider", "slot")):
        raise ValueError("Ajanın adını, modelini ve görevini gir.")
    name, duty, model, provider, slot = (data[key].strip() for key in ("name", "duty", "model", "provider", "slot"))
    if (not name or len(name) > 64 or not duty or len(duty) > 6000 or len(model) > 160
            or any("\0" in value for value in (name, duty, model)) or provider not in KINDS or slot not in SLOTS
            or (not model and provider not in {"codex", "claude"})):
        raise ValueError("Ajan adı, model, sağlayıcı veya görev alanı geçersiz.")
    branch = data.get("branch", agent_roles.LEGACY_BRANCH[slot])
    role = data.get("role", "")
    if (not isinstance(branch, str) or branch not in agent_roles.BRANCHES
            or not isinstance(role, str) or (role and
                (role not in agent_roles.ROLES or agent_roles.ROLES[role][0] != branch))):
        raise ValueError("Dala ait geçerli bir rol seç.")
    handle_value = data.get("handle") or name
    if not isinstance(handle_value, str) or len(handle_value) > 64:
        raise ValueError("En fazla 64 karakterlik @etiket gir.")
    handle = discussion.agent_handle(handle_value)
    if handle in {"herkes", "kullanici", "panel"}:
        raise ValueError("Bu etiket ayrılmış.")
    endpoint = data.get("endpoint", "")
    key_env = data.get("key_env", "")
    if not isinstance(endpoint, str) or not isinstance(key_env, str):
        raise ValueError("API bağlantısı geçersiz.")
    endpoint = endpoint.strip().rstrip("/")
    key_env = key_env.strip()
    if provider in {"openai", "anthropic"}:
        endpoint = endpoint or ("https://api.anthropic.com/v1" if provider == "anthropic" else "https://api.openai.com/v1")
        url = urlsplit(endpoint)
        if (not url.hostname or url.username or url.password or url.query or url.fragment
                or not (url.scheme == "https" or (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}))):
            raise ValueError("HTTPS API adresi veya yerel HTTP adresi gir.")
        if key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
            raise ValueError("API anahtarı yerine ortam değişkeninin adını gir.")
    else:
        endpoint = key_env = ""
    enabled, watch = data.get("enabled", True), data.get("watch", False)
    if type(enabled) is not bool or type(watch) is not bool:
        raise ValueError("Ajan durumu geçersiz.")
    members = effective_team(project) or {}
    identity = data.get("id")
    if identity is not None and (not isinstance(identity, str) or identity not in members):
        raise ValueError("Ajan bulunamadı.")
    identity = identity or "custom-" + uuid.uuid4().hex[:10]
    for key, member in members.items():
        if key != identity and member["handle"] == handle:
            raise ValueError("Bu @etiket başka bir ajanda kullanılıyor.")
        if key != identity and enabled and member["enabled"] and slot != "member" and member["slot"] == slot:
            raise ValueError("Bu akış adımında etkin bir ajan var. Önce görevini değiştir veya devre dışı bırak.")
    agent = {"id": identity, "name": name, "duty": duty, "handle": handle, "model": model, "provider": provider,
             "slot": slot, "branch": branch, "role": role, "endpoint": endpoint,
             "key_env": key_env, "enabled": enabled, "watch": watch}
    if enabled and provider in {"openai", "anthropic"}:
        path, approved = local_endpoints()
        key = endpoint_key(agent)
        if key not in approved:
            discussion.write_json(path, [*approved, key])
    if project:
        discussion.project_memory(Path(project), create=True)
    discussion.write_json(team_file(Path(project) if project else None), {**members, identity: agent})
    return agent


def describe(agent):
    return f"{agent['name']} · {agent['provider']} · {agent['model'] or 'varsayılan'}"


def project_context(project, query="", agent_id=None, topic_id=None, *, structured=False):
    import discussion
    import evidence
    topics = list(discussion.project_topics(project))
    terms = discussion.keywords(query)
    requested_round = re.search(r"\btur\s+(\d{1,3})\b", discussion.plain(query))
    round_prefix = f"round-{int(requested_round[1]):02}/" if requested_round else None

    def score(text):
        return len(terms & discussion.keywords(text))

    def decision_source(item):
        return f"[{item['topic']}/{item['id']}] (Pano onay tarihi: {item['time'][:10]})"

    records = discussion.project_decisions(project, topics)
    decisions = [entry for entry in records if entry["active"]]
    decisions.sort(key=lambda item: score(item["text"]), reverse=True)
    current = ["Onaylı güncel kararlar (ilgili ve yeni olanlar öncelikli):"]
    current.extend(f"- {decision_source(item)} {item['text'][:550]}" for item in decisions[:6])
    if not decisions:
        current.append("- Kayıtlı onaylı karar yok.")

    by_id = {entry["id"]: entry for entry in records}
    history = []
    for successor in records:
        previous = by_id.get(successor.get("replaces"))
        if previous is None:
            continue
        relevance = max(score(previous["text"]), score(successor["text"]))
        if previous["id"] in query or successor["id"] in query:
            relevance += len(terms) + 1
        if relevance:
            history.append((relevance, previous, successor))
    if history:
        current.append("İlgili onaylı karar geçmişi (değiştirilen kararlar artık geçerli değil):")
        for _, old, new in sorted(history, key=lambda item: (item[0], item[2]["time"]), reverse=True)[:3]:
            status = "Güncel" if new["active"] else "Sonradan değiştirildi"
            current.append(f"- Değiştirildi: {decision_source(old)} {old['text'][:550]}\n"
                           f"  Yerine geçen karar ({status}): {decision_source(new)} {new['text'][:550]}")

    messages, reports = [], []
    for topic in topics:
        title = topic["title"]
        if topic["id"] != topic_id:
            for item in topic["messages"]:
                relevance = score(item["text"])
                if relevance and not item.get("decision"):
                    messages.append((relevance, item.get("agent_id") == agent_id, item["time"],
                                     f"[{topic['id']}/{item['id']}] {title} · {item['author']}: {item['text'][:450]}"))
        entries = discussion.reports(topic, None)
        run_text = {}
        for item in topic["messages"]:
            if item.get("run_id"):
                run_text.setdefault(item["run_id"], []).append(item["text"])
        for entry in entries:
            name = entry["path"]
            if not name.endswith(("05-check.txt", "06-review.json", ".review.md")):
                continue
            run = entry["run"]
            relevance = score(title + " " + " ".join(run_text.get(run, [])))
            if not relevance and run.casefold() not in query.casefold():
                continue
            reports.append((run.casefold() in query.casefold(), bool(round_prefix and name.startswith(round_prefix)),
                            relevance, topic["messages"][-1]["time"], name, run, topic["project"]))

    current.append("İlgili eski konuşmalar (görüş ve öneriler):")
    current.extend("- " + item[-1] for item in sorted(messages, reverse=True)[:3])
    current.append("İlgili test ve denetim raporları (kanıt kapsamı alıntıda açıkça belirtilen davranışlarla sınırlıdır):")
    ranked = sorted(set(reports), reverse=True)
    test_records = []
    emitted = set()
    for _, _, _, _, name, run, owner in ranked if structured else []:
        if not name.endswith("05-check.txt") or (run, name) in emitted:
            continue
        folder = discussion.report_root({"project": owner}, None)
        try:
            path = discussion.memory_path(folder, run, name)
            output = path.read_text(encoding="utf-8", errors="replace")
            sidecar = path.with_name("05-check-evidence.json")
            record = evidence.test_record(output)
            record["recorded_at"] = None
            if sidecar.is_file() and not sidecar.is_symlink():
                stored = json.loads(sidecar.read_text(encoding="utf-8"))
                if isinstance(stored, dict) and stored.get("output_sha256") == record["output_sha256"]:
                    record.update(stored)
        except (OSError, ValueError):
            continue
        emitted.add((run, name))
        test_records.append({**record, "id": f"{run}/{name}", "run": run, "path": name, "excerpt": output[:550]})
        if len(test_records) == 4:
            break
    for _, _, _, _, name, run, owner in ranked[:4]:
        folder = discussion.report_root({"project": owner}, None)
        try:
            excerpt = discussion.read_excerpt(discussion.memory_path(folder, run, name), 550)
        except OSError:
            continue
        current.append(f"- [{run}/{name}] {excerpt}")
    summary = "\n".join(current)
    if structured:
        return {"project_memory": summary, "test_evidence": test_records,
                "decision_records": decisions[:6] + [old for _, old, _ in history[:3]]}
    return summary


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def api_answer(agent, instruction, text):
    provider = agent["provider"]
    if endpoint_key(agent) not in local_endpoints()[1]:
        raise ValueError("Bu API bağlantısını bu bilgisayarda kullanmak için ajanı açıp bağlantıyı kaydet.")
    env = agent["key_env"]
    key = os.environ.get(env) if env else None
    if env and not key:
        raise ValueError(f"{env} ortam değişkeni ayarlanmamış.")
    headers = {"Content-Type": "application/json"}
    if provider == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        if key:
            headers["x-api-key"] = key
        path = "/messages"
        payload = {"model": agent["model"], "system": instruction, "max_tokens": 4096,
                   "messages": [{"role": "user", "content": text}]}
    else:
        if key:
            headers["Authorization"] = f"Bearer {key}"
        path = "/chat/completions"
        payload = {"model": agent["model"], "messages": [{"role": "system", "content": instruction},
                                                         {"role": "user", "content": text}]}
    request = Request(agent["endpoint"] + path, headers=headers, data=json.dumps(payload).encode("utf-8"))
    try:
        with build_opener(NoRedirect()).open(request, timeout=180) as response:
            result = json.load(response)
    except HTTPError as error:
        raise ValueError(f"Model API’si HTTP {error.code} yanıtı verdi.") from error
    if provider == "anthropic":
        return "\n".join(part["text"] for part in result["content"] if part.get("type") == "text")
    return result["choices"][0]["message"]["content"]


def cli_answer(agent, prompt):
    import team
    runs = team.ROOT / "runs"
    runs.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=runs) as folder:
        work = Path(folder)
        if agent["provider"] == "claude":
            args = ["claude", "-p", "--no-session-persistence", "--tools", "", "--output-format", "text"]
            if agent["model"]:
                args += ["--model", agent["model"]]
            result = team.run(args, work, prompt, timeout=240)
            answer = result.stdout
        else:
            final = work / "answer.txt"
            args = ["codex", "-a", "never", "-c", 'web_search="disabled"']
            if os.name == "nt":
                args += ["-c", 'windows.sandbox="mxc"']
            args += ["exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                     "--sandbox", "read-only", "-o", str(final)]
            if agent["model"]:
                args += ["--model", agent["model"]]
            result = team.run(args + ["-"], work, prompt, timeout=240)
            answer = final.read_text(encoding="utf-8") if final.is_file() else ""
        if result.returncode or not answer.strip():
            raise ValueError(f"{agent['provider']} CLI yanıt üretemedi. Model ve oturum ayarlarını kontrol et.")
        return answer


def ask(agent, instruction, evidence, schema=None, on_text=None, temperature=None):
    import team
    identity = agent_identity(agent)
    instruction = identity + "\n" + instruction
    if agent["provider"] == "ollama":
        return team.ask(agent["model"], instruction, evidence, schema, on_text, temperature)
    if schema:
        instruction += "\nYalnızca gelen JSON şemasına uyan geçerli JSON döndür: " + json.dumps(schema, ensure_ascii=False)
    text = json.dumps(evidence, ensure_ascii=False)
    answer = (cli_answer(agent, instruction + "\n\n" + text) if agent["provider"] in {"codex", "claude"}
              else api_answer(agent, instruction, text))
    answer = answer.strip()
    if schema and answer.startswith("```"):
        answer = answer.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    if not answer:
        raise ValueError("Model boş yanıt verdi.")
    if on_text:
        on_text(answer)
    return answer


def agent_identity(agent):
    assignment = agent_roles.assignment(agent)
    role = assignment["role_label"] or "Özel görev"
    return (f"Projedeki adın {agent['name']}, çağrı etiketin @{agent['handle']}. "
            f"Dalın: {assignment['branch_label']}. Rolün: {role}. Görevin: {agent['duty']}")


def develop(agent, work, prompt, live):
    import team
    if agent["provider"] == "codex":
        args = ["codex", "-a", "never"]
        if os.name == "nt":
            args += ["-c", 'windows.sandbox="mxc"']
        args += ["exec", "--ignore-user-config", "--ephemeral", "--sandbox", "workspace-write", "--color", "never"]
        if agent["model"]:
            args += ["--model", agent["model"]]
        return team.stream_codex(args + ["-"], work, agent_identity(agent) + "\n\n" + prompt, live)
    paths = list(dict.fromkeys(team.git(work, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()))
    paths.sort(key=lambda name: (name not in prompt, len(name)))
    files = {}
    budget = 15_000
    for name in paths:
        path = work / name
        if budget <= 0 or len(files) == 15:
            break
        if (not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(work.resolve())
                or protected(name) or path.stat().st_size > min(budget, 5000)):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        files[name] = content
        budget -= len(content)
    schema = {"type": "object", "required": ["files"], "properties": {"files": {
        "type": "array", "items": {"type": "object", "required": ["path", "content"],
        "properties": {"path": {"type": "string"}, "content": {"type": ["string", "null"]}}}}}}
    raw = ask(agent, "Geliştirici olarak istenen dosya değişikliklerini JSON ile öner. "
              "Değişen dosyanın tamamını yaz. Silmek için content null yaz. İçeriği verilmeyen var olan "
              "dosyaya dokunma. Panel değişiklikleri izole kopyaya uygular ve test eder.",
              {"task": prompt, "files": files, "paths": paths[:500]}, schema)
    edits = json.loads(raw)["files"]
    if not isinstance(edits, list) or not 1 <= len(edits) <= 20:
        raise ValueError("Geliştirici uygulanabilir bir dosya önerisi vermedi.")
    changes = {}
    for edit in edits:
        name, content = edit["path"], edit["content"]
        if not isinstance(name, str) or protected(name) or (content is not None and not isinstance(content, str)):
            raise ValueError("Geliştiricinin önerdiği dosya yolu veya içeriği güvenli değil.")
        path = work / name
        if not path.resolve().is_relative_to(work.resolve()) or path.is_symlink() or path in changes:
            raise ValueError("Önerilen dosya proje kopyasına ait değil.")
        if (path.exists() and name not in files) or (content is None and name not in files):
            raise ValueError("Geliştirici okumadığı bir dosyayı değiştirmeyi önerdi.")
        changes[path] = content
    for path, content in changes.items():
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        live("append", f"Dosya değişikliği: {path.relative_to(work)}\n")
    return subprocess.CompletedProcess([agent["provider"]], 0, raw, "")


def protected(name):
    parts = name.split("/")
    return (not name or any(not part or part in {".", ".."} or part.endswith((".", " "))
            or part.casefold() in {".git", ".agents", ".codex", ".aws", "proje-hafizasi"}
            or re.fullmatch(r"(con|prn|aux|nul|com[1-9]|lpt[1-9])", part.split(".")[0], re.IGNORECASE)
            or not re.fullmatch(r"[\w .-]+", part) for part in parts))
