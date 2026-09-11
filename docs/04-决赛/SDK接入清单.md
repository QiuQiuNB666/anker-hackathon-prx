# PRX 决赛 24 小时接入清单（Anker soundcore Work 3200 听诊器）

路径约定：`$A` = `/private/tmp/claude-502/-Users-qiu----/c00ed005-0a29-4029-8214-4a438c90e6d1/scratchpad/SoundcoreSDKDemo/SoundcoreSDK AndroidDemo/app/src/main/java/com/oceanwing/soundcore/sdk/`；`$I` = `.../SoundcoreSDKDemo/SoundcoreSDKiOSDemo/SoundcoreSDKDemo/`；`$HDR` = iOS 伞头文件 `module_spplinkKit.h`。标 **[不确定]** 的是读码推断，需真机验证。

---

## 一、选平台：Android，不要犹豫

| 维度 | Android | iOS | 结论 |
|---|---|---|---|
| Demo 完整度 | 实时分片真的到应用层：`D3200EventManager.kt:335-347` → `D3200MainActivity.kt:854-879`；录音状态一来自动拉流 `D3200MainActivity.kt:491-504` | 协议要求的 7 参 `onReceiveAudioFragment` 是空函数 `$I/Adapters/BusinessCallbackAdapter.swift:78-80`，真正转发的 5 参版本 KMP 永远不会调；`log()` 函数体被注释 `RecordingFilesViewModel.swift:1274-1277`；鉴权 guard 被注释 `HomeViewController.swift:100-104` | Android 胜。iOS Demo 现状下**一个字节实时数据都拿不到**，先要修 adapter |
| 坑的多少 | 致命坑 2 个（`TODO()` 崩、license 原文），其余都是"静默不工作"可查日志 | 致命坑：`mCurrentSyncAudioFile` 为 null 时 KMP 层 NPE，iOS 上 fatal 不可 catch（`RecordingFilesViewModel.swift:1199-1201, 1230-1231`）；MFi/EASession 建不起来 acquire 不报错也没数据（`$HDR:8209-8220`）；`UIBackgroundModes` 无 `bluetooth-central`（`Info.plist:71-74`）；TEAM_ID 空、需付费开发者账号（`Configuration/Config.xcconfig:1`） | Android 胜。Android 版 SDK 对同一情况是打 log 后挂起 pending 自动补拉列表（`D3200Device.acquireRealtimeAudioData` 字节码），**[不确定]** 两端 SDK 版本可能不同步 |
| 实时流路径清晰度 | 5 个前置条件全部可从日志判断：鉴权 / connect / `mCurrentRecordingStatus==1` / 文件 ID 已知 / savaPath 可写（见第二节） | 同样 5 条但设备通知 → SDK 写 fileID 这一步在 iOS 上不可见（adapter 把 fileID 丢了 `BusinessCallbackAdapter.swift:59-64`） | Android 胜 |
| Opus 解码便利性 | 自带 `libs/opus-lib-0.0.2.aar`（JNI，`AudioTranscoder.kt:40,90-96` 有调用签名） | 无任何解码器，要 SPM 引 libopus | 平局——**推荐两端都不解码，160 字节直接转发给 Python**（第三节） |
| 队长技能 | Kotlin + Gradle，Android Studio 一台 Windows/Mac 都行；已有 Foreground Service 套路 | Xcode + Swift + CocoaPods 1.16 + Apple 签名链 | Android 胜 |

额外加分：Android SDK 的 `proxy.resumeRecord` 在字节码里就是 `device.startRecord()` → `AUDIO_CONTROL 0x01`（`SDKManagerImp.resumeRecord → D3200Device.startRecord → AudioEventSendManager.startRecord`）。**[不确定]** 固件是否接受从 STOP 冷启动录音，但如果成立，"录音只能物理按键发起"这个前提被推翻，可以纯 App 自动开始监听。iOS 端 `SoundcoreViewModel.swift:515-518` 同样调 `sdk.resumeRecord`，没验证过。

