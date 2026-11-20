# Desktop 本机编译

三个脚本分别在对应系统上运行，包含前端、Rust 主程序和 Python sidecar 的打包。
最终用户无需安装 Python、Node.js 或 Rust。

| 系统 | 仓库内脚本 | 产物 |
| --- | --- | --- |
| Linux | `desktop/scripts/build-linux.sh` | `.deb`、`.AppImage` |
| macOS | `desktop/scripts/build-macos.sh` | `Legendary.app`、`.dmg` |
| Windows | `desktop/scripts/build-windows.ps1` | 单文件便携版 `.exe`、NSIS 安装程序 `.exe`、WiX 安装包 `.msi` |

所有脚本都按 **Rust 本机工具链的架构**编译，并检查 Python 的系统、架构和位数是否匹配。
例如 Apple Silicon 使用 `aarch64-apple-darwin`，Intel Mac 使用
`x86_64-apple-darwin`；macOS 脚本不生成 universal 安装包。不要在 Linux/WSL
中运行 Windows 脚本，Windows 版需要在 Windows 中构建。

## 通用工具

- Node.js 22+。
- pnpm 10，项目固定为 10.28.2：`npm install -g pnpm@10.28.2`。
- Rust stable（包含 Cargo），通过 <https://rustup.rs/> 安装。
- uv 0.11+，安装方式见 <https://docs.astral.sh/uv/getting-started/installation/>。

首次运行脚本时会使用 `uv sync --locked --python 3.12 --extra pyinstaller_build`
准备项目的 `.venv`，必要时由 uv 下载 Python 3.12；随后执行
`pnpm install --frozen-lockfile`。构建安装器时也可能下载 Tauri 打包工具。
无需手动激活 Python 环境。

## Linux

Ubuntu 22.04/24.04 及提供 WebKitGTK 4.1 的更新版本，可先安装：

```bash
sudo apt-get update
sudo apt-get install -y build-essential pkg-config libgtk-3-dev \
  libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev \
  libssl-dev patchelf file curl wget
```

在仓库根目录运行：

```bash
bash desktop/scripts/build-linux.sh
```

产物（以 x86_64 为例）：

```text
desktop/src-tauri/target/x86_64-unknown-linux-gnu/release/bundle/deb/*.deb
desktop/src-tauri/target/x86_64-unknown-linux-gnu/release/bundle/appimage/*.AppImage
```

Linux 二进制的 glibc 兼容范围取决于构建系统；需要支持较旧发行版时，
请在满足 Tauri 依赖的最旧目标发行版上构建。

## macOS

先安装 Xcode Command Line Tools，并完成系统提示的安装：

```bash
xcode-select --install
```

在仓库根目录运行：

```bash
bash desktop/scripts/build-macos.sh
```

产物（以 Apple Silicon 为例）：

```text
desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/Legendary.app
desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/*.dmg
```

此脚本按项目现有配置打包；面向其他用户分发时，Apple Developer ID 签名、
公证和最低支持系统版本需按发布要求另行配置。现有 Tauri 签名环境变量会照常传入。

## Windows

安装 Rust/Cargo 和 MSVC C++ 编译环境可使用仓库脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\desktop\scripts\install-rust-windows.ps1
```

脚本自动选择 Windows 本机的 x64/ARM64 MSVC 工具链，安装 Rust stable 和 Cargo，
在缺少 C++ 工具时从 Microsoft 官网下载安装 Visual Studio 2022 Build Tools
及推荐的 Windows SDK，并编译运行一个小程序验证链接器。安装 C++ 工具时可能
出现管理员授权提示；如提示需要重启，请重启后再次运行脚本。安装后重新打开
PowerShell；Cargo 会加入用户 PATH，现有其他 PATH 项会保留。

如只需 Rust/Cargo：

```powershell
.\desktop\scripts\install-rust-windows.ps1 -SkipBuildTools
```

现有 `CARGO_HOME`、`RUSTUP_HOME` 设置会保留，默认工具链将设为本机的 stable MSVC。
此脚本安装编译环境；Node.js、pnpm 和 WebView2 仍需另行准备。

Windows 构建脚本会自动检查并安装 uv 0.11+。也可以单独执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\desktop\scripts\install-uv-windows.ps1
```

此脚本使用 Astral 官方安装程序，不需要管理员权限，并更新当前进程和用户的
PATH。默认安装到 `%USERPROFILE%\.local\bin`，也支持 `UV_INSTALL_DIR`。

