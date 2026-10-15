# VRC 语伴

**在 VRChat 里按 Y，用 Windows 输入法打字。**

VRC 语伴是一个 Windows 桌面小工具。它会在游戏前方打开输入条，让你像平时一样输入中文、日文、英文，使用系统候选框选词，再通过 OSC 把文字送到 VRChat 聊天气泡。

适合需要在几种语言之间切换、希望直接使用系统输入法的玩家。语言和词库来自你安装的输入法；工具本身不提供翻译，也不向游戏注入代码。

## 能做什么

- **按 Y 开始聊天**：VRChat 在前台时唤出输入条，按住 Shift 奔跑时也能使用。
- **使用系统候选框**：保留输入法的组词、选词和语言切换方式。
- **边打边显示**：确认后的文字实时同步到游戏气泡，Enter 发送并关闭。
- **随时收起**：Esc 取消；切到其他窗口时自动关闭输入条。
- **避开菜单和相机**：根据窗口、鼠标和相机状态判断是否接管 Y。
- **自动启动**：可选随 VRChat 启动，或登录 Windows 时启动。

> 输入条会实时发送已确认的文字，其他玩家可能在你按 Enter 前就看到内容。取消输入会尝试清空气泡，但不能撤回别人已经读到的文字。

## 开始使用

需要 Windows 10 / 11 64 位、VRChat PC 客户端，以及你平时使用的 Windows 输入法。**免安装版已带齐运行环境，不需要安装 Python。**

1. 到 [Releases 下载](https://github.com/sunzhaocn/VRC-LinguaBar/releases/latest) **VRC-Yuban.exe**，保存到一个固定位置。
2. 在 VRChat 的 Action Menu 中打开 **OSC → Enabled**，然后双击 **下载的 EXE**。
3. 回到游戏，关闭菜单和相机，按 **Y** 开始输入，按 **Enter** 发送并收起。

下载一个 EXE 就能运行，所需环境已包含在程序中。可以直接放在桌面或其他固定位置，双击后会留在系统托盘，同一用户会话只运行一份。

更新前，使用“随 VRChat 启动”的用户先把自动启动设为“关闭”，等待约 3 秒，再从托盘退出程序。下载并启动新版后，可以重新开启自动启动。若换了存放位置，请重新设置。

## 日常操作

| 操作 | 效果 |
|---|---|
| 游戏前台按 Y | 打开输入条 |
| 使用 Windows 输入法快捷键 | 切换语言和输入模式 |
| 输入并确认候选词 | 文字同步到聊天气泡 |
| Enter | 发送并关闭；正在选词时优先确认候选 |
| Esc | 取消并关闭；正在组词时可能需要按两次 |
| 点击其他位置或 Alt+Tab | 取消本次输入并关闭 |
| 托盘菜单 → 退出 | 退出后台程序 |

右键托盘图标，在 **自动启动** 中选择“关闭”“随 VRChat 启动”或“登录 Windows 时启动”。设置只作用于当前 Windows 用户。移动 exe 后，需要重新设置。

## 使用范围

- 面向 Windows PC 的桌面键盘输入，不是 VR 头显内的原生浮层。
- 默认向本机 `9000` 端口发送聊天文字，在 `9001` 端口接收相机状态。其他 OSC 工具占用接收端口时，需要调整输出或转发配置。
- 菜单识别依赖外部窗口和鼠标状态，不能覆盖所有游戏界面；第三方输入法和不同显示模式也可能有兼容差异。
- 单行输入，最多 144 个 UTF-16 单元；常见汉字通常占 1 个，部分 emoji 占 2 个。
- OSC 使用 UDP，没有游戏端送达确认。

## 从源码运行

开发者可以使用 Python 3.10 以上版本运行源码，发布包使用 Python 3.12 x64 打包。下面以 Python 3.12 为例，在项目目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m linguabar
```

也可以用 `.\.venv\Scripts\python.exe .\vrchat_linguabar.py` 启动。源码与免安装版使用相同的托盘设置和 OSC 参数。

打包使用 Windows x64 和 Python 3.12。先提交源码改动，再执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tools/build_release.py
```

生成的单文件 EXE 和 SHA-256 校验文件位于 `dist` 目录。

## 更多说明

- [使用指南](docs/使用指南.md)：输入习惯、自动启动、命令行参数和常见问题。
- [设计说明](docs/设计说明.md)：模块职责、输入法处理和 OSC 数据流。
- [测试与兼容性](docs/测试与兼容性.md)：测试命令和真实游戏中的检查方法。

运行日志：`%LOCALAPPDATA%\VRC LinguaBar\linguabar.log`。
