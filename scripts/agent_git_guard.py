"""Codex PreToolUse policy. Inspect input only; never execute the requested command."""
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys


READ_ONLY = {
    "status", "log", "show", "diff", "rev-parse", "ls-files", "ls-tree",
    "rev-list", "describe", "merge-base", "name-rev", "shortlog", "blame",
}
GIT = re.compile(r"(?i)(?<![\w-])git(?:\.exe)?(?![\w-])")


def sensitive_path(name):
    path = PurePosixPath(name.replace("\\", "/").lower())
    parts, base = path.parts, path.name
    return (
        base in {"ai_clients.json", "credentials.json", "secrets.json", "users.json"}
        or (base.startswith(".env") and base not in {".env.example", ".env.sample"})
        or path.suffix in {".pem", ".key", ".p12", ".pfx"}
        or str(path) == ".streamlit/secrets.toml"
        or str(path).startswith(("data/private/", "data/raw/uploads/", ".pr_review/"))
        or base == "mcp_config.local.json"
        or any(p in {".aws", ".agents"} for p in parts)
        or (parts and parts[0] == ".codex" and str(path) != ".codex/hooks.json")
    )


def check_commit(flags, cwd, git_options=()):
    # An ordinary commit uses the already-reviewed index. Reject options that
    # rewrite history, bypass hooks, or silently change which files are committed.
    index = 0
    while index < len(flags):
        flag = flags[index]
        if flag in {"-m", "--message"}:
            index += 2
            if index > len(flags):
                return "commit 缺少提交说明。"
        elif flag.startswith("--message=") or (flag.startswith("-m") and len(flag) > 2):
            index += 1
        elif flag in {"-v", "--verbose", "-s", "--signoff", "--allow-empty", "--allow-empty-message"}:
            index += 1
        else:
            return "仅放行普通暂存区 commit；不允许 --amend、--no-verify、-a、路径参数或其他改变提交范围的选项。"
    try:
        result = subprocess.run(
            ["git", *git_options, "diff", "--cached", "--no-relative", "--name-only", "--diff-filter=ACMRT", "-z"],
            cwd=cwd, capture_output=True, timeout=5, check=True,
        )
        names = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    except (OSError, subprocess.SubprocessError):
        return "无法读取目标仓库暂存区，commit 安全检查未通过。"
    if any(sensitive_path(name) for name in names if name):
        return "暂存区包含凭据、私人数据、上传原件或本地审查/配置文件，commit 被拒绝；请先移出暂存区。"
    return None


def check(command, cwd=None):
    """Return a denial reason, or None for commands outside scope / safe Git."""
    if not GIT.search(command):
        return None
    # Shell parsing varies across PowerShell, cmd and Bash. Only accept simple,
    # literal Git invocations. Do not guess what an expression will execute.
    if any(c in command for c in ";\n\r&|><`$(){}"):
        return "Git 命令包含组合命令、重定向或动态表达式，无法可靠检查；请拆成独立命令。"
    try:
        args = shlex.split(command, posix=True)
    except ValueError:
        return "Git 命令引号不完整，检查未通过。"
    if not args or args[0].lower() not in {"git", "git.exe"}:
        return "Git 被包装器、脚本或路径间接调用，检查未通过；请使用独立 git 命令。"
    index = 1
    target = Path(cwd or Path.cwd())
    git_options = []
    while index < len(args):
        option = args[index]
        if option == "-C":
            if index + 1 >= len(args):
                return "Git -C 缺少目标目录。"
            target = (target / args[index + 1]).resolve()
            index += 2
        elif option in {"--no-pager", "--no-optional-locks"}:
            index += 1
        elif option == "-c" and index + 1 < len(args) and args[index + 1].startswith("safe.directory="):
            value = args[index + 1].split("=", 1)[1]
            if not value or "*" in value:
                return "safe.directory 只允许本次调用的明确目录，不能全局信任所有仓库。"
            git_options.extend(["-c", args[index + 1]])
            index += 2
        else:
            break
    if index >= len(args):
        return "缺少可核验的 Git 子命令。"
    sub, flags = args[index].lower(), args[index + 1:]
    if sub == "add":
        paths = flags[1:] if flags and flags[0] == "--chmod=+x" else flags
        paths = paths[1:] if paths and paths[0] == "--" else paths
        if not paths or any(p.startswith("-") or p in {".", ".."} or any(c in p for c in "*?[:") for p in paths):
            return "仅允许显式文件路径的 git add；不允许强制添加、批量选项或路径表达式。"
        try:
            root = Path(subprocess.run(["git", *git_options, "rev-parse", "--show-toplevel"], cwd=target,
                        capture_output=True, text=True, encoding="utf-8", check=True, timeout=5).stdout.strip())
            for name in paths:
                candidate = (target / name).resolve()
                relative = candidate.relative_to(root.resolve()).as_posix()
                if candidate.is_dir() or sensitive_path(relative):
                    return "git add 仅允许非敏感的显式文件；目录或敏感文件被拒绝。"
        except (OSError, ValueError, subprocess.SubprocessError):
            return "无法确认 git add 路径位于目标仓库，检查未通过。"
        return None
    if sub == "commit":
        return check_commit(flags, target, git_options)
    if sub in READ_ONLY:
        if any(f.startswith(("--output", "--ext-diff", "--textconv")) for f in flags):
            return "只读 Git 命令请求写文件或执行外部处理器，检查未通过。"
        return None
    if sub == "branch" and (not flags or all(f in {"--list", "-l", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--show-current"} for f in flags)):
        return None
    if sub == "clean" and any(f in {"-n", "--dry-run"} for f in flags) and not any(f in {"-f", "-ff", "--force"} for f in flags):
        return None
    if sub == "remote" and flags in ([], ["-v"], ["--verbose"]):
        return None
    if sub in {"reset", "clean", "restore", "checkout", "switch", "rm"}:
        return "该操作可能丢弃工作区、暂存区或未跟踪文件；默认拒绝，需用户核对后手动执行。"
    if sub in {"push", "pull", "merge", "rebase", "cherry-pick", "revert", "am"}:
        return "该操作涉及远端写入、合并或历史修改；默认拒绝，需用户核对后手动执行。"
    return "该 Git 子命令未列入安全白名单，可能修改仓库或通过别名执行其他程序；默认拒绝。"


def main():
    try:
        event = json.load(sys.stdin)
        tool_input = event.get("tool_input", {})
        if isinstance(tool_input, str):
            tool_input = json.loads(tool_input)
        command = tool_input.get("command", tool_input.get("cmd"))
        if not isinstance(command, str):
            reason = "Hook 未收到可检查的命令，拒绝执行。"
        else:
            reason = check(command, tool_input.get("workdir") or event.get("cwd"))
    except Exception:
        reason = "Hook 输入解析失败，拒绝执行；请检查 hook 输入契约。"
    if reason:
        message = "[Git 安全拦截] " + reason + " Agent 必须立即向用户报告本次拦截及原因，不得改写命令绕过检查。"
        # Do not echo commands: they may contain remote credentials or tokens.
        print(json.dumps({"systemMessage": message, "hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": message,
        }}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
