"""Run one round's test independently of the panel server process."""
import json
import sys
from pathlib import Path

import evidence
import team
from worker_process import round_lock, wait_for_worker


def completed_test(folder, base, diff, command):
    receipt = folder / "05-check-evidence.json"
    if not receipt.is_file():
        return None
    record = json.loads(receipt.read_text(encoding="utf-8"))
    output = (folder / "05-check.txt").read_text(encoding="utf-8")
    if (record["base_commit"] != base or record["diff_sha256"] != evidence.digest(diff)
            or record["command"] != command or record["output_sha256"] != evidence.digest(output)
            or type(record["exit_code"]) is not int):
        raise ValueError("Kayıtlı test sonucu bu Git farkı veya komutla eşleşmiyor.")
    return record["exit_code"], output


def worker(folder, work, base, command):
    with round_lock(folder / "05-check.lock"):
        diff = (folder / "04-change.diff").read_text(encoding="utf-8")
        if completed_test(folder, base, diff, command) is not None:
            return
        team.git(work, "add", "-N", "--", ".")
        if (team.git(work, "rev-parse", "HEAD").strip() != base
                or team.git(work, "diff", "--no-ext-diff", "--no-textconv", "--binary", base, "--") != diff):
            raise ValueError("Test kopyası kaydedilen Git farkıyla eşleşmiyor.")
        result = team.run(command, work)
        output = result.stdout + "\n" + result.stderr
        team.save_round_file(folder, "05-check.txt", output)
        team.save_round_file(folder, "05-check-evidence.json", json.dumps(
            evidence.test_record(output, base=base, diff=diff, command=command, exit_code=result.returncode),
            ensure_ascii=False, indent=2))


def run_test(folder, work, base, diff, command):
    return wait_for_worker(Path(__file__).resolve(), [str(folder), str(work), base, json.dumps(command)],
                           folder / "05-check-worker.log", lambda: completed_test(folder, base, diff, command))


if __name__ == "__main__":
    worker(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], json.loads(sys.argv[4]))
