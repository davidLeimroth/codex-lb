"""Apply the tracked source delta to the preserved runtime, refusing any context mismatch."""

from __future__ import annotations

import re
from pathlib import Path

patch = Path("/tmp/claude-source-stream-errors.patch").read_text().splitlines(keepends=True)
index = 0
allowed = {"app/modules/model_sources/forwarding.py", "app/modules/proxy/api.py"}
while index < len(patch):
    if not patch[index].startswith("--- a/"):
        index += 1
        continue
    name = patch[index][6:].strip()
    if name not in allowed or patch[index + 1].strip() != "+++ b/" + name:
        raise RuntimeError("Unexpected patch target")
    index += 2
    path = Path("/app") / name
    lines = path.read_text().splitlines(keepends=True)
    while index < len(patch) and not patch[index].startswith("diff --git"):
        match = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", patch[index])
        if not match:
            raise RuntimeError("Unexpected patch syntax")
        start = int(match[1]) - 1
        index += 1
        before, after = [], []
        while index < len(patch) and patch[index][:1] in {" ", "-", "+"}:
            line = patch[index]
            if line[0] != "+":
                before.append(line[1:])
            if line[0] != "-":
                after.append(line[1:])
            index += 1
        # Production carries native-thread fixes elsewhere in the file. Locate exact
        # context rather than assuming upstream line numbers or permitting fuzzy patches.
        candidates = [i for i in range(len(lines) - len(before) + 1) if lines[i : i + len(before)] == before]
        if len(candidates) != 1:
            raise RuntimeError(f"Runtime context mismatch in {name} at upstream line {start + 1}")
        at = candidates[0]
        lines[at : at + len(before)] = after
    path.write_text("".join(lines))
    print(f"Applied Claude source stream delta to {name}")
