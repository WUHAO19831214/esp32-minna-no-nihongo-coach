import Foundation
import IOBluetooth
import CoreAudio
import AudioToolbox

class InquiryDelegate: NSObject, IOBluetoothDeviceInquiryDelegate {
    var foundDevices: [IOBluetoothDevice] = []
    var finished = false

    func deviceInquiryDeviceFound(_ sender: IOBluetoothDeviceInquiry!, device: IOBluetoothDevice!) {
        if let d = device {
            if !foundDevices.contains(where: { ($0.addressString ?? "") == (d.addressString ?? "") }) {
                foundDevices.append(d)
            }
        }
    }

    func deviceInquiryComplete(_ sender: IOBluetoothDeviceInquiry!, error: IOReturn, aborted: Bool) {
        finished = true
        CFRunLoopStop(CFRunLoopGetCurrent())
    }
}

class PairDelegate: NSObject, IOBluetoothDevicePairDelegate {
    var finished = false
    var pairError: IOReturn = kIOReturnSuccess

    func devicePairingFinished(_ sender: Any!, error: IOReturn) {
        pairError = error
        finished = true
        CFRunLoopStop(CFRunLoopGetCurrent())
    }

    func devicePairingPINCodeRequest(_ sender: Any!) {
        if let pair = sender as? IOBluetoothDevicePair {
            var pin = BluetoothPINCode()
            pin.data.0 = 0x30 // '0'
            pin.data.1 = 0x30 // '0'
            pin.data.2 = 0x30 // '0'
            pin.data.3 = 0x30 // '0'
            pair.replyPINCode(4, pinCode: &pin)
        }
    }

    func devicePairingUserConfirmationRequest(_ sender: Any!, numericValue: BluetoothNumericValue) {
        if let pair = sender as? IOBluetoothDevicePair {
            pair.replyUserConfirmation(true)
        }
    }
}

func isNonAudioPeripheral(_ d: IOBluetoothDevice) -> Bool {
    let name = (d.nameOrAddress ?? "").lowercased()
    if name.contains("magic keyboard") || name.contains("magic mouse") || name.contains("magic trackpad") ||
       name.contains("mx master") || name.contains("keychron") || name.contains("keyboard") ||
       name.contains("mouse") || name.contains("trackpad") || name.contains("macbook") ||
       name.contains("ipad") || name.contains("iphone") || name.contains("watch") {
        return true
    }
    let major = d.deviceClassMajor
    // Exclude Peripheral (Keyboards/Mice = 0x05), Computer (0x01), Phone (0x02), Wearable (0x07) unless audio
    if major == kBluetoothDeviceClassMajorPeripheral || major == kBluetoothDeviceClassMajorComputer ||
       major == kBluetoothDeviceClassMajorPhone || major == kBluetoothDeviceClassMajorWearable {
        if !(name.contains("airpods") || name.contains("buds") || name.contains("speaker") || name.contains("audio") || name.contains("耳机") || name.contains("音箱") || name.contains("bv")) {
            return true
        }
    }
    return false
}

func isLikelyAudioDevice(_ d: IOBluetoothDevice) -> Bool {
    let name = (d.nameOrAddress ?? "").lowercased()
    if d.deviceClassMajor == kBluetoothDeviceClassMajorAudio {
        return true
    }
    let keywords = ["airpods", "headphone", "headset", "buds", "ear", "sony", "bose", "beats", "jbl", "marshall", "sennheiser", "shokz", "edifier", "anker", "soundcore", "huawei", "xiaomi", "redmi", "oppo", "vivo", "nothing", "b&o", "bowers", "devialet", "harman", "speaker", "audio", "耳机", "音箱", "音响", "蓝牙"]
    for kw in keywords {
        if name.contains(kw) {
            return true
        }
    }
    return false
}

let cachePath = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
    .appendingPathComponent(".runtime/bt_scan_cache.json")

func loadScanCache() -> [[String: Any]] {
    guard let data = try? Data(contentsOf: cachePath),
          let arr = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
        return []
    }
    return arr
}

