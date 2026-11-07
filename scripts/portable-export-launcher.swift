import AppKit
import Darwin
import Foundation
import WebKit

private let defaultSupportDirectory = FileManager.default.homeDirectoryForCurrentUser
  .appendingPathComponent("Library/Application Support/PiRouter", isDirectory: true)
private let supportDirectory: URL = {
  if let path = ProcessInfo.processInfo.environment["PIROUTER_SUPPORT_DIR"], !path.isEmpty {
    return URL(fileURLWithPath: (path as NSString).expandingTildeInPath, isDirectory: true).standardizedFileURL
  }
  return defaultSupportDirectory
}()
private let configURL: URL = {
  if let path = ProcessInfo.processInfo.environment["PIROUTER_PORTABLE_EXPORT_CONFIG"], !path.isEmpty {
    return URL(fileURLWithPath: (path as NSString).expandingTildeInPath).standardizedFileURL
  }
  return supportDirectory.appendingPathComponent("portable-export.env")
}()
private let backgroundLabel = "com.pirouter.portable-export"
private let isolatedConfiguration = supportDirectory.standardizedFileURL != defaultSupportDirectory.standardizedFileURL
  || configURL != defaultSupportDirectory.appendingPathComponent("portable-export.env")
private let backgroundActionsAllowed = ProcessInfo.processInfo.environment["PIROUTER_NO_BACKGROUND"] != "1" && !isolatedConfiguration

private struct AppFailure: LocalizedError {
  let message: String
  var errorDescription: String? { message }
  init(_ message: String) { self.message = message }
}

// Decode the literal forms written by Bash printf %q, without evaluating shell
// expressions, launching a shell, or expanding variables from the configuration.
private func shellLiteral(_ source: String) -> String? {
  let bytes = Array(source.utf8)
  var output: [UInt8] = []
  var index = 0
  func ansiEscape() -> Bool {
    guard index < bytes.count else { return false }
    let value = bytes[index]; index += 1
    let escapes: [UInt8: UInt8] = [97: 7, 98: 8, 101: 27, 69: 27, 102: 12, 110: 10, 114: 13, 116: 9, 118: 11, 92: 92, 39: 39, 34: 34]
    if let decoded = escapes[value] { output.append(decoded); return true }
    if value >= 48 && value <= 55 {
      var number = Int(value - 48); var count = 1
      while count < 3 && index < bytes.count && bytes[index] >= 48 && bytes[index] <= 55 {
        number = number * 8 + Int(bytes[index] - 48); index += 1; count += 1
      }
      guard number > 0 && number <= 255 else { return false }
      output.append(UInt8(number)); return true
    }
    if value == 120 || value == 117 || value == 85 {
      let maximum = value == 120 ? 2 : value == 117 ? 4 : 8
      var digits = ""
      while digits.count < maximum && index < bytes.count {
        let byte = bytes[index]
        guard (48...57).contains(byte) || (65...70).contains(byte) || (97...102).contains(byte) else { break }
        digits.append(Character(UnicodeScalar(byte))); index += 1
      }
      guard let number = UInt32(digits, radix: 16), number != 0 else { return false }
      if value == 120 { output.append(UInt8(number)) }
      else if let scalar = UnicodeScalar(number) { output.append(contentsOf: String(scalar).utf8) }
      else { return false }
      return true
    }
    output.append(92); output.append(value); return true
  }
  while index < bytes.count {
    let byte = bytes[index]; index += 1
    if byte == 92 {
      guard index < bytes.count else { return nil }
      output.append(bytes[index]); index += 1
    } else if byte == 39 {
      while index < bytes.count && bytes[index] != 39 { output.append(bytes[index]); index += 1 }
      guard index < bytes.count else { return nil }; index += 1
    } else if byte == 34 {
      var closed = false
      while index < bytes.count {
        let next = bytes[index]; index += 1
        if next == 34 { closed = true; break }
        if next == 92, index < bytes.count, [34, 92, 36, 96].contains(bytes[index]) {
          output.append(bytes[index]); index += 1
        } else { output.append(next) }
      }
      guard closed else { return nil }
    } else if byte == 36 && index < bytes.count && bytes[index] == 39 {
      index += 1; var closed = false
      while index < bytes.count {
        let next = bytes[index]; index += 1
        if next == 39 { closed = true; break }
        if next == 92 { guard ansiEscape() else { return nil } }
        else { output.append(next) }
      }
      guard closed else { return nil }
    } else { output.append(byte) }
  }
  return String(bytes: output, encoding: .utf8)
}

private func environmentKey(_ rawLine: String) -> String? {
  var line = rawLine.trimmingCharacters(in: .whitespaces)
  if line.hasPrefix("export ") { line.removeFirst(7) }
  guard !line.hasPrefix("#"), let separator = line.firstIndex(of: "=") else { return nil }
  let key = String(line[..<separator]).trimmingCharacters(in: .whitespaces)
  return key.range(of: "^[A-Za-z_][A-Za-z0-9_]*$", options: .regularExpression) == nil ? nil : key
}

private func readEnvironmentFile(_ url: URL) -> [String: String] {
  guard let contents = try? String(contentsOf: url, encoding: .utf8) else { return [:] }
  var values: [String: String] = [:]
  for line in contents.components(separatedBy: .newlines) {
    guard let key = environmentKey(line), let separator = line.firstIndex(of: "=") else { continue }
    let raw = String(line[line.index(after: separator)...]).trimmingCharacters(in: .whitespaces)
    if let literal = shellLiteral(raw) { values[key] = literal }
  }
  return values
}

private func quotedShellLiteral(_ value: String) -> String {
  "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
}

private func writeEnvironmentValues(_ updates: [String: String], to url: URL) throws {
  let existing = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
  var seen = Set<String>()
  var lines: [String] = []
  for line in existing.components(separatedBy: .newlines) {
    if let key = environmentKey(line), let value = updates[key] {
      if seen.insert(key).inserted { lines.append("\(key)=\(quotedShellLiteral(value))") }
    } else { lines.append(line) }
  }
  while lines.last == "" { lines.removeLast() }
  for key in updates.keys.sorted() where !seen.contains(key) {
    lines.append("\(key)=\(quotedShellLiteral(updates[key]!))")
  }
  try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
  let temporary = url.deletingLastPathComponent().appendingPathComponent(".\(url.lastPathComponent).\(UUID().uuidString).tmp")
  guard FileManager.default.createFile(atPath: temporary.path, contents: Data((lines.joined(separator: "\n") + "\n").utf8), attributes: [.posixPermissions: 0o600]) else {
    throw AppFailure("The settings file could not be created. Check access to your Application Support folder.")
  }
  defer { try? FileManager.default.removeItem(at: temporary) }
  let handle = try FileHandle(forWritingTo: temporary)
  try handle.synchronize(); try handle.close()
  guard rename(temporary.path, url.path) == 0 else { throw AppFailure("The settings file could not be saved: \(String(cString: strerror(errno)))") }
}

private func normalizedBaseURL(_ value: String) -> URL? {
  var trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
  guard !trimmed.isEmpty, !trimmed.contains(where: { $0.isWhitespace }) else { return nil }
  if !trimmed.contains("://") {
    let tailnet = trimmed.lowercased().hasSuffix(".ts.net")
    trimmed = (tailnet ? "https://" : "http://") + trimmed
  }
  guard var components = URLComponents(string: trimmed),
        components.scheme == "http" || components.scheme == "https",
        let host = components.host, !host.isEmpty,
        components.user == nil, components.password == nil,
        components.port == nil || (1...65535).contains(components.port!)
  else { return nil }
  components.path = ""; components.query = nil; components.fragment = nil
  return components.url
}

private func sameOrigin(_ first: URL, _ second: URL) -> Bool {
  func port(_ url: URL) -> Int { url.port ?? (url.scheme?.lowercased() == "https" ? 443 : 80) }
  return first.scheme?.lowercased() == second.scheme?.lowercased()
    && first.host?.lowercased() == second.host?.lowercased() && port(first) == port(second)
}

