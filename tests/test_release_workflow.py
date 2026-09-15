from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).parents[1]
CANDIDATE_SHA = "a" * 40
OTHER_SHA = "b" * 40


FAKE_GH = r'''#!/bin/sh
set -eu
if [ -n "${GH_CALL_LOG:-}" ]; then
  printf '%s\n' "$*" >> "$GH_CALL_LOG"
fi
for arg in "$@"; do
  case "$arg" in
    */statuses*)
      if [ "${GH_FAIL:-}" = statuses ]; then
        echo "synthetic status API failure" >&2
        exit 42
      fi
      printf '%s\n' "${STATUS_JSON:?}"
      exit 0
      ;;
    */deployments*)
      if [ "${GH_FAIL:-}" = deployments ]; then
        echo "synthetic deployment API failure" >&2
        exit 41
      fi
      printf '%s\n' "${DEPLOYMENTS_JSON:?}"
      exit 0
      ;;
  esac
done
echo "unexpected gh api endpoint" >&2
exit 40
'''


FAKE_AWS = r'''#!/bin/sh
set -eu
case "$*" in
  *describe-repositories*)
    if [ "${AWS_FAIL:-}" = repositories ]; then
      echo "synthetic repository API failure" >&2
      exit 41
    fi
    printf '%s\n' "${TAG_MUTABILITY:?}"
    ;;
  *describe-images*)
    printf '%s\n' 'sha256:synthetic'
    ;;
  *)
    echo "unexpected aws endpoint" >&2
    exit 40
    ;;
esac
'''


def _workflow_step(name: str) -> tuple[dict, str]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["release"]["steps"]
        if step.get("name") == name
    )
    return workflow, step["run"]


def _deployment(identifier: int, sha: str, minute: int) -> dict:
    return {
        "id": identifier,
        "sha": sha,
        "environment": "staging",
        "created_at": f"2026-09-15T00:{minute:02d}:00Z",
    }


def _status(identifier: int, state: str, minute: int) -> dict:
    return {
        "id": identifier,
        "state": state,
        "created_at": f"2026-09-15T00:{minute:02d}:00Z",
    }


STAGING_DEPLOYMENT = _deployment(22, CANDIDATE_SHA, 1)
CASES = (
    pytest.param(
        [
            _deployment(21, CANDIDATE_SHA, 0),
            STAGING_DEPLOYMENT,
            _deployment(99, OTHER_SHA, 2),
        ],
        [_status(2, "success", 1), _status(1, "in_progress", 0)],
        "",
        True,
        "",
        id="success",
    ),
    pytest.param(
        [_deployment(21, CANDIDATE_SHA, 0), STAGING_DEPLOYMENT],
        [_status(2, "failure", 1), _status(1, "success", 0)],
        "",
        False,
        "Latest staging deployment status",
        id="failed-latest",
    ),
    pytest.param(
        [], [], "", False, "No staging deployment exists", id="missing"
    ),
    pytest.param(
        [_deployment(22, OTHER_SHA, 1)],
        [],
        "",
        False,
        "No staging deployment exists",
        id="wrong-candidate",
    ),
    pytest.param(
        [],
        [],
        "deployments",
        False,
        "Unable to read staging deployments",
        id="deployment-api-failure",
    ),
    pytest.param(
        [STAGING_DEPLOYMENT],
        [],
        "statuses",
        False,
        "Unable to read status for staging deployment",
        id="status-api-failure",
    ),
)

ECR_CASES = (
    pytest.param("IMMUTABLE", "", True, "", id="success"),
    pytest.param("MUTABLE", "", False, "not immutable", id="mutable"),
    pytest.param(
        "",
        "repositories",
        False,
        "Unable to verify ECR tag mutability",
        id="api-failure",
    ),
)


@pytest.mark.parametrize(
    "deployments,statuses,gh_fail,succeeds,expected_error", CASES
)
def test_production_gate_executes_workflow_step_with_mocked_github_api(
    tmp_path: Path,
    deployments: list[dict],
    statuses: list[dict],
    gh_fail: str,
    succeeds: bool,
    expected_error: str,
) -> None:
    workflow, script = _workflow_step("Verify successful staging deployment")
    assert workflow["permissions"]["deployments"] == "read"
    assert "ImageTagMutability: IMMUTABLE" in (
        ROOT / "infra/aws/foundation.yml"
    ).read_text()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    call_log = tmp_path / "gh.calls"
    environment = os.environ.copy()
    environment.update(
        {
            "CANDIDATE_SHA": CANDIDATE_SHA,
            "REPOSITORY": "czeyik/AI-Customer-Service",
            "GH_TOKEN": "test-token",
            "DEPLOYMENTS_JSON": json.dumps(deployments),
            "STATUS_JSON": json.dumps(statuses),
            "GH_FAIL": gh_fail,
            "GH_CALL_LOG": str(call_log),
            "PATH": f"{bin_dir}:{environment['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    if succeeds:
        assert result.returncode == 0, result.stderr
        assert "Staging deployment 22 succeeded" in result.stdout
        calls = call_log.read_text().splitlines()
        assert len(calls) == 2
        assert f"sha={CANDIDATE_SHA}&environment=staging" in calls[0]
        assert "deployments/22/statuses?per_page=100" in calls[1]
    else:
        assert result.returncode != 0
        assert expected_error in result.stderr


@pytest.mark.parametrize(
    "tag_mutability,aws_fail,succeeds,expected_error", ECR_CASES
)
def test_ecr_mutability_check_executes_workflow_step(
    tmp_path: Path,
    tag_mutability: str,
    aws_fail: str,
    succeeds: bool,
    expected_error: str,
) -> None:
    _, script = _workflow_step("Resolve immutable digests")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(FAKE_AWS)
    aws.chmod(0o755)
    output = tmp_path / "github-output"
    environment = os.environ.copy()
    environment.update(
        {
            "RELEASE_REPOSITORY": "123456789012.dkr.ecr.ap-southeast-5.amazonaws.com/dudu-support",
            "RELEASE_SHA": CANDIDATE_SHA,
            "TAG_MUTABILITY": tag_mutability,
            "AWS_FAIL": aws_fail,
            "GITHUB_OUTPUT": str(output),
            "PATH": f"{bin_dir}:{environment['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    if succeeds:
        assert result.returncode == 0, result.stderr
        resolved = output.read_text()
        image = "123456789012.dkr.ecr.ap-southeast-5.amazonaws.com/dudu-support@sha256:synthetic"
        assert f"app={image}" in resolved
        assert f"clamav={image}" in resolved
    else:
        assert result.returncode != 0
        assert expected_error in result.stderr
