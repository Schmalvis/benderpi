"""Run the deploy script's dry-run harness as part of the suite.

scripts/deploy_hey_bender.sh is the last gate before a model reaches the
device, and its refusal path (ship gates failed -> change nothing, restart
nothing) is exactly the branch nobody exercises by hand. The harness stubs
sudo, the HF download and the evaluation, so this touches no real device,
config, service or network.
"""
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(REPO, "scripts", "test_deploy_hey_bender.sh")


@pytest.mark.skipif(not os.path.exists(HARNESS), reason="harness missing")
def test_deploy_script_scenarios():
    r = subprocess.run(["bash", HARNESS], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "failed: 0" in r.stdout
