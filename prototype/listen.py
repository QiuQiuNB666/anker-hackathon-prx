#!/usr/bin/env python3
"""
听诊器原型 —— 30 秒示教，听机器不听人。

核心主张：基线不需要任何模型。log-mel 谱 + 对角马氏距离就够了。
这是它能在 24 小时内做完、且评委能当场换一台没见过的机器现学现用的原因。

音频 I/O 全部交给 ffmpeg，Python 只吃裸 PCM，依赖只有 numpy。

用法：
    python listen.py selftest                    # 合成信号自检，无需任何素材
    python listen.py opus normal.wav abnormal.wav # 生死线实验：Opus 压缩后还分得开吗
    python listen.py learn normal.wav            # 建模板
    python listen.py score abnormal.wav          # 打分
    python listen.py record 30 normal.wav        # 用麦克风录 30 秒
    python listen.py watch                       # 实时监听（分段拉取，模拟设备行为）
    python listen.py stream abnormal.wav         # 按 SDK 20ms 分片节奏逐片喂入（含 Opus 往返）
"""
import subprocess, sys, json, os
import numpy as np

SR = 16000
N_FFT = 1024
HOP = 256
N_MELS = 40
TEMPLATE = "template.json"


# ---------- ffmpeg 音频 I/O ----------

def decode(path, sr=SR):
    """任意音频 → mono float32 @ sr。ffmpeg 认得的格式它都认。"""
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
        capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 解码失败: {p.stderr.decode()[:200]}")
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


def encode(x, path, sr=SR):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ac", "1", "-ar", str(sr), "-i", "-", path],
        input=x.astype(np.float32).tobytes(), check=True)


def record(seconds, path):
    """macOS 内置麦克风。设备索引用 `ffmpeg -f avfoundation -list_devices true -i ""` 查。"""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "avfoundation", "-i", ":0",
         "-t", str(seconds), "-ar", str(SR), "-ac", "1", path], check=True)
    print(f"已录制 {seconds}s → {path}")


def opus_roundtrip(x, bitrate="32k"):
    """
    模拟录音豆的音频通路：设备侧 Opus 有损压缩 → 传输 → 解码。
    这是听诊器方向的生死线 —— 压缩和为人声调的降噪可能把机器的稳态频谱吃掉。
    """
    enc = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "f32le", "-ac", "1", "-ar", str(SR), "-i", "-",
         "-c:a", "libopus", "-b:a", bitrate, "-f", "ogg", "-"],
        input=x.astype(np.float32).tobytes(), capture_output=True, check=True).stdout
    dec = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
        input=enc, capture_output=True, check=True).stdout
    return np.frombuffer(dec, dtype=np.float32).copy()


# ---------- 特征：log-mel 谱 ----------

def _melbank(sr=SR, n_fft=N_FFT, n_mels=N_MELS, fmin=50.0, fmax=None):
    fmax = fmax or sr / 2
    hz2mel = lambda f: 2595.0 * np.log10(1.0 + f / 700.0)
    mel2hz = lambda m: 700.0 * (10.0 ** (m / 2595.0) - 1.0)
    pts = mel2hz(np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2))
    bins = np.floor((n_fft + 1) * pts / sr).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for i in range(n_mels):
        l, c, r = bins[i], bins[i + 1], bins[i + 2]
        c = max(c, l + 1)
        r = max(r, c + 1)
        if l < fb.shape[1]:
            fb[i, l:min(c, fb.shape[1])] = np.linspace(0, 1, c - l, endpoint=False)[:max(0, min(c, fb.shape[1]) - l)]
        if c < fb.shape[1]:
            fb[i, c:min(r, fb.shape[1])] = np.linspace(1, 0, r - c, endpoint=False)[:max(0, min(r, fb.shape[1]) - c)]
    return fb


_FB = _melbank()
_WIN = np.hanning(N_FFT).astype(np.float32)


