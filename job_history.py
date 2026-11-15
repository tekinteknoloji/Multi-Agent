"""Read historical work from a project's portable board and reports."""
import json
import uuid

import discussion
import evidence
import team


APPLICATION_MESSAGES = (
    "Onaylanan değişiklik kaynak projenin çalışma dosyalarına uygulandı.",
    "Panelin uyguladığı değişiklik geri alındı.",
    "Geri alınan değişiklik kaynak projeye yeniden uygulandı.",
)


def project_runs(project, topics=None):
    runs = {}
    for topic in topics if topics is not None else discussion.project_topics(project):
        for message in topic["messages"]:
            run = message.get("run_id")
            if not run or not isinstance(run, str) or len(run) != 12 or any(c not in "0123456789abcdef" for c in run):
                continue
            entry = runs.setdefault(run, {"id": run, "topic": topic, "messages": []})
            entry["messages"].append(message)
    return runs


def run_title(entry):
    first = next((message for message in entry["messages"] if message["author"] == "Kullanıcı"), None)
    if first:
        text = first["text"].split("\n\n", 1)[-1].strip()
        if text:
            return text.splitlines()[0][:130]
    return entry["topic"]["title"]


def task_history(project):
    return [{"id": entry["id"], "title": run_title(entry), "time": entry["messages"][0]["time"]}
            for entry in sorted(project_runs(project).values(),
                                key=lambda item: item["messages"][0]["time"], reverse=True)]

def project_test_checks(project, runs):
    archive = discussion.memory_path(discussion.project_memory(project), "raporlar")
    checks = []
    for run, entry in runs.items():
        for number, phase in ((n, p) for n in range(1, team.MAX_CORRECTIONS + 2) for p in ("05-check", "05-integration")):
            prefix = f"round-{number:02}/"
            name = prefix + phase + ".txt"
            try:
                output_path = discussion.memory_path(archive, run, name)
                if not output_path.is_file():
                    continue
                output = output_path.read_text(encoding="utf-8")
                metadata = discussion.memory_path(archive, run, prefix + phase + "-evidence.json")
                record = json.loads(metadata.read_text(encoding="utf-8")) if metadata.is_file() else evidence.test_record(output)
            except (ValueError, OSError):
                continue
            if not isinstance(record, dict) or record.get("output_sha256") != evidence.digest(output):
                continue
            items = record.get("checks", [])
            if not isinstance(items, list):
                continue
            items = [item for item in items if isinstance(item, dict) and isinstance(item.get("name"), str)
                     and isinstance(item.get("result"), str)]
            names = [item["name"] for item in items]
            title = "Birleşik entegrasyon testi" if phase == "05-integration" else "Test sonucu"
            checks.extend({"run": run, "report": name, "name": item["name"], "result": item["result"],
                           "title": f"Tur {number} · {title}", "task_title": run_title(entry)}
                          for item in items if names.count(item["name"]) == 1)
    return checks