**决定：Android。iOS 工程一行都不要碰。**

---

## 二、从零到第一个实时分片：最小调用序列

标记：**[L]** 需要主办方 license；**[D]** 需要设备在场；**[M]** 无设备、无 license 时可先写好并跑通（含 mock）。

| # | 步骤 | 文件:行号 | 标记 |
|---|---|---|---|
| 1 | `AndroidManifest.xml` 注册 `.SoundcoreApplication` + 蓝牙/定位/INTERNET 权限 | `AndroidManifest.xml:4-12, :20` | [M] |
| 2 | Application.onCreate：拼 `licenseJson` + `token` + `license_signature`（主办方原文，一个字节不改） | `SoundcoreApplication.java:23-36` | [L] |
| 3 | 首次触碰 `D3200EventManager.instance` → init 块 `registerBusinessCallback(this)`（可先于 initSDK，不过 checkAuth） | `D3200EventManager.kt:41-43, :588-590` | [M] |
| 4 | `initManager()` 构造 `SDKInitConfig`（environment 用主办方指定的 DEV/PROD，两套公钥不同）→ `initSDK(log, config, OnAuthResultListener)` | `D3200EventManager.kt:45-100` | [L] |
| 5 | 等 `onAuthResult(success=true)`；**把这里改成 LiveData/StateFlow**，UI 等它 true 再放行。没有它，6-9 全部静默失败（日志 "Authentication required. Please wait authentication to complete."） | `D3200EventManager.kt:81-97` | [L] 无设备可验 |
| 6 | 运行时权限：API≥31 `BLUETOOTH_SCAN + BLUETOOTH_CONNECT`，任何版本 `ACCESS_FINE_LOCATION`；Android<12 还要系统定位开关打开 | `utils/BluetoothPermissionHelper.java:20-55` | [M] |
| 7 | `startScan(bleScan=true, sppScan=false, filters=["020cf5da-0000-1000-8000-00805f9b34fb"], cb)`，`onScanSuccess` 按 mac 去重 | `SearchDeviceActivity.kt:103-119` | [L][D] |
| 8 | `stopScan()` → `connectDevice(mac, cb)` → `proxy.connect(mac, uuid, SoundcoreConnectionCallback)` | `SearchDeviceActivity.kt:58-59` → `D3200EventManager.kt:124-146` | [D] |
| 9 | `onConnected` → `secureBindingDevice(mac, uid)` → `proxy.bindingDevice(mac, uuid, true, uid, now/1000)`；bindingKey 回来存 SharedPreferences | `SearchDeviceActivity.kt:62` → `SecureBindingService.kt:61-79` → `D3200EventManager.kt:377-383` | [D] **[不确定]** 不绑定能否出流，第一次连上就试 |
| 10 | **延时 1000ms**（等 SPP 通道就绪，别删）→ `proxy.getDeviceInfo(mac, uuid)`。这一步不是装饰：SDK 内部由此写 `mCurrentRecordingStatus` | `D3200MainActivity.kt:279-281, :394-400` | [D] |
| 11 | `onGetDeviceInfo` → 只读 `info.recording`（其余字段全 `?.`）→ `proxy.getAllAudioRecordFiles(mac, uuid)`，SDK 由此拿到当前录音文件 ID `mCurrentSyncAudioFile` | `D3200MainActivity.kt:522-549`（:547） | [D] |
| 12 | 让设备录音：先试 `proxy.resumeRecord(mac, uuid)`（**[不确定]**，5 分钟预算），不行按物理键 | `D3200MainActivity.kt:388-392` | [D] |
| 13 | `onAudioRecordStatusChanged(status=1 RECORDING, fileID)` → `startTransfer()` | `D3200EventManager.kt:217-228` → `D3200MainActivity.kt:465-519`（:491-504） | [D] |
| 14 | `savaPath = cacheDir/audio/`，`mkdirs()`，`proxy.acquireRealtimeAudioData(mac, uuid, savaPath)`——void，无返回，无成功回调 | `D3200MainActivity.kt:364-378`（:373-377） | [D] |
| 15 | `onReceiveAudioFragment(mac, uuid, fileID, audioData[160], isAppendPreAudio, seq, isMark)`，在 SDK 蓝牙线程同步回来 | `D3200EventManager.kt:335-347` → 接口 `D3200MainActivity.kt:1586-1594` | [M] handler 可用假包先写 |
| 16 | 停：`proxy.pauseRecord(mac, uuid)`（尾包 + 补包会继续来几秒）或 `disconnect` | `D3200MainActivity.kt:380-386`；`D3200EventManager.kt:149-151` | [D] |

