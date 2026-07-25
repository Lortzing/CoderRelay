# CoderRelay

简体中文 | [English](README.en.md)

[![CI](https://github.com/Lortzing/CoderRelay/actions/workflows/ci.yml/badge.svg)](https://github.com/Lortzing/CoderRelay/actions/workflows/ci.yml)
[![GitHub Release](https://img.shields.io/github/v/release/Lortzing/CoderRelay)](https://github.com/Lortzing/CoderRelay/releases)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/github/license/Lortzing/CoderRelay)](LICENSE)

CoderRelay 是面向编码智能体 CLI 的账户、Profile 与 API 路由管理工具。当前完整支持 OpenAI Codex CLI；后续将接入 Claude Code。

当前能力包括：

- 管理多个 ChatGPT/Codex 登录 Profile；
- 管理 `API Key + Base URL + Model` 类型的 OpenAI 兼容 API；
- 按稳定账号标识去重并同步当前配置；
- 在切换前保存 Codex 刷新后的 OAuth 凭据；
- 支持 `auth.json` 与 macOS Keychain 中的 Codex CLI 凭据；
- 手动切换、健康检查、自动故障转移和恢复切回；
- 从最新稳定 GitHub Release 自动下载、校验并安装更新；
- Bash、Zsh 和 Fish 自动补全。

> Claude Code 支持属于后续功能，当前版本不会修改 Claude Code 的配置。

## 命令

```bash
cdy --help
coder-relay --help
```

## 安装

### Windows

| 架构 | 安装程序 | 便携版 |
|---|---|---|
| x86 32 位 | `CoderRelay-Setup-<版本>-windows-x86.exe` | `CoderRelay-Portable-<版本>-windows-x86.zip` |
| x86_64 / x64 | `CoderRelay-Setup-<版本>-windows-x86_64.exe` | `CoderRelay-Portable-<版本>-windows-x86_64.zip` |
| ARM64 | `CoderRelay-Setup-<版本>-windows-arm64.exe` | `CoderRelay-Portable-<版本>-windows-arm64.zip` |

### macOS

| 架构 | 安装镜像 |
|---|---|
| Intel x86_64 | `CoderRelay-<版本>-macOS-x86_64.dmg` |
| Apple Silicon ARM64 | `CoderRelay-<版本>-macOS-arm64.dmg` |

打开 DMG 后运行其中的 PKG。运行时安装到 `/usr/local/lib/coder-relay/`，命令入口为 `/usr/local/bin/cdy`。

### Linux

| 架构 | 通用包 | Debian/Ubuntu | Fedora/RHEL |
|---|---|---|---|
| x86_64 | `coder-relay-<版本>-linux-x86_64.tar.gz` | `coder-relay_<版本>_amd64.deb` | `coder-relay-<版本>-1.x86_64.rpm` |
| ARM64/AArch64 | `coder-relay-<版本>-linux-aarch64.tar.gz` | `coder-relay_<版本>_arm64.deb` | `coder-relay-<版本>-1.aarch64.rpm` |

### 从源码安装

```bash
git clone https://github.com/Lortzing/CoderRelay.git
cd CoderRelay
./install.sh
```

固定安装 v0.8.1：

```bash
uv tool install --force git+https://github.com/Lortzing/CoderRelay.git@v0.8.1
```

## 快速开始

```bash
cdy status
cdy status --no-probe
cdy import-current
cdy use official
cdy auto official backup --watch
cdy launch -p official -p backup --
```

添加 ChatGPT 登录文件：

```bash
cdy add-auth official ~/.codex/auth.json
```

添加 OpenAI 兼容 API：

```bash
cdy add-api backup \
  --url https://gateway.example.com/v1 \
  --model gpt-5.6 \
  --api-key-stdin
```

## 当前账号与导入

从 v0.8.1 开始，`import-current` 不再按名称盲目创建新目录，而是读取稳定账号标识：

- ChatGPT：优先使用 `chatgpt_account_id`；
- API：使用 API 凭据哈希。

重复执行：

```bash
cdy import-current
```

会更新已有匹配 Profile，而不会继续创建 `name-2`、`name-3`。

Codex CLI 可能把凭据保存在：

- `$CODEX_HOME/auth.json`；
- macOS Keychain 的 Codex CLI 凭据项。

通常使用自动识别：

```bash
cdy import-current
```

macOS 可显式指定：

```bash
cdy import-current --auth-source file
cdy import-current --auth-source keyring
```

当当前 CLI 配置仍是 API Profile，而你从 Keychain 导入另一个 ChatGPT 账号时，该 Profile 只会被保存，不会错误地标记为当前活动账号。随后显式执行：

```bash
cdy use <Profile名>
```

仅存在于桌面应用内部、没有导出到 CLI 文件或 Keychain 的会话无法安全复制。此时先在终端使用同一账号运行：

```bash
codex login
```

再执行 `cdy import-current`。

详细说明见 [docs/auth-and-switching.md](docs/auth-and-switching.md)。

## 切换行为

执行：

```bash
cdy use <Profile名>
```

CoderRelay 会：

1. 识别当前实际账号；
2. 将 Codex 刷新后的 Token 和当前配置回写到对应 Profile；
3. 备份活动配置；
4. 写入目标 Profile 的凭据和配置；
5. 校验后更新活动状态。

API Profile 会显式设置：

```toml
cli_auth_credentials_store = "file"
```

避免无关的桌面端或 Keychain ChatGPT 登录覆盖 API Key。

切换后需要重新启动已存在的 Codex CLI/App 进程，使其重新载入配置。

## 健康检查

Responses API 只有在返回成功 JSON 且包含输出文本时才算健康。HTTP 200 的 HTML 防护页会显示为：

```text
invalid_response
```

过长错误详情不会在状态表中展开。完整自动化场景可使用 `--json` 输出。

## 自动切换规则

1. 参数越靠前，Profile 优先级越高；
2. 当前 Profile 连续失败达到阈值后切换；
3. 高优先级 Profile 连续恢复达到阈值后切回；
4. 恢复切换受冷却时间限制，紧急故障转移不受限制；
5. 所有候选均不健康时，保持当前活动配置不变。

## 数据目录

```text
~/.config/coder-relay/
├── profiles/
├── backups/
├── state.json
└── switch.lock
```

可以通过 `CODER_RELAY_HOME` 覆盖。默认 Codex 目录为 `~/.codex`，也可以通过 `CODEX_HOME` 覆盖。

现有重复 Profile 不会自动删除，因为同一账号可能有意保存不同模型或配置。确认无用后执行：

```bash
cdy remove <Profile名>
```

## 自动更新与卸载

```bash
cdy update
cdy update -y
cdy update --force

cdy uninstall
cdy uninstall --purge
```

自动更新会选择对应平台资产，下载 `SHA256SUMS.txt`，完成 SHA-256 校验后再安装。

macOS PKG 卸载：

```bash
sudo cdy uninstall --yes
```

## 发布

```bash
git tag -a v0.8.1 -m "CoderRelay v0.8.1"
git push origin v0.8.1
```

Release Workflow 会构建 Windows Setup/ZIP、macOS DMG/PKG、Linux TAR/DEB/RPM，并生成 `SHA256SUMS.txt`。安装包尚未数字签名。

## 开发

```bash
uv sync --extra dev
uv run pytest
uv run cdy --help
uv build --no-sources
```

## License

MIT
