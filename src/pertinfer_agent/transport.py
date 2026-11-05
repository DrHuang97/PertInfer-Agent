"""Provider-neutral JSON command adapter. No shell interpolation or default model."""
import json
import os
import signal
import subprocess


class CommandModel:
    """A trusted executable reads one JSON request and writes one JSON response.

    Configure provider credentials in the adapter's environment, not argv or
    model responses. The executable can use a model SDK of the user's choice.
    It is not an OS sandbox. Tool access policy is enforced by AgentRunner.
    """

    def __init__(self, command, *, timeout=120):
        if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
            raise ValueError("model command must be a nonempty argv list")
        if timeout <= 0:
            raise ValueError("positive model timeout required")
        self.command = command
        self.timeout = timeout

    def next_action(self, request):
        process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(json.dumps(request, ensure_ascii=False), timeout=self.timeout)
        except subprocess.TimeoutExpired:
            # Include adapter child processes in cancellation on the supported POSIX hosts.
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            raise TimeoutError("model command exceeded timeout") from None
        if process.returncode:
            # Provider stderr can contain credentials; do not copy it into artifacts.
            raise RuntimeError(f"model command exited with code {process.returncode}")
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            raise ValueError("model command must return one JSON object on stdout") from None
        if not isinstance(result, dict) or not isinstance(result.get("action"), dict):
            raise ValueError("model response requires an action object")
        return result
