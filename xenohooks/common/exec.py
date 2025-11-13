"""
Subprocess helpers with timeouts and structured results.
"""

from dataclasses import dataclass
from subprocess import PIPE, CalledProcessError, TimeoutExpired, run
from time import perf_counter
from typing import Any, Mapping


@dataclass(slots=True)
class CmdResult:
    code: int
    out: str
    err: str
    timed_out: bool
    elapsed_seconds: float

def run_command(
    cmd: list[str] | str,
    cwd: str | None = None,
    timeout_seconds: float | int | None = None,
    env: Mapping[str, str] | None = None,
    shell: bool | None = None,
) -> CmdResult:
    """
    Execute a command and capture output.

    - cmd: list[str] (preferred) or a single str; if str and shell is None, shell=True
    - returns: CmdResult with stdout/stderr, exit code, timeout flag, and elapsed time
    - never raises: wraps CalledProcessError into a CmdResult
    """
    if isinstance(cmd, str) and shell is None:
        shell = True
    if shell is None:
        shell = False

    start = perf_counter()
    try:
        proc = run(
            cmd,  # type: ignore[arg-type]
            cwd=cwd,
            env=dict(env) if env is not None else None,
            timeout=float(timeout_seconds) if timeout_seconds is not None else None,
            shell=shell,
            check=False,
            text=True,
            stdout=PIPE,
            stderr=PIPE,
        )
        elapsed = perf_counter() - start
        return CmdResult(
            code=int(proc.returncode),
            out=proc.stdout or "",
            err=proc.stderr or "",
            timed_out=False,
            elapsed_seconds=elapsed,
        )
    except TimeoutExpired as e:
        elapsed = perf_counter() - start
        return CmdResult(
            code=-1,
            out=e.stdout or "",
            err=e.stderr or "",
            timed_out=True,
            elapsed_seconds=elapsed,
        )
    except CalledProcessError as e:
        elapsed = perf_counter() - start
        return CmdResult(
            code=int(e.returncode),
            out=e.stdout.decode() if isinstance(e.stdout, (bytes, bytearray)) else (e.stdout or ""),
            err=e.stderr.decode() if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or ""),
            timed_out=False,
            elapsed_seconds=elapsed,
        )
    except Exception as e:
        elapsed = perf_counter() - start
        return CmdResult(
            code=-2,
            out="",
            err=str(e),
            timed_out=False,
            elapsed_seconds=elapsed,
        )
