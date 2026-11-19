// Keeps the Mac's keyboard input sources equal to a list, through Carbon TIS (no logout needed).
// Usage: input-sources <list|check|apply> <enabled-file>
import Carbon
import Foundation

func prop(_ s: TISInputSource, _ key: CFString) -> AnyObject? {
    guard let p = TISGetInputSourceProperty(s, key) else { return nil }
    return Unmanaged<AnyObject>.fromOpaque(p).takeUnretainedValue()
}
func sourceID(_ s: TISInputSource) -> String { prop(s, kTISPropertyInputSourceID) as? String ?? "" }
func flag(_ s: TISInputSource, _ key: CFString) -> Bool { prop(s, key) as? Bool ?? false }
func sources(includeAll: Bool) -> [TISInputSource] {
    (TISCreateInputSourceList(nil, includeAll)?.takeRetainedValue() as? [TISInputSource]) ?? []
}
// Keyboard sources a user can switch to; a method's parent entry is not select-capable and is left out.
func enabledKeyboards() -> [String] {
    sources(includeAll: false).filter {
        prop($0, kTISPropertyInputSourceCategory) as? String == kTISCategoryKeyboardInputSource as String
            && flag($0, kTISPropertyInputSourceIsSelectCapable)
    }.map(sourceID)
}
func current() -> String { sourceID(TISCopyCurrentKeyboardInputSource().takeRetainedValue()) }

func check(_ want: [String]) -> Bool {
    let have = Set(enabledKeyboards())
    var ok = true
    for id in want where !have.contains(id) { print("missing \(id)"); ok = false }
    for id in have.sorted() where !want.contains(id) { print("extra \(id)"); ok = false }
    return ok
}

func enable(_ want: [String]) {
    let all = sources(includeAll: true)
    let wanted = all.filter { want.contains(sourceID($0)) }
    let bundles = Set(wanted.compactMap { prop($0, kTISPropertyBundleID) as? String })
    // A mode works only after its input method's parent entry is enabled too.
    for s in all where bundles.contains(prop(s, kTISPropertyBundleID) as? String ?? "")
        && prop(s, kTISPropertyInputSourceType) as? String != kTISTypeKeyboardInputMode as String {
        TISEnableInputSource(s)
    }
    for s in wanted { TISEnableInputSource(s) }
}

// Run from scripts/input-sources.sh, which checks the result in a fresh process: inside this one, TIS keeps
// reporting stale entries (even sources that were never enabled) after the calls below.
func apply(_ want: [String]) -> Bool {
    let dir = "/Library/Input Methods"
    for app in (try? FileManager.default.contentsOfDirectory(atPath: dir)) ?? [] where app.hasSuffix(".app") {
        TISRegisterInputSource(URL(fileURLWithPath: dir + "/" + app) as CFURL)
    }
    let installed = Set(sources(includeAll: true).map { sourceID($0) })
    let absent = want.filter { !installed.contains($0) }
    if !absent.isEmpty {
        // Stop before disabling anything: with 구름 not installed, the steps below would leave no keyboard layout.
        absent.forEach { print("not installed \($0)") }
        return false
    }
    enable(want)
    for s in sources(includeAll: false) where !want.contains(sourceID(s)) && enabledKeyboards().contains(sourceID(s)) {
        TISDisableInputSource(s)
    }
    // TISDisableInputSource returns noErr but keeps the layout the current mode types with (ABC under 구름).
    // Enabling a third-party method makes TIS reread AppleEnabledInputSources, so drop keyboard entries there first.
    let key = "AppleEnabledInputSources" as CFString, domain = "com.apple.HIToolbox" as CFString
    let list = CFPreferencesCopyAppValue(key, domain) as? [[String: Any]] ?? []
    CFPreferencesSetAppValue(key, list.filter { $0["InputSourceKind"] as? String == "Non Keyboard Input Method" } as CFArray, domain)
    CFPreferencesAppSynchronize(domain)
    enable(want)
    if !want.contains(current()), let first = want.first {
        // Right after enabling, TISSelectInputSource returns -50 (paramErr) for a few seconds.
        var selected = false
        for _ in 0..<10 {
            if let s = sources(includeAll: false).first(where: { sourceID($0) == first }), TISSelectInputSource(s) == noErr {
                selected = true
                break
            }
            Thread.sleep(forTimeInterval: 0.5)
        }
        if !selected {
            print("select failed \(first)")
            return false
        }
    }
    return true
}

let args = CommandLine.arguments
guard args.count == 3, ["list", "check", "apply"].contains(args[1]),
      let text = try? String(contentsOfFile: args[2], encoding: .utf8) else {
    FileHandle.standardError.write("usage: input-sources <list|check|apply> <enabled-file>\n".data(using: .utf8)!)
    exit(2)
}
let want = text.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }
    .filter { !$0.isEmpty && !$0.hasPrefix("#") }
switch args[1] {
case "list":
    enabledKeyboards().forEach { print("enabled \($0)") }
    print("current \(current())")
case "check":
    exit(check(want) ? 0 : 1)
default:
    exit(apply(want) ? 0 : 1)
}