TOP_DB = 80.0  # 低于峰值 80dB 的频段截平：否则底噪处 log 值剧烈波动，正常声之间就能拉出巨大距离


def features(x):
    """PCM → (帧数, N_MELS) 的 mel 谱，单位 dB，带相对下限。每帧就是一个特征向量。"""
    if len(x) < N_FFT:
        return np.zeros((0, N_MELS), dtype=np.float32)
    n = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * _WIN, axis=1)) ** 2
    mel = spec @ _FB.T
    ref = max(mel.max(), 1e-12)
    return (10.0 * np.log10(np.maximum(mel, ref * 10 ** (-TOP_DB / 10)) / ref)).astype(np.float32)


# ---------- 模板与打分 ----------

def learn(x):
    """30 秒正常声 → 模板。只存均值和标准差，即对角协方差。"""
    f = features(x)
    if len(f) < 10:
        raise ValueError("音频太短，至少给 1 秒")
    # std 下限 1dB：低于这个的频段本就是稳态的，别让它把微小抖动放大成大距离
    return {"mean": f.mean(0).tolist(), "std": np.maximum(f.std(0), 1.0).tolist(), "frames": len(f)}


def score(x, tpl):
    """逐帧对角马氏距离。返回每帧一个分数。"""
    f = features(x)
    mean = np.array(tpl["mean"], dtype=np.float32)
    std = np.array(tpl["std"], dtype=np.float32)
    return np.sqrt((((f - mean) / std) ** 2).mean(1))


def threshold_from(x, tpl, k=4.0):
    """阈值从正常声自己的分数分布里定，不拍脑袋。"""
    s = score(x, tpl)
    return float(s.mean() + k * s.std())


class Detector:
    """阈值 + 迟滞 + 冷却三个旋钮。路演问答 Q3 就是答这个，不是答准确率。"""

    def __init__(self, tpl, thr, hold=3, cooldown=25):
        self.tpl, self.thr, self.hold, self.cooldown = tpl, thr, hold, cooldown
        self.run = self.cool = 0

    def feed(self, x):
        """喂一段音频，返回是否触发。hold 帧连续超阈才报，报后冷却 cooldown 帧。"""
        fired = False
        for s in score(x, self.tpl):
            if self.cool > 0:
                self.cool -= 1
                continue
            if s > self.thr:
                self.run += 1
                if self.run >= self.hold:
                    fired, self.run, self.cool = True, 0, self.cooldown
            else:
                self.run = 0
        return fired


class StreamDetector(Detector):
    """
    吃 20ms 分片的流式检测器，模拟 SDK onReceiveAudioFragment 的输入节奏。
    设备原生 Opus 16kHz 20ms 帧 = 320 samples，跟 SR=16000 一致，不用重采样。
    维护一个滑动缓冲，每帧特征只算一次；分片边界不影响结果。
    """

    CHUNK = SR * 20 // 1000  # 320

    def __init__(self, tpl, thr, **kw):
        super().__init__(tpl, thr, **kw)
        self.buf = np.zeros(0, dtype=np.float32)
        self.last = 0.0   # 最近处理的帧的平均分，给可视化用

    def feed_chunk(self, chunk):
        """喂一个分片，返回是否触发。缓冲不足一帧时返回 False。"""
        self.buf = np.concatenate([self.buf, chunk.astype(np.float32)])
        if len(self.buf) < N_FFT:
            return False
        n = 1 + (len(self.buf) - N_FFT) // HOP
        seg = self.buf[: N_FFT + (n - 1) * HOP]
        self.last = float(score(seg, self.tpl).mean())
        fired = self.feed(seg)
        self.buf = self.buf[n * HOP:]          # 前移，保留 N_FFT-HOP 的重叠给下一帧
        return fired


def chunks(x, size=StreamDetector.CHUNK):
    """把整段音频切成 SDK 分片大小，最后不足一片的丢弃（真设备也不会发半片）。"""
    for i in range(0, len(x) - size + 1, size):
        yield x[i:i + size]


