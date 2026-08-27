# Enprato

英语视频听写 / 跟读台。**16:9** 左边播视频、右边听写；**9:16** 上面播视频、下面听写。

## 怎么练

1. 导入英语视频，或粘贴 YouTube / Bilibili / mp4 直链。可附带 `.srt` / `.vtt` 英文字幕（链接常自带英文字幕；没有则自动转写分句）。
2. **第一遍不显示字幕**。默认播放完一句就停，你跟读一句，语音写入右侧稿纸；识别不准可以手改。
3. **暂停 / 重复本句**：随时停。重复默认是刚播的那一句，直到你点「听写完成」。
4. 听写完成后显示英文字幕，点选生词会标成青绿，并给出中英文释义和发音。
5. 全部句子听写完后，打开字幕做**完整跟读**，按语调、语速、节奏、内容打分。

课与进度保存在 `backend/data/sessions/`。刷新页面会回到上次那条；导入页的「继续上次」可点开历史课。同一条链接再贴一次会接着练，不会重新下载。

语音识别用本机 Whisper（CUDA）。听写阶段**不会**拿当前句正确答案去“改卷”，只用前面已练过的句子当上下文，帮助口音场景下把词拼对。

## 启动

需要：Python 3.12、Node 22、ffmpeg、yt-dlp、NVIDIA 驱动（本机已有 `faster-whisper` 与 CUDA）。

在 `products/enprato` 下：

```powershell
.\start.ps1
```

浏览器打开提示的本地地址（默认 http://127.0.0.1:5173 ）。后端在 `127.0.0.1:18787`。

环境变量可选：

- `FFMPEG_PATH` 指定 ffmpeg
- `YT_DLP_PATH` 指定 yt-dlp（用链接学习时需要）
- `ENPRATO_COOKIES` 可选，cookies.txt 路径；年龄限制或登录墙视频用
- `WHISPER_MODEL` 默认 `small.en`，可改 `medium.en` 提高分句和口音识别
- `WHISPER_DEVICE` 默认 `cuda`

## 目录

- `backend/` FastAPI：分句、听写 STT、词典、跟读打分
- `frontend/` Vite + React：听写台界面
