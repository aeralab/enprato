from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import librosa
import numpy as np

from .asr import transcribe_speech


def _wer(ref: str, hyp: str) -> float:
    ref_t = _tokens(ref)
    hyp_t = _tokens(hyp)
    if not ref_t:
        return 0.0 if not hyp_t else 1.0
    n, m = len(ref_t), len(hyp_t)
    dp = np.zeros((n + 1, m + 1), dtype=np.int32)
    dp[:, 0] = np.arange(n + 1)
    dp[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref_t[i - 1] == hyp_t[j - 1] else 1
            dp[i, j] = min(
                dp[i - 1, j] + 1,
                dp[i, j - 1] + 1,
                dp[i - 1, j - 1] + cost,
            )
    return float(dp[n, m]) / float(n)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 8 or b.size < 8:
        return 0.0
    if float(a.std()) < 1e-6 or float(b.std()) < 1e-6:
        return 0.0
    r = float(np.corrcoef(a, b)[0, 1])
    if math.isnan(r):
        return 0.0
    return r


def _resample(arr: np.ndarray, n: int = 240) -> np.ndarray:
    if arr.size == 0:
        return np.zeros(n)
    x = np.linspace(0.0, 1.0, arr.size)
    xi = np.linspace(0.0, 1.0, n)
    return np.interp(xi, x, arr)


def _pitch(y: np.ndarray, sr: int) -> np.ndarray:
    f0, _, _ = librosa.pyin(
        y,
        fmin=70,
        fmax=380,
        sr=sr,
        frame_length=2048,
    )
    f0 = np.asarray(f0, dtype=np.float64)
    voiced = np.nan_to_num(f0, nan=0.0)
    return voiced


def _speed_score(orig_dur: float, user_dur: float) -> float:
    if orig_dur <= 0.05 or user_dur <= 0.05:
        return 0.0
    ratio = user_dur / orig_dur
    return float(max(0.0, min(100.0, 100.0 * math.exp(-2.8 * abs(math.log(ratio))))))


def score_shadowing(
    original_wav: Path,
    user_wav: Path,
    reference_text: str,
) -> dict[str, Any]:
    orig, sr = librosa.load(str(original_wav), sr=16000, mono=True)
    user, _ = librosa.load(str(user_wav), sr=16000, mono=True)
    orig_dur = float(len(orig) / sr)
    user_dur = float(len(user) / sr)

    speed = _speed_score(orig_dur, user_dur)
    window = sr * 90
    orig_w, user_w = orig[:window], user[:window]

    f0_o = _resample(_pitch(orig_w, sr))
    f0_u = _resample(_pitch(user_w, sr))
    pitch_r = max(0.0, _pearson(f0_o, f0_u))
    pitch = round(100.0 * pitch_r, 1)

    onset_o = _resample(librosa.onset.onset_strength(y=orig_w, sr=sr))
    onset_u = _resample(librosa.onset.onset_strength(y=user_w, sr=sr))
    rhythm_r = max(0.0, _pearson(onset_o, onset_u))
    rhythm = round(100.0 * rhythm_r, 1)

    spoken = transcribe_speech(user_wav, context=reference_text[:500], target=reference_text[:800])
    wer = _wer(reference_text, spoken)
    content = round(max(0.0, min(100.0, 100.0 * (1.0 - wer))), 1)

    overall = round(0.32 * pitch + 0.28 * speed + 0.15 * rhythm + 0.25 * content, 1)
    return {
        "overall": overall,
        "pitch": round(pitch, 1),
        "speed": round(speed, 1),
        "rhythm": round(rhythm, 1),
        "content": content,
        "orig_duration": round(orig_dur, 2),
        "user_duration": round(user_dur, 2),
        "transcript": spoken,
        "wer": round(wer, 3),
    }
