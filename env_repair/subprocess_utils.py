import json
import os
import subprocess
import sys
import threading


class OperationInterrupted(RuntimeError):
    def __init__(self, cmd, *, returncode=130):
        super().__init__("Operation interrupted")
        self.cmd = cmd
        self.returncode = returncode


def _as_cmd_exe(cmd):
    """
    Windows fallback: execute via cmd.exe so that .bat/.cmd shims in PATH work.
    """
    if not cmd:
        return cmd
    # list2cmdline is Windows-aware quoting.
    return ["cmd", "/d", "/c", subprocess.list2cmdline(cmd)]


def run_cmd_capture(cmd):
    """
    Run a command and capture stdout/stderr.
    Returns CompletedProcess-like tuple: (returncode, stdout, stderr).
    """
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        if os.name == "nt":
            res = subprocess.run(_as_cmd_exe(cmd), capture_output=True, text=True, check=False)
        else:
            raise
    except KeyboardInterrupt as e:
        raise OperationInterrupted(cmd, returncode=130) from e
    return res.returncode, res.stdout, res.stderr


def run_cmd_live(cmd):
    try:
        proc = subprocess.Popen(cmd)
    except FileNotFoundError:
        if os.name == "nt":
            proc = subprocess.Popen(_as_cmd_exe(cmd))
        else:
            raise
    try:
        return proc.wait()
    except KeyboardInterrupt as e:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        raise OperationInterrupted(cmd, returncode=130) from e


def _stream_reader(stream, sink, buffer):
    for line in iter(stream.readline, ""):
        if sink:
            sink.write(line)
            sink.flush()
        if buffer is not None:
            buffer.append(line)


def run_json_cmd(cmd, *, show_json_output):
    if not show_json_output:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            if os.name == "nt":
                res = subprocess.run(_as_cmd_exe(cmd), capture_output=True, text=True, check=False)
            else:
                raise
        except KeyboardInterrupt as e:
            raise OperationInterrupted(cmd, returncode=130) from e
        if res.returncode != 0:
            return None
        try:
            return json.loads(res.stdout)
        except ValueError:
            return None

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    except FileNotFoundError:
        if os.name == "nt":
            proc = subprocess.Popen(
                _as_cmd_exe(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        else:
            raise
    out_buf = []
    err_buf = []
    t_out = threading.Thread(target=_stream_reader, args=(proc.stdout, sys.stdout, out_buf))
    t_err = threading.Thread(target=_stream_reader, args=(proc.stderr, sys.stderr, err_buf))
    t_out.start()
    t_err.start()
    try:
        rc = proc.wait()
    except KeyboardInterrupt as e:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        raise OperationInterrupted(cmd, returncode=130) from e
    t_out.join()
    t_err.join()
    if rc != 0:
        return None
    try:
        return json.loads("".join(out_buf))
    except ValueError:
        return None