private struct Endpoint: Equatable {
  enum Kind { case local, tailscale, remote }
  let baseURL: URL
  let kind: Kind
  var statusText: String {
    switch kind {
    case .local: return "Connected directly on the local network"
    case .tailscale: return "Connected securely through Tailscale"
    case .remote: return "Connected to PiRouter"
    }
  }
  var name: String {
    switch kind { case .local: return "Local"; case .tailscale: return "Tailscale"; case .remote: return "Remote" }
  }
}

private struct AppConfiguration {
  let values: [String: String]
  let endpoints: [Endpoint]
  var exportEnabled: Bool {
    if let value = values["EXPORT_ENABLED"] { return value == "true" }
    return !(values["PIROUTER_TOKEN"] ?? "").isEmpty && !(values["EXPORT_HOST"] ?? "").isEmpty && !(values["EXPORT_SSH_KEY"] ?? "").isEmpty
  }
  static func load() -> AppConfiguration { from(readEnvironmentFile(configURL)) }
  static func from(_ values: [String: String]) -> AppConfiguration {
    var endpoints: [Endpoint] = []
    func append(_ value: String, kind: Endpoint.Kind) {
      guard let url = normalizedBaseURL(value), !endpoints.contains(where: { $0.baseURL == url }) else { return }
      endpoints.append(Endpoint(baseURL: url, kind: kind))
    }
    let local = [values["PIROUTER_LOCAL_URLS"], values["PIROUTER_LOCAL_URL"]].compactMap { $0 }
      .flatMap { $0.split(separator: ",").map(String.init) }
    for value in local { append(value, kind: .local) }
    if let remote = values["PIROUTER_URL"], let remoteURL = normalizedBaseURL(remote) {
      if local.isEmpty, let host = remoteURL.host, host.hasSuffix(".ts.net"),
         let short = host.split(separator: ".").first,
         short.range(of: "^[A-Za-z][A-Za-z0-9-]{0,62}$", options: .regularExpression) != nil {
        append("http://\(short).local:3000", kind: .local)
      }
      append(remote, kind: remoteURL.host?.hasSuffix(".ts.net") == true ? .tailscale : .remote)
    }
    return AppConfiguration(values: values, endpoints: endpoints)
  }
}

private func runtimeEnvironment() -> [String: String] {
  var values = ProcessInfo.processInfo.environment
  values["PIROUTER_SUPPORT_DIR"] = supportDirectory.path
  values["PIROUTER_PORTABLE_EXPORT_CONFIG"] = configURL.path
  if let frameworks = Bundle.main.privateFrameworksURL {
    let home = frameworks.appendingPathComponent("Python.framework/Versions/Current", isDirectory: true)
    let executable = home.appendingPathComponent("bin/python3")
    if FileManager.default.isExecutableFile(atPath: executable.path) {
      values["PIROUTER_PYTHON"] = executable.path
      values["PYTHONHOME"] = home.path
      values["PYTHONDONTWRITEBYTECODE"] = "1"
      values["PYTHONNOUSERSITE"] = "1"
      let certificates = home.appendingPathComponent("etc/pirouter-ca.pem")
      if FileManager.default.fileExists(atPath: certificates.path) { values["SSL_CERT_FILE"] = certificates.path }
    }
  }
  return values
}

private func runBundledScript(_ name: String, python: Bool = false) -> Int32 {
  if !python && (!AppConfiguration.load().exportEnabled || !backgroundActionsAllowed) { return 0 }
  guard let script = Bundle.main.resourceURL?.appendingPathComponent(name), FileManager.default.fileExists(atPath: script.path) else {
    FileHandle.standardError.write(Data("PiRouter Exporter is missing a bundled helper. Reinstall the app.\n".utf8)); return 1
  }
  let process = Process()
  let environment = runtimeEnvironment()
  process.environment = environment
  process.executableURL = URL(fileURLWithPath: python ? (environment["PIROUTER_PYTHON"] ?? "/usr/bin/python3") : "/bin/bash")
  process.arguments = [script.path]
  process.standardInput = FileHandle.nullDevice
  process.standardOutput = FileHandle.standardOutput
  process.standardError = FileHandle.standardError
  signal(SIGINT, SIG_IGN); signal(SIGTERM, SIG_IGN)
  let interrupt = DispatchSource.makeSignalSource(signal: SIGINT, queue: .global())
  let terminate = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .global())
  interrupt.setEventHandler { if process.isRunning { process.interrupt() } }
  terminate.setEventHandler { if process.isRunning { process.terminate() } }
  interrupt.resume(); terminate.resume()
  defer { interrupt.cancel(); terminate.cancel() }
  do { try process.run(); withExtendedLifetime((interrupt, terminate)) { process.waitUntilExit() } }
  catch {
    FileHandle.standardError.write(Data("PiRouter Exporter could not start its helper: \(error.localizedDescription)\n".utf8)); return 1
  }
  return process.terminationReason == .exit ? process.terminationStatus : 128 + process.terminationStatus
}

private func runCommand(_ path: String, _ arguments: [String], timeout: TimeInterval = 15, capture: Bool = false, environment: [String: String]? = nil) throws -> (Int32, String) {
  let process = Process()
  let temporary = FileManager.default.temporaryDirectory.appendingPathComponent("pirouter-command-" + UUID().uuidString)
  var captureHandle: FileHandle?
  if capture {
    guard FileManager.default.createFile(atPath: temporary.path, contents: nil, attributes: [.posixPermissions: 0o600]) else {
      throw AppFailure("A private connection-test log could not be created.")
    }
    captureHandle = try FileHandle(forUpdating: temporary)
  }
  defer { try? captureHandle?.close(); if capture { try? FileManager.default.removeItem(at: temporary) } }
  process.executableURL = URL(fileURLWithPath: path); process.arguments = arguments
  if let environment { process.environment = environment }
  process.standardInput = FileHandle.nullDevice
  // A private file avoids pipe back-pressure when an SSH relay writes diagnostics.
  process.standardOutput = captureHandle ?? FileHandle.nullDevice
  process.standardError = captureHandle ?? FileHandle.nullDevice
  try process.run()
  let deadline = Date().addingTimeInterval(timeout)
  var oversized = false
  while process.isRunning && Date() < deadline {
    if capture, let size = (try? FileManager.default.attributesOfItem(atPath: temporary.path)[.size]) as? NSNumber, size.intValue > 262_144 { oversized = true; break }
    Thread.sleep(forTimeInterval: 0.05)
  }
  if process.isRunning {
    process.terminate()
    let grace = Date().addingTimeInterval(1)
    while process.isRunning && Date() < grace { Thread.sleep(forTimeInterval: 0.05) }
    if process.isRunning { kill(process.processIdentifier, SIGKILL) }
    process.waitUntilExit()
    throw AppFailure(oversized ? "The connection test returned unexpectedly large diagnostics. Check the relay command." : "The operation took too long. Try again after checking the connection.")
  }
  process.waitUntilExit()
  var text = ""
  if let captureHandle { try captureHandle.seek(toOffset: 0); text = String(decoding: try captureHandle.read(upToCount: 65_536) ?? Data(), as: UTF8.self) }
  return (process.terminationStatus, text)
}

private func processTranslationState() -> Int32? {
  var translated: Int32 = 0
  var size = MemoryLayout<Int32>.size
  if sysctlbyname("sysctl.proc_translated", &translated, &size, nil, 0) == 0 { return translated }
  return errno == ENOENT ? 0 : nil
}

