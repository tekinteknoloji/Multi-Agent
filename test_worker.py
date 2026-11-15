"""Run one round's test independently of the panel server process."""
import json
import sys
from pathlib import Path

import evidence
import team
from worker_process import round_lock, wait_for_worker


def completed_test(folder, base, diff, command, phase="05-check"):
    receipt = folder / f"{phase}-evidence.json"
    if not receipt.is_file():
        return None
    record = json.loads(receipt.read_text(encoding="utf-8"))
    output = (folder / f"{phase}.txt").read_text(encoding="utf-8")
    if (record["base_commit"] != base or record["diff_sha256"] != evidence.digest(diff)
            or record["command"] != command or record["output_sha256"] != evidence.digest(output)
            or type(record["exit_code"]) is not int):
        raise ValueError("Kayıtlı test sonucu bu Git farkı veya komutla eşleşmiyor.")
    return record["exit_code"], output


def worker(folder, work, base, command, phase="05-check"):
    if phase not in ("05-check", "05-integration"):
        raise ValueError("Test aşaması geçersiz.")
    with round_lock(folder / f"{phase}.lock"):
        diff = (folder / "04-change.diff").read_text(encoding="utf-8")
        if completed_test(folder, base, diff, command, phase) is not None:
            return
        team.git(work, "add", "-N", "--", ".")
        if (team.git(work, "rev-parse", "HEAD").strip() != base
                or team.git(work, "diff", "--no-ext-diff", "--no-textconv", "--binary", base, "--") != diff):
            raise ValueError("Test kopyası kaydedilen Git farkıyla eşleşmiyor.")
        result = team.run(command, work)
        output = result.stdout + "\n" + result.stderr
        team.save_round_file(folder, f"{phase}.txt", output)
        team.save_round_file(folder, f"{phase}-evidence.json", json.dumps(
            evidence.test_record(output, base=base, diff=diff, command=command, exit_code=result.returncode),
            ensure_ascii=False, indent=2))


def run_test(folder, work, base, diff, command, phase="05-check"):
    return wait_for_worker(Path(__file__).resolve(), [str(folder), str(work), base, json.dumps(command), phase],
                           folder / f"{phase}-worker.log", lambda: completed_test(folder, base, diff, command, phase))


if __name__ == "__main__":
    worker(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], json.loads(sys.argv[4]), *sys.argv[5:])
