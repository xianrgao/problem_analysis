"""Executor replacement example for a company isolation environment.

settings.json::

    "executor": {"factory": "examples.adapters.isolated_executor:create"}

The factory receives ``(settings, variables)`` and returns an object with the
``ProcessRunner`` contract: ``execute(session_id, code, inputs, producer, scripts)``,
``execute_shell(session_id, shell, executable, script)`` and ``kill(session_id)``.
Inputs are immutable variable snapshots; outputs are staged and published by
the host only after the whole execution succeeds.
"""
from __future__ import annotations

from line_engineer.execution.runner import ProcessRunner


class IsolatedRunner(ProcessRunner):
    """Example: run analysis code with a dedicated interpreter and a reduced environment."""

    ALLOWED_ENV = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONPATH", "PYTHONIOENCODING", "LANG", "HOME", "USERPROFILE")

    def environment(self):
        env = super().environment()
        return {key: value for key, value in env.items() if key in self.ALLOWED_ENV}


def create(settings, variables):
    return IsolatedRunner(settings, variables)
