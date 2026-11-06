"""python -m audit.run_ablations [output.json]"""
import json
import hashlib
import subprocess
from pathlib import Path
import sys
import pytest
from audit.test_ablations import RESULTS


def main():
    RESULTS.clear()
    code = pytest.main(["-q", "audit/test_ablations.py"])
    if code:
        raise SystemExit(code)
    result = {"schema_version":1,"kind":"paired deterministic mechanism probes",
              "baseline_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
              "working_tree_modified":bool(subprocess.check_output(["git","status","--porcelain"],text=True).strip()),
              "source_sha256":{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in [
                  "services/jobs.py","services/state.py","agent/runtime.py","agent/contracts.py","audit/test_ablations.py"]},
              "cases":RESULTS,
              "limitations":["No production accuracy, real model quality, hardware timing or Broker delivery claim.",
                             "Each row uses its own controlled fixture; outcomes are not one shared score."]}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
