"""Run approved build steps under the Controller's existing MXC and Job fence."""
from time import monotonic

from .lease import Blocked
from .mxc_host_probe import OwnedMxcStopped, _args, _observe
from .replica_evidence import fingerprint


def limits(seconds):
    return {"network": "OFF", "max_seconds": seconds, "memory_bytes": 512 * 1024 * 1024,
            "max_processes": 8, "output_limit": 16384}


class BuildExecution:
    def __init__(self, launcher, tool, work, output, environment, policy, check):
        self.launcher = launcher
        self.tool = tool
        self.work = work
        self.output = output
        self.environment = environment
        self.policy = policy
        self.deadline = monotonic() + policy["max_seconds"]
        self.check = check

    def step(self, name, arguments, *, interact=None):
        try:
            self.check()
            remaining = self.deadline - monotonic()
            if remaining <= 0:
                raise Blocked("Build execution time budget exhausted")
        except Blocked as error:
            raise OwnedMxcStopped(str(error)) from error
        scratch = self.output / name
        scratch.mkdir()
        report = _observe(
            self.launcher, _args(self.tool, *arguments), self.work, scratch, self.environment,
            seconds=remaining, memory=self.policy["memory_bytes"], check=self.check,
            output_limit=self.policy["output_limit"], encoding=self.policy.get("encoding", "ascii"),
            interact=interact,
        )
        receipt = {key: report[key] for key in ("exitCode", "job_active", "job_total")}
        for stream in ("stdout", "stderr"):
            receipt[stream + "_sha256"] = fingerprint(
                scratch / ("probe." + stream), self.policy["output_limit"],
            )["sha256"]
        return report, receipt