private func nativeHardwareArchitecture() -> String? {
  var arm64: Int32 = 0
  var size = MemoryLayout<Int32>.size
  if sysctlbyname("hw.optional.arm64", &arm64, &size, nil, 0) == 0 { return arm64 == 1 ? "arm64" : "x86_64" }
  var cpu: Int32 = 0
  size = MemoryLayout<Int32>.size
  guard sysctlbyname("hw.cputype", &cpu, &size, nil, 0) == 0 else { return nil }
  if cpu == CPU_TYPE_ARM64 { return "arm64" }
  if cpu == CPU_TYPE_X86_64 { return "x86_64" }
  return nil
}

// This diagnostic never loads configuration, opens a window, or starts exports.
// Query each process itself: a universal binary's available slices alone do not
// establish which architecture macOS actually selected at runtime.
private func runRuntimeCheck() -> Int32 {
  #if arch(arm64)
  let architecture = "arm64"
  #elseif arch(x86_64)
  let architecture = "x86_64"
  #else
  let architecture = "unknown"
  #endif
  let hardware = nativeHardwareArchitecture()
  let translated = processTranslationState()
  var errors: [String] = []
  if hardware == nil { errors.append("The native hardware architecture could not be determined.") }
  if translated == nil { errors.append("The launcher's translation state could not be determined.") }
  else if translated != 0 { errors.append("The launcher is running under translation. Reopen the native PiRouter Exporter app.") }
  if hardware != architecture { errors.append("The launcher architecture does not match the native hardware.") }
  var python: Any = NSNull()
  let executable = Bundle.main.privateFrameworksURL?.appendingPathComponent("Python.framework/Versions/Current/bin/python3")
  if let executable, FileManager.default.isExecutableFile(atPath: executable.path) {
    let code = """
import ctypes, errno, json, platform, sys
libc = ctypes.CDLL(None, use_errno=True)
libc.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
libc.sysctlbyname.restype = ctypes.c_int
value = ctypes.c_int(0)
size = ctypes.c_size_t(ctypes.sizeof(value))
result = libc.sysctlbyname(b'sysctl.proc_translated', ctypes.byref(value), ctypes.byref(size), None, 0)
translated = bool(value.value) if result == 0 else (False if ctypes.get_errno() == errno.ENOENT else None)
print(json.dumps({'architecture': platform.machine(), 'translated': translated, 'executable': sys.executable}))
sys.exit(0 if translated is not None else 1)
"""
    do {
      let result = try runCommand(executable.path, ["-c", code], timeout: 10, capture: true, environment: runtimeEnvironment())
      guard result.0 == 0, let data = result.1.data(using: .utf8), let report = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        throw AppFailure("The bundled Python runtime did not return a valid architecture report.")
      }
      python = report
      if report["translated"] as? Bool != false { errors.append("The bundled Python runtime is translated or its translation state is unknown.") }
      if report["architecture"] as? String != hardware { errors.append("The bundled Python architecture does not match the native hardware.") }
    } catch { errors.append(error.localizedDescription) }
  } else { errors.append("The bundled Python runtime is missing or is not executable.") }
  let report: [String: Any] = [
    "ok": errors.isEmpty, "hardware_architecture": hardware ?? "unknown",
    "launcher": ["architecture": architecture, "translated": translated.map { $0 != 0 } as Any? ?? NSNull()],
    "python": python, "errors": errors,
  ]
  if let data = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) {
    FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data("\n".utf8))
  }
  return errors.isEmpty ? 0 : 1
}

private enum BackgroundExporter {
  private static let operationLock = NSLock()
  static var agentURL: URL {
    let directory = ProcessInfo.processInfo.environment["PIROUTER_LAUNCH_AGENT_DIR"].map { URL(fileURLWithPath: $0, isDirectory: true) }
      ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/LaunchAgents", isDirectory: true)
    return directory.appendingPathComponent(backgroundLabel + ".plist")
  }
  static var domain: String { "gui/\(getuid())" }
  private static func stopLoadedAgent() throws {
    let loaded = (try runCommand("/bin/launchctl", ["print", domain + "/" + backgroundLabel])).0 == 0
    guard loaded else { return }
    _ = try runCommand("/bin/launchctl", ["bootout", domain + "/" + backgroundLabel], timeout: 30)
    let remainsLoaded = (try runCommand("/bin/launchctl", ["print", domain + "/" + backgroundLabel])).0 == 0
    guard !remainsLoaded else { throw AppFailure("The background exporter could not be stopped. Its existing service has been preserved; try saving again after the current export settles.") }
  }
  static func apply(restart: Bool) throws {
    guard backgroundActionsAllowed else { return }
    operationLock.lock(); defer { operationLock.unlock() }
    // Read inside the serialized operation so a queued startup cannot undo a
    // later explicit Settings change with an older enabled/disabled value.
    let enabled = AppConfiguration.load().exportEnabled
    if !enabled {
      try stopLoadedAgent()
      if FileManager.default.fileExists(atPath: agentURL.path) { try FileManager.default.removeItem(at: agentURL) }
      return
    }
    guard let executable = Bundle.main.executableURL else { throw AppFailure("The app executable could not be found. Reinstall PiRouter Exporter.") }
    if Bundle.main.bundleURL.path.hasPrefix("/Volumes/") || (try? Bundle.main.bundleURL.resourceValues(forKeys: [.volumeIsReadOnlyKey]).volumeIsReadOnly) == true {
      throw AppFailure("Move PiRouter Exporter to Applications before enabling background exports.")
    }
    try FileManager.default.createDirectory(at: supportDirectory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    try FileManager.default.createDirectory(at: agentURL.deletingLastPathComponent(), withIntermediateDirectories: true)
    let plist: [String: Any] = [
      "Label": backgroundLabel, "ProgramArguments": [executable.path, "--background"],
      "RunAtLoad": true, "StartInterval": 300, "ProcessType": "Background", "LowPriorityIO": true,
      "Umask": 0o077,
      "EnvironmentVariables": ["PIROUTER_SUPPORT_DIR": supportDirectory.path, "PIROUTER_PORTABLE_EXPORT_CONFIG": configURL.path],
      "StandardOutPath": supportDirectory.appendingPathComponent("export.log").path,
      "StandardErrorPath": supportDirectory.appendingPathComponent("export-error.log").path,
    ]
    let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
    let existing = try? Data(contentsOf: agentURL)
    let loaded = (try? runCommand("/bin/launchctl", ["print", domain + "/" + backgroundLabel]).0) == 0
    let previous = existing.flatMap { try? PropertyListSerialization.propertyList(from: $0, options: [], format: nil) as? [String: Any] }
    let sameExecutable = previous?["ProgramArguments"] as? [String] == [executable.path, "--background"]
    if !restart && sameExecutable && loaded {
      // Refresh next-login metadata without interrupting a running transfer.
      if existing != data { try data.write(to: agentURL, options: .atomic) }
      return
    }
    if loaded { try stopLoadedAgent() }
    try data.write(to: agentURL, options: .atomic)
    try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: agentURL.path)
    _ = try? runCommand("/bin/launchctl", ["enable", domain + "/" + backgroundLabel])
    let result = try runCommand("/bin/launchctl", ["bootstrap", domain, agentURL.path])
    guard result.0 == 0 else { throw AppFailure("Background exports could not be registered. Reopen Settings and try saving again.") }
  }
  static func startCheck() {
    guard backgroundActionsAllowed else { return }
    DispatchQueue.global(qos: .utility).async {
      operationLock.lock(); defer { operationLock.unlock() }
      guard AppConfiguration.load().exportEnabled else { return }
      _ = try? runCommand("/bin/launchctl", ["kickstart", domain + "/" + backgroundLabel])
    }
  }
}

private final class FlippedDocumentView: NSView {
  override var isFlipped: Bool { true }
}

