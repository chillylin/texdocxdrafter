r"""Run external tools (TeX engines, biber, poppler, ghostscript) with limits.

Every subprocess gets a wall-clock timeout, a CPU-time limit and a cap on the size of files it may
write, and a filtered environment. Where bubblewrap (``bwrap``) is available, tools run in a mount
namespace that contains only the system and TeX directories (read-only), the document's input
directories (read-only) and the run's work directory (writable), with no network. TeX's own cache
(TEXMFVAR, where LuaTeX keeps Lua font caches and formats may live) is never writable: each run gets a
throwaway TEXMFVAR inside its work directory, so nothing a document plants survives the run. TeX
engines additionally run with shell escape disabled and ``openout_any=p``; LuaTeX, whose Lua ``io``
library ignores ``openout_any``, also loads a small init script that refuses file writes outside the
allowed directories.

Without bubblewrap a TeX document can still read any file the user can read (``\input{/abs/path}``,
``\openin``; TEXINPUTS only steers searching) and LuaTeX documents run Lua code. So ``compile_tex`` is
the single policy boundary: without an OS sandbox it refuses to run, and the pipeline instead writes the
side documents for the user to compile with their own TeX setup (see ``sidecompile``).
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


@functools.lru_cache(maxsize=4)
def _kpse_var(name: str) -> str:
    try:
        r = subprocess.run(["kpsewhich", "-var-value", name], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


@functools.lru_cache(maxsize=1)
def ro_cache_dirs() -> tuple[Path, ...]:
    """The user's TeX and fontconfig caches: visible to a run read-only, so existing font databases are
    found, but never writable (a document could plant Lua code or a format file that later runs outside)."""
    out: list[Path] = []
    var = _kpse_var("TEXMFVAR")
    if var and Path(var).is_dir():
        out.append(Path(var))
    fc = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "fontconfig"
    if fc.is_dir():
        out.append(fc)
    return tuple(out)


def throwaway_texmfvar(work: Path) -> dict:
    """Environment that points TEXMFVAR at a fresh directory inside ``work``; the real caches stay readable
    through TEXMFCACHE, so LuaTeX's font database is still found but anything written lands in ``work``."""
    var = Path(work) / ".texmfvar"
    var.mkdir(parents=True, exist_ok=True)
    real = _kpse_var("TEXMFCACHE").replace(";", ":")
    return {"TEXMFVAR": str(var), "TEXMFCACHE": str(var) + (":" + real if real else "")}


SYSTEM_RO = ("/usr", "/lib", "/lib32", "/lib64", "/bin", "/sbin", "/etc", "/opt", "/var/lib/texmf",
             "/var/lib/ghostscript", "/var/cache/fontconfig", "/run/current-system")
ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "TERM", "TZ", "SOURCE_DATE_EPOCH")
ENV_PREFIXES = ("LC_", "TEXMF", "KPATHSEA", "FONTCONFIG", "OSFONTDIR", "TEXINPUTS", "BIBINPUTS", "BSTINPUTS",
                "openout_any", "shell_escape", "TMPDIR", "TEMP", "TMP", "XDG_CACHE_HOME", "XDG_CONFIG_HOME")


