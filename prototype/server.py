#!/usr/bin/env python3
"""
听诊器服务端：收手机转发的录音豆分片，解码，喂检测器。

帧格式（与 Android 侧约定，见 docs/04-决赛/SDK接入清单.md 第三节）：
    [type u8][fileId i32 LE][seq i32 LE][flags u8][payload]
    type  0 = 设备 Opus packet（160B，20ms，16kHz 双声道，CBR 64k）
          1 = 手机麦 PCM16 mono 16kHz（兜底路径，同一条管线）
    flags bit0 = isMark（物理键打标）  bit1 = isAppendPreAudio（续传/前置缓冲，跳过）

手机不解码，160 字节原样转发；这里 opuslib 一行解完。
决赛 17:00 切兜底 = 客户端把 type 从 0 改成 1，服务端一行不动。

用法：
    .venv/bin/python server.py                 # 起在 :8765
    然后手机 adb reverse tcp:8765 tcp:8765，或本机跑 fake_bean.py 模拟

注意：uvloop 0.22 在 Python 3.14 上会静默卡死（不绑端口、不输出），
所以这里固定 loop=asyncio http=h11。别用裸 `uvicorn server:app`。
"""
import json, struct, time
import numpy as np
import opuslib
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

import listen

app = FastAPI()
HDR = struct.Struct("<BiiB")          # type, fileId, seq, flags → 10 字节
SR, CH, FRAME = 16000, 2, 320         # 每声道 320 采样 = 20ms


def on_trigger(fileId, seq, score):
    """触发动作。10/16 这里接智能插座 HTTP；现在只打印。"""
    print(f"\n⚠  触发  file={fileId} seq={seq} t={seq * 20}ms score={score:.2f}\n", flush=True)


@app.websocket("/ws/audio")
async def ws_audio(ws: WebSocket):
    await ws.accept()
    tpl = json.load(open(listen.TEMPLATE))
    det = listen.StreamDetector(tpl, tpl["threshold"])
    dec = opuslib.Decoder(SR, CH)
    n = t0 = 0
    marks = []
    print(f"客户端接入，阈值 {tpl['threshold']:.3f}", flush=True)
    try:
        while True:
            raw = await ws.receive_bytes()
            typ, fileId, seq, flags = HDR.unpack_from(raw)
            payload = raw[HDR.size:]

            if flags & 0b10:              # isAppendPreAudio：续传/前置缓冲，不进检测器
                continue
            if typ == 0:                  # 设备 Opus → 1280B int16 交错双声道 → mono float32
                pcm = np.frombuffer(dec.decode(payload, FRAME), np.int16).reshape(-1, CH).mean(1)
            elif typ == 1:                # 手机麦 PCM16 mono
                pcm = np.frombuffer(payload, np.int16).astype(np.float32)
            else:
                continue
            chunk = (pcm / 32768.0).astype(np.float32)

            if flags & 0b01:              # isMark：物理键打标，先记下来；在线学习在这基础上做
                marks.append((seq, det.last))
                print(f"  ★ 标记 seq={seq} score={det.last:.2f}", flush=True)

            if det.feed_chunk(chunk):
                on_trigger(fileId, seq, det.last)

            n += 1
            if n % 50 == 0:               # 每秒一行心跳
                now = time.time()
                rate = 50 / (now - t0) if t0 else 0
                t0 = now
                bar = "█" * min(int(det.last / tpl["threshold"] * 10), 40)
                print(f"seq={seq:6d} {rate:4.0f}片/s  {det.last:6.2f} {bar}", flush=True)
    except WebSocketDisconnect:
        print(f"客户端断开，共 {n} 片，{len(marks)} 个标记", flush=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765, loop="asyncio", http="h11", log_level="warning")
