# VRC LinguaBar｜语伴输入条

**在 VRChat 中切换不同语言，也能用熟悉的系统输入法选词、打字。**

VRC LinguaBar 是一个面向 Windows 桌面键盘输入的 VRChat 辅助工具。正常游戏画面中按 **Y**，打开一个支持系统输入法的 PyQt5 实体输入条；使用中文、日文等输入法完成组词和选词，再通过 OSC 把文字发送到 VRChat 聊天气泡。

项目名中的 **Lingua** 指语言，**Bar** 指轻量输入条；中文名“语伴”表达的是陪伴不同语言玩家交流。建议 GitHub 仓库名：`vrc-linguabar`。

> 当前为实验性源码版本。29 项自动化测试通过，但真实 VRChat、不同系统输入法和全屏模式尚未完成全面联调。候选框支持是设计目标与实现路径，不是对所有输入法的兼容保证。

## 为什么做这个项目

在多语言社区中，玩家可能需要在中文、日文、英文之间不断切换。遇到游戏输入框与系统输入法配合不顺畅时，往往会出现候选框看不到、组词不方便，或者只能先在其他软件中打好再复制回游戏的情况。

这个项目希望让输入回到玩家熟悉的方式：**沿用游戏的 Y 聊天习惯，把文字编辑交给标准桌面输入控件，让候选框由系统输入法正常管理，再把结果同步到游戏。**

它不提供翻译、词库或新的输入法，也不向游戏注入代码。你使用哪种语言、哪套候选词，仍由 Windows 中安装的输入法决定。

## 当前行为

| 操作 | 结果 |
|---|---|
| 正常游戏画面按 Y | 接管这次 Y 按键，打开输入条 |
| 切换已安装的系统输入法 | 在输入条内进行组词、选词；候选框由系统 IME 提供 |
| 编辑已确认的文字 | 立即尝试通过 OSC 同步纯文本，不追加动态点 |
| Enter | 发送最终文字，并关闭输入条；组词确认优先交给输入法 |
| Esc | 取消并关闭；组词时可能先取消候选，再按一次关闭 |
| 点击其他位置 / Alt+Tab | 关闭输入条，取消本次输入，并尝试清空预览、关闭打字灯 |
| 关闭菜单 / 相机 | 在状态检查恢复正常后，Y 自动恢复接管，无需手动启用 |
| 托盘菜单“退出” / 控制台 Ctrl+C | 退出工具并释放钩子、窗口和网络资源 |

**实时同步的文字可能已经被其他玩家看到。** 这里的“预览”不是私有草稿；Esc 和失焦清理不能撤回别人已经读到的内容。

## 环境与依赖版本

| 项目 | 要求 / 当前验证版本 | 作用 |
|---|---|---|
| 操作系统 | 目标为 Windows 10 / 11；本次验证为 Windows 11 x64，Build 26200 | 提供键盘钩子、窗口焦点和系统输入法 |
| Python | 代码及固定依赖要求 **Python 3.10+**；建议先使用 **CPython 3.13 x64**；本次验证 **3.13.15** | 运行程序 |
| PyQt5 | **5.15.11** | 输入条、文本编辑、输入法事件、定时器、托盘和 Qt 网络事件 |
| python-osc | **1.10.2** | OSC 消息编码与解析 |
| pywin32 | **312** | Windows 窗口枚举与显示器信息 |
| PyQt5-Qt5 | **5.15.2**，由 PyQt5 依赖引入 | 实际 Qt 运行库 |
| PyQt5-sip | **12.19.0**，由 PyQt5 依赖引入 | Python 与 Qt C++ 对象之间的绑定 |
| VRChat | Windows PC 客户端，开启 OSC | 接收聊天文字；可选输出相机模式 |
| 输入法 | 在 Windows 中安装所需语言输入法 | 提供中日文等语言的候选框、组词与转换 |

“Python 3.10+”是最低要求，不表示每个更新版本都已验证；Windows 10、32 位 Python、Windows ARM 和 VR 头显内的输入体验均未在本次验证中确认。程序使用 Win32 API，不能直接在 macOS 或 Linux 运行。使用 `.py` 源码运行，不需要另装 Unity SDK、Visual Studio、Qt Designer 或独立 Qt SDK。

直接依赖固定在 [requirements.txt](requirements.txt)，完整的本次版本组合固定在 [requirements-lock.txt](requirements-lock.txt)。这些是版本固定文件，不包含安装包或哈希锁定。

## 快速开始

1. 安装 **CPython 3.13 x64**，并安装需要的 Windows 语言与输入法。先在记事本中确认中文或日文候选框能正常出现。
2. 下载并解压本项目，进入包含 `vrchat_linguabar.py` 的目录，在终端执行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
```

3. 在 VRChat 的 Action Menu 中启用 **OSC → Enabled**。如果你修改过游戏的 OSC 端口，请同步修改本工具参数。[VRChat OSC 官方说明](https://docs.vrchat.com/docs/osc-overview)
4. 先关闭其他旧版输入条实例，再启动：

```powershell
.\.venv\Scripts\python.exe .\vrchat_linguabar.py
```

5. 回到正常游戏画面，关闭菜单和相机，直接按 **Y**。在输入条中选择系统输入法、组词并选词，按 Enter 发送。

如果系统没有 `py` 命令，先确认 `python --version` 为你安装的兼容版本，再用 `python -m venv .venv` 创建环境。这里直接调用虚拟环境中的 Python，不需要修改 PowerShell 执行策略。

## 进一步阅读

- [完整使用指南](docs/使用指南.md)：语言切换、候选框、操作习惯、全部参数及排错。
- [设计初衷与技术说明](docs/设计说明.md)：每个组件用了什么技术、为什么需要它，以及数据如何流转。
- [测试与兼容性](docs/测试与兼容性.md)：29 项测试覆盖什么、哪些还需真实游戏验证。

## 需要了解的边界

- 菜单防护采用窗口与系统光标检查，无法读取全部 Unity 自绘 UI 的内部状态；不能承诺与原生聊天框在每个场景中完全一致。
- 相机保护依赖收到 `/usercamera/Mode`。默认监听 UDP 9001；端口冲突或丢失更新会影响判断。未收到状态不等于确定关闭。
- 外部输入法候选窗兼容处理目前主要覆盖部分微软输入法窗口，第三方输入法需要实测。
- 字数按 **144 个 UTF-16 单元**保守限制；常见汉字占 1 个单元，部分 emoji 占 2 个。当前是单行输入框。
- UDP 没有送达确认；发送清空包不代表游戏端必定清除。低级键盘钩子和系统调度也不能保证字面意义上的零延迟。

## 开发与测试

应用入口仍是一个独立 Python 文件；文档、依赖清单和测试文件不属于运行时拆分模块。

```text
vrc-linguabar/
├─ vrchat_linguabar.py
├─ requirements.txt
├─ requirements-lock.txt
├─ README.md
├─ docs/
│  ├─ 使用指南.md
│  ├─ 设计说明.md
│  └─ 测试与兼容性.md
└─ tests/
   └─ test_linguabar.py
```

在 Windows 上运行测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用标准库 unittest 和 PyQt5 自带的 QtTest，不需要额外安装 pytest。普通运行不需要执行测试。

依赖项目的版本与许可信息：[PyQt5](https://pypi.org/project/PyQt5/5.15.11/)、[python-osc](https://pypi.org/project/python-osc/1.10.2/)、[pywin32](https://pypi.org/project/pywin32/312/)。本包未替项目作者选择或附加项目源代码许可证；依赖项目的许可不等同于本项目的许可声明。