@functools.lru_cache(maxsize=1)
def tex_dirs() -> tuple[Path, ...]:
    """TeX trees and user font directories a compile may need to read."""
    out: list[Path] = []
    for var in ("TEXMFROOT", "TEXMFDIST", "TEXMFLOCAL", "TEXMFSYSVAR", "TEXMFSYSCONFIG", "TEXMFHOME", "TEXMFCONFIG"):
        try:
            r = subprocess.run(["kpsewhich", "-var-value", var], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for item in r.stdout.strip().split(":"):
            if item and Path(item).is_dir():
                out.append(Path(item))
    home = Path.home()
    for d in (home / ".fonts", home / ".local" / "share" / "fonts", home / ".config" / "fontconfig"):
        if d.is_dir():
            out.append(d)
    return tuple(out)


def filtered_env(env: dict | None) -> dict:
    """Only the variables a TeX or converter run needs; nothing else from the caller's environment leaks in."""
    src = env if env is not None else os.environ
    return {k: v for k, v in src.items() if k in ENV_KEEP or k.startswith(ENV_PREFIXES)}


def _wrap(cmd: list[str], rw: list[Path], ro: list[Path]) -> list[str]:
    """bubblewrap: system, TeX and cache directories read-only, the inputs read-only, ``rw`` writable, nothing else visible."""
    exe = _bwrap() if not _disabled["bwrap"] else None
    if not exe:
        return cmd
    args = [exe, "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", "/var/tmp"]
    for d in SYSTEM_RO:
        if Path(d).is_dir():
            args += ["--ro-bind", d, d]
    for d in list(tex_dirs()) + [Path(x) for x in ro]:
        if d.exists():
            args += ["--ro-bind", str(d), str(d)]
    for d in rw:
        d = Path(d)
        if d.exists():
            args += ["--bind", str(d), str(d)]
    args += ["--unshare-all", "--die-with-parent", "--new-session", "--"]
    return args + cmd


def run(cmd: list[str], cwd: Path | str, env: dict | None = None, timeout: int = TOOL_TIMEOUT,
        max_bytes: int = MAX_FILE_BYTES, max_memory: int | None = None,
        rw: list[Path] | None = None, ro: list[Path] | None = None) -> subprocess.CompletedProcess:
    """subprocess.run with a timeout and resource limits. A timeout yields returncode -1, never an exception.

    With ``rw`` given the command is sandboxed (when bubblewrap is available): it sees the system and TeX
    directories, the ``ro`` inputs and the ``rw`` directories, and may write only to the latter."""
    env = filtered_env(env)
    if rw is not None:
        cmd = _wrap(cmd, list(rw), list(ro or []) + list(ro_cache_dirs()))
        if rw:
            for var in ("TMPDIR", "TEMP", "TMP"):
                env[var] = str(rw[0])
            env.update(throwaway_texmfvar(Path(rw[0])))
    kw = dict(cwd=str(cwd), env=env, capture_output=True, text=True, errors="replace", timeout=timeout,
              stdin=subprocess.DEVNULL)
    if os.name == "posix":
        kw["preexec_fn"] = _limits(int(timeout), max_bytes, max_memory)
    try:
        return subprocess.run(cmd, **kw)
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return subprocess.CompletedProcess(cmd, -1, partial, f"timed out after {timeout} s")
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
local function absolute(p)
  local cwd = (lfs_ok and lfs.currentdir()) or "."
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
  for _, fn in ipairs({ "mkdir", "rmdir", "touch" }) do
    local orig = lfs[fn]
    if orig then lfs[fn] = function(name, ...) if not permitted(name) then return nil, refuse end return orig(name, ...) end end
  end
  local lfs_link = lfs.link
  if lfs_link then
    lfs.link = function(old, new, ...)
      if not (permitted(old) and permitted(new)) then return nil, refuse end
      return lfs_link(old, new, ...)
    end
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
        cmd.append(f"--lua={lua_guard(work, [work])}")
    cmd += [f"-output-directory={work}", f"-jobname={jobname}", str(texfile)]
    return cmd


class SandboxPolicyError(RuntimeError):
    """A side compile was requested while no OS sandbox is available."""


def compile_tex(engine: str, work: Path, jobname: str, texfile: Path, dirs: list[Path],
                timeout: int = COMPILE_TIMEOUT, check_policy: bool = True) -> subprocess.CompletedProcess:
    """One engine run inside ``work``; the document may read from ``dirs`` and write only into ``work``.

    This is the policy boundary for every side compile: it raises SandboxPolicyError unless an OS sandbox
    is active, whatever path the call came in by. ``check_policy=False`` exists for the tests of the
    in-TeX restrictions (openout_any, the Lua guard), which are defence in depth inside the sandbox."""
    reason = sandbox_missing()
    if reason and check_policy:
        raise SandboxPolicyError(reason)
    return run(tex_cmd(engine, work, jobname, texfile), cwd=work, env=tex_env(dirs), timeout=timeout, rw=[work], ro=dirs)


def is_luatex(engine: str) -> bool:
    return "lua" in Path(engine).name


def sandbox_missing() -> str | None:
    """None when side compiles may run here; otherwise why the tool will not start a TeX engine itself."""
    if have_os_sandbox():
        return None
    return ("no OS sandbox (bubblewrap) is available, and a TeX document can read any file you can read, "
            "so the tool does not run a TeX engine itself")
