"""Test execution records and source-backed summaries."""
import hashlib
import re
from datetime import datetime, timezone


def test_record(output, *, base=None, diff=None, command=None, exit_code=None):
    return {
        "version": 1,
        "kind": "test_execution",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "base_commit": base,
        "diff_sha256": digest(diff) if diff is not None else None,
        "command": command,
        "exit_code": exit_code,
        "output_sha256": digest(output),
        "checks": [
            {"name": name, "result": status}
            for name, status in re.findall(
                r"(?m)^(test\w*) \([^\r\n]+\) \.\.\. (ok|FAIL|ERROR|skipped[^\r\n]*)$", output
            )
        ],
    }


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def report_answer(ids, records):
    """Render only statements backed by the selected execution records."""
    selected = {item for item in ids if isinstance(item, str)}
    lines = []
    for record in records:
        if record["id"] not in selected:
            continue
        line = f"- {record['run']} · {record['path']}"
        code = record.get("exit_code")
        if code is not None:
            line += f" · Çıkış kodu: {code}"
        if record.get("observations"):
            line += "\n  Doğrulanan davranış: " + record["observations"]
        elif record.get("checks"):
            line += "\n  Kaydedilen kontroller: " + ", ".join(
                f"{item['name']} ({item['result']})" for item in record["checks"][:8]
            )
        elif record.get("excerpt"):
            line += "\n  Test çıktısı: " + record["excerpt"]
        if record.get("outside_scope"):
            line += "\n  Test kapsamı dışında: " + record["outside_scope"]
        lines.append(line)
    return "\n".join(lines) or "Bu soruyu doğrulayan bir test raporu seçilemedi."
