"""Run external tools (TeX engines, biber, poppler, ghostscript) with limits.

Every subprocess gets a wall-clock timeout, a CPU-time limit and a cap on the size of files it may
write. Where bubblewrap (``bwrap``) is available, tools run in a mount namespace in which the whole
file system is read-only except the run's work directory and TeX's own cache directories, with no
network. TeX engines additionally run with shell escape disabled and ``openout_any=p``; LuaTeX, whose
Lua ``io`` library ignores ``openout_any``, also loads a small init script that refuses file writes
outside the allowed directories, so documents stay contained even without bubblewrap.
"""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path

COMPILE_TIMEOUT = 120  # seconds, per TeX or biber run
TOOL_TIMEOUT = 120  # seconds, per converter run
MAX_FILE_BYTES = 512 * 1024 * 1024  # largest file a child may write
MAX_CONVERTER_MEMORY = 4 * 1024 * 1024 * 1024  # address-space cap for image converters

_disabled = {"bwrap": False}  # tests flip this to exercise the fallback


def _limits(cpu_seconds: int, max_bytes: int, max_memory: int | None):
    def apply():
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
            resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes, max_bytes))
            if max_memory:
                resource.setrlimit(resource.RLIMIT_AS, (max_memory, max_memory))
        except (ImportError, ValueError, OSError):
            pass

    return apply


@functools.lru_cache(maxsize=1)
def _bwrap() -> str | None:
    """Path of a working bubblewrap, or None (absent, or user namespaces not permitted)."""
    exe = shutil.which("bwrap")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--ro-bind", "/", "/", "--unshare-all", "--die-with-parent", "--", "/bin/true"],
                           capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return exe if r.returncode == 0 else None


def have_os_sandbox() -> bool:
    return not _disabled["bwrap"] and _bwrap() is not None


@functools.lru_cache(maxsize=1)
def cache_dirs() -> tuple[Path, ...]:
    """Directories TeX tools legitimately write to besides the work directory: font and format caches."""
    out: list[Path] = []
    try:
        r = subprocess.run(["kpsewhich", "-var-value", "TEXMFVAR"], capture_output=True, text=True, timeout=20)
        var = Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, subprocess.TimeoutExpired):
        var = None
    if var:
        var.mkdir(parents=True, exist_ok=True)
        out.append(var)
    fc = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "fontconfig"
    if fc.is_dir():
        out.append(fc)
    return tuple(out)


def _wrap(cmd: list[str], rw: list[Path]) -> list[str]:
    exe = _bwrap() if not _disabled["bwrap"] else None
    if not exe:
        return cmd
    args = [exe, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    for d in rw:
        d = Path(d)
        if d.exists():
            args += ["--bind", str(d), str(d)]
    args += ["--unshare-all", "--die-with-parent", "--new-session", "--"]
    return args + cmd


def run(cmd: list[str], cwd: Path | str, env: dict | None = None, timeout: int = TOOL_TIMEOUT,
        max_bytes: int = MAX_FILE_BYTES, max_memory: int | None = None,
        rw: list[Path] | None = None) -> subprocess.CompletedProcess:
    """subprocess.run with a timeout and resource limits. A timeout yields returncode -1, never an exception.

    With ``rw`` given, the command runs in the OS sandbox (when available) and may write only there."""
    if rw is not None:
        cmd = _wrap(cmd, list(rw) + list(cache_dirs()))
        if rw and have_os_sandbox():
            # the rest of the file system is read-only; give the tool a writable temp inside its own directory
            env = dict(env or os.environ)
            for var in ("TMPDIR", "TEMP", "TMP"):
                env[var] = str(rw[0])
    kw = dict(cwd=str(cwd), env=env, capture_output=True, text=True, errors="replace", timeout=timeout,
              stdin=subprocess.DEVNULL)
    if os.name == "posix":
        kw["preexec_fn"] = _limits(int(timeout), max_bytes, max_memory)
    try:
        return subprocess.run(cmd, **kw)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, -1, exc.stdout or "", f"timed out after {timeout} s")
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, -1, "", str(exc))


def tex_env(dirs: list[Path]) -> dict:
    """Environment for a TeX run: inputs searched in ``dirs``, no shell escape, no writes outside cwd."""
    env = dict(os.environ)
    path = "".join(f"{d}:" for d in dirs)
    for var in ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS"):
        env[var] = path + env.get(var, "")
    env["openout_any"] = "p"
    env["shell_escape"] = "f"
    return env


LUA_GUARD = r"""
-- texdocxdrafter: refuse Lua file writes outside the allowed directories (openout_any does not cover io.open)
local allowed = { @@ALLOWED@@ }
local lfs_ok, lfs = pcall(require, "lfs")
local cwd = (lfs_ok and lfs.currentdir()) or "."
local function absolute(p)
  if p:sub(1, 1) ~= "/" then p = cwd .. "/" .. p end
  p = p:gsub("/%./", "/"):gsub("/+", "/")
  while true do
    local q = p:gsub("/[^/]+/%.%./", "/", 1)
    if q == p then break end
    p = q
  end
  return p
end
local function permitted(name)
  if type(name) ~= "string" then return false end
  local p = absolute(name)
  for _, d in ipairs(allowed) do
    if p == d or p:sub(1, #d + 1) == d .. "/" then return true end
  end
  return false
end
local refuse = "texdocxdrafter: writing outside the work directory is not allowed"
local io_open, io_output, io_lines = io.open, io.output, io.lines
io.open = function(name, mode)
  if mode and mode:find("[wa+]") and not permitted(name) then return nil, refuse end
  return io_open(name, mode)
end
io.output = function(name)
  if type(name) == "string" and not permitted(name) then error(refuse) end
  return io_output(name)
end
local os_remove, os_rename = os.remove, os.rename
os.remove = function(name) if not permitted(name) then return nil, refuse end return os_remove(name) end
os.rename = function(a, b) if not (permitted(a) and permitted(b)) then return nil, refuse end return os_rename(a, b) end
if lfs_ok and lfs then
  for _, fn in ipairs({ "mkdir", "rmdir", "touch", "link" }) do
    local orig = lfs[fn]
    if orig then lfs[fn] = function(name, ...) if not permitted(name) then return nil, refuse end return orig(name, ...) end end
  end
end
if debug then debug = { traceback = debug.traceback, getinfo = debug.getinfo } end
"""


def lua_guard(work: Path, allowed: list[Path]) -> Path:
    def lit(p: Path) -> str:
        return '"' + str(p.resolve()).replace("\\", "\\\\").replace('"', '\\"') + '"'

    guard = work / ".texdocxdrafter-guard.lua"
    guard.write_text(LUA_GUARD.replace("@@ALLOWED@@", ", ".join(lit(p) for p in allowed)), encoding="utf-8")
    return guard


def tex_cmd(engine: str, work: Path, jobname: str, texfile: Path) -> list[str]:
    cmd = [engine, "-no-shell-escape", "-interaction=batchmode"]
    if "lua" in Path(engine).name:
        cmd.append(f"--lua={lua_guard(work, [work, *cache_dirs()])}")
    cmd += [f"-output-directory={work}", f"-jobname={jobname}", str(texfile)]
    return cmd


def compile_tex(engine: str, work: Path, jobname: str, texfile: Path, dirs: list[Path],
                timeout: int = COMPILE_TIMEOUT) -> subprocess.CompletedProcess:
    """One engine run inside ``work``; the document may read from ``dirs`` and write only into ``work``."""
    return run(tex_cmd(engine, work, jobname, texfile), cwd=work, env=tex_env(dirs), timeout=timeout, rw=[work])