关键排序：**5 之前不能扫描；11 之前不能 acquire；13 之后才 acquire**。调早了 SDK 只打 `log.e "acquireRealtimeAudioData failed: device not recording or current file unknown"`，无任何回调。

---

## 三、实时分片 → 异常检测器：数据管道

**audioData 是什么**：一片 = 解密后的**裸 Opus packet**，恒 160 字节 = 20ms（SDK 内部 166 字节链路包 → `decryptDataChunk` → 160；`AudioTranscoder.kt:33-37` 的 bitrate=64000/frameDuration=20/perFileSize=160 三者自洽：64000×0.02/8=160，CBR）。16kHz 双声道（`AudioTranscoder.kt:27,39`；`$HDR:892`）。无 Ogg 页、无长度前缀，一次回调就是一个 `opus_decode` 输入。每秒 50 片。`isAppendPreAudio=true` 的片是续传/前置缓冲（两处文档解释不同 `$HDR:893` vs `:1343-1346`），**检测器一律跳过**。

**最省事的方案：手机不解码，160 字节原样转发给队长的 FastAPI**

理由：opus-lib JNI 在 Demo 里 decode 返回值到底是 320 还是 640 未验证（`AudioTranscoder.kt:99,104`），x86 模拟器一碰就 `UnsatisfiedLinkError`；而 Python 侧 `opuslib` 一行解完，还能离线用文件回放调参。

```
Android(前台服务)                                  Mac/PC (FastAPI)
onReceiveAudioFragment ─┐
  ByteArray 拷贝入队      │ ArrayBlockingQueue(4096)
  (蓝牙线程只做这一件事)   ▼
  sender 线程 ───OkHttp WebSocket 二进制帧───▶ /ws/audio
      帧 = [type u8][fileId i32 LE][seq i32 LE][flags u8: bit0=isMark bit1=isAppendPre][160B opus]
                                                 opuslib.Decoder(16000,2).decode(pkt, 320)
                                                 → int16 ×640 → reshape(-1,2).mean(1)  (单声道)
                                                 → ring buffer 1s = 50 片 = 16000 采样
                                                 → log-mel → 模板比对 → 智能插座
```

- OkHttp 已在依赖里（`gradle/libs.versions.toml:24,60`，强制 3.12.13，WebSocket 自 3.5 起支持），**零新依赖**。
- 传输链路：**`adb reverse tcp:8765 tcp:8765` 走 USB**，手机连 `ws://127.0.0.1:8765`，不吃现场 WiFi。备用才用局域网 IP。
- `type` 字节：0=opus 包（设备），1=PCM16 mono 16k（手机麦兜底路径）。服务端按 type 分流解码，**这样 17:00 切兜底只是改客户端一行**。
- Python 解码：`opuslib.Decoder(16000, 2).decode(bytes(pkt), 320)` 返回 1280 字节（320 帧 × 2 声道 × 2 字节）；`frame_size` 是每声道样本数。装 `brew install opus` + `pip install opuslib`。**[不确定]** 双声道是两个 mic 还是复制，拿第一包看 L/R 相关性再决定 mean 还是取单路。
- 时间轴：`t_ms = seq × 20`（`$HDR:1310-1328` 明文）。丢包 SDK 自己会补（日志 "Packet loss detected"），检测器按 seq 补零即可。
- 打标：`isMark=true` 的那一片 = 物理键"这段是样本"，服务端收到就把前后 2s 的 mel 切片存为模板。**不要**依赖 `onTransferFileStatusChanged.markTimeStampList`，Demo 转发时丢了（`D3200EventManager.kt:252-262`）。
- 落盘副产品：SDK 无法关闭地把同样字节追加到 `<cacheDir>/audio/<fileID>.opus`（裸 160 字节拼接，不是标准 .opus），64kbps ≈ 28.8MB/h。这个文件就是第六节"从设备拉到一个音频文件"的证据，Python 侧 `f.read(160)` 循环解码即可播放。
- 停流后：pauseRecord 之后"录音器会继续发送未传输的音频数据"（`$HDR:176-178`）+ 补包 `onTransferFileSupplementProgress`（`D3200MainActivity.kt:836-852`），服务端在断电指令后要忽略后续包，别二次触发。