def read_criteria(archive, run):
    path = discussion.memory_path(archive, run, "01-criteria.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def evaluate_criteria(criteria, run, by_check):
    for criterion in criteria:
        criterion["report_run"] = criterion.get("report_run") or run
        linked = by_check.get((criterion["report_run"], criterion.get("report"), criterion.get("check")))
        criterion["report_available"] = linked is not None
        criterion["status"] = {"ok": "Doğrulandı", "FAIL": "Başarısız", "ERROR": "Başarısız"}.get(
            linked["result"] if linked else None, "Kanıt yok")
    return criteria


def relevant_criteria(project, topics, query):
    runs = project_runs(project, topics)
    archive = discussion.memory_path(discussion.project_memory(project), "raporlar")
    words = discussion.keywords(query) - {"kabul", "kriter", "kriteri", "kriterini", "test", "rapor", "raporu",
                                         "durumunu", "kayitli", "bagli"}
    groups = []
    for run, entry in runs.items():
        criteria = read_criteria(archive, run)
        if not criteria:
            continue
        title = run_title(entry)
        score = len(words & discussion.keywords(title + " " + " ".join(item["title"] for item in criteria)))
        if score or run in query:
            groups.append((run in query, score, entry["messages"][0]["time"], run, title, criteria))
    if not groups:
        return []
    linked_runs = {item.get("report_run") or run for _, _, _, run, _, criteria in groups for item in criteria}
    checks = project_test_checks(project, {run: entry for run, entry in runs.items() if run in linked_runs})
    by_check = {(item["run"], item["report"], item["name"]): item for item in checks}
    selected = []
    for _, _, _, run, title, criteria in sorted(groups, key=lambda item: item[:4], reverse=True):
        for item in evaluate_criteria(criteria, run, by_check):
            selected.append({**item, "task_run": run, "task_title": title})
            if len(selected) == 12:
                return selected
    return selected


def task_detail(project, run, filename=None):
    runs = project_runs(project)
    entry = runs.get(run)
    if entry is None:
        raise ValueError("Bu projede görev bulunamadı.")
    topic = entry["topic"]
    catalog = {report["path"]: report for report in discussion.reports(topic, team.ROOT / "runs") if report["run"] == run}
    archive = discussion.report_root(topic, team.ROOT / "runs")

    def read(name, limit):
        if name in catalog:
            return discussion.read_excerpt(discussion.memory_path(archive, run, name), limit)
        return ""

    if filename is not None:
        if filename not in catalog:
            raise ValueError("Bu göreve ait dosya bulunamadı.")
        return discussion.memory_path(archive, run, filename)

    task = read("01-task.md", 16_000) or next((item["text"] for item in entry["messages"]
                                               if item["author"] == "Kullanıcı"), "")
    team_text = read("00-team.json", 250_000)
    checks = project_test_checks(project, runs)
    rounds = []
    for number in range(1, team.MAX_CORRECTIONS + 2):
        prefix = f"round-{number:02}/"
        files = [report for name, report in catalog.items() if name.startswith(prefix)]
        if not files:
            continue
        coverage_path = prefix + "06-file-review.json"
        coverage = json.loads(discussion.memory_path(archive, run, coverage_path).read_text(encoding="utf-8")) if coverage_path in catalog else None
        rounds.append({"number": number, "files": files,
                       "diff": read(prefix + "04-change.diff", 10_000),
                       "test": read(prefix + "05-check.txt", 6000),
                       "integration": read(prefix + "05-integration.txt", 6000), "coverage": coverage,
                       "review": read(prefix + "06-review.json", 6000)})
    by_check = {(item["run"], item["report"], item["name"]): item for item in checks}
    criteria = evaluate_criteria(read_criteria(archive, run), run, by_check)
    return {
        "id": run, "title": run_title(entry), "task": task, "team": json.loads(team_text) if team_text else [],
        "base": read("01-base.txt", 100).strip(), "plan": read("02-plan-sent.md", 16_000) or read("02-plan.md", 16_000),
        "notes": [{"text": item["text"], "time": item["time"]} for item in entry["messages"]
                  if item["author"] == "Panel" and item["text"] not in APPLICATION_MESSAGES],
        "operations": [{"text": item["text"], "time": item["time"]} for item in entry["messages"]
                       if item["author"] == "Panel" and item["text"] in APPLICATION_MESSAGES],
        "rounds": rounds,
        "criteria": criteria, "test_checks": checks,
        "plan_reviews": [{**item, "text": read(name, 6000)} for name, item in catalog.items()
                         if name.endswith(".review.md")],
    }


def save_criterion(project, run, data):
    detail = task_detail(project, run)
    title, identity = data.get("title"), data.get("id")
    report, check = data.get("report"), data.get("check")
    report_run = data.get("report_run") or run
    if not isinstance(title, str) or not title.strip() or len(title) > 500 or "\0" in title:
        raise ValueError("En fazla 500 karakterlik bir kabul kriteri yaz.")
    if report is not None or check is not None:
        if not any(item["run"] == report_run and item["report"] == report and item["name"] == check
                   for item in detail["test_checks"]):
            raise ValueError("Bu projeye ait geçerli bir test sonucu seç.")
    criteria = detail["criteria"]
    if identity is None:
        if len(criteria) >= 50:
            raise ValueError("Bir göreve en fazla 50 kabul kriteri eklenebilir.")
        identity = uuid.uuid4().hex[:12]
        criteria.append({"id": identity})
    selected = next((item for item in criteria if item["id"] == identity), None)
    if selected is None:
        raise ValueError("Kabul kriteri bulunamadı.")
    selected.update(title=title.strip(), report=report, check=check, report_run=report_run)
    root = discussion.project_memory(project)
    discussion.write_json(discussion.memory_path(root, "raporlar", run, "01-criteria.json"),
                          [{key: item[key] for key in ("id", "title", "report", "check", "report_run")} for item in criteria])
    return task_detail(project, run)
