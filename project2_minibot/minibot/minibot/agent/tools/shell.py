"""Shell execution tool."""

import asyncio
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from minibot.agent.tools.base import Tool, tool_parameters
from minibot.agent.tools.sandbox import wrap_command
from minibot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from minibot.config.paths import get_media_dir

_IS_WINDOWS = sys.platform == "win32"

_DEFAULT_ALLOW_PATTERNS = [
    # Common read-only / diagnostic commands (incl. Windows cmd / PowerShell idioms).
    r"\b(?:echo|cat|head|tail|wc|grep|ls|pwd|printenv|dir|curl|wget|sleep|python|python3|type|more|findstr)\b",
    r"\bGet-Content\b",
    r"\bSelect-String\b",
]

# Extra exec whitelist entries merged (setdefault) for *subagents only* when the user
# sets tools.exec.allowedCommands — avoids Windows models failing on Get-Content etc.
_SUBAGENT_EXTRA_ALLOWED_COMMANDS: dict[str, list[str]] = {
    # cmd.exe (Windows default shell for ExecTool._spawn)
    "type": [],
    "more": [],
    "findstr": [],
    # PowerShell-style first token (some models emit these even though cmd is the shell;
    # cmd may resolve powershell.exe from PATH for chained invocations).
    "Get-Content": [],
    "Select-String": [],
    # Explicit PowerShell launcher: arguments must mention a read-style cmdlet.
    "powershell": [
        r"(?i)\bGet-Content\b",
        r"(?i)\bSelect-String\b",
        r"(?i)\bGet-ChildItem\b",
    ],
    "pwsh": [
        r"(?i)\bGet-Content\b",
        r"(?i)\bSelect-String\b",
        r"(?i)\bGet-ChildItem\b",
    ],
}


def merge_subagent_exec_allowed_commands(
    user: dict[str, list[str]] | None,
) -> dict[str, list[str]] | None:
    """Extend a user ``allowed_commands`` map with read-only Windows / shell helpers.

    When *user* is ``None``, exec runs without the per-command whitelist (existing
    behavior). When *user* is set, subagents would otherwise often hit
    ``Command 'Get-Content' is not in the allowed_commands whitelist``; we only add
    keys that are **missing** so explicit user entries always win.
    """
    if user is None:
        return None
    merged: dict[str, list[str]] = {k: list(v) for k, v in user.items()}
    for cmd, patterns in _SUBAGENT_EXTRA_ALLOWED_COMMANDS.items():
        merged.setdefault(cmd, list(patterns))
    return merged


