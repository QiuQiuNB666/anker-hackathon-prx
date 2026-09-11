# 听诊器原型

Anker 黑客松 · 智能录音赛道 · PRX

让录音豆去听机器，不听人。30 秒学会一台机器健康时的声音，异响出现时触发动作。

## 现在就能跑

```bash
.venv/bin/python listen.py selftest
```

合成一台电机的正常声与磨损声，验证整条管线，顺带跑一次 Opus 压缩往返对比。不需要任何素材和设备。

## 为什么没有模型

基线不需要。log-mel 谱 + 对角马氏距离就够把稳态谐波和磨损啸叫分开（自检里 8×）。

这不是偷懒，是这个方案能成立的前提：

- 24 小时里不用训练、不用调参、不用等 GPU
- 评委可以当场换一台没见过的机器，30 秒现学现用 —— 这是最强的可验证性演示
- 端侧跑，飞行模式下完整可用

要不要上模型，等实测发现 log-mel 对环境噪声太敏感再说。届时的选择是 YAMNet（TFLite 约 4MB，AudioSet 预训练），用它的 embedding 替换 log-mel 做模板，其余管线不动。

## 生死线实验

录音豆给出的是 Opus 有损压缩音频，且设备为人声做了降噪和 AGC。如果这些处理把机器的稳态频谱吃掉，整个方向不成立。

```bash
.venv/bin/python listen.py opus 正常.wav 异常.wav
```

跑 raw / 64k / 32k / 24k / 16k 的分离度梯度。**分离度掉到 1.5× 以下 = 该码率下方向不成立，改用瞬态特征**（撞击、摩擦、尖啸抗压缩得多）。

合成信号上 16k 仍保留 93%，但真机的降噪才是真正的威胁 —— 拿到设备后必须用真实录音复验。

## 端到端：手机 → WebSocket → 检测器

10/16 那天的数据链已经在本机跑通，只差把假设备换成真回调。

```bash
.venv/bin/python server.py                                     # 起服务端 :8765
.venv/bin/python fake_bean.py samples/demo_abnormal.wav --mark 2.5   # 另开终端，模拟录音豆
```

`fake_bean.py` 把 WAV 编成设备格式——CBR 64k、20ms、16kHz 双声道，每包恒 160 字节——按 50 片/秒推给服务端。`server.py` 用 opuslib 解码、喂 `StreamDetector`、触发时打印。

帧格式（与 Android 侧约定，见 `docs/04-决赛/SDK接入清单.md` 第三节）：

```
[type u8][fileId i32 LE][seq i32 LE][flags u8][payload]
type 0 = 设备 Opus 160B    type 1 = 手机麦 PCM16 mono（兜底）
flags bit0 = isMark        bit1 = isAppendPreAudio（跳过）
```

**决赛 17:00 切兜底 = 客户端把 type 从 0 改 1**，服务端一行不动。`fake_bean.py --pcm` 就是在演这条路。

手机侧不解码，160 字节原样转发，`adb reverse tcp:8765 tcp:8765` 走 USB 不吃现场 WiFi。

> uvloop 0.22 在 Python 3.14 上会静默卡死（进程活着、不绑端口、零输出）。`server.py` 里已固定 `loop=asyncio http=h11`，别用裸 `uvicorn server:app`。

## 用真实机器试

```bash
.venv/bin/python listen.py record 30 normal.wav   # 录正常运转
.venv/bin/python listen.py learn normal.wav       # 建模板
.venv/bin/python listen.py record 10 test.wav     # 制造异常再录
.venv/bin/python listen.py score test.wav
.venv/bin/python listen.py watch                  # 实时监听
.venv/bin/python listen.py stream test.wav        # 按 SDK 20ms 分片节奏逐片喂入
```

家里的风扇、抽油烟机、洗衣机、冰箱压缩机都是合格的实验对象。制造异常：往扇叶塞纸片、放个硬物进滚筒、松开一颗螺丝。

首次运行 `record` 时 macOS 会请求麦克风权限。

## 设计取舍

**音频 I/O 全部交给 ffmpeg**，Python 只吃裸 PCM，依赖只剩 numpy。绕开 librosa 是因为它拖 numba，而 numba 对 Python 3.14 支持滞后。副作用是这套代码在任何装了 ffmpeg 的机器上都能跑。

**`StreamDetector` 对应 SDK 的 `onReceiveAudioFragment` 回调。** 9/11 从官方 Demo 代码核实：设备原生 Opus 16kHz 双声道 20ms 帧，有实时分片回调（README 没写，代码里有）。`feed_chunk()` 每次吃 320 个采样点，维护滑动缓冲，每帧特征只算一次，分片边界不影响结果。自检里验证了流式与批式触发一致。10/16 那天只需把 `chunks(wav)` 换成真回调。

`watch` 用分段录制，是设备 SDK 核实之前的保守设计，现在保留作为无设备时的兜底。

**检测器是阈值 + 迟滞 + 冷却三个旋钮**，不是一个准确率数字。路演被问误报率时答机制，不要编百分比。

## 下一步

- [ ] 用真实机器跑一遍，看 log-mel 模板在环境噪声下是否稳
- [ ] 拿到设备后用真实录音复跑 `opus` 实验
- [ ] 接执行层：智能插座断电 / 推送
- [ ] 双击标记 → 在线学习：`onReceiveAudioFragment` 的 `isMark` 标志对应的分片加进模板
- [x] 流式接口（`StreamDetector`），对应 SDK 实时分片回调
- [x] 服务端 + 假设备，手机→WS→解码→检测 端到端跑通，Opus 与 PCM 兜底两条路都验过
- [ ] Android 侧：前台服务 + OkHttp WebSocket 发送端（`docs/04-决赛/SDK接入清单.md` 第五节 1–4、7）