---

## 四、Demo 里的坑，按致命程度排序

### S 级：不修就崩 / 一个字节都拿不到

| # | 坑 | 触发条件 | 怎么避 |
|---|---|---|---|
| S1 | license JSON 原文被改（重排字段、加空格、换行）→ 签名失效 → 鉴权失败 → `startScan/connect/getDeviceInfo/getAllAudioRecordFiles/resume/pause` 全部**静默**不动 | `SoundcoreApplication.java:16-36` 占位符 "xxxxx"，硬编码 `license_expire_time=1768118700`（2026-01-11 已过期）；`README.md:101-103`；SDK `AuthService.verifyLicenseFromFullJson` | 主办方给什么塞什么，用 raw string；environment DEV/PROD 要和签发方一致（`SDKConfig.getLicensePublicKey` 两把公钥）；提前要 license 过期时间 > 10/17 |
| S2 | `onOtaError` 是 `TODO("Not yet implemented")` → `NotImplementedError` 进程崩 | `D3200EventManager.kt:305-307`；SDK 内部 `handleOtaDecision` 版本比对随时可能回调 | 复制类时改空实现 |
| S3 | 鉴权是异步的（可能走网络 `speaker-api(-ci).anker-in.com`），`onCreate` 之后立刻扫描 → 静默失败 | `D3200EventManager.kt:81-97` 只 Toast；SDK `SDKManagerImp.checkAuth` | onAuthResult 改 StateFlow，扫描按钮等 true |
| S4 | acquire 调早：SDK 缓存 `mCurrentRecordingStatus != 1` 或文件 ID 未知 → `log.e` 后返回，**无回调**（iOS 版是直接 NPE 崩） | `D3200MainActivity.kt:303-312`；SDK `D3200Device.acquireRealtimeAudioData`；iOS `RecordingFilesViewModel.swift:1199-1201` | 严格按第二节 10→11→13→14 顺序；logcat 过滤 "acquireRealtimeAudioData failed" |
| S5 | Activity 生命周期绑 BLE：`onDestroy` 里 `disconnect`；旋屏/后台/切页流就断 | `D3200MainActivity.kt:1213-1217` | SDK 调用全部搬进 Foreground Service（`D3200EventManager` 已是 Application 级单例，只是别在 Activity 里 disconnect）；加 `PARTIAL_WAKE_LOCK`；删 `:356-360` 从不释放的 WifiLock |
| S6 | 新工程漏依赖 → 运行时 `NoClassDefFoundError`（aar 无 POM，ktor/okhttp/coroutines/spongycastle/XXPermissions 全手写 + 强制版本） | `app/build.gradle.kts:51-64, :66-102`；`settings.gradle.kts:15` jitpack | 整块复制 dependencies + resolutionStrategy + `libs.versions.toml`；只出 debug 包（release 开了 minify 但 `proguard-rules.pro` 为空 `build.gradle.kts:31-38`） |

