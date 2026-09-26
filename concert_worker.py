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
# 人声/伴奏分离（Kimberley Jensen 的 Mel-Band RoFormer 人声模型，约 230 MB）：只取人声，伴奏 = 原来 − 人声，
# 这样两者相加严格等于不分离时的结果，默认设置下音色完全不变
VOCAL_MODEL = ("vocals_mel_band_roformer.ckpt", "vocals")
# 音质修复：Apollo Universal（修复有损压缩：补回被砍掉的高频、减轻压缩失真）
APOLLO_FILE = "apollo_universal_model.ckpt"
APOLLO_PATH = "ASesYusuf1/Apollo_universal_model/resolve/main/apollo_universal_model.ckpt"
# 官方站连不上时自动改用国内镜像
APOLLO_URLS = ["https://huggingface.co/" + APOLLO_PATH, "https://hf-mirror.com/" + APOLLO_PATH]
APOLLO_CONFIG = dict(sr=44100, win=20, feature_dim=384, layer=6)
APOLLO_CHUNK = 132300  # 3 秒，和训练时一致
STEP_NAMES = {"crowd": "去观众声", "denoise": "去底噪", "restore": "音质修复", "split": "分离人声和伴奏"}
SEGMENT_SECONDS = 300
OVERLAP_SECONDS = 2


def emit(kind: str, **data) -> None:
    data["type"] = kind
    print(json.dumps(data, ensure_ascii=False), flush=True)


def make_separator(models_dir: str, out_dir: str):
    from audio_separator.separator import Separator

    return Separator(
        log_level=logging.WARNING,
        model_file_dir=models_dir,
        output_dir=out_dir,
        output_format="WAV",
        normalization_threshold=1.0,  # 输入已预先压到 0.5 峰值，不会触发自动缩放
        use_soundfile=True,
    )


def download(models_dir: str) -> None:
    os.makedirs(models_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        sep = make_separator(models_dir, tmp)
        models = [model for model, _ in MODELS.values()] + [VOCAL_MODEL[0]]
        for i, model in enumerate(models, 1):
            emit("progress", stage=f"下载模型 {i}/{len(models)}", fraction=(i - 1) / len(models))
            sep.download_model_files(model)
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
    if total and done != total:
        raise RuntimeError("没有下载完整")


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
    highpass = tone.highpass_fir(cutoff, rate) if restore else None

    seg, ov = SEGMENT_SECONDS * rate, OVERLAP_SECONDS * rate
    starts = list(range(0, max(total, 1), seg))
    loaded = {}
    meter_base, meter_high, meter_voc = tone.SpectrumMeter(rate), tone.SpectrumMeter(rate), tone.SpectrumMeter(rate)
    layout = {"base": 0, "highs": 2 if restore else None, "vocals": (4 if restore else 2) if split else None}
    width = 2 + 2 * restore + 2 * split

    with tempfile.TemporaryDirectory(prefix="concert_") as tmp, \
            sf.SoundFile(out, "w", samplerate=rate, channels=width, subtype="PCM_24") as dst:
        sep = make_separator(models_dir, tmp) if (sep_steps or split) else None
        if restore:  # 先准备好（必要时下载）音质修复模型，免得处理了一半才失败
            loaded["apollo"] = ApolloRestorer(models_dir)

        def write(data):
            dst.write(data)
            meter_base.add(data[:, :2])
            if restore:
                meter_high.add(data[:, 2:4])
            if split:
                meter_voc.add(data[:, layout["vocals"]:layout["vocals"] + 2])

        tail = None  # 上一段末尾的重叠部分（等待与下一段交叉淡化）
        total_steps = len(sep_steps) + (1 if restore else 0) + (1 if split else 0)
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
                emit("progress", stage=f"第 {index + 1}/{len(starts)} 段 · {STEP_NAMES[step]}", fraction=frac)
                model, keep = MODELS[step]
                if loaded.get("model") != model:
                    sep.load_model(model)
                    loaded["model"] = model
                result = separate_file(sep, current, keep)
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
                if loaded.get("model") != VOCAL_MODEL[0]:
                    if not os.path.isfile(os.path.join(models_dir, VOCAL_MODEL[0])):
                        emit("notice", message="第一次分离人声，正在下载人声分离模型（约 230 MB）……")
                    sep.load_model(VOCAL_MODEL[0])
                    loaded["model"] = VOCAL_MODEL[0]
                vocal_path = separate_file(sep, base_path, VOCAL_MODEL[1])
                vocals, _ = sf.read(vocal_path, dtype="float32", always_2d=True)
                os.remove(vocal_path)
                os.remove(base_path)
                if len(vocals) < want:
                    vocals = np.vstack([vocals, np.zeros((want - len(vocals), vocals.shape[1]), dtype=np.float32)])
                done = np.hstack([done, vocals[:want, :2]])

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
        with open(analysis_path, "w", encoding="utf-8") as handle:
            json.dump(analysis, handle)
    emit("done", output=out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--models", required=True)
    parser.add_argument("--steps", default="crowd,denoise")
    parser.add_argument("--analysis")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        if args.download:
            download(args.models)
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
