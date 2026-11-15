"""Develop in the round's Git copy independently of the panel server."""
import codecs
import json
import sys
from pathlib import Path

import providers
import team
from worker_process import round_lock, wait_for_worker


def worker(folder, work):
    with round_lock(folder / "03-development.lock"):
        receipt = folder / "03-development-result.json"
        if receipt.is_file():
            return
        request = json.loads((folder / "03-development-request.json").read_text(encoding="utf-8"))
        if team.git(work, "rev-parse", "HEAD").strip() != request["base"]:
            raise ValueError("Geliştirme kopyasının başlangıç commit’i değişti.")
        started = folder / "03-development-started"
        prompt = request["prompt"]
        if started.exists():
            prompt += ("\n\nÖnceki geliştirme işlemi yarıda kaldı. Mevcut Git kopyasındaki "
                       "değişiklikleri incele, tamamlanan işi koru ve yalnız kalan işi bitir.")
        started.touch()

        def emit(action, value):
            if action == "append":
                with (folder / "03-development-live.txt").open("ab") as log:
                    log.write(value.encode("utf-8"))

        if request["developer"]:
            result = providers.develop(request["developer"], work, prompt, emit)
        else:
            result = team.stream_codex(request["codex"], work, prompt, emit)
        team.save_round_file(folder, "03-codex.txt", result.stdout + "\n" + result.stderr)
        team.save_round_file(folder, receipt.name, json.dumps({"exit_code": result.returncode}))


def run_development(folder, work, emit):
    position = 0
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def refresh():
        nonlocal position
        path = folder / "03-development-live.txt"
        if not path.is_file():
            return
        with path.open("rb") as log:
            log.seek(position)
            chunk = log.read()
            position = log.tell()
        if chunk:
            emit("append", decoder.decode(chunk))

    def completed():
        path = folder / "03-development-result.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))["exit_code"]
        return None

    return wait_for_worker(Path(__file__).resolve(), [str(folder), str(work)],
                           folder / "03-development-worker.log", completed, refresh)


if __name__ == "__main__":
    worker(Path(sys.argv[1]), Path(sys.argv[2]))