### A 级：跑得起来但丢包/卡死/误判

| # | 坑 | 触发条件 | 怎么避 |
|---|---|---|---|
| A1 | 分片回调在 SDK 蓝牙线程**同步直调**；回调里做解码/推理 → 阻塞收包 → 丢包 + `get_realtime_file` 超时 | `D3200MainActivity.kt:854-879`；SDK `SoundcoreBusinessDispatch.onReceiveAudioFragment` | 回调只做 `audioData.copyOf()` 入 `ArrayBlockingQueue`，其余另开线程 |
| A2 | 监听列表是普通 `ArrayList`，SDK 线程遍历时主线程 add/remove → `ConcurrentModificationException`，正好在 20ms 热路径 | `D3200EventManager.kt:31, :209-215, :344-346` | `CopyOnWriteArrayList` 或干脆单监听者 |
| A3 | 没有 `stopRealtime`；pauseRecord 后尾包 + 补包持续数秒 | `$HDR:176-178`；`D3200MainActivity.kt:474-489` | 服务端"断电后静默窗口"；用 `onTransferFileStatusChanged(REAL_TIME_TRANSFER_COMPLETED=3)` 收尾，别照抄 `:478-485` 的裸线程 `while(isDataTransferring) sleep(200)` 轮询（补包卡住线程永不退出） |
| A4 | 文件传输/补包中设备开始录音 → SDK 主动 abort 回 `onTransferFileError(DEVICE_RECORDING)` 并清 realtime 状态；等 SPP 时开录会取消 SPP | SDK `D3200Device.onAudioStatusChanged` 字节码 | 监听开始前后不要拉离线文件；连上后等 1s 再让设备开录 |
| A5 | 每次连上都 `bindingDevice(mac, "demo_user_1234567")`；被拒 `0xFE` 自动 `silentResetBindingKey` | `D3200EventManager.kt:398-400`；`SearchDeviceActivity.kt:62`；`SecureBindingService.kt:61-79` | uid 用自己固定串，首次绑定后不改；指令被拒先看 `onCommandRejectedByAuthentication` 日志（`:392-401`）。**[不确定]** 绑定是否需要充电盒按键确认（`$HDR:182-189` 这么说） |
| A6 | `DeviceInfo` 可空字段 `!!` 解包：`leftBattery!!`/`totalMemoryKB!!`/`freeMemoryKB!!`/`chargingBoxInfo.battery` | `D3200DeviceDetailActivity.kt:222-230`；`D3200MainActivity.kt:531-535` | 只读 `recording`，其余 `?.` |
| A7 | `acquireRealtimeAudioData` 的 `savaPath` 目录不存在 → "mSaveFolder is null"/"savePath is empty" 静默失败；且 SDK 强制落盘 28.8MB/h | `D3200MainActivity.kt:373-377` | `mkdirs()` 后再调；传 `cacheDir` 让系统可回收；24h 演示每小时删旧文件 |
| A8 | 第二套死代码 `initSDK`（license 全空，PRODUCTION）且 `exported=true` | `MainActivity.kt:36-64`；`AndroidManifest.xml:55-62` | 整文件删 |
| A9 | 蓝牙开关检查 `checkBluetoothEnabled` 实际启动流程里没执行；Android<12 需系统定位服务打开 | `SoundcoreMainActivity.kt:49-65` | 扫描前 `BluetoothAdapter.isEnabled` 判一下 |

### B 级：不影响出流，影响标记/时间轴/调试