func saveScanCache(_ items: [[String: Any]]) {
    try? FileManager.default.createDirectory(at: cachePath.deletingLastPathComponent(), withIntermediateDirectories: true)
    if let data = try? JSONSerialization.data(withJSONObject: items, options: [.prettyPrinted]) {
        try? data.write(to: cachePath)
    }
}

func collectAllDevices(runInquirySeconds: Double = 0) -> [[String: Any]] {
    var byAddress: [String: [String: Any]] = [:]
    var order: [String] = []

    func upsert(address: String, name: String, connected: Bool, paired: Bool, isAudio: Bool) {
        let addr = address.lowercased()
        guard !addr.isEmpty else { return }
        if byAddress[addr] == nil {
            order.append(addr)
        }
        let cleanName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let prevName = (byAddress[addr]?["name"] as? String) ?? ""
        let finalName = (!cleanName.isEmpty && cleanName.lowercased() != addr) ? cleanName : (prevName.isEmpty ? addr : prevName)
        byAddress[addr] = [
            "address": addr,
            "name": finalName,
            "connected": connected,
            "paired": paired,
            "is_audio": isAudio
        ]
    }

    // 1. Paired devices from IOBluetooth
    if let pairedList = IOBluetoothDevice.pairedDevices() as? [IOBluetoothDevice] {
        for d in pairedList {
            if isNonAudioPeripheral(d) { continue }
            let addr = d.addressString ?? ""
            let name = d.nameOrAddress ?? addr
            upsert(
                address: addr,
                name: name,
                connected: d.isConnected(),
                paired: true,
                isAudio: isLikelyAudioDevice(d)
            )
        }
    }

    // 2. Previously cached nearby scan results
    for item in loadScanCache() {
        guard let addr = item["address"] as? String,
              let name = item["name"] as? String else { continue }
        let low = name.lowercased()
        if low.contains("macbook") || low.contains("ipad") || low.contains("iphone") || low.contains("watch") ||
           low.contains("keyboard") || low.contains("mouse") || low.contains("trackpad") {
            continue
        }
        let dev = IOBluetoothDevice(addressString: addr)
        if let d = dev, isNonAudioPeripheral(d) { continue }
        let conn = dev?.isConnected() ?? false
        let paired = dev?.isPaired() ?? (item["paired"] as? Bool ?? false)
        upsert(
            address: addr,
            name: dev?.nameOrAddress ?? name,
            connected: conn,
            paired: paired,
            isAudio: (item["is_audio"] as? Bool) ?? true
        )
    }

    // 3. Optional live Bluetooth inquiry scan for nearby unpaired/discoverable devices
    if runInquirySeconds > 0 {
        let delegate = InquiryDelegate()
        if let inquiry = IOBluetoothDeviceInquiry(delegate: delegate) {
            inquiry.inquiryLength = UInt8(max(3, min(10, Int(runInquirySeconds))))
            inquiry.updateNewDeviceNames = true
            if inquiry.start() == kIOReturnSuccess {
                let deadline = Date().addingTimeInterval(runInquirySeconds + 1.5)
                while !delegate.finished && Date() < deadline {
                    CFRunLoopRunInMode(CFRunLoopMode.defaultMode, 0.25, true)
                }
                inquiry.stop()
            }
            for d in delegate.foundDevices {
                if isNonAudioPeripheral(d) { continue }
                let addr = d.addressString ?? ""
                let name = d.nameOrAddress ?? addr
                upsert(
                    address: addr,
                    name: name,
                    connected: d.isConnected(),
                    paired: d.isPaired(),
                    isAudio: isLikelyAudioDevice(d)
                )
            }
        }
    }

    var results: [[String: Any]] = []
    for addr in order {
        if let item = byAddress[addr] {
            results.append(item)
        }
    }

    // Sort: connected first, then audio devices, then paired, then discovered
    results.sort { a, b in
        let ca = (a["connected"] as? Bool) ?? false
        let cb = (b["connected"] as? Bool) ?? false
        if ca != cb { return ca && !cb }
        let aa = (a["is_audio"] as? Bool) ?? false
        let ab = (b["is_audio"] as? Bool) ?? false
        if aa != ab { return aa && !ab }
        let pa = (a["paired"] as? Bool) ?? false
        let pb = (b["paired"] as? Bool) ?? false
        if pa != pb { return pa && !pb }
        return ((a["name"] as? String) ?? "") < ((b["name"] as? String) ?? "")
    }

    saveScanCache(results)
    return results
}