private final class SettingsWindowController: NSWindowController, NSWindowDelegate {
  var onSaved: (() -> Void)?
  var onClosed: (() -> Void)?
  private let original = AppConfiguration.load()
  private let address = NSTextField()
  private let localAddress = NSTextField()
  private let token = NSSecureTextField()
  private let server = NSTextField()
  private let keyPath = NSTextField()
  private let destination = NSTextField()
  private let relay = NSTextField()
  private let exportToggle = NSButton(checkboxWithTitle: "Run the background exporter on this Mac", target: nil, action: nil)
  private let exportFields = NSStackView()
  private let errorLabel = NSTextField(wrappingLabelWithString: "")
  private let saveButton = NSButton(title: "Save and Connect", target: nil, action: nil)
  private let cancelButton = NSButton(title: "Cancel", target: nil, action: nil)
  private let testButton = NSButton(title: "Test Exporter Access", target: nil, action: nil)
  private let activity = NSProgressIndicator()
  private let formScroll = NSScrollView()
  private var busy = false
  private var originalLocalText = ""

  init() {
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 650, height: 680), styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
    window.title = original.endpoints.isEmpty ? "Set Up PiRouter Exporter" : "PiRouter Exporter Settings"
    window.minSize = NSSize(width: 540, height: 440)
    super.init(window: window)
    window.delegate = self; window.tabbingMode = .disallowed
    buildForm(); loadValues()
  }
  required init?(coder: NSCoder) { nil }

  private func label(_ text: String, size: CGFloat = 12, weight: NSFont.Weight = .regular) -> NSTextField {
    let label = NSTextField(wrappingLabelWithString: text)
    label.font = .systemFont(ofSize: size, weight: weight)
    label.maximumNumberOfLines = 0
    return label
  }
  private func field(_ title: String, _ input: NSTextField, placeholder: String, help: String? = nil) -> NSView {
    input.placeholderString = placeholder; input.font = .systemFont(ofSize: 13)
    input.setAccessibilityLabel(title); input.translatesAutoresizingMaskIntoConstraints = false
    input.heightAnchor.constraint(greaterThanOrEqualToConstant: 27).isActive = true
    let stack = NSStackView(views: [label(title, size: 12, weight: .medium), input])
    stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 5
    input.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
    if let help { let note = label(help, size: 11); note.textColor = .secondaryLabelColor; stack.addArrangedSubview(note); note.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true }
    return stack
  }
  private func buildForm() {
    guard let content = window?.contentView else { return }
    let scroll = formScroll; scroll.hasVerticalScroller = true; scroll.drawsBackground = false
    scroll.translatesAutoresizingMaskIntoConstraints = false
    let document = FlippedDocumentView(); document.translatesAutoresizingMaskIntoConstraints = false
    let stack = NSStackView(); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 18; stack.translatesAutoresizingMaskIntoConstraints = false
    scroll.documentView = document; document.addSubview(stack); content.addSubview(scroll)
    let heading = label("Your PiRouter, on this Mac", size: 23, weight: .semibold)
    let intro = label("Connect to the dashboard first. Background file exports are optional and can be configured whenever you need them.", size: 13)
    intro.textColor = .secondaryLabelColor
    stack.addArrangedSubview(heading); stack.addArrangedSubview(intro)
    stack.addArrangedSubview(field("PiRouter address", address, placeholder: "http://pirouter.local:3000", help: "Use the dashboard address on your local network, or its HTTPS Tailscale address."))
    stack.addArrangedSubview(field("Local address (optional)", localAddress, placeholder: "http://192.168.1.20:3000", help: "A direct local address is tried before the remote address. Separate multiple addresses with commas."))
    exportToggle.target = self; exportToggle.action = #selector(toggleExports)
    stack.addArrangedSubview(exportToggle)
    exportFields.orientation = .vertical; exportFields.alignment = .leading; exportFields.spacing = 16
    let note = label("Exports require a paired PiRouter, an SSH key already authorized on the archive server, and Full Disk Access for PiRouter Exporter. Your existing export checkpoints are preserved.", size: 12)
    note.textColor = .secondaryLabelColor; exportFields.addArrangedSubview(note)
    exportFields.addArrangedSubview(field("Exporter pairing token", token, placeholder: "Paste your PiRouter pairing token"))
    exportFields.addArrangedSubview(field("Archive server (SSH)", server, placeholder: "backup@server"))
    let keyField = field("SSH private key", keyPath, placeholder: "Choose an authorized SSH key")
    exportFields.addArrangedSubview(keyField)
    let choose = NSButton(title: "Choose SSH Key…", target: self, action: #selector(chooseKey))
    exportFields.addArrangedSubview(choose)
    exportFields.addArrangedSubview(field("Archive share", destination, placeholder: "/mnt/user/time-machine-austin", help: "Browsable files are stored inside this share's snapshots folder."))
    exportFields.addArrangedSubview(field("SSH relay command (optional)", relay, placeholder: "Leave blank for a direct SSH connection", help: "Advanced: an existing SSH ProxyCommand for servers reached through a trusted relay."))
    testButton.target = self; testButton.action = #selector(testAccess); exportFields.addArrangedSubview(testButton)
    stack.addArrangedSubview(exportFields)
    for view in stack.arrangedSubviews { view.translatesAutoresizingMaskIntoConstraints = false; view.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true }
    for view in exportFields.arrangedSubviews where !(view is NSButton) { view.translatesAutoresizingMaskIntoConstraints = false; view.widthAnchor.constraint(equalTo: exportFields.widthAnchor).isActive = true }
    let footer = NSView(); footer.translatesAutoresizingMaskIntoConstraints = false; content.addSubview(footer)
    errorLabel.font = .systemFont(ofSize: 12); errorLabel.textColor = .systemRed; errorLabel.maximumNumberOfLines = 3
    errorLabel.translatesAutoresizingMaskIntoConstraints = false
    saveButton.bezelStyle = .rounded; saveButton.keyEquivalent = "\r"; saveButton.target = self; saveButton.action = #selector(save)
    cancelButton.bezelStyle = .rounded; cancelButton.keyEquivalent = "\u{1b}"; cancelButton.target = self; cancelButton.action = #selector(cancel)
    saveButton.translatesAutoresizingMaskIntoConstraints = false; cancelButton.translatesAutoresizingMaskIntoConstraints = false
    activity.style = .spinning; activity.controlSize = .small; activity.isDisplayedWhenStopped = false; activity.translatesAutoresizingMaskIntoConstraints = false
    footer.addSubview(errorLabel); footer.addSubview(saveButton); footer.addSubview(cancelButton); footer.addSubview(activity)
    NSLayoutConstraint.activate([
      scroll.topAnchor.constraint(equalTo: content.topAnchor), scroll.leadingAnchor.constraint(equalTo: content.leadingAnchor), scroll.trailingAnchor.constraint(equalTo: content.trailingAnchor), scroll.bottomAnchor.constraint(equalTo: footer.topAnchor),
      document.widthAnchor.constraint(equalTo: scroll.contentView.widthAnchor),
      stack.topAnchor.constraint(equalTo: document.topAnchor, constant: 22), stack.leadingAnchor.constraint(equalTo: document.leadingAnchor, constant: 24), stack.trailingAnchor.constraint(equalTo: document.trailingAnchor, constant: -24), stack.bottomAnchor.constraint(equalTo: document.bottomAnchor, constant: -24),
      footer.leadingAnchor.constraint(equalTo: content.leadingAnchor), footer.trailingAnchor.constraint(equalTo: content.trailingAnchor), footer.bottomAnchor.constraint(equalTo: content.bottomAnchor), footer.heightAnchor.constraint(equalToConstant: 100),
      errorLabel.leadingAnchor.constraint(equalTo: footer.leadingAnchor, constant: 24), errorLabel.trailingAnchor.constraint(equalTo: footer.trailingAnchor, constant: -24), errorLabel.topAnchor.constraint(equalTo: footer.topAnchor, constant: 5),
      saveButton.trailingAnchor.constraint(equalTo: footer.trailingAnchor, constant: -22), saveButton.bottomAnchor.constraint(equalTo: footer.bottomAnchor, constant: -18),
      cancelButton.trailingAnchor.constraint(equalTo: saveButton.leadingAnchor, constant: -10), cancelButton.centerYAnchor.constraint(equalTo: saveButton.centerYAnchor),
      activity.leadingAnchor.constraint(equalTo: footer.leadingAnchor, constant: 24), activity.centerYAnchor.constraint(equalTo: saveButton.centerYAnchor), activity.widthAnchor.constraint(equalToConstant: 16), activity.heightAnchor.constraint(equalToConstant: 16),
    ])
  }
  private func loadValues() {
    let values = original.values
    address.stringValue = values["PIROUTER_URL"] ?? ""
    var locals: [String] = []
    for value in [values["PIROUTER_LOCAL_URLS"], values["PIROUTER_LOCAL_URL"]].compactMap({ $0 }) {
      for local in value.split(separator: ",").map({ $0.trimmingCharacters(in: .whitespaces) }) where !locals.contains(local) { locals.append(local) }
    }
    originalLocalText = locals.joined(separator: ", "); localAddress.stringValue = originalLocalText
    token.stringValue = values["PIROUTER_TOKEN"] ?? ""; server.stringValue = values["EXPORT_HOST"] ?? ""
    keyPath.stringValue = values["EXPORT_SSH_KEY"] ?? ""
    destination.stringValue = values["EXPORT_ROOT"] ?? "/mnt/user/time-machine-austin"
    relay.stringValue = values["EXPORT_SSH_PROXY_COMMAND"] ?? ""
    exportToggle.state = original.exportEnabled ? .on : .off
    toggleExports()
  }
  @objc private func toggleExports() {
    let previousOrigin = formScroll.contentView.bounds.origin
    exportFields.isHidden = exportToggle.state != .on
    window?.contentView?.layoutSubtreeIfNeeded()
    let maximumY = max(0, (formScroll.documentView?.bounds.height ?? 0) - formScroll.contentSize.height)
    formScroll.contentView.scroll(to: NSPoint(x: previousOrigin.x, y: min(previousOrigin.y, maximumY)))
    formScroll.reflectScrolledClipView(formScroll.contentView)
  }
  @objc private func chooseKey() {
    guard !busy, let window else { return }
    let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.canChooseFiles = true; panel.allowsMultipleSelection = false
    panel.title = "Choose an authorized SSH private key"
    panel.directoryURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".ssh", isDirectory: true)
    panel.beginSheetModal(for: window) { [weak self] response in
      if response == .OK, let url = panel.url { self?.keyPath.stringValue = url.path }
    }
  }
  private func updates() throws -> [String: String] {
    guard let url = normalizedBaseURL(address.stringValue) else { throw AppFailure("Enter a valid HTTP or HTTPS PiRouter address without a username or password.") }
    var result: [String: String] = ["PIROUTER_URL": url.absoluteString, "EXPORT_ENABLED": exportToggle.state == .on ? "true" : "false"]
    if localAddress.stringValue != originalLocalText {
      let parts = localAddress.stringValue.split(separator: ",").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
      var urls: [String] = []
      for part in parts {
        guard let local = normalizedBaseURL(part) else { throw AppFailure("One of the local addresses is invalid. Use HTTP or HTTPS addresses separated by commas.") }
        if !urls.contains(local.absoluteString) { urls.append(local.absoluteString) }
      }
      result["PIROUTER_LOCAL_URLS"] = urls.joined(separator: ","); result["PIROUTER_LOCAL_URL"] = urls.first ?? ""
    }
    if exportToggle.state == .on {
      if backgroundActionsAllowed && (Bundle.main.bundleURL.path.hasPrefix("/Volumes/") || (try? Bundle.main.bundleURL.resourceValues(forKeys: [.volumeIsReadOnlyKey]).volumeIsReadOnly) == true) {
        throw AppFailure("Drag PiRouter Exporter into Applications before enabling background exports.")
      }
      let pairing = token.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
      let host = server.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
      let key = (keyPath.stringValue.trimmingCharacters(in: .whitespacesAndNewlines) as NSString).expandingTildeInPath
      let share = destination.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
      guard !pairing.isEmpty, !pairing.contains(where: { $0.isWhitespace }) else { throw AppFailure("Enter the PiRouter exporter pairing token, or leave background exports disabled.") }
      guard host.range(of: "^[A-Za-z0-9][A-Za-z0-9._@:-]*$", options: .regularExpression) != nil else { throw AppFailure("Enter an SSH destination such as backup@server, without spaces or command options.") }
      var directory: ObjCBool = false
      guard key.hasPrefix("/"), FileManager.default.fileExists(atPath: key, isDirectory: &directory), !directory.boolValue, FileManager.default.isReadableFile(atPath: key) else { throw AppFailure("Choose a readable SSH private key on this Mac.") }
      guard share.range(of: "^/mnt/user/[A-Za-z0-9._-]+$", options: .regularExpression) != nil, ![".", ".."].contains((share as NSString).lastPathComponent) else { throw AppFailure("Use a dedicated Unraid share such as /mnt/user/time-machine-austin.") }
      result.merge(["PIROUTER_TOKEN": pairing, "EXPORT_HOST": host, "EXPORT_SSH_KEY": key, "EXPORT_ROOT": share, "EXPORT_SSH_PROXY_COMMAND": relay.stringValue]) { _,new in new }
      for (key,value) in ["AUTOMATIC_EXPORT":"true", "AUTOMATIC_EXPORT_MIN_HOURS":"24", "CONTINUOUS_EXPORT":"true", "RSYNC_BANDWIDTH_LIMIT_KB":"40000"] where original.values[key] == nil { result[key] = value }
    }
    guard !result.values.contains(where: { $0.contains("\0") || $0.contains("\n") || $0.contains("\r") }) else { throw AppFailure("Settings must be single-line values.") }
    return result
  }
  private func setBusy(_ value: Bool, message: String = "") {
    busy = value; saveButton.isEnabled = !value; cancelButton.isEnabled = !value; testButton.isEnabled = !value; exportToggle.isEnabled = !value
    for input in [address, localAddress, token, server, keyPath, destination, relay] { input.isEnabled = !value }
    errorLabel.stringValue = message
    if value { activity.startAnimation(nil) } else { activity.stopAnimation(nil) }
  }
  @objc private func save() {
    do {
      let changes = try updates()
      var merged = original.values; merged.merge(changes) { _,new in new }
      let relevant = ["PIROUTER_URL", "PIROUTER_LOCAL_URLS", "PIROUTER_LOCAL_URL", "PIROUTER_TOKEN", "EXPORT_HOST", "EXPORT_SSH_KEY", "EXPORT_ROOT", "EXPORT_SSH_PROXY_COMMAND"]
      let changed = relevant.contains { original.values[$0] != merged[$0] }
      setBusy(true, message: "Saving settings…")
      DispatchQueue.global(qos: .userInitiated).async { [weak self] in
        do {
          try writeEnvironmentValues(changes, to: configURL)
          try BackgroundExporter.apply(restart: changed)
          DispatchQueue.main.async { self?.onSaved?(); self?.busy = false; self?.window?.close() }
        } catch { DispatchQueue.main.async { self?.setBusy(false, message: error.localizedDescription) } }
      }
    } catch { errorLabel.stringValue = error.localizedDescription }
  }
  @objc private func testAccess() {
    do {
      var values = original.values; values.merge(try updates()) { _,new in new }
      guard values["EXPORT_ENABLED"] == "true" else { return }
      setBusy(true, message: "Testing SSH access without uploading any files…")
      DispatchQueue.global(qos: .userInitiated).async { [weak self] in
        do {
          var arguments = ["-i", values["EXPORT_SSH_KEY"]!, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8"]
          if let proxy = values["EXPORT_SSH_PROXY_COMMAND"], !proxy.isEmpty { arguments += ["-o", "ProxyCommand=" + proxy] }
          arguments += [values["EXPORT_HOST"]!, "true"]
          let result = try runCommand("/usr/bin/ssh", arguments, timeout: 12, capture: true)
          let message: String
          if result.0 == 0 { message = "SSH access works. Save to enable exports; Full Disk Access is still required." }
          else if result.1.localizedCaseInsensitiveContains("host key") {
            message = "The server is not trusted yet. Confirm its identity once in your SSH client, then test again. Host-key checking remains enabled."
          } else if result.1.localizedCaseInsensitiveContains("Permission denied") {
            message = "The server did not accept this SSH key. Authorize its public key on the archive server, then test again."
          } else { message = "The SSH server could not be reached. Check its address, Tailscale connection, and any relay command." }
          DispatchQueue.main.async { self?.setBusy(false, message: message) }
        } catch { DispatchQueue.main.async { self?.setBusy(false, message: error.localizedDescription) } }
      }
    } catch { errorLabel.stringValue = error.localizedDescription }
  }
  @objc private func cancel() { window?.close() }
  func windowShouldClose(_ sender: NSWindow) -> Bool { !busy }
  func windowWillClose(_ notification: Notification) { onClosed?() }
}