| # | 坑 | 位置 | 怎么避 |
|---|---|---|---|
| B1 | 160 vs 166 两个包大小并存；`onTransferFileProgress.current` 一处当字节一处当包数 | `D3200MainActivity.kt:97, :771, :1349-1352`；`D3200DeviceDetailActivity.kt:229` | 第一包打 `audioData.size` 日志确认 160，时间轴只用 `seq×20` |
| B2 | `markTimeStampList`、`isAllAudioFiles` 转发时丢弃；`onRealtimeTransferSwitch`/`onDeviceTransferStateChanged` 只打日志 | `D3200EventManager.kt:252-262, :230-239, :568-579` | 实时打标用 `isMark+seq` 就够；`onRealtimeTransferSwitch` 来了表示 fileID 变了，重置解码器 |
| B3 | `AudioTranscoder` 一堆 bug：decoder 从不 destroy、`totalDataSize` 不清零、append 写 WAV、decode 传 `inputBuffer.size` 而非 `bytesRead`、`copyOfRange(0,numSamples)` 双声道可能丢一半 | `AudioTranscoder.kt:40, :48, :66-71, :90-104, :129` | 不复用，只当 JNI 签名参考；采用第三节"手机不解码" |
| B4 | opus-lib 只有 arm64-v8a / armeabi-v7a，x86 模拟器碰到 `OpusUtils` 就崩 | `AudioTranscoder.kt:7`；`libs/opus-lib-0.0.2.aar` | 手机不解码就不引用它 |
| B5 | `FileTransferError` 是 enum，`when` 里用 `==` 不能 `is` | `D3200MainActivity.kt:668` | 照抄 |
| B6 | 一串 `!!`：`item.macAddress!!`、`currentMacAddress!!`、`fileName!!` | `SearchDeviceActivity.kt:59,62`；`MainActivity.kt:373,509`；`DeviceLogManagementActivity.kt:246-399` | 判空一次 |
| B7 | 六处 ⚠️ 注释 "file_progress/recording_status/progressText not found in layout" / "无法找到对应的 item view" | `D3200MainActivity.kt:183-197, :253-267, :599-602, :717-720, :803-809, :820-832` | 纯 RecyclerView UI 空保护，不带列表 UI 就没了 |
| B8 | "注意: 这个回调只表示 SDK 命令执行结果, 不是传输完成回调" | `DeviceLogManagementActivity.kt:512` | 所有 `*Result` 回调都是"指令已下发"语义，别当完成用 |
| B9 | README 路径过期（写的是 composeApp/，实际 app/） | `README.md:57-79, :157` | 按实际目录 |
| B10 | 多余权限：`BLUETOOTH_ADVERTISE`、WiFi 四件套、`WRITE_SETTINGS`；两个 aar 的 manifest 还会合并进 `RECORD_AUDIO/READ/WRITE_EXTERNAL_STORAGE` | `AndroidManifest.xml:8, :13-16`；aar manifest | 想干净用 `tools:node="remove"`，黑客松可不管 |

### iOS 独有（只在你们违背第一节时才看）
`BusinessCallbackAdapter.swift:78-80/:82-84/:73-75` 协议重载错位空实现；`:59-64` 丢 fileID；`:146` 全部 hop 主线程；`KotlinByteArray` 无 `toNSData`（`$HDR:8434-8443`）；`RecordingFilesViewController.swift:1577-1591` 收到 `onRealtimeTransferSwitch` 直接断连；单例闭包槽最后赋值者赢（`SoundcoreViewModel.swift:100-135`）；`Info.plist:71-74` 无 `bluetooth-central`。

---

## 五、没有设备时能先写好的部分（9/11–10/15）

