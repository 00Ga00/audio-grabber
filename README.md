# 音频提取器（Audio Grabber）

一个简单的 Windows 桌面工具：粘贴视频链接，把音频保存到本地。底层使用
[yt-dlp](https://github.com/yt-dlp/yt-dlp) 和 ffmpeg。

## 功能

- 支持 B站、YouTube，以及 yt-dlp 支持的其他网站
- 输出 mp3、m4a、flac、wav、opus，或直接保存原始音频
- 三档音质；可按开始和结束时间截取片段
- 可读取 Firefox、Edge、Chrome 登录状态，也可选择 cookies.txt
- 显示下载进度，支持取消任务、复制错误日志、打开生成文件
- 自动记住保存位置、格式和音质（不会保存 cookies 文件路径）
- 使用独立的 Python 环境，不污染电脑上已有的 Python 软件包

## 快速开始（Windows）

1. 安装 [Python 3.10+](https://www.python.org/downloads/)，安装时勾选
   **Add python.exe to PATH**。
2. 下载本仓库（Code → Download ZIP）并解压。
3. 双击 `start.bat`。首次运行会自动安装所需组件，之后会直接打开窗口。

如果网站更新后突然无法解析，双击 `update.bat` 更新组件，再重试。

更详细的中文说明见 [USAGE_zh.txt](USAGE_zh.txt)。

## 隐私与安全

- `cookies.txt` 相当于登录凭证，请勿发送给别人或提交到 GitHub。
- 本项目的 `.gitignore` 已排除常见 cookies 文件和下载的音频。
- 浏览器登录信息只由本机上的 yt-dlp 在任务期间读取，本工具不会上传或保存它。

## 开发与测试

```powershell
python -m unittest -v test_audio_grabber.py
```

## 免责声明

请只下载你有权保存的内容，并遵守相关网站的服务条款和版权法律。本工具不用于绕过
付费、地区或其他访问限制。

## 许可证

[MIT](LICENSE)