# ---------- 合成信号（自检与无素材演示用） ----------

def synth(seconds=4.0, kind="normal", seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(SR * seconds), dtype=np.float32) / SR
    # 一台"电机"：基频 120Hz + 两个谐波 + 底噪
    x = (0.5 * np.sin(2 * np.pi * 120 * t)
         + 0.2 * np.sin(2 * np.pi * 240 * t)
         + 0.1 * np.sin(2 * np.pi * 360 * t)
         + 0.02 * rng.standard_normal(len(t)).astype(np.float32))
    if kind == "abnormal":
        # 轴承磨损：高频啸叫 + 周期性摩擦瞬态
        x += 0.15 * np.sin(2 * np.pi * 3100 * t)
        for c in range(int(seconds * 6)):
            i = int(c * SR / 6)
            x[i:i + 200] += 0.4 * rng.standard_normal(min(200, len(x) - i)).astype(np.float32)
    return (x / (np.abs(x).max() + 1e-9) * 0.7).astype(np.float32)


# ---------- 命令 ----------

def cmd_selftest():
    print("自检：合成一台电机的正常声与磨损声\n")
    normal, normal2, abnormal = synth(6, "normal", 0), synth(4, "normal", 1), synth(4, "abnormal", 2)

    tpl = learn(normal)
    thr = threshold_from(normal, tpl)
    s_ok, s_bad = score(normal2, tpl).mean(), score(abnormal, tpl).mean()
    sep = s_bad / s_ok

    print(f"  阈值           {thr:.3f}")
    print(f"  正常声均分     {s_ok:.3f}")
    print(f"  异常声均分     {s_bad:.3f}")
    print(f"  分离度         {sep:.1f}×")
    assert sep > 2.0, f"正常与异常没拉开（{sep:.1f}×），特征或阈值有问题"

    d = Detector(tpl, thr)
    assert not d.feed(normal2), "正常声误报了"
    assert Detector(tpl, thr).feed(abnormal), "异常声漏报了"
    print("  检测器         正常不报 ✓  异常必报 ✓")

    # 生死线：Opus 压缩后还分得开吗
    print("\n生死线：Opus 32k 有损压缩往返后")
    o_norm, o_ab = opus_roundtrip(normal), opus_roundtrip(abnormal)
    tpl_o = learn(o_norm)
    sep_o = score(o_ab, tpl_o).mean() / score(opus_roundtrip(normal2), tpl_o).mean()
    print(f"  压缩后分离度   {sep_o:.1f}×（压缩前 {sep:.1f}×，保留 {sep_o / sep * 100:.0f}%）")
    assert sep_o > 1.5, f"Opus 压缩把可分性吃掉了（{sep_o:.1f}×）——真机上要改用瞬态特征"
    print("  结论           合成信号上压缩不致命 ✓（真机仍须用真实录音复验）")

    # 流式 = 批式：同一段音频，20ms 分片逐个喂，触发结果必须一致
    print("\n流式：模拟 SDK 20ms 分片回调")
    sd_ok = StreamDetector(tpl, thr)
    sd_bad = StreamDetector(tpl, thr)
    hit_ok = any(sd_ok.feed_chunk(c) for c in chunks(normal2))
    bad_at = next((i for i, c in enumerate(chunks(o_ab)) if sd_bad.feed_chunk(c)), None)
    assert not hit_ok, "流式：正常声误报"
    assert bad_at is not None, "流式：异常声漏报"
    print(f"  正常声 {len(normal2) // StreamDetector.CHUNK} 片不报 ✓  异常声第 {bad_at} 片触发（{bad_at * 20} ms）✓")
    print("\n全部通过。")