### 无设备、无 license 就能完成并跑通
1. **Gradle 工程 + 两个 aar 链接**：复制 `app/build.gradle.kts:51-102` + `gradle/libs.versions.toml` + `settings.gradle.kts:15` + `app/libs/*.aar`。验证标准：装到真机（ARM）后 logcat 出现 SDK 自己的日志 "First return: License JSON is blank"——证明 aar 加载、类路径完整，S6 已排除。
2. **前台服务骨架**：`Service` + notification + `PARTIAL_WAKE_LOCK`，持有 `D3200EventManager`（把 `BusinessCallBack` 接口从 `D3200MainActivity.kt:1507-1707` 抽成独立文件，只留 7 个方法；`:305-307` TODO 改空；listener 改 `CopyOnWriteArrayList`）。
3. **运行时权限流**：`BluetoothPermissionHelper.java:20-55` 照抄。
4. **WebSocket 发送端**：OkHttp WebSocket + `ArrayBlockingQueue` + 第三节帧格式 + `adb reverse`。
5. **FakeSoundcore 回放器**：一个线程按 50 片/秒调你的 `onReceiveAudioFragment(mac, uuid, fileID, bytes[160], false, seq++, isMark)`。假包来源：Python `opuslib.Encoder(16000, 2, 'audio')`，`bitrate=64000`、`vbr=0`（CBR）、`frame_size=320`，把任意 WAV（先用手机录的机器声/敲击声）编成 160 字节包序列存文件。**这样整条 手机→WS→Python→解码→mel→比对→插座 在 10/15 前就能端到端跑通，10/16 只换数据源。**
6. **Python 服务端全部**：`/ws/audio` 收帧分流、`opuslib.Decoder`、ring buffer、log-mel、模板比对、阈值、智能插座 HTTP 调用、`isMark` 存模板逻辑。
7. **手机麦兜底路径**（`type=1` PCM 帧）：`AudioRecord(16000, MONO, PCM_16BIT)` → 同一个 WebSocket。**先做这个**——它既是兜底，又是无设备时验证检测器的真实数据源。
8. **17:00 检查脚本**：Python `pull_and_play.py`：`adb shell run-as com.oceanwing.soundcore.sdk ls -l cache/audio/` → pull → 160 字节切块解码 → 写 WAV → 播放。

### 只要 license、不要设备就能验证
9. **鉴权链路**（第二节步骤 2-5）：真机联网跑一次看 Toast "鉴权成功"（`D3200EventManager.kt:81-97`），再关网跑一次验证离线 license（`AuthService.authenticateOfflineWithLicenseInfo`，`isOnline=false`）。**license 一到手当天就做**，这是最早能拆的雷（S1/S3）。向主办方要的东西：`app_id`、`user_id`、`device_id`、`token`、`licenseJson` 原文、`license_signature`、指定 `SDKEnvironment`、过期时间。

### 必须设备在场才能回答（10/16 上午第一小时的实验清单）
- `resumeRecord` 能否从 STOP 冷启动录音（第二节 12）
- 不 `bindingDevice` 能否出流；绑定是否要充电盒确认（A5）
- `audioData.size` 恒 160？双声道 L/R 是否相同？（B1、第三节）
- 首包延迟（acquire → 第一片）
- pauseRecord 后尾包持续多久（A3）

---

## 六、决赛当天 17:00 关口：5 条打勾项

每条给"看什么日志/文件"和"失败即切换"的判据。过滤 tag 用 SDK 的 `SoundcoreLogUtil` 输出 + 你自己的 tag。

- [ ] **1. 鉴权通过**（对应 S1/S3）
  - 操作：冷启动 App，联网。
  - 通过：`D3200EventManager.kt:81-97` 的 `onAuthResult` 回 `success=true`（Toast "鉴权成功"）；logcat **没有** "License JSON is blank"、"Offline License invalid or expired"（AuthError 1002）、"Authentication required"。
  - 失败：先关网重启看离线路径；再核对 licenseJson 是否原文、environment 是否对。10 分钟没过 → 这是主办方问题，找主办方，同时切兜底。

- [ ] **2. 连接 + 指令通道活**（对应 S5/A5）
  - 操作：扫描 → 点设备 → 等 1s → `getDeviceInfo`。
  - 通过：`onScanSuccess` 出现 mac（`SearchDeviceActivity.kt:106-119`）；`onConnected`（`D3200EventManager.kt:124-146`）；`onConnectSPPLinkStatusChanged(isConnect=true)`（`D3200EventManager.kt:581`，Demo 里空的，**你要打日志**）；`onGetDeviceInfo` 回来且 `info.recording` 有值（`D3200MainActivity.kt:522-549`）；`onCommandRejectedByAuthentication` **没有**出现（`:392-401`）。
  - 失败：看 `onDisconnected(code)`——code 6 = `CONNECT_NOT_AUTH` 回到第 1 条；rejectCode 0xFE = 绑定问题，清 `BindingKeyStore` 重连一次。