private final class DashboardViewController: NSViewController, WKNavigationDelegate, WKUIDelegate {
  var onShowSettings: (() -> Void)?
  private var configuration = AppConfiguration.load()
  private let webView: WKWebView
  private let statusLabel = NSTextField(labelWithString: "Finding PiRouter…")
  private let progress = NSProgressIndicator()
  private let reloadButton = NSButton(image: NSImage(systemSymbolName: "arrow.clockwise", accessibilityDescription: "Reconnect and reload")!, target: nil, action: nil)
  private let settingsButton = NSButton(image: NSImage(systemSymbolName: "gearshape", accessibilityDescription: "Connection settings")!, target: nil, action: nil)
  private let browserButton = NSButton(image: NSImage(systemSymbolName: "safari", accessibilityDescription: "Open dashboard in browser")!, target: nil, action: nil)
  private let placeholder = NSStackView()
  private let placeholderTitle = NSTextField(labelWithString: "Connect to PiRouter")
  private let placeholderBody = NSTextField(wrappingLabelWithString: "")
  private let retryButton = NSButton(title: "Try Again", target: nil, action: nil)
  private var currentEndpoint: Endpoint?
  private var monitorTimer: Timer?
  private var probeTask: URLSessionDataTask?
  private var probeGeneration = 0
  private var isResolving = false
  private var retryScheduled = false