@tool_parameters(
    tool_parameters_schema(
        command=StringSchema("The shell command to execute"),
        working_dir=StringSchema("Optional working directory for the command"),
        timeout=IntegerSchema(
            60,
            description=(
                "Timeout in seconds. Increase for long-running commands "
                "like compilation or installation (default 60, max 600)."
            ),
            minimum=1,
            maximum=600,
        ),
        required=["command"],
    )
)
class ExecTool(Tool):
    """Tool to execute shell commands."""

    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        sandbox: str = "",
        path_append: str = "",
        allowed_env_keys: list[str] | None = None,
        allowed_commands: dict[str, list[str]] | None = None,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.sandbox = sandbox
        self.allowed_commands = allowed_commands
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",          # rm -r, rm -rf, rm -fr
            r"\bdel\s+/[fq]\b",              # del /f, del /q
            r"\brmdir\s+/s\b",               # rmdir /s
            r"(?:^|[;&|]\s*)format\b",       # format (as standalone command only)
            r"\b(mkfs|diskpart)\b",          # disk operations
            r"\bdd\s+if=",                   # dd
            r">\s*/dev/sd",                  # write to disk
            r"\b(shutdown|reboot|poweroff)\b",  # system power
            r":\(\)\s*\{.*\};\s*:",          # fork bomb
            # Block writes to minibot internal state files (#2989).
            # history.jsonl / .dream_cursor are managed by append_history();
            # direct writes corrupt the cursor format and crash /dream.
            r">>?\s*\S*(?:history\.jsonl|\.dream_cursor)",            # > / >> redirect
            r"\btee\b[^|;&<>]*(?:history\.jsonl|\.dream_cursor)",     # tee / tee -a
            r"\b(?:cp|mv)\b(?:\s+[^\s|;&<>]+)+\s+\S*(?:history\.jsonl|\.dream_cursor)",  # cp/mv target
            r"\bdd\b[^|;&<>]*\bof=\S*(?:history\.jsonl|\.dream_cursor)",  # dd of=
            r"\bsed\s+-i[^|;&<>]*(?:history\.jsonl|\.dream_cursor)",  # sed -i
        ]
        self.allow_patterns = allow_patterns or []
        if allow_patterns is None:
            self.allow_patterns = list(_DEFAULT_ALLOW_PATTERNS)
        self.restrict_to_workspace = restrict_to_workspace
        self.path_append = path_append
        self.allowed_env_keys = allowed_env_keys or []

    @property
    def name(self) -> str:
        return "exec"

    _MAX_TIMEOUT = 600
    _MAX_OUTPUT = 10_000

    @property
    def description(self) -> str:
        return (
            "Execute a shell command and return its output. "
            "Prefer read_file/write_file/edit_file over cat/echo/sed, "
            "and grep/glob over shell find/grep. "
            "Use -y or --yes flags to avoid interactive prompts. "
            "Output is truncated at 10 000 chars; timeout defaults to 60s."
        )

    @property
    def exclusive(self) -> bool:
        return True

    async def execute(
        self, command: str, working_dir: str | None = None,
        timeout: int | None = None, **kwargs: Any,
    ) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()

        # Prevent an LLM-supplied working_dir from escaping the configured
        # workspace when restrict_to_workspace is enabled (#2826). Without
        # this, a caller can pass working_dir="/etc" and then all absolute
        # paths under /etc would pass the _guard_command check that anchors
        # on cwd.
        if self.restrict_to_workspace and self.working_dir:
            try:
                requested = Path(cwd).expanduser().resolve()
                workspace_root = Path(self.working_dir).expanduser().resolve()
            except Exception:
                return "Error: working_dir could not be resolved"
            if requested != workspace_root and workspace_root not in requested.parents:
                return "Error: working_dir is outside the configured workspace"

        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error

        if self.sandbox:
            if _IS_WINDOWS:
                logger.warning(
                    "Sandbox '{}' is not supported on Windows; running unsandboxed",
                    self.sandbox,
                )
            else:
                workspace = self.working_dir or cwd
                command = wrap_command(self.sandbox, command, workspace, cwd)
                cwd = str(Path(workspace).resolve())

        effective_timeout = min(timeout or self.timeout, self._MAX_TIMEOUT)
        env = self._build_env()

        if self.path_append:
            if _IS_WINDOWS:
                env["PATH"] = env.get("PATH", "") + ";" + self.path_append
            else:
                command = f'export PATH="$PATH:{self.path_append}"; {command}'

        try:
            process = await self._spawn(command, cwd, env)

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=effective_timeout,
                )
            except asyncio.TimeoutError:
                await self._kill_process(process)
                return f"Error: Command timed out after {effective_timeout} seconds"
            except asyncio.CancelledError:
                await self._kill_process(process)
                raise

            output_parts = []

            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))

            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")

            output_parts.append(f"\nExit code: {process.returncode}")

            result = "\n".join(output_parts) if output_parts else "(no output)"

            max_len = self._MAX_OUTPUT
            if len(result) > max_len:
                half = max_len // 2
                result = (
                    result[:half]
                    + f"\n\n... ({len(result) - max_len:,} chars truncated) ...\n\n"
                    + result[-half:]
                )

            return result

        except Exception as e:
            return f"Error executing command: {str(e)}"

    @staticmethod
    async def _spawn(
        command: str, cwd: str, env: dict[str, str],
    ) -> asyncio.subprocess.Process:
        """Launch *command* in a platform-appropriate shell."""
        if _IS_WINDOWS:
            comspec = env.get("COMSPEC", os.environ.get("COMSPEC", "cmd.exe"))
            return await asyncio.create_subprocess_exec(
                comspec, "/c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        bash = shutil.which("bash") or "/bin/bash"
        return await asyncio.create_subprocess_exec(
            bash, "-l", "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

    @staticmethod
    async def _kill_process(process: asyncio.subprocess.Process) -> None:
        """Kill a subprocess and reap it to prevent zombies."""
        process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        finally:
            if not _IS_WINDOWS:
                try:
                    os.waitpid(process.pid, os.WNOHANG)
                except (ProcessLookupError, ChildProcessError) as e:
                    logger.debug("Process already reaped or not found: {}", e)

    def _build_env(self) -> dict[str, str]:
        """Build a minimal environment for subprocess execution.

        On Unix, only HOME/LANG/TERM are passed; ``bash -l`` sources the
        user's profile which sets PATH and other essentials.

        On Windows, ``cmd.exe`` has no login-profile mechanism, so a curated
        set of system variables (including PATH) is forwarded.  API keys and
        other secrets are still excluded.
        """
        if _IS_WINDOWS:
            sr = os.environ.get("SYSTEMROOT", r"C:\Windows")
            env = {
                "SYSTEMROOT": sr,
                "COMSPEC": os.environ.get("COMSPEC", f"{sr}\\system32\\cmd.exe"),
                "USERPROFILE": os.environ.get("USERPROFILE", ""),
                "HOMEDRIVE": os.environ.get("HOMEDRIVE", "C:"),
                "HOMEPATH": os.environ.get("HOMEPATH", "\\"),
                "TEMP": os.environ.get("TEMP", f"{sr}\\Temp"),
                "TMP": os.environ.get("TMP", f"{sr}\\Temp"),
                "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
                "PATH": os.environ.get("PATH", f"{sr}\\system32;{sr}"),
                "APPDATA": os.environ.get("APPDATA", ""),
                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
                "ProgramData": os.environ.get("ProgramData", ""),
                "ProgramFiles": os.environ.get("ProgramFiles", ""),
                "ProgramFiles(x86)": os.environ.get("ProgramFiles(x86)", ""),
                "ProgramW6432": os.environ.get("ProgramW6432", ""),
            }
            for key in self.allowed_env_keys:
                val = os.environ.get(key)
                if val is not None:
                    env[key] = val
            return env
        home = os.environ.get("HOME", "/tmp")
        env = {
            "HOME": home,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TERM": os.environ.get("TERM", "dumb"),
        }
        for key in self.allowed_env_keys:
            val = os.environ.get(key)
            if val is not None:
                env[key] = val
        return env

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        whitelist_mode = self.allowed_commands is not None
        if self.allowed_commands is not None:
            import shlex
            try:
                lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
                lexer.whitespace_split = True
                tokens = list(lexer)
            except ValueError as e:
                return f"Error: Command syntax invalid ({e})"

            control_ops = {'|', '||', '&', '&&', ';', ';;', '\n'}
            redirect_ops = {'<', '>', '>>', '>&', '<&'}
            
            subcommands: list[list[str]] = []
            current: list[str] = []
            for token in tokens:
                if token in control_ops:
                    if current:
                        subcommands.append(current)
                        current = []
                else:
                    current.append(token)
            if current:
                subcommands.append(current)

            for sub in subcommands:
                cmd_idx = 0
                while cmd_idx < len(sub):
                    if sub[cmd_idx] in redirect_ops:
                        cmd_idx += 2
                        continue
                    if '=' in sub[cmd_idx] and re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*=', sub[cmd_idx]):
                        cmd_idx += 1
                        continue
                    break
                
                if cmd_idx >= len(sub):
                    continue
                
                cleaned_argv: list[str] = []
                skip_next = False
                for token in sub[cmd_idx:]:
                    if skip_next:
                        skip_next = False
                        continue
                    if token in redirect_ops:
                        skip_next = True
                        continue
                    cleaned_argv.append(token)
                
                if not cleaned_argv:
                    continue
                
                cmd_name = cleaned_argv[0]
                basename = os.path.basename(cmd_name)
                
                if cmd_name not in self.allowed_commands and basename not in self.allowed_commands:
                    return f"Error: Command '{cmd_name}' is not in the allowed_commands whitelist"
                
                patterns = self.allowed_commands.get(cmd_name)
                if patterns is None:
                    patterns = self.allowed_commands.get(basename)
                
                if patterns == []:
                    continue
                
                args_str = " ".join(cleaned_argv[1:])
                matched = False
                for pat in patterns:
                    if re.search(pat, args_str):
                        matched = True
                        break
                
                if not matched and patterns:
                    return f"Error: Arguments for command '{cmd_name}' failed validation against allowed patterns"

        # When the user configured an explicit per-command whitelist, that list is
        # the gate — do not also require a match against generic allow_patterns
        # (would reject e.g. Get-Content even after whitelist merge).
        if self.allow_patterns and not whitelist_mode:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        from minibot.security.network import contains_internal_url
        if contains_internal_url(cmd):
            return "Error: Command blocked by safety guard (internal/private URL detected)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            for raw in self._extract_absolute_paths(cmd):
                try:
                    expanded = os.path.expandvars(raw.strip())
                    p = Path(expanded).expanduser().resolve()
                except Exception:
                    continue

                media_path = get_media_dir().resolve()
                if (p.is_absolute() 
                    and cwd_path not in p.parents 
                    and p != cwd_path
                    and media_path not in p.parents
                    and p != media_path
                ):
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        # Windows: match drive-root paths like `C:\` as well as `C:\path\to\file`
        # NOTE: `*` is required so `C:\` (nothing after the slash) is still extracted.
        win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]*", command)
        posix_paths = re.findall(r"(?:^|[\s|>'\"])(/[^\s\"'>;|<]+)", command) # POSIX: /absolute only
        home_paths = re.findall(r"(?:^|[\s|>'\"])(~[^\s\"'>;|<]*)", command) # POSIX/Windows home shortcut: ~
        return win_paths + posix_paths + home_paths
