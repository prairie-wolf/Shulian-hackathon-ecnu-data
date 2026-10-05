# Agent Git 操作检查

本项目使用 Codex 的 `PreToolUse` 命令 hook，在支持的 shell 工具调用执行前检查命令。配置在 `.codex/hooks.json`，脚本在 `scripts/agent_git_guard.py`；仅使用 Python 标准库。Windows 需要 PATH 中有 PowerShell、Git、Python；其他系统需要 Git、Python 3。

## 启用

1. 在本项目重新开启 Codex 会话，使项目配置重新加载。
2. 在 Codex CLI 输入 `/hooks`，查看并信任本项目的 `PreToolUse` 定义。项目配置层也需要处于受信任状态。
3. 确认 hooks 功能没有被用户配置或管理员策略关闭。修改 hook 定义后须重新审查其信任状态。

配置文件落盘和脚本测试成功不代表当前聊天已加载、信任并运行 hook。不要使用跳过 hook 信任的启动选项作为日常启用方式。

## 检查规则和报告

- 允许独立、静态的只读查询，例如 `git status`、`git log`、`git diff`、列出分支，以及 `git clean -n` 预览。
- 允许 `git add` 暂存非敏感的显式文件路径；不允许目录、通配符、批量选项或强制添加。用于按范围记录阶段性更新。
- 允许普通本地 `git commit`、`git commit -m "说明"`，以及 verbose、signoff、allow-empty 等选项。检查目标仓库暂存区路径，发现凭据、私人数据、上传原件或本地审查/配置文件时拒绝。支持 `-C` 和工具的 `workdir`；读取失败时拒绝。
- commit 的 `--amend`、`--no-verify`、`-a`、路径参数及其他未放行选项仍拒绝；先显式暂存需要的文件，再普通提交。路径检查不等于凭据内容扫描，提交前仍须审查 diff。
- 默认拒绝 reset、restore、checkout、clean 删除、分支删除、push、pull、merge、rebase、配置修改及未知别名；用户审查后手动执行。
- 含 Git 的组合命令、重定向、动态表达式或包装器调用无法可靠解析，拒绝执行；改为独立只读命令。
- 拒绝输出 Codex 支持的 `permissionDecision: deny` 与 `systemMessage`，提示 `[Git 安全拦截]`，要求 agent 立即在当前聊天报告失败原因。无需邮件或外部服务；不承诺系统桌面弹窗。
- 不记录或回显原始命令，避免凭据进入报告。不修改工作区、索引、Git 配置或账号数据。
- 输入损坏或缺失时返回明确拒绝，而不是正常放行。

## 能力边界

这是命令文本策略，不是操作系统级 Git 防火墙。它只覆盖匹配的 Codex 工具事件；其他 agent 产品、手动终端、未匹配工具、脚本内部隐藏或编码后的 Git 调用、已有 shell 会话的后续输入均不保证覆盖。仓库本身的 Git 配置或外部程序也可能带来命令文本之外的行为。agent 不得利用这些边界绕过规则。

Codex 对未信任或禁用的 hook 会跳过执行；hook 解释器缺失、超时或运行失败也不能保证阻断。若需要防止恶意绕过，应进一步使用受管理的 hook 和系统权限策略。

## 验证

运行 `python -m unittest discover -s tests -p "test_agent_git_guard.py" -v`。测试使用临时 Git 仓库验证暂存区检查，不执行被检查的 commit 或危险 Git 操作，不访问真实私人数据。

启用后可让 agent 单独执行 `git status --short` 验证通过，再请求 `git clean -n -f` 验证拒绝。后者即使 hook 未生效也是 Git dry-run，但 hook 应返回拦截消息；没有消息则不能认为已启用。

官方协议与信任要求：https://learn.chatgpt.com/docs/hooks

## 提交时间戳与阶段记录

运行 `python scripts/install_git_hooks.py` 启用原生 Git 的 `prepare-commit-msg`，安装器只设置当前仓库的 `core.hooksPath=.githooks`。已有自定义 hook 或其他 hooksPath 时显式报错，不覆盖它们。新 clone 需重新运行；卸载可手动执行 `git config --local --unset core.hooksPath`，不会改动已有提交。

Windows 沙箱账号和登录账号可能具有不同的仓库所有权。安装器对明确选定的项目目录使用仅本次进程有效的 `safe.directory`；不写全局信任配置。安全 hook 也接受 `git -c safe.directory=明确路径 ...`，但不接受通配目录或其他 `-c` 配置。

启用后普通提交标题自动变为 `[2026-10-05T20:34:56+08:00] feat: 简短说明`，时间取提交准备时刻并固定为 Asia/Taipei（UTC+8）。重复处理同一消息不重复添加；正文保持不变。时间戳用于辨识，Git 原生提交时间仍保留。编辑器打开后用户可编辑标题；合并/重放沿用已有带时间戳的消息时不刷新其前缀。

此 hook 通过 Git 执行，独立于 Codex 的 `/hooks` 信任流程。需要 Git 与 Python 3；失败返回非零并中止提交。消息文件使用同目录临时文件加原子替换；Git 本身协调同一工作区的提交，进程异常后不会留下半写消息，残留临时文件不参与后续提交。不要同时在同一工作区并发发起 commit。

阶段性更新完成并验证后，将本次相关文件显式暂存，并用例如 `git commit -m "feat: 增加版本记录" -m "Notes: 增加时间戳与提交约定；相关测试通过。"` 提交。Notes 是提交正文中的短说明，不使用独立 `git notes`，因此跟随普通分支推送。小拼写改动可并入同一阶段，禁止把他人修改、凭据或真实用户数据顺带提交。

完成大规模版本更新（主要功能、兼容性变化或交付里程碑）后，建议附注 tag `vX.Y.Z` 和推送分支及该 tag。明确建议原因及版本号，但仅建议，不自动执行 tag/push。

时间戳验证命令：`python -m unittest discover -s tests -p "test_commit_timestamp.py" -v`，在临时仓库实际提交并验证标题、正文、重复安装和已有 hook 的保护。