Windows 构建请使用本地磁盘上的独立源码目录（例如 `C:\src\legendary`）。
从 `\\wsl.localhost\...` 或 `\\wsl$\...` 复制源码时，跳过 `.venv`、
`desktop/node_modules`、`desktop/.sidecar-build`、`desktop/src-tauri/target` 和
`desktop/src-tauri/binaries/legendary-sidecar-*`；这些目录包含 Linux 构建依赖或
二进制，Windows 构建脚本会重新生成它们。

先安装以下工具：

- Visual Studio 2022 Build Tools，选择 **使用 C++ 的桌面开发**，包含 MSVC 和 Windows SDK。
- Rust stable 的 MSVC 工具链，例如 `x86_64-pc-windows-msvc`。
- Microsoft Edge WebView2 Runtime。
- Windows 的 **可选功能** 中启用 **VBScript**（生成 MSI 时 WiX 可能需要，
  具体入口取决于 Windows 版本）。

在仓库根目录的 PowerShell 中运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\desktop\scripts\build-windows.ps1
```

`ExecutionPolicy Bypass` 仅针对本次 PowerShell 进程，不修改系统执行策略。
也可以在允许运行本地脚本的 PowerShell 中直接执行：

```powershell
.\desktop\scripts\build-windows.ps1
```

产物（以 x64 为例）：

```text
desktop\src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis\*-setup.exe
desktop\src-tauri\target\x86_64-pc-windows-msvc\release\bundle\msi\*.msi
desktop\src-tauri\target\x86_64-pc-windows-msvc\release\portable\Legendary_<版本>_x86_64_portable.exe
```

只生成单文件便携版、不生成安装器：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\desktop\scripts\build-windows.ps1 -PortableOnly
# 依赖已安装时可同时添加 -SkipDependencies
```

便携版只需携带 `Legendary_<版本>_x86_64_portable.exe`，无需同目录的
`legendary-sidecar.exe`，无需安装 Python。构建仍会先生成与本机架构一致的
PyInstaller sidecar，再通过 `embedded-sidecar` Cargo feature 嵌入桌面程序。
Windows 平台配置也对 NSIS/MSI 使用同一内嵌方式，避免重复携带后端文件。

首次启动会把后端释放到：

```text
%LOCALAPPDATA%\gl.legendary.desktop\backend\<Rust目标架构>\<SHA256>\legendary-sidecar.exe
```

之后每次连接后端都会校验缓存，内容匹配时复用，缺失或损坏时原子替换。
多进程共用缓存时使用文件锁；新旧后端使用不同哈希目录，更新不会覆盖仍在运行的
旧后端。旧缓存不自动删除，可在应用及其下载任务退出后手动清理 `backend` 目录，
下次启动会重新释放。游戏目录、登录和安装记录仍沿用 Legendary 的配置规则，
“便携版”仅指可执行文件分发形式，不把用户数据迁入 EXE 所在目录。

运行时仍有后端子进程和 PyInstaller 的临时解包文件。缓存目录需有写入空间；
Windows 便携版依赖已安装的 WebView2，安装包则按 Tauri 配置处理 WebView2。
Windows 安装包和便携版的代码签名需另外配置；便携 EXE 可在生成后单独签名，
签名后需重新生成其校验文件。此实现不会把 Windows 服务二进制用于 Linux/macOS。

## 重复构建

依赖已准备好时，可以跳过安装依赖；如使用自建 Python 环境，请提供其**绝对路径**：

```bash
LEGENDARY_DESKTOP_PYTHON=/absolute/path/to/python \
  bash desktop/scripts/build-linux.sh --skip-deps
# macOS 同样支持 --skip-deps
```

```powershell
$env:LEGENDARY_DESKTOP_PYTHON = 'C:\absolute\path\to\python.exe'
.\desktop\scripts\build-windows.ps1 -SkipDependencies
```

不提供变量时使用仓库的 `.venv`。自建环境需要 Python 3.10–3.13、项目依赖和
PyInstaller。脚本会从任意当前目录定位仓库，并传递现有代理、签名、Cargo
配置等环境设置。自定义 `CARGO_TARGET_DIR` 时，产物位于其下的
`<Rust host triple>/release/bundle/`，Windows 便携版位于同级的 `portable/`。
Windows 脚本中的相对 `CARGO_TARGET_DIR` 按 `desktop/src-tauri` 解析，建议使用绝对路径。

查看帮助：Linux/macOS 使用 `--help`，Windows 使用 `-Help`。