func setDefaultAudioOutput(matchingName targetName: String, matchingAddress targetAddr: String = "") -> Bool {
    var propertyAddress = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var dataSize: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &propertyAddress, 0, nil, &dataSize) == noErr else {
        return false
    }
    let count = Int(dataSize) / MemoryLayout<AudioDeviceID>.size
    var deviceIDs = [AudioDeviceID](repeating: 0, count: count)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &propertyAddress, 0, nil, &dataSize, &deviceIDs) == noErr else {
        return false
    }

    for id in deviceIDs {
        var streamAddr = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreams,
            mScope: kAudioDevicePropertyScopeOutput,
            mElement: kAudioObjectPropertyElementMain
        )
        var streamSize: UInt32 = 0
        AudioObjectGetPropertyDataSize(id, &streamAddr, 0, nil, &streamSize)
        if streamSize == 0 { continue }

        var nameAddr = AudioObjectPropertyAddress(
            mSelector: kAudioObjectPropertyName,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var cfName: Unmanaged<CFString>?
        var nameSize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        if AudioObjectGetPropertyData(id, &nameAddr, 0, nil, &nameSize, &cfName) == noErr,
           let unwrapped = cfName?.takeUnretainedValue() {
            let devName = (unwrapped as String)
            if (!targetName.isEmpty && devName.lowercased().contains(targetName.lowercased())) ||
               (!targetAddr.isEmpty && devName.lowercased().contains(targetAddr.lowercased())) {
                var defaultAddr = AudioObjectPropertyAddress(
                    mSelector: kAudioHardwarePropertyDefaultOutputDevice,
                    mScope: kAudioObjectPropertyScopeGlobal,
                    mElement: kAudioObjectPropertyElementMain
                )
                var mutableID = id
                let status = AudioObjectSetPropertyData(
                    AudioObjectID(kAudioObjectSystemObject),
                    &defaultAddr,
                    0,
                    nil,
                    UInt32(MemoryLayout<AudioDeviceID>.size),
                    &mutableID
                )
                // Also ensure hardware/virtual main volume on this output device is 100% (1.0) and unmuted
                var volAddr = AudioObjectPropertyAddress(
                    mSelector: kAudioHardwareServiceDeviceProperty_VirtualMainVolume,
                    mScope: kAudioDevicePropertyScopeOutput,
                    mElement: kAudioObjectPropertyElementMain
                )
                var fullVol: Float32 = 1.0
                AudioObjectSetPropertyData(id, &volAddr, 0, nil, UInt32(MemoryLayout<Float32>.size), &fullVol)
                for ch: UInt32 in [0, 1, 2] {
                    var chVolAddr = AudioObjectPropertyAddress(
                        mSelector: kAudioDevicePropertyVolumeScalar,
                        mScope: kAudioDevicePropertyScopeOutput,
                        mElement: ch
                    )
                    AudioObjectSetPropertyData(id, &chVolAddr, 0, nil, UInt32(MemoryLayout<Float32>.size), &fullVol)
                    var muteAddr = AudioObjectPropertyAddress(
                        mSelector: kAudioDevicePropertyMute,
                        mScope: kAudioDevicePropertyScopeOutput,
                        mElement: ch
                    )
                    var unmuted: UInt32 = 0
                    AudioObjectSetPropertyData(id, &muteAddr, 0, nil, UInt32(MemoryLayout<UInt32>.size), &unmuted)
                }
                return status == noErr
            }
        }
    }
    return false
}

func resolveTargetDevice(_ query: String) -> IOBluetoothDevice? {
    let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    let paired = (IOBluetoothDevice.pairedDevices() as? [IOBluetoothDevice]) ?? []
    if !q.isEmpty {
        for d in paired {
            let addr = (d.addressString ?? "").lowercased()
            let name = (d.nameOrAddress ?? "").lowercased()
            if addr == q || addr.replacingOccurrences(of: "-", with: ":") == q.replacingOccurrences(of: "-", with: ":") || name.contains(q) {
                return d
            }
        }
        for item in loadScanCache() {
            let name = ((item["name"] as? String) ?? "").lowercased()
            let addr = ((item["address"] as? String) ?? "")
            if addr.lowercased() == q || name.contains(q) {
                if let byAddr = IOBluetoothDevice(addressString: addr) {
                    return byAddr
                }
            }
        }
        if let byAddr = IOBluetoothDevice(addressString: q) {
            return byAddr
        }
    }
    let all = collectAllDevices(runInquirySeconds: 0)
    if let firstAddr = all.first?["address"] as? String {
        return IOBluetoothDevice(addressString: firstAddr)
    }
    return nil
}