  init() {
    let config = WKWebViewConfiguration(); config.websiteDataStore = .default()
    webView = WKWebView(frame: .zero, configuration: config)
    webView.underPageBackgroundColor = NSColor(calibratedRed: 0.03, green: 0.07, blue: 0.05, alpha: 1)
    super.init(nibName: nil, bundle: nil)
  }
  required init?(coder: NSCoder) { nil }
  override func loadView() {
    let root = NSView(); root.wantsLayer = true; view = root
    let bar = NSVisualEffectView(); bar.material = .headerView; bar.blendingMode = .withinWindow
    bar.translatesAutoresizingMaskIntoConstraints = false; webView.translatesAutoresizingMaskIntoConstraints = false
    statusLabel.font = .systemFont(ofSize: 12); statusLabel.textColor = .secondaryLabelColor; statusLabel.lineBreakMode = .byTruncatingTail
    statusLabel.translatesAutoresizingMaskIntoConstraints = false; progress.translatesAutoresizingMaskIntoConstraints = false
    progress.style = .spinning; progress.controlSize = .small; progress.isDisplayedWhenStopped = false
    for button in [browserButton, settingsButton, reloadButton] { button.isBordered = false; button.translatesAutoresizingMaskIntoConstraints = false; button.widthAnchor.constraint(equalToConstant: 28).isActive = true; button.heightAnchor.constraint(equalToConstant: 28).isActive = true }
    reloadButton.target = self; reloadButton.action = #selector(reloadDashboard); reloadButton.toolTip = "Reconnect and reload (⌘R)"
    settingsButton.target = self; settingsButton.action = #selector(showSettings); settingsButton.toolTip = "Connection settings (⌘,)"
    browserButton.target = self; browserButton.action = #selector(openInBrowser); browserButton.toolTip = "Open dashboard in browser"
    webView.navigationDelegate = self; webView.uiDelegate = self; webView.allowsMagnification = true
    root.addSubview(bar); root.addSubview(webView)
    for item in [progress, statusLabel, browserButton, settingsButton, reloadButton] { bar.addSubview(item) }
    placeholder.orientation = .vertical; placeholder.alignment = .centerX; placeholder.spacing = 16; placeholder.translatesAutoresizingMaskIntoConstraints = false
    let icon = NSImageView(image: NSImage(systemSymbolName: "network", accessibilityDescription: nil)!); icon.contentTintColor = .systemTeal
    icon.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 48, weight: .medium)
    placeholderTitle.font = .systemFont(ofSize: 26, weight: .semibold)
    placeholderBody.font = .systemFont(ofSize: 14); placeholderBody.textColor = .secondaryLabelColor; placeholderBody.alignment = .center; placeholderBody.maximumNumberOfLines = 0
    let setup = NSButton(title: "Connection Settings…", target: self, action: #selector(showSettings)); setup.bezelStyle = .rounded
    retryButton.target = self; retryButton.action = #selector(reloadDashboard); retryButton.bezelStyle = .rounded
    let actions = NSStackView(views: [retryButton, setup]); actions.orientation = .horizontal; actions.spacing = 10
    for item in [icon, placeholderTitle, placeholderBody, actions] { placeholder.addArrangedSubview(item) }
    root.addSubview(placeholder)
    NSLayoutConstraint.activate([
      bar.topAnchor.constraint(equalTo: root.topAnchor), bar.leadingAnchor.constraint(equalTo: root.leadingAnchor), bar.trailingAnchor.constraint(equalTo: root.trailingAnchor), bar.heightAnchor.constraint(equalToConstant: 42),
      progress.leadingAnchor.constraint(equalTo: bar.leadingAnchor, constant: 13), progress.centerYAnchor.constraint(equalTo: bar.centerYAnchor), progress.widthAnchor.constraint(equalToConstant: 16), progress.heightAnchor.constraint(equalToConstant: 16),
      statusLabel.leadingAnchor.constraint(equalTo: progress.trailingAnchor, constant: 9), statusLabel.centerYAnchor.constraint(equalTo: bar.centerYAnchor),
      reloadButton.trailingAnchor.constraint(equalTo: bar.trailingAnchor, constant: -9), reloadButton.centerYAnchor.constraint(equalTo: bar.centerYAnchor),
      settingsButton.trailingAnchor.constraint(equalTo: reloadButton.leadingAnchor, constant: -6), settingsButton.centerYAnchor.constraint(equalTo: bar.centerYAnchor),
      browserButton.trailingAnchor.constraint(equalTo: settingsButton.leadingAnchor, constant: -6), browserButton.centerYAnchor.constraint(equalTo: bar.centerYAnchor),
      statusLabel.trailingAnchor.constraint(lessThanOrEqualTo: browserButton.leadingAnchor, constant: -10),
      webView.topAnchor.constraint(equalTo: bar.bottomAnchor), webView.leadingAnchor.constraint(equalTo: root.leadingAnchor), webView.trailingAnchor.constraint(equalTo: root.trailingAnchor), webView.bottomAnchor.constraint(equalTo: root.bottomAnchor),
      placeholder.centerXAnchor.constraint(equalTo: webView.centerXAnchor), placeholder.centerYAnchor.constraint(equalTo: webView.centerYAnchor), placeholder.widthAnchor.constraint(lessThanOrEqualToConstant: 460), placeholder.leadingAnchor.constraint(greaterThanOrEqualTo: root.leadingAnchor, constant: 32), placeholder.trailingAnchor.constraint(lessThanOrEqualTo: root.trailingAnchor, constant: -32),
      placeholderBody.widthAnchor.constraint(equalTo: placeholder.widthAnchor),
    ])
    displayPlaceholder("Connect to PiRouter", "Open Connection Settings to add your PiRouter address. Viewing the dashboard does not require an SSH key or exporter token.", canRetry: false)
  }
  override func viewDidAppear() {
    super.viewDidAppear()
    if monitorTimer == nil {
      monitorTimer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { [weak self] _ in self?.resolveAndLoad() }
      resolveAndLoad()
    }
  }
  deinit { monitorTimer?.invalidate(); probeTask?.cancel() }
  func stopMonitoring() { monitorTimer?.invalidate(); monitorTimer = nil; probeTask?.cancel(); probeGeneration += 1 }
  func configurationChanged() {
    probeTask?.cancel(); probeGeneration += 1; isResolving = false
    configuration = .load(); currentEndpoint = nil
    // Retire the previous dashboard immediately; its controls may target a
    // different Pi than the newly saved configuration.
    webView.stopLoading(); webView.load(URLRequest(url: URL(string: "about:blank")!))
    displayPlaceholder("Connecting to PiRouter", "Checking your saved connection settings…", canRetry: false)
    resolveAndLoad(forceReload: true)
  }
  @objc func showSettings() { onShowSettings?() }
  @objc func reloadDashboard() { resolveAndLoad(forceReload: true) }
  @objc func openInBrowser() { if let url = currentEndpoint?.baseURL ?? configuration.endpoints.first?.baseURL { NSWorkspace.shared.open(url) } }
  private func displayPlaceholder(_ title: String, _ message: String, canRetry: Bool) {
    placeholderTitle.stringValue = title; placeholderBody.stringValue = message; retryButton.isHidden = !canRetry; placeholder.isHidden = false; webView.isHidden = true
  }
  private func resolveAndLoad(forceReload: Bool = false) {
    if isResolving && !forceReload { return }
    if forceReload { probeTask?.cancel(); isResolving = false }
    guard !configuration.endpoints.isEmpty else {
      currentEndpoint = nil; statusLabel.stringValue = "Add a PiRouter address to get started"; statusLabel.toolTip = statusLabel.stringValue
      displayPlaceholder("Connect to PiRouter", "View your dashboard on this Mac. Open Connection Settings to add a local or Tailscale address; file exports can be set up separately.", canRetry: false)
      progress.stopAnimation(nil); return
    }
    isResolving = true; probeGeneration += 1; let generation = probeGeneration
    progress.startAnimation(nil)
    if currentEndpoint == nil { statusLabel.stringValue = "Finding PiRouter on your network…" }
    probe(configuration.endpoints, index: 0, generation: generation) { [weak self] endpoint in
      guard let self, generation == self.probeGeneration else { return }
      self.isResolving = false; self.progress.stopAnimation(nil)
      guard let endpoint else { self.showOffline("PiRouter is unavailable. Check Wi-Fi or Tailscale, then try again."); return }
      self.statusLabel.textColor = .secondaryLabelColor; self.statusLabel.stringValue = endpoint.statusText; self.statusLabel.toolTip = endpoint.baseURL.absoluteString
      let load = forceReload || endpoint != self.currentEndpoint || self.webView.url == nil
      self.currentEndpoint = endpoint; self.view.window?.title = "PiRouter Exporter — \(endpoint.name)"
      self.placeholder.isHidden = true; self.webView.isHidden = false
      if load { self.webView.load(URLRequest(url: endpoint.baseURL, cachePolicy: .reloadRevalidatingCacheData, timeoutInterval: 15)) }
    }
  }
  private func probe(_ endpoints: [Endpoint], index: Int, generation: Int, completion: @escaping (Endpoint?) -> Void) {
    guard index < endpoints.count else { completion(nil); return }
    let endpoint = endpoints[index]
    var request = URLRequest(url: endpoint.baseURL.appendingPathComponent("api/status"), cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: endpoint.kind == .local ? 1.5 : 5)
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    probeTask = URLSession.shared.dataTask(with: request) { [weak self] data, response, _ in
      DispatchQueue.main.async {
        guard let self, generation == self.probeGeneration else { return }
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        let dictionary = data.flatMap { $0.count <= 2_097_152 ? (try? JSONSerialization.jsonObject(with: $0)) as? [String: Any] : nil }
        let valid = code == 200 && response?.url.map { sameOrigin($0, endpoint.baseURL) } == true
          && dictionary?["system"] is [String: Any] && dictionary?["network"] is [String: Any] && dictionary?["summary"] is [String: Any]
        if valid { completion(endpoint) }
        else { self.probe(endpoints, index: index + 1, generation: generation, completion: completion) }
      }
    }
    probeTask?.resume()
  }
  private func showOffline(_ message: String) {
    currentEndpoint = nil; statusLabel.textColor = .systemOrange; statusLabel.stringValue = message; statusLabel.toolTip = message
    view.window?.title = "PiRouter Exporter — Disconnected"
    if webView.url == nil || webView.isHidden { displayPlaceholder("PiRouter is offline", message + " Your export checkpoints stay on this Mac and the archive server.", canRetry: true) }
  }
  private func navigationFailed(_ error: Error) {
    if (error as NSError).code == NSURLErrorCancelled { return }
    progress.stopAnimation(nil); currentEndpoint = nil; webView.isHidden = true
    showOffline("The dashboard connection was interrupted. Retrying automatically…")
    guard !retryScheduled else { return }; retryScheduled = true
    DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.retryScheduled = false; self?.resolveAndLoad(forceReload: true) }
  }
  func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) { progress.startAnimation(nil) }
  func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { progress.stopAnimation(nil); if let endpoint = currentEndpoint { statusLabel.stringValue = endpoint.statusText; statusLabel.textColor = .secondaryLabelColor } }
  func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { navigationFailed(error) }
  func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { navigationFailed(error) }
  func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { navigationFailed(AppFailure("Dashboard content process stopped")) }
  func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
    guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
    if url.absoluteString == "about:blank" { decisionHandler(.allow); return }
    guard ["http", "https"].contains(url.scheme?.lowercased() ?? ""), URLComponents(url: url, resolvingAgainstBaseURL: false)?.user == nil else { decisionHandler(.cancel); return }
    if navigationAction.targetFrame?.isMainFrame == false { decisionHandler(.allow); return }
    if configuration.endpoints.contains(where: { sameOrigin($0.baseURL, url) }) { decisionHandler(.allow) }
    else {
      if navigationAction.navigationType == .linkActivated { NSWorkspace.shared.open(url) }
      decisionHandler(.cancel)
    }
  }
  func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
    if let url = navigationAction.request.url, ["http", "https"].contains(url.scheme?.lowercased() ?? ""), URLComponents(url: url, resolvingAgainstBaseURL: false)?.user == nil { NSWorkspace.shared.open(url) }
    return nil
  }
}