- [ ] **3. 设备进入录音态且 SDK 知道文件 ID**（对应 S4）
  - 操作：先调 `proxy.resumeRecord`（`D3200MainActivity.kt:388-392`）等 3s；没反应按物理录音键。
  - 通过：`onAudioRecordStatusChanged(status=1, fileID≠-1)`（`D3200EventManager.kt:217-228`）；`onReceiveOfflineFile` 已回过一次（`:230-239`，`currTransportFileTimestamp` 由此写入 SDK）。
  - 失败：物理键也不回 status=1 → 设备/固件问题，切兜底。

- [ ] **4. 实时流 10 秒稳定**（对应 A1/A7/B1）
  - 操作：`acquireRealtimeAudioData(mac, uuid, cacheDir/audio)`（`D3200MainActivity.kt:373-377`），敲一下设备壳。
  - 通过：5s 内首片到达 `onReceiveAudioFragment`（`D3200EventManager.kt:335-347`）；打一个每秒计数器，连续 10s 每秒 45-50 片；`audioData.size==160`；`seq` 单调递增；FastAPI 端 `/ws/audio` 收到并解码成功（`opuslib` 不抛 `OpusError`），mel 图上能看到敲击那一下。logcat **没有** "acquireRealtimeAudioData failed: device not recording or current file unknown"、"mSaveFolder is null"、"get_realtime_file" 超时。
  - 失败：有 "device not recording" → 回第 3 条重做顺序；片到了但 Python 解码报错 → 用第 5 条拉下来的文件离线排查，不要现场猜格式。

- [ ] **5. 从设备拉到一个音频文件并能听**（原排期的字面要求）
  - 操作：第 4 条跑 30s 后按物理键停录（触发补包），然后跑 `pull_and_play.py`：
    `adb shell run-as com.oceanwing.soundcore.sdk ls -l cache/audio/` → 应有 `<fileID>.opus`，大小 ≈ 8KB×秒数（30s ≈ 240KB）→ `run-as ... cat cache/audio/<fileID>.opus > local.opus` → Python 160 字节切块 `Decoder(16000,2).decode(chunk,320)` → 写 WAV → 播放。
  - 通过：能听到那一下敲击；`onTransferFileSupplementProgress current==total`（`D3200MainActivity.kt:836-852`）出现，证明收尾链路完整。
  - 失败：文件存在但解不出 → 数据格式假设错（**[不确定]** 项之一），此时实时链路即使通了也别信检测结果，切兜底。

**切换规则**：17:00 时 5 条中任意一条未打勾 → 客户端改 `type=1` 走手机麦（第五节第 7 项，预先做好，改一个 flag），检测器和插座逻辑一行不动；留 1 人最多再追设备 2 小时，其余人推进演示。18:00 仍未通，设备路线彻底放弃，演示稿改口"手机贴机"。

---

### 本清单里的 [不确定] 汇总（现场第一小时逐个消掉）
1. `resumeRecord` 能否从 STOP 冷启动录音（字节码 `AUDIO_CONTROL 0x01`）
2. 不 `bindingDevice` 能否出流；绑定是否需充电盒确认
3. `audioData.size` 恒 160；双声道是否为两路独立 mic
4. Android 版 SDK 对"文件 ID 未知"是 pending 还是像 iOS 文档说的 crash（两端 SDK 构建可能不同）
5. `onDecodeFileMarkers` 单位是否毫秒（本方案不用，可不验）
6. 落盘 `.opus` 是否为纯 160 字节拼接、无额外头（`$HDR:2300/2410` 暗示可能带包序号；Android 字节码 `writeToFile(fileId, path, data)` 看起来是裸写）