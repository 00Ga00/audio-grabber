# Audio Studio

一个面向 Windows 的本地音频工具：从视频提取音频、录制电脑声音，以及增强已有音频。
底层使用 [yt-dlp](https://github.com/yt-dlp/yt-dlp)、ffmpeg、Windows WASAPI 和可选的 DeepFilterNet3。

## 功能

- 支持 B站、YouTube，以及 yt-dlp 支持的其他网站
- 输出 mp3、m4a、flac、wav、opus，或直接保存原始音频
- 三档音质；可按开始和结束时间截取片段
- 可读取 Firefox、Edge、Chrome 登录状态，也可选择 cookies.txt
- 显示下载进度，支持取消任务、复制错误日志、打开生成文件
- 自动记住保存位置、格式和音质（不会保存 cookies 文件路径）
- 自动移除常见分享链接跟踪参数，输出文件只写入必要的标题和作者信息
- 使用独立的 Python 环境，不污染电脑上已有的 Python 软件包

### 电脑录音

- WASAPI 数字环回录音，可录制全部电脑声音
- Windows 10 2004 及以上可只录指定程序及其子进程，例如只录浏览器
- 暂停/继续、定时停止、静音自动停止
- 全局快捷键：`Ctrl+Alt+R` 开始/停止，`Ctrl+Alt+P` 暂停/继续
- 自动去掉首尾静音、按静音分段、响度标准化
- 输出 WAV、FLAC、MP3、M4A 或 Opus

首次使用录音页时，点击“安装/修复录音组件”。组件从本仓库的 GitHub Release 下载，
采用微软的进程环回接口；不会通过麦克风录制。

### 音质增强

- “AI 人声增强”使用本地 DeepFilterNet3，适合语音降噪和清晰度提升
- “清晰增强（传统）”无需安装 AI 模型，使用滤波、频谱降噪和响度标准化
- AI 模型不会恢复原文件中不存在的细节，也不建议用于音乐母带
- 首次使用 AI 模式时点击“安装 AI 组件”；模型和音频都在本机处理

## 快速开始（Windows）

1. 安装 [Python 3.10+](https://www.python.org/downloads/)，安装时勾选
   **Add python.exe to PATH**。
2. 下载本仓库（Code → Download ZIP）并解压。
3. 双击 `start.bat`。首次运行会自动安装基础组件，之后会直接打开窗口。

如果网站更新后突然无法解析，双击 `update.bat` 更新组件，再重试。

更详细的中文说明见 [USAGE_zh.txt](USAGE_zh.txt)。

## 隐私与安全

- `cookies.txt` 相当于登录凭证，请勿发送给别人或提交到 GitHub。
- 本项目的 `.gitignore` 已排除常见 cookies 文件和下载的音频。
- 浏览器登录信息只由本机上的 yt-dlp 在任务期间读取，本工具不会上传或保存它。

## 开发与测试

```powershell
python -m unittest -v
```

## 免责声明

请只下载你有权保存的内容，并遵守相关网站的服务条款和版权法律。本工具不用于绕过
付费、地区或其他访问限制。

## 许可证

[MIT](LICENSE)
