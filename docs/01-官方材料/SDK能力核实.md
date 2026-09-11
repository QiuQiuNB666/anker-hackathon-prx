# SoundcoreSDK 能力核实

> 来源：`github.com/AnkerInnovations/SoundcoreSDKDemo`，2026-09-11 clone。Apache 2.0。
> README 列的六组能力清单**不完整**，以下从 Demo 代码反推。

## 三个此前的未知项，全部有答案

| 问题 | 答案 | 依据 |
|---|---|---|
| 有没有实时音频流 | **有** | `acquireRealtimeAudioData(mac, uuid, savePath)` + `onReceiveAudioFragment` 回调。iOS Demo 注释：「recording while transferring」 |
| 重点标记是实时回调还是文件元数据 | **都有** | 实时：`onReceiveAudioFragment(..., isMark: Boolean)`；文件：`onDecodeFileMarkers(fileID, markerTimes: IntArray)` |
| 设备侧按键能否被 App 感知 | **能** | `onAudioRecordStatusChanged(mac, uuid, status, recordingDuration, fileID)` |

## 音频格式

`AudioTranscoder.kt`：Opus，**16 kHz，2 声道，20 ms 帧**。Demo 自带 Opus → WAV 解码（`com.anker.lib_opus.OpusUtils`），AAR 在 `app/libs/opus-lib-0.0.2.aar`。

对听诊器原型的含义：`listen.py` 的 `SR=16000` 与设备原生采样率一致，不需要重采样；双声道取均值或只取一路即可。

## 实时传输流程（iOS Demo 注释原文）

```
device recording → device sends COMMAND_ID_TRANSPORTING_WITH_RECORD
                 → SDK sets fileID → app calls acquireRealtimeAudioData
```

前提：设备必须已在录音（`isRecording == true`），且由设备侧发起通知。App 不能凭空开启实时流。

## 完整回调清单（`BusinessCallBack`）

```
onReceiveOfflineFile          (mac, uuid, OfflineFileData)
onAudioRecordStatusChanged    (mac, uuid, status, recordingDuration, fileID)
onAudioRecordDuration         (mac, uuid, durationMs)
onGetDeviceInfo               (mac, uuid, DeviceInfo)
onTransferFileStatusChanged   (mac, uuid, fileID, status)
onDecodeFileCompleted         (mac, uuid, fileID, filePath)
onDecodeFileMarkers           (mac, uuid, fileID, markerTimes: IntArray)
onTransferFileError           (mac, uuid, fileID, FileTransferError)
onTransferFileProgress        (mac, uuid, fileID, total, current, sequenceNumber, isMark)
onTransferFileSupplementProgress (mac, uuid, fileID, total, current)
onReceiveAudioFragment        (mac, uuid, fileID, audioData: ByteArray, isAppendPreAudio, sequenceNumber, isMark)
onFindMyEnableResult          (mac, uuid, success)
onSyncTimeResult              (mac, uuid, result)
onDeviceDisconnected          (mac)
onLocalKeyMissing             (mac, uuid)
onAuthRejected                (mac, uuid, rejectCode)
onReceiveWaitingDeviceOpenWIFI (mac, uuid)
onConnectWIFIError            (mac, uuid, errorCode)
onCloseSyncFileByWIFI         (mac, uuid)
```

## App 侧可调用的控制接口（`SoundcoreSDK.proxy.*`）

```
initSDK / registerBusinessCallback
startScan / stopScan / connect / disconnect
bindingDevice / authConnection / silentResetBindingKey
getDeviceInfo / resetDevice / syncTime / setFindMyEnable
pauseRecord / resumeRecord            ← 注意：没有 startRecord，录音由设备侧按键发起
getAllAudioRecordFiles / startSyncAudioFile / deleteFiles
acquireRealtimeAudioData              ← 实时流
isDataTransferring
startOta / startCombinedOta
setDeviceLogEnableCollection / setDeviceLogTransfer / requestDeviceLogList / deleteDeviceLogFile
```

**没有 `startRecord`**——录音的开始必须由设备侧物理按键触发，App 只能暂停/恢复。这与「主动录音」的产品设计一致，也意味着任何「App 远程开启录音」的方案在 SDK 层不成立。

## 二进制与许可

- Android：`module_spplink-release.aar`（SPP 链路）+ `opus-lib-0.0.2.aar`
- iOS：`SoundcoreWorkAppleSDK.xcframework` + `module_spplinkKit.xcframework`
- iOS 侧另有 `MFiManager`（Made for iPhone 配件协议，`ExternalAccessory` 框架），`registerDataCallback` 为事件驱动的数据接收
- 初始化需要 `licenseJson` + `license_signature`，签名由服务端签发，**必须向主办方申请**

## 加密

`BLEKeyManager`：ECDH 密钥交换 → HKDF 派生会话密钥；文件级 `initDevDecrypt(fileId, encryptFileKey, sessionNonce, nonce)`。与社区逆向文档描述的 ECDH P-256 → HKDF-SHA256 → AES-256-CTR 一致。

## 对两个方向的影响

**听诊器**：最大利好。之前按「分段拉取、5–15 秒延迟」设计，现在可以做近实时——分片 20ms 一帧，Opus 解码后直接喂梅尔谱。路演可以承诺秒级响应。`isMark` 标志让「双击 = 在线学习标注」在实时链路上直接成立。

**防霸凌**：「按下 → App 感知」在 SDK 层是通的。但这不改变第二轮核查的结论——事中按不了是行为层问题，不是 SDK 层问题。
