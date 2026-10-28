# J.A.R.V.I.S. — 语音 AI 助手

一个**原创自建**的 Windows 语音助手：语音对话 + 电脑操控 + 长期记忆。网页全息 HUD 界面，前后端分离，模块化可改可控。

> 借鉴了 Aivy-OS、huashu-chrome 等开源项目的思路，但完全自建，拥有全部代码。

## 功能

- 🎤 **语音回路**：按住说话 → 本地语音识别（faster-whisper）→ LLM（DeepSeek）→ 语音合成（edge-tts）
- 🖱️ **四层电脑操控**：L0 结构接口（PowerShell / WPS COM）→ L1 UIA 控件树 → L2 坐标 → L3 截图 + 视觉模型
- 🌐 **浏览器操控**：借力 [huashu-chrome](https://github.com/alchaincyf/huashu-chrome)，操控你**自己已登录**的浏览器
- 📊 **WPS 办公**：走 COM 接口建表格、写文档
- 🧠 **长期记忆**：SQLite + 语义检索，对话后自动记忆
- ⏰ **定时提醒**：一次性 + 每天/每周/每月循环
- 👁️ **视觉兜底**：DeepSeek 多模态模型看屏幕
- 🔀 **子智能体**：并行处理多个独立任务

## 架构

```
交互层   网页 HUD 前端 + edge-tts 语音
   ↓
认知层   DeepSeek（function calling）+ 视觉模型 + SQLite 记忆
   ↓
编排层   工具注册表 + 子智能体编排
   ↓
执行层   桌面操控(UIA/坐标) + 浏览器(huashu-chrome) + WPS(COM) + 系统命令
   ↓
感知层   语音(faster-whisper) + 屏幕(截图+视觉)
```

## 环境要求

- **Windows**（桌面操控、WPS COM 等依赖 Windows API）
- **Python 3.11**（避开 3.14 的 wheel 兼容问题）
- **Edge 浏览器**（语音识别 + 浏览器操控用）
- **ffmpeg**（STT 转码依赖）
- **DeepSeek API Key**（LLM + 视觉，免费注册 https://platform.deepseek.com）

## 安装

**一键安装**：双击 `install.bat`（自动建虚拟环境、装依赖、引导填 Key）。

**手动安装**：

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 配置 API Key（复制 .env.example 为 .env，填入你的 Key）
copy .env.example .env

# ffmpeg 放到项目下 ffmpeg\bin\ffmpeg.exe，或加入系统 PATH
```

模型文件（whisper + 语义模型）会在首次运行时自动下载（走 hf-mirror 镜像）。

## 启动

双击 `start.bat`，浏览器打开 **http://localhost:8000**（必须用 Edge/Chrome）。

按住 TALK 说话。首次使用允许麦克风权限。

## 浏览器功能（可选）

浏览器操控依赖 huashu-chrome 扩展：

```bash
npx -y huashu-chrome install
```

然后 Edge 打开 `edge://extensions` → 开发者模式 → 加载 `npx -y huashu-chrome extension` 打印出的目录。

## 项目结构

```
├── server.py          # FastAPI 后端入口（/stt /ask /tts）
├── index.html         # HUD 前端
├── js/                # 前端逻辑（语音回路、barge-in）
├── css/               # 样式
├── core/              # 大脑(brain)、记忆(memory)、配置(config)、工具注册表(skills)
├── skills/            # 工具实现：系统/桌面/浏览器/WPS/提醒/视觉/子智能体
├── stt/               # 语音识别（faster-whisper）
├── tts/               # 语音合成（edge-tts）
├── data/              # 运行时生成（记忆库、截图、提醒）——已 gitignore
└── model/             # 模型文件——已 gitignore，运行时自动下载
```

## 安全设计

- 不可逆动作（删除/格式化/改注册表等）自动拦截，交还用户确认
- 每个写操作读回验证，防静默失败
- 危险命令黑名单 + 每步截图取证

## License

[MIT](LICENSE)

依赖的第三方库均使用宽松开源协议（MIT/Apache），详见各库。
