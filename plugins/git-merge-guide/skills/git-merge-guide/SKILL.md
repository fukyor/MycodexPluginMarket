---
name: git-merge-guide
description: 为用户提供安全、可确认的 Git 操作指引，尤其适用于分支比对、fetch、merge、冲突分析、手工合并、提交前验证等场景。用户要求合并分支、比对本地和远程差异、处理冲突、逐步确认 Git 操作，或强调“一个文件一个文件确认”“解释冲突代码块作用”“推荐使用哪一边”时使用。
---

# Git 合并指引

## 核心原则

把用户的代码安全放在第一位。Git 操作必须先观察、再解释、再等待确认，最后才执行会改变仓库状态的命令。

处理分支合并冲突时必须贯彻这个核心流程：

1. 一次只处理一个冲突文件。
2. 对该文件逐个定位冲突块行号。
3. 对每个冲突块解释两边代码的主要功能、影响范围和风险。
4. 明确推荐使用本地、远程，或手工整合。
5. 等用户确认后再修改该文件。
6. 该文件处理并 `git add` 后，再进入下一个文件。

除非用户明确要求批量处理，否则不要一次性解决多个冲突文件。

## 安全边界

- 不要在用户确认前执行 `git merge`、`git rebase`、`git reset`、`git checkout --ours`、`git checkout --theirs`、`git add`、`git commit`、`git push`。
- 不要默认 rebase。用户说“合并远程分支”时，优先按 merge 理解。
- 不要在未说明影响的情况下清理、删除、覆盖未跟踪文件。
- 工作区不干净时，先报告修改清单和风险，让用户决定是提交、暂存、stash，还是继续。
- 已经处于 merge 冲突状态时，不要重新发起 merge；先读取当前冲突状态。

## 合并前只读检查

先运行只读命令建立事实：

```bash
git status --short --branch
git branch --all --verbose --no-abbrev
git remote --verbose
git log --oneline --decorate --graph --left-right 当前分支...目标分支
git diff --stat 当前分支..目标分支
git diff --name-status 当前分支..目标分支
```

向用户说明：

- 当前分支是什么。
- 工作区是否干净。
- 本地和目标分支是否分叉。
- 合并是快进、普通 merge，还是已有冲突状态。
- 主要差异文件和高风险文件。

只有用户确认后，才执行会进入合并状态的命令，例如：

```bash
git merge --no-commit --no-ff origin/master
```

## 冲突识别

进入冲突状态后，先只读列出冲突：

```bash
git status --short --branch
git diff --name-only --diff-filter=U
git ls-files -u
```

解释暂存区阶段：

- `:1:path` 是共同祖先。
- `:2:path` 是当前分支，也就是 ours / HEAD。
- `:3:path` 是被合入分支，也就是 theirs / 目标分支。

## 单文件冲突分析

对当前文件先定位冲突标记：

```bash
rg -n "^(<<<<<<<|=======|>>>>>>>)" path/to/file
nl -ba path/to/file
```

如果文件冲突标记交错、过大或不易阅读，改用暂存版本干净对比：

```bash
git diff --unified=3 :2:path/to/file :3:path/to/file
git show :2:path/to/file
git show :3:path/to/file
```

向用户按以下格式说明，不要省略功能解释：

```text
文件：path/to/file
冲突块：第 X-Y 行
作用：这个代码块负责什么功能。

本地 HEAD 版本：
- 主要功能：
- 影响：
- 风险：

目标分支版本：
- 主要功能：
- 影响：
- 风险：

推荐：使用本地 / 使用目标分支 / 手工整合。
理由：...

请确认是否按推荐处理这个冲突块或这个文件。
```

用户确认前不要修改文件。

## 选择版本

如果用户确认整个文件使用本地版本：

```bash
git checkout --ours path/to/file
git add path/to/file
```

如果用户确认整个文件使用目标分支版本：

```bash
git checkout --theirs path/to/file
git add path/to/file
```

如果需要手工整合：

- 用 `apply_patch` 修改冲突块。
- 删除所有冲突标记。
- 保留双方必要逻辑。
- 修改后运行：

```bash
rg -n "^(<<<<<<<|=======|>>>>>>>)" path/to/file
git diff -- path/to/file
git add path/to/file
```

每个文件处理完后，报告已处理文件，再进入下一个文件。

## 用户给出全局策略时

用户可能会说：

```text
采用 origin/master 的筛选功能和虚拟滚动；
保留本地 Docker 配置；
其他样式使用远程。
```

这类策略可以作为推荐依据，但仍要逐文件解释冲突。只有在用户明确说“后续全部按这个策略批量处理”时，才可以批量执行。

遇到配置类例外时，要单独验证。例如用户要求保留 Docker 的 `minio:9000`，合并后必须检查：

```bash
rg "minio:9000|127\\.0\\.0\\.1:9000" 配置文件路径
```

## 验证与提交

所有冲突解决后执行：

```bash
git diff --name-only --diff-filter=U
rg -n "^(<<<<<<<|=======|>>>>>>>)" .
git diff --cached --stat
git diff --cached --name-status
```

根据项目类型运行合适验证，例如前端项目：

```bash
npm run build
```

验证通过后，报告：

- 无未解决冲突。
- 哪些文件已按本地或目标分支处理。
- 哪些用户指定配置已保留。
- 是否已构建或测试通过。
- 是否尚未提交。

只有用户明确给出提交命令或确认提交后，才执行：

```bash
git commit -m "提交信息"
```

提交后再检查：

```bash
git status --short --branch
git log --oneline --decorate --graph -n 6
```