private final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
  private var window: NSWindow?
  private var dashboard: DashboardViewController?
  private var settings: SettingsWindowController?
  func applicationDidFinishLaunching(_ notification: Notification) {
    let controller = DashboardViewController()
    controller.onShowSettings = { [weak self] in self?.showSettings() }
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 780), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
    window.title = "PiRouter Exporter"; window.minSize = NSSize(width: 620, height: 480); window.contentViewController = controller
    window.center(); window.setFrameAutosaveName("PiRouterExporterWindow"); window.tabbingMode = .disallowed; window.delegate = self
    dashboard = controller; self.window = window; installMenus(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    let configuration = AppConfiguration.load()
    if configuration.endpoints.isEmpty { DispatchQueue.main.async { [weak self] in self?.showSettings() } }
    else if configuration.exportEnabled && backgroundActionsAllowed {
      DispatchQueue.global(qos: .utility).async { try? BackgroundExporter.apply(restart: false) }
    }
  }
  @objc private func showSettings() {
    if let settings { settings.showWindow(nil); settings.window?.makeKeyAndOrderFront(nil); return }
    let controller = SettingsWindowController()
    controller.onSaved = { [weak self] in self?.dashboard?.configurationChanged() }
    controller.onClosed = { [weak self] in self?.settings = nil }
    settings = controller; controller.showWindow(nil); controller.window?.center(); controller.window?.makeKeyAndOrderFront(nil)
  }
  @objc private func showLogs() {
    try? FileManager.default.createDirectory(at: supportDirectory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    let log = supportDirectory.appendingPathComponent("export.log")
    if FileManager.default.fileExists(atPath: log.path) { NSWorkspace.shared.activateFileViewerSelecting([log]) }
    else { NSWorkspace.shared.open(supportDirectory) }
  }
  @objc private func showFullDiskAccess() {
    if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles") { NSWorkspace.shared.open(url) }
  }
  @objc private func runExportCheck() { BackgroundExporter.startCheck() }
  func windowWillClose(_ notification: Notification) { if (notification.object as? NSWindow) === window { dashboard?.stopMonitoring(); settings?.window?.close() } }
  func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
  func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { window?.makeKeyAndOrderFront(nil); return true }
  private func item(_ title: String, action: Selector, key: String = "", target: AnyObject? = nil) -> NSMenuItem {
    let item = NSMenuItem(title: title, action: action, keyEquivalent: key); item.target = target ?? self; return item
  }
  private func installMenus() {
    let main = NSMenu(); let appItem = NSMenuItem(); let viewItem = NSMenuItem(); let exportsItem = NSMenuItem()
    main.addItem(appItem); main.addItem(viewItem); main.addItem(exportsItem)
    let app = NSMenu(title: "PiRouter Exporter")
    app.addItem(item("About PiRouter Exporter", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), target: NSApp))
    app.addItem(.separator()); app.addItem(item("Settings…", action: #selector(showSettings), key: ",")); app.addItem(.separator())
    app.addItem(item("Quit PiRouter Exporter", action: #selector(NSApplication.terminate(_:)), key: "q", target: NSApp)); appItem.submenu = app
    let view = NSMenu(title: "View")
    view.addItem(item("Reconnect and Reload", action: #selector(DashboardViewController.reloadDashboard), key: "r", target: dashboard))
    view.addItem(item("Open Dashboard in Browser", action: #selector(DashboardViewController.openInBrowser), target: dashboard))
    view.addItem(.separator()); let fullScreen = NSMenuItem(title: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f"); fullScreen.keyEquivalentModifierMask = [.control, .command]; view.addItem(fullScreen); viewItem.submenu = view
    let exports = NSMenu(title: "Exports")
    exports.addItem(item("Run Export Check Now", action: #selector(runExportCheck)))
    exports.addItem(item("Show Export Logs", action: #selector(showLogs)))
    exports.addItem(.separator()); exports.addItem(item("Full Disk Access…", action: #selector(showFullDiskAccess))); exportsItem.submenu = exports
    let editItem = NSMenuItem(); main.insertItem(editItem, at: 1); let edit = NSMenu(title: "Edit")
    for (title, selector, key) in [("Undo", Selector(("undo:")), "z"), ("Cut", #selector(NSText.cut(_:)), "x"), ("Copy", #selector(NSText.copy(_:)), "c"), ("Paste", #selector(NSText.paste(_:)), "v"), ("Select All", #selector(NSText.selectAll(_:)), "a")] {
      edit.addItem(NSMenuItem(title: title, action: selector, keyEquivalent: key))
    }
    editItem.submenu = edit; NSApp.mainMenu = main
  }
}

private func runSelfTests() -> Int32 {
  var count = 0
  func check(_ valid: @autoclosure () -> Bool, _ name: String) throws { guard valid() else { throw AppFailure("Failed: " + name) }; count += 1 }
  do {
    try check(shellLiteral("/Users/test/Library/Application\\ Support/key") == "/Users/test/Library/Application Support/key", "printf-q spaces")
    try check(shellLiteral("'it'\\''s safe'") == "it's safe", "single quote concatenation")
    try check(shellLiteral("$'line\\ntext'") == "line\ntext", "ANSI-C newline")
    try check(shellLiteral("$'\\303\\251'") == "é", "ANSI-C UTF-8")
    try check(shellLiteral("'unterminated") == nil, "unterminated literal")
    try check(normalizedBaseURL("https://name:password@example.invalid") == nil, "URL credentials rejected")
    try check(normalizedBaseURL("file:///tmp/test") == nil, "file URLs rejected")
    try check(normalizedBaseURL("https://example.invalid:99999") == nil, "invalid port rejected")
    try check(AppConfiguration.from(["PIROUTER_URL":"http://100.64.0.1:3000"]).endpoints.count == 1, "numeric hosts not inferred as mDNS")
    try check(!AppConfiguration.from(["PIROUTER_URL":"http://router.local:3000"]).exportEnabled, "dashboard-only requires no export credentials")
    try check(AppConfiguration.from(["PIROUTER_TOKEN":"token", "EXPORT_HOST":"user@server", "EXPORT_SSH_KEY":"/key"]).exportEnabled, "legacy export enabled")
    try check(!AppConfiguration.from(["EXPORT_ENABLED":"false", "PIROUTER_TOKEN":"token", "EXPORT_HOST":"user@server", "EXPORT_SSH_KEY":"/key"]).exportEnabled, "explicit export disabled")
    let temporary = FileManager.default.temporaryDirectory.appendingPathComponent("pirouter-native-test-" + UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: false)
    defer { try? FileManager.default.removeItem(at: temporary) }
    let file = temporary.appendingPathComponent("settings.env")
    try "# keep this comment\nCUSTOM_POLICY='leave me alone'\nPIROUTER_URL=http://old.invalid\nPIROUTER_URL=http://duplicate.invalid\nRSYNC_BANDWIDTH_LIMIT_KB=12345\n".write(to: file, atomically: false, encoding: .utf8)
    let proxy = "/usr/bin/ssh -i '/Users/test/Key With Spaces' relay -- literal$()"
    try writeEnvironmentValues(["PIROUTER_URL":"http://new.invalid:3000", "EXPORT_SSH_PROXY_COMMAND":proxy], to: file)
    let values = readEnvironmentFile(file)
    try check(values["EXPORT_SSH_PROXY_COMMAND"] == proxy, "proxy round trip without evaluation")
    try check(values["CUSTOM_POLICY"] == "leave me alone" && values["RSYNC_BANDWIDTH_LIMIT_KB"] == "12345", "unknown settings and policies preserved")
    let saved = try String(contentsOf: file, encoding: .utf8)
    let permissions = try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? NSNumber
    try check(saved.contains("# keep this comment"), "comments preserved")
    try check(permissions?.intValue == 0o600, "configuration permissions")
    let largeCapture = try runCommand("/usr/bin/head", ["-c", "131072", "/dev/zero"], timeout: 3, capture: true)
    try check(largeCapture.0 == 0 && largeCapture.1.utf8.count == 65_536, "large diagnostics captured without pipe backpressure")
    var timedOut = false
    do { _ = try runCommand("/bin/sleep", ["2"], timeout: 0.05, capture: true) }
    catch { timedOut = error.localizedDescription.contains("took too long") }
    try check(timedOut, "timed-out child terminated and reaped")
    print("PiRouter native self-tests passed: \(count)")
    return 0
  } catch { FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8)); return 1 }
}

if CommandLine.arguments.contains("--runtime-check") { exit(runRuntimeCheck()) }
if CommandLine.arguments.contains("--self-test") { exit(runSelfTests()) }
if CommandLine.arguments.contains("--background") { exit(runBundledScript("portable-export-macos.sh")) }
if CommandLine.arguments.contains("--verify-volumes") { exit(runBundledScript("verify-time-machine-volumes.py", python: true)) }
let application = NSApplication.shared
private let delegate = AppDelegate()
application.setActivationPolicy(.regular)
application.delegate = delegate
application.run()
