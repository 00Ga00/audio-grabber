"""演唱会降噪的 AI 处理进程（运行在独立环境 .venv-ai 里）。

用法：
  python concert_worker.py --download --models DIR
  python concert_worker.py --input in.wav --output out.wav --models DIR --steps crowd,denoise

输出每行一个 JSON：device / progress / done / error。
长音频按 5 分钟分段、段间重叠 2 秒线性交叉淡化，内存占用小，还能报告进度。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import traceback

MODELS = {
    # 去观众声：输出 crowd（观众）和 other（其余=音乐），保留 other
    "crowd": ("mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt", "other"),
    # 去底噪：输出 dry（干净）和 other（噪声），保留 dry
    "denoise": ("denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt", "dry"),
}
# 人声/伴奏分离（Kimberley Jensen 的 Mel-Band RoFormer 人声模型，约 900 MB）：只取人声，伴奏 = 原来 − 人声，
# 这样两者相加严格等于不分离时的结果，默认设置下音色完全不变
VOCAL_MODEL = ("vocals_mel_band_roformer.ckpt", "vocals")
# 去混响（anvuew 的 Mel-Band RoFormer De-Reverb，SDR 19.2）：只存“混响”这一部分，用滑块决定留多少
DEREVERB_MODEL = ("dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt", "noreverb")
# 音质修复：Apollo Universal（修复有损压缩：补回被砍掉的高频、减轻压缩失真）
APOLLO_FILE = "apollo_universal_model.ckpt"
APOLLO_PATH = "ASesYusuf1/Apollo_universal_model/resolve/main/apollo_universal_model.ckpt"
# 官方站连不上时自动改用国内镜像
APOLLO_URLS = ["https://huggingface.co/" + APOLLO_PATH, "https://hf-mirror.com/" + APOLLO_PATH]
APOLLO_CONFIG = dict(sr=44100, win=20, feature_dim=384, layer=6)
APOLLO_CHUNK = 132300  # 3 秒，和训练时一致
STEP_NAMES = {"crowd": "去观众声", "denoise": "去底噪", "restore": "音质修复", "split": "分离人声和伴奏", "dereverb": "分离混响"}
SEGMENT_SECONDS = 300
OVERLAP_SECONDS = 2


def emit(kind: str, **data) -> None:
    data["type"] = kind
    print(json.dumps(data, ensure_ascii=False), flush=True)


# 模型内部每处理一小块就回调一次（用来显示细致的进度），见 _install_hooks
PROGRESS = {"cb": None}
FAST = {"on": True}


def _install_hooks() -> None:
    """1) 接管 audio-separator 的 tqdm 进度条 → 细致进度；
    2) 模型下载改成“先下到 .part、完整后再改名”并显示 MB 进度（原来中断后会留下损坏的模型文件）。"""
    import audio_separator.separator.architectures.mdxc_separator as mdxc
    from audio_separator.separator import Separator

    def progress_iter(iterable=None, *args, **kwargs):
        items = list(iterable) if iterable is not None else []
        count = max(len(items), 1)
        for i, item in enumerate(items):
            if PROGRESS["cb"]:
                PROGRESS["cb"](i / count)
            yield item

    mdxc.tqdm = progress_iter

    def safe_download(self, url, output_path):
        if os.path.isfile(output_path):
            return
        import requests

        name = os.path.basename(output_path)
        part = output_path + ".part"
        urls = [url] + ([url.replace("https://huggingface.co/", "https://hf-mirror.com/")] if "huggingface.co/" in url else [])
        response, problem = None, ""
        for candidate in urls:           # Hugging Face 连不上时自动改用镜像
            try:
                response = requests.get(candidate, stream=True, timeout=60)
                if response.status_code == 200:
                    break
                problem = f"HTTP {response.status_code}"
            except Exception as error:
                problem = str(error)[:200]
            response = None
        if response is None:
            raise RuntimeError(f"下载 {name} 失败（{problem}），请检查网络后重试。")
        total = int(response.headers.get("content-length", 0))
        done, last = 0, 0
        with open(part, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                handle.write(chunk)
                done += len(chunk)
                if total > 20_000_000 and done - last > 5_000_000:
                    last = done
                    emit("download", name=name, done=done, total=total)
        if total and done != total:
            raise RuntimeError(f"{name} 没有下载完整，请重试。")
        os.replace(part, output_path)

    Separator.download_file_if_not_exists = safe_download


def make_separator(models_dir: str, out_dir: str):
    from audio_separator.separator import Separator
    import torch

    return Separator(
        log_level=logging.WARNING,
        model_file_dir=models_dir,
        output_dir=out_dir,
        output_format="WAV",
        normalization_threshold=1.0,  # 输入已预先压到 0.5 峰值，不会触发自动缩放
        use_soundfile=True,
        # 显卡上用半精度（autocast）：通常快 1.5–2 倍；可在“设置…”里关掉（关掉后和旧版结果逐位一致）
        use_autocast=bool(FAST["on"] and torch.cuda.is_available()),
    )


class ModelPool:
    """每个模型只加载一次、一直留在显存里（原来每段都要重新加载一遍）。显存不够时自动释放其他模型再试。"""

    def __init__(self, models_dir: str, out_dir: str):
        self.models_dir, self.out_dir, self.pool = models_dir, out_dir, {}

    def get(self, model: str):
        if model not in self.pool and FAST.get("low_vram") and self.pool:
            import torch   # 显存小：一次只放一个模型
            self.pool.clear()
            torch.cuda.empty_cache()
        if model not in self.pool:
            if not os.path.isfile(os.path.join(self.models_dir, model)):
                emit("notice", message=f"第一次使用，正在下载模型 {model}（约 900 MB）……")
            sep = make_separator(self.models_dir, self.out_dir)
            sep.load_model(model)
            self.pool[model] = sep
        return self.pool[model]

    def separate(self, model: str, path: str, keep: str) -> str:
        import torch
        try:
            return separate_file(self.get(model), path, keep)
        except torch.cuda.OutOfMemoryError:
            for name in [m for m in self.pool if m != model]:
                del self.pool[name]
            torch.cuda.empty_cache()
            emit("notice", message="显存不够，改为一次只放一个模型（会慢一点）。")
            return separate_file(self.get(model), path, keep)


def download(models_dir: str, steps: list[str] | None = None) -> None:
    """按安装的版本只下载要用的模型（配置低的电脑不下大模型）。steps 为空 = 全部。"""
    steps = steps or ["crowd", "denoise", "split", "dereverb", "restore"]
    os.makedirs(models_dir, exist_ok=True)
    table = dict((k, v[0]) for k, v in MODELS.items())
    table.update(split=VOCAL_MODEL[0], dereverb=DEREVERB_MODEL[0])
    models = [table[k] for k in steps if k in table]
    with tempfile.TemporaryDirectory() as tmp:
        sep = make_separator(models_dir, tmp)
        for i, model in enumerate(models, 1):
            emit("progress", stage=f"下载模型 {i}/{len(models)}", fraction=(i - 1) / max(len(models), 1))
            sep.download_model_files(model)
    if "restore" in steps:
        download_apollo(models_dir)
    emit("done", output=models_dir)


def download_apollo(models_dir: str) -> str:
    """下载音质修复模型（约 150 MB），已存在就跳过；先下到 .part，完整后再改名。"""
    target = os.path.join(models_dir, APOLLO_FILE)
    if os.path.isfile(target) and os.path.getsize(target) > 10_000_000:
        return target
    os.makedirs(models_dir, exist_ok=True)
    part = target + ".part"
    errors = []
    for url in APOLLO_URLS:
        try:
            _fetch(url, part)
            break
        except Exception as error:  # 换下一个地址
            errors.append(f"{url.split('/')[2]}：{error}")
    else:
        raise RuntimeError("音质修复模型下载失败（请检查网络后重试）：\n" + "\n".join(errors))
    os.replace(part, target)
    return target


def _fetch(url: str, part: str) -> None:
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "AudioStudio"})
    with urllib.request.urlopen(request, timeout=60) as response, open(part, "wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            if total:
                emit("progress", stage=f"下载音质修复模型 {done >> 20}/{total >> 20} MB", fraction=done / total)
                emit("download", name="Apollo 音质修复模型", done=done, total=total)
    if total and done != total:
        raise RuntimeError("没有下载完整")


DOWNLOAD_NAME = {"name": "模型"}


def hook_byte_downloads(default_name: str | None = None) -> None:
    """Hugging Face、torch.hub 下载模型时用的都是 tqdm（单位 B）；接管它，把字节进度发给界面。"""
    import time as _time

    if default_name:
        DOWNLOAD_NAME["name"] = default_name
    try:
        from tqdm import std
    except Exception:
        return
    if getattr(std.tqdm, "_studio_hooked", False):
        return
    original = std.tqdm.update
    original_init = std.tqdm.__init__
    last = {"t": 0.0}

    def init(self, *args, **kwargs):
        # 关掉显示的 tqdm 不会记 unit/desc，这里自己记一份
        self._studio = (kwargs.get("unit", ""), kwargs.get("desc", "") or "", kwargs.get("total"))
        original_init(self, *args, **kwargs)

    def update(self, n=1):
        result = original(self, n)
        try:
            unit, desc, total = getattr(self, "_studio", ("", "", None))
            unit = getattr(self, "unit", "") or unit
            total = getattr(self, "total", None) or total
            if unit == "B" and total:
                self._studio_n = getattr(self, "_studio_n", 0) + (n or 0)
                done = max(getattr(self, "n", 0) or 0, self._studio_n)
                now = _time.time()
                if now - last["t"] > 0.3 or done >= total:
                    last["t"] = now
                    name = (getattr(self, "desc", "") or desc or "").strip(" :") or DOWNLOAD_NAME["name"]
                    emit("download", name=name, done=int(done), total=int(total))
        except Exception:
            pass
        return result

    std.tqdm.update = update
    std.tqdm.__init__ = init
    std.tqdm._studio_hooked = True


class ApolloRestorer:
    """Apollo 音质修复：3 秒一块、50% 重叠、汉宁窗叠加（窗口之和恒为 1，没有接缝）。"""

    def __init__(self, models_dir: str):
        import torch
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from apollo_model import Apollo

        path = download_apollo(models_dir)
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        model = Apollo(**APOLLO_CONFIG)
        state = torch.load(path, map_location="cpu", weights_only=False)
        for key in ("state", "state_dict", "model_state_dict"):
            if isinstance(state, dict) and key in state and isinstance(state[key], dict):
                state = state[key]
        own = model.state_dict()
        fixed = {}
        for name, value in state.items():
            for prefix in ("audio_model.", "model.", "module."):
                if name.startswith(prefix) and name[len(prefix):] in own:
                    name = name[len(prefix):]
                    break
            fixed[name] = value
        missing = [k for k in own if k not in fixed]
        if missing:
            raise RuntimeError(f"音质修复模型和程序不匹配（缺少 {len(missing)} 个参数，例如 {missing[0]}）。")
        model.load_state_dict({k: fixed[k] for k in own})
        self.model = model.to(self.device).eval()

    def __call__(self, audio):
        """audio: (样本数, 声道) float32 → 同样形状的修复结果。"""
        import numpy as np
        torch = self.torch
        n, channels = audio.shape
        size, hop = APOLLO_CHUNK, APOLLO_CHUNK // 2
        window = np.sin(np.pi * (np.arange(size) + 0.5) / size) ** 2  # 50% 重叠时逐点相加 = 1
        padded = np.pad(audio.T, ((0, 0), (hop, hop + (-n) % hop)))
        out = np.zeros_like(padded)
        starts = list(range(0, padded.shape[1] - size + 1, hop))
        batch = 1  # 一次一块：显存/内存占用约 1–2 GB
        with torch.inference_mode():
            for i in range(0, len(starts), batch):
                group = starts[i:i + batch]
                chunk = np.stack([padded[:, s:s + size] for s in group])  # B, C, T
                x = torch.from_numpy(chunk).float().to(self.device)
                y = self.model(x.reshape(-1, 1, size)).reshape(len(group), channels, size)
                y = y.float().cpu().numpy()
                for s, piece in zip(group, y):
                    out[:, s:s + size] += piece * window
        return out[:, hop:hop + n].T.astype(np.float32)


KEY_NAMES = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"]


def detect_key_tempo(mono, rate: int) -> dict:
    """调性（Krumhansl 调性轮廓）和速度（librosa 节拍跟踪）。只是参考，现场录音可能不准。"""
    import numpy as np
    if len(mono) < rate * 10:
        return {}
    try:
        import librosa
        chroma = librosa.feature.chroma_stft(y=mono, sr=rate, hop_length=4096).mean(axis=1)
        major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
        minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
        best = max(((np.corrcoef(np.roll(profile, shift), chroma)[0, 1], shift, mode)
                    for mode, profile in (("major", major), ("minor", minor)) for shift in range(12)))
        tempo, _ = librosa.beat.beat_track(y=mono, sr=rate, hop_length=512)
        return {"key": KEY_NAMES[best[1]] + (" 大调" if best[2] == "major" else " 小调"),
                "key_confidence": round(float(best[0]), 2),
                "bpm": round(float(np.atleast_1d(tempo)[0]))}
    except Exception as error:  # 分析失败不影响主流程
        emit("notice", message=f"调性/速度分析失败：{str(error)[:120]}")
        return {}


def reference_levels(path: str) -> None:
    """参考曲的长期 1/3 倍频程频谱（给“参考曲音色匹配”用）。"""
    import soundfile as sf
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import tone
    info = sf.info(path)
    meter = tone.SpectrumMeter(info.samplerate)
    for block in sf.blocks(path, blocksize=info.samplerate * 30, dtype="float32", always_2d=True):
        meter.add(block)
    emit("reference", levels=[round(float(v), 2) for v in tone.band_levels(*meter.spectrum())])
    emit("done", output=path)


def separate_file(sep, path: str, keep: str) -> str:
    outputs = sep.separate(path)
    for name in outputs:
        full = name if os.path.isabs(name) else os.path.join(sep.output_dir, name)
        if f"({keep})".lower() in os.path.basename(full).lower():
            return full
    raise RuntimeError(f"模型没有输出 {keep} 声部：{outputs}")


def process(inp: str, out: str, models_dir: str, steps: list[str], analysis_path: str | None = None) -> None:
    """AI 处理。输出文件：声道 1–2 = 去观众声/去底噪后的声音；
    如果做了音质修复，再加声道 3–4 = 模型补回的、原来被压缩砍掉的高频（只有截止频率以上的部分）。
    这样最后的混合、均衡都能在不重跑 AI 的情况下随时调整。"""
    import numpy as np
    import soundfile as sf
    import torch
    from scipy.signal import fftconvolve
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import tone

    if torch.cuda.is_available():
        emit("device", name=torch.cuda.get_device_name(0), gpu=True)
    else:
        emit("device", name="CPU（没有检测到可用的 NVIDIA 显卡，会比较慢）", gpu=False)

    info = sf.info(inp)
    rate, total = info.samplerate, info.frames

    # 先整体分析一遍原声：频谱（给可视化用）和压缩截止频率
    meter_in = tone.SpectrumMeter(rate)
    for block in sf.blocks(inp, blocksize=rate * 30, dtype="float32", always_2d=True):
        meter_in.add(block)
    freqs, power_in = meter_in.spectrum()
    cutoff = tone.detect_cutoff(freqs, power_in)
    restore = "restore" in steps and cutoff is not None
    if "restore" in steps and cutoff is None:
        emit("notice", message="这段声音的高音是完整的，不需要音质修复。")
    elif restore:
        emit("notice", message=f"检测到压缩截止频率约 {cutoff / 1000:.1f} kHz，只补这以上的部分。")
    sep_steps = [s for s in steps if s in MODELS]
    split = "split" in steps
    dereverb = "dereverb" in steps
    highpass = tone.highpass_fir(cutoff, rate) if restore else None

    seg, ov = SEGMENT_SECONDS * rate, OVERLAP_SECONDS * rate
    starts = list(range(0, max(total, 1), seg))
    loaded = {}
    meter_base, meter_high, meter_voc = tone.SpectrumMeter(rate), tone.SpectrumMeter(rate), tone.SpectrumMeter(rate)
    line_base, line_inst, line_voc = tone.Timeline(rate), tone.Timeline(rate), tone.Timeline(rate)
    layout = {"base": 0, "highs": None, "vocals": None, "reverb": None}
    width = 2
    for key, on in (("highs", restore), ("vocals", split), ("reverb", dereverb)):
        if on:
            layout[key] = width
            width += 2
    tonal = []  # 调性/速度分析用：最多 10 分钟、22 kHz 单声道

    with tempfile.TemporaryDirectory(prefix="concert_") as tmp, \
            sf.SoundFile(out, "w", samplerate=rate, channels=width, subtype="PCM_24") as dst:
        pool = ModelPool(models_dir, tmp)
        if restore:  # 先准备好（必要时下载）音质修复模型，免得处理了一半才失败
            loaded["apollo"] = ApolloRestorer(models_dir)

        def write(data):
            dst.write(data)
            meter_base.add(data[:, :2])
            if sum(len(t) for t in tonal) < 22050 * 600:
                tonal.append(data[::2, :2].mean(axis=1).astype(np.float32))
            line_base.add(data[:, :2])
            if split:
                vocals_part = data[:, layout["vocals"]:layout["vocals"] + 2]
                line_voc.add(vocals_part)
                line_inst.add(data[:, :2] - vocals_part)
            if restore:
                meter_high.add(data[:, 2:4])
            if split:
                meter_voc.add(data[:, layout["vocals"]:layout["vocals"] + 2])

        tail = None  # 上一段末尾的重叠部分（等待与下一段交叉淡化）
        total_steps = len(sep_steps) + (1 if restore else 0) + (1 if split else 0) + (1 if dereverb else 0)
        for index, start in enumerate(starts):
            read_start = max(0, start - ov) if index > 0 else 0
            read_end = min(total, start + seg + (ov if index + 1 < len(starts) else 0))
            block, _ = sf.read(inp, start=read_start, stop=read_end, dtype="float32", always_2d=True)
            if block.shape[1] == 1:
                block = np.repeat(block, 2, axis=1)
            piece = os.path.join(tmp, f"seg{index:04d}.wav")
            sf.write(piece, block[:, :2], rate, subtype="FLOAT")

            current = piece
            for s_i, step in enumerate(sep_steps):
                frac = (index + s_i / max(total_steps, 1)) / len(starts)
                stage = f"第 {index + 1}/{len(starts)} 段 · {STEP_NAMES[step]}"
                emit("progress", stage=stage, fraction=frac)
                PROGRESS["cb"] = lambda x, b=frac, st=stage: emit("progress", stage=st, fraction=b + x / max(total_steps, 1) / len(starts))
                model, keep = MODELS[step]
                result = pool.separate(model, current, keep)
                PROGRESS["cb"] = None
                if current != piece:
                    os.remove(current)
                current = result
            done, _ = sf.read(current, dtype="float32", always_2d=True)
            if current != piece:
                os.remove(current)
            os.remove(piece)
            want = read_end - read_start
            if len(done) < want:
                done = np.vstack([done, np.zeros((want - len(done), done.shape[1]), dtype=np.float32)])
            done = done[:want, :2]

            if restore:
                frac = (index + len(sep_steps) / max(total_steps, 1)) / len(starts)
                emit("progress", stage=f"第 {index + 1}/{len(starts)} 段 · 音质修复", fraction=frac)
                repaired = loaded["apollo"](done)
                highs = fftconvolve(repaired, highpass[:, None], mode="same", axes=0).astype(np.float32)
                done = np.hstack([done, highs])

            if split:
                frac = (index + (len(sep_steps) + restore) / max(total_steps, 1)) / len(starts)
                emit("progress", stage=f"第 {index + 1}/{len(starts)} 段 · 分离人声和伴奏", fraction=frac)
                base_path = os.path.join(tmp, f"seg{index:04d}_base.wav")
                sf.write(base_path, done[:, :2], rate, subtype="FLOAT")
                stage = f"第 {index + 1}/{len(starts)} 段 · 分离人声和伴奏"
                PROGRESS["cb"] = lambda x, b=frac, st=stage: emit("progress", stage=st, fraction=b + x / max(total_steps, 1) / len(starts))
                vocal_path = pool.separate(VOCAL_MODEL[0], base_path, VOCAL_MODEL[1])
                PROGRESS["cb"] = None
                vocals, _ = sf.read(vocal_path, dtype="float32", always_2d=True)
                os.remove(vocal_path)
                os.remove(base_path)
                if len(vocals) < want:
                    vocals = np.vstack([vocals, np.zeros((want - len(vocals), vocals.shape[1]), dtype=np.float32)])
                done = np.hstack([done, vocals[:want, :2]])

            if dereverb:
                frac = (index + (total_steps - 1) / max(total_steps, 1)) / len(starts)
                stage = f"第 {index + 1}/{len(starts)} 段 · 分离混响"
                emit("progress", stage=stage, fraction=frac)
                base_path = os.path.join(tmp, f"seg{index:04d}_rev.wav")
                sf.write(base_path, done[:, :2], rate, subtype="FLOAT")
                PROGRESS["cb"] = lambda x, b=frac, st=stage: emit("progress", stage=st, fraction=b + x / max(total_steps, 1) / len(starts))
                dry_path = pool.separate(DEREVERB_MODEL[0], base_path, DEREVERB_MODEL[1])
                PROGRESS["cb"] = None
                dry, _ = sf.read(dry_path, dtype="float32", always_2d=True)
                os.remove(dry_path)
                os.remove(base_path)
                if len(dry) < want:
                    dry = np.vstack([dry, np.zeros((want - len(dry), dry.shape[1]), dtype=np.float32)])
                done = np.hstack([done, done[:, :2] - dry[:want, :2]])   # 混响 = 原来 − 去混响后

            # 本段读入范围 = [start-ov, end+ov]：左边 ov 只作上下文丢弃；
            # [start, start+ov) 与上一段多处理的尾巴交叉淡化；[end, end+ov) 留作本段的尾巴。
            ctx = start - read_start
            body_end = min(total, start + seg) - read_start
            pos = ctx
            if tail is not None and len(tail):
                n = min(len(tail), body_end - ctx)
                fade = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
                write(tail[:n] * (1 - fade) + done[ctx:ctx + n] * fade)
                pos = ctx + n
            write(done[pos:body_end])
            tail = done[body_end:] if body_end < want else None
        emit("progress", stage="完成", fraction=1.0)

    if analysis_path:
        _, power_base = meter_base.spectrum()
        analysis = {
            "centers": [round(c, 1) for c in tone.CENTERS],
            "original": [round(float(v), 2) for v in tone.band_levels(freqs, power_in)],
            "processed": [round(float(v), 2) for v in tone.band_levels(freqs, power_base)],
            "highs": [round(float(v), 2) for v in tone.band_levels(freqs, meter_high.spectrum()[1])] if restore else None,
            "cutoff": cutoff,
            "auto_curve": tone.tone_curve(freqs, power_base, cutoff),
            "has_highs": restore,
            "layout": layout,
            "vocals": [round(float(v), 2) for v in tone.band_levels(freqs, meter_voc.spectrum()[1])] if split else None,
        }
        analysis.update(detect_key_tempo(np.concatenate(tonal) if tonal else np.zeros(0, np.float32), 22050))
        loud, harsh = line_base.result()
        analysis["timeline"] = {"loud": loud, "harsh": harsh}
        if split:
            analysis["timeline"]["vocal"] = line_voc.result()[0]
            analysis["timeline"]["inst_harsh"] = line_inst.result()[1]
        analysis["layout"] = layout
        with open(analysis_path, "w", encoding="utf-8") as handle:
            json.dump(analysis, handle)
    emit("done", output=out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--download-steps", help="只下载这些步骤的模型（逗号分隔）")
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--models", required=True)
    parser.add_argument("--steps", default="crowd,denoise")
    parser.add_argument("--analysis")
    parser.add_argument("--reference", help="只分析参考曲的频谱")
    parser.add_argument("--low-vram", action="store_true", help="显存小：一次只放一个模型")
    parser.add_argument("--no-fast", action="store_true", help="不用半精度（结果和旧版逐位一致，速度慢一些）")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        FAST["on"] = not args.no_fast
        FAST["low_vram"] = args.low_vram
        _install_hooks()
        if args.reference:
            reference_levels(args.reference)
        elif args.download:
            download(args.models, [x for x in (args.download_steps or "").split(",") if x] or None)
        else:
            steps = [s for s in args.steps.split(",") if s in STEP_NAMES]
            if not steps:
                raise ValueError("没有选择任何处理步骤。")
            process(args.input, args.output, args.models, steps, args.analysis)
        return 0
    except Exception as error:
        emit("error", message=str(error), detail=traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