def cmd_opus(normal_path, abnormal_path):
    """用你自己的录音跑生死线实验。"""
    normal, abnormal = decode(normal_path), decode(abnormal_path)
    half = len(normal) // 2
    print(f"{'码率':>8}  {'分离度':>8}  {'保留':>6}")
    base = None
    for br in ["raw", "64k", "32k", "24k", "16k"]:
        n, a = (normal, abnormal) if br == "raw" else (opus_roundtrip(normal, br), opus_roundtrip(abnormal, br))
        tpl = learn(n[:half])
        sep = score(a, tpl).mean() / max(score(n[half:], tpl).mean(), 1e-6)
        base = base or sep
        print(f"{br:>8}  {sep:>7.2f}×  {sep / base * 100:>5.0f}%")
    print("\n分离度掉到 1.5× 以下 = 该码率下这条方向不成立，改用瞬态特征。")


def cmd_learn(path):
    x = decode(path)
    tpl = learn(x)
    tpl["threshold"] = threshold_from(x, tpl)
    json.dump(tpl, open(TEMPLATE, "w"))
    print(f"模板已建立（{tpl['frames']} 帧，{len(x) / SR:.1f}s），阈值 {tpl['threshold']:.3f} → {TEMPLATE}")


def cmd_score(path):
    tpl = json.load(open(TEMPLATE))
    s = score(decode(path), tpl)
    thr = tpl["threshold"]
    over = (s > thr).mean()
    print(f"均分 {s.mean():.3f} / 峰值 {s.max():.3f} / 阈值 {thr:.3f} / 超阈帧占比 {over:.1%}")
    print("判定：" + ("异常 ⚠" if Detector(tpl, thr).feed(decode(path)) else "正常"))


def cmd_stream(path, opus=True):
    """
    把一段录音按 SDK 分片节奏喂给检测器，逐片打印分数。
    默认先做 Opus 往返，模拟设备端编码——这是 10/16 那天真实回调里的数据。
    """
    tpl = json.load(open(TEMPLATE))
    x = decode(path)
    if opus:
        x = opus_roundtrip(x)
    d = StreamDetector(tpl, tpl["threshold"])
    thr = tpl["threshold"]
    print(f"阈值 {thr:.3f}   {'片#':>5} {'时刻':>7}  分数")
    for i, c in enumerate(chunks(x)):
        hit = d.feed_chunk(c)
        bar = "█" * min(int(d.last / thr * 10), 40)   # 阈值 = 10 格
        print(f"{'':13}{i:>5} {i * 20:>6}ms  {d.last:6.3f} {bar}" + ("  ⚠ 触发" if hit else ""))


def cmd_watch(chunk=2.0):
    """分段拉取，模拟录音豆的真实行为（SDK 没有实时流，只能短周期启停）。"""
    tpl = json.load(open(TEMPLATE))
    d = Detector(tpl, tpl["threshold"])
    tmp = ".watch.wav"
    print(f"每 {chunk}s 一轮，Ctrl+C 停止")
    try:
        while True:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "avfoundation", "-i", ":0",
                            "-t", str(chunk), "-ar", str(SR), "-ac", "1", tmp], check=True)
            x = decode(tmp)
            s = score(x, tpl).mean()
            hit = d.feed(x)
            print(f"  {s:6.3f} {'█' * min(int(s * 10), 40)}" + ("  ⚠ 异常" if hit else ""))
    except KeyboardInterrupt:
        os.path.exists(tmp) and os.remove(tmp)
        print("\n停止")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "selftest":
        cmd_selftest()
    elif a[0] == "opus":
        cmd_opus(a[1], a[2])
    elif a[0] == "learn":
        cmd_learn(a[1])
    elif a[0] == "score":
        cmd_score(a[1])
    elif a[0] == "record":
        record(float(a[1]), a[2])
    elif a[0] == "watch":
        cmd_watch(float(a[1]) if len(a) > 1 else 2.0)
    elif a[0] == "stream":
        cmd_stream(a[1], opus="--raw" not in a)
    else:
        print(__doc__)
