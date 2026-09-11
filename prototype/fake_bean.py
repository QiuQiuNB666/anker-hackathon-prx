#!/usr/bin/env python3
"""
假录音豆：把一段 WAV 编成设备格式的 Opus 包序列，按 50 片/秒 推给服务端。

模拟的是 Android 侧 onReceiveAudioFragment → WebSocket 这条链，
让 手机→WS→Python→解码→mel→比对 在没有设备、没有手机的情况下端到端跑通。
10/16 只换数据源，其余一行不动。

用法：
    .venv/bin/python fake_bean.py samples/demo_abnormal.wav            # 实时节奏
    .venv/bin/python fake_bean.py samples/demo_abnormal.wav --fast     # 不 sleep，压测
    .venv/bin/python fake_bean.py samples/demo_abnormal.wav --mark 2.5 # 2.5s 处打一个标记
    .venv/bin/python fake_bean.py samples/demo_abnormal.wav --pcm      # 走 type=1 手机麦路径
"""
import asyncio, struct, sys, time
import numpy as np
import opuslib
import websockets

import listen

HDR = struct.Struct("<BiiB")
SR, CH, FRAME = 16000, 2, 320


def opus_packets(x):
    """mono float32 → 交错双声道 int16 → CBR 64k 20ms Opus 包，每包应恒为 160 字节。"""
    enc = opuslib.Encoder(SR, CH, "audio")
    enc.bitrate, enc.vbr = 64000, 0
    pcm = np.clip(x * 32767, -32768, 32767).astype(np.int16)
    for i in range(0, len(pcm) - FRAME + 1, FRAME):
        stereo = np.repeat(pcm[i:i + FRAME], CH)          # L=R，模拟两个 mic 收到同一路
        yield enc.encode(stereo.tobytes(), FRAME)


def pcm_packets(x):
    pcm = np.clip(x * 32767, -32768, 32767).astype(np.int16)
    for i in range(0, len(pcm) - FRAME + 1, FRAME):
        yield pcm[i:i + FRAME].tobytes()


async def main(path, url="ws://127.0.0.1:8765/ws/audio", fast=False, mark_at=None, pcm=False):
    x = listen.decode(path)
    typ = 1 if pcm else 0
    packets = pcm_packets(x) if pcm else opus_packets(x)
    fileId = int(time.time())
    mark_seq = int(mark_at * 1000 / 20) if mark_at is not None else -1
    async with websockets.connect(url, max_size=None) as ws:
        t0 = time.perf_counter()
        for seq, pkt in enumerate(packets):
            if seq == 0 and not pcm:
                assert len(pkt) == 160, f"CBR 包长应为 160，实际 {len(pkt)}"
            flags = 0b01 if seq == mark_seq else 0
            await ws.send(HDR.pack(typ, fileId, seq, flags) + pkt)
            if not fast:
                await asyncio.sleep(max(0, t0 + (seq + 1) * 0.02 - time.perf_counter()))
        print(f"已发 {seq + 1} 片（{(seq + 1) * 20 / 1000:.1f}s），{'PCM' if pcm else 'Opus 160B'}")
        await asyncio.sleep(0.2)


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        print(__doc__); sys.exit(1)
    mark = float(a[a.index("--mark") + 1]) if "--mark" in a else None
    asyncio.run(main(a[0], fast="--fast" in a, mark_at=mark, pcm="--pcm" in a))