func pairAndConnect(_ dev: IOBluetoothDevice) -> (Bool, String) {
    let name = dev.nameOrAddress ?? dev.addressString ?? "蓝牙音频设备"
    if !dev.isPaired() {
        let delegate = PairDelegate()
        if let pairer = IOBluetoothDevicePair(device: dev) {
            pairer.delegate = delegate
            if pairer.start() == kIOReturnSuccess {
                let deadline = Date().addingTimeInterval(8.0)
                while !delegate.finished && Date() < deadline {
                    CFRunLoopRunInMode(CFRunLoopMode.defaultMode, 0.25, true)
                }
            }
        }
    }
    if !dev.isConnected() {
        _ = dev.openConnection()
        for _ in 0..<8 {
            if dev.isConnected() { break }
            Thread.sleep(forTimeInterval: 0.35)
        }
    }
    let isConn = dev.isConnected()
    if isConn {
        for _ in 0..<8 {
            if setDefaultAudioOutput(matchingName: name, matchingAddress: dev.addressString ?? "") {
                break
            }
            Thread.sleep(forTimeInterval: 0.35)
        }
    }
    return (isConn, dev.nameOrAddress ?? name)
}

func emitJSON(_ payload: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: payload),
       let s = String(data: data, encoding: .utf8) {
        print(s)
    }
}

let args = CommandLine.arguments
let action = args.count > 1 ? args[1] : "status"
let targetArg = args.count > 2 ? args[2] : ""

if action == "scan" {
    let secs = Double(targetArg) ?? 4.0
    let devices = collectAllDevices(runInquirySeconds: secs)
    let connDev = devices.first(where: { ($0["connected"] as? Bool) == true }) ?? devices.first
    emitJSON([
        "ok": true,
        "scanned": true,
        "connected": (connDev?["connected"] as? Bool) ?? false,
        "name": (connDev?["name"] as? String) ?? "未选择设备",
        "address": (connDev?["address"] as? String) ?? "",
        "devices": devices
    ])
} else if action == "connect" || action == "pair" {
    var isConn = false
    var devName = targetArg.isEmpty ? "蓝牙音频" : targetArg
    var devAddr = ""
    if let dev = resolveTargetDevice(targetArg) {
        devAddr = dev.addressString ?? ""
        let res = pairAndConnect(dev)
        isConn = res.0
        devName = res.1
    }
    let devices = collectAllDevices(runInquirySeconds: 0)
    emitJSON([
        "ok": true,
        "connected": isConn,
        "name": devName,
        "address": devAddr,
        "devices": devices
    ])
} else if action == "disconnect" {
    if !targetArg.isEmpty, let dev = resolveTargetDevice(targetArg) {
        if dev.isConnected() {
            _ = dev.closeConnection()
        }
    } else if let paired = IOBluetoothDevice.pairedDevices() as? [IOBluetoothDevice] {
        for d in paired {
            if !isNonAudioPeripheral(d) && d.isConnected() {
                _ = d.closeConnection()
            }
        }
    }
    let devices = collectAllDevices(runInquirySeconds: 0)
    emitJSON([
        "ok": true,
        "connected": false,
        "devices": devices
    ])
} else {
    let devices = collectAllDevices(runInquirySeconds: 0)
    let connDev = devices.first(where: { ($0["connected"] as? Bool) == true }) ?? devices.first
    emitJSON([
        "ok": true,
        "connected": (connDev?["connected"] as? Bool) ?? false,
        "name": (connDev?["name"] as? String) ?? "未选择设备",
        "address": (connDev?["address"] as? String) ?? "",
        "devices": devices
    ])
}
