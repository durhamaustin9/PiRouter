import AppKit
import Darwin
import Foundation
import WebKit

private let supportDirectory = FileManager.default.homeDirectoryForCurrentUser
  .appendingPathComponent("Library/Application Support/PiRouter", isDirectory: true)
private let configURL = supportDirectory.appendingPathComponent("portable-export.env")

private struct Endpoint: Equatable {
  enum Kind {
    case local
    case tailscale
    case remote
  }

  let baseURL: URL
  let kind: Kind

  var statusText: String {
    switch kind {
    case .local:
      return "Connected directly on the local network"
    case .tailscale:
      return "Connected securely through Tailscale"
    case .remote:
      return "Connected to the remote PiRouter"
    }
  }
}

private struct AppConfiguration {
  let endpoints: [Endpoint]

  static func load() -> AppConfiguration {
    let values = readEnvironmentFile(configURL)
    var endpoints: [Endpoint] = []

    let configuredLocal = [
      values["PIROUTER_LOCAL_URLS"],
      values["PIROUTER_LOCAL_URL"],
    ]
      .compactMap { $0 }
      .flatMap { $0.split(separator: ",").map(String.init) }

    for value in configuredLocal {
      appendEndpoint(value, kind: .local, to: &endpoints)
    }

    if let remoteValue = values["PIROUTER_URL"],
       let remoteURL = normalizedBaseURL(remoteValue)
    {
      if configuredLocal.isEmpty,
         let host = remoteURL.host,
         let shortName = host.split(separator: ".").first,
         !shortName.isEmpty
      {
        appendEndpoint("http://\(shortName).local:3000", kind: .local, to: &endpoints)
      }

      let kind: Endpoint.Kind = remoteURL.host?.hasSuffix(".ts.net") == true
        ? .tailscale
        : .remote
      appendEndpoint(remoteValue, kind: kind, to: &endpoints)
    }

    return AppConfiguration(endpoints: endpoints)
  }

  private static func appendEndpoint(
    _ value: String,
    kind: Endpoint.Kind,
    to endpoints: inout [Endpoint]
  ) {
    guard let url = normalizedBaseURL(value),
          !endpoints.contains(where: { $0.baseURL == url })
    else { return }
    endpoints.append(Endpoint(baseURL: url, kind: kind))
  }
}

private func readEnvironmentFile(_ url: URL) -> [String: String] {
  guard let contents = try? String(contentsOf: url, encoding: .utf8) else {
    return [:]
  }

  var result: [String: String] = [:]
  for rawLine in contents.components(separatedBy: .newlines) {
    let line = rawLine.trimmingCharacters(in: .whitespaces)
    guard !line.isEmpty, !line.hasPrefix("#"),
          let separator = line.firstIndex(of: "=")
    else { continue }

    let key = String(line[..<separator]).trimmingCharacters(in: .whitespaces)
    var value = String(line[line.index(after: separator)...])
      .trimmingCharacters(in: .whitespaces)
    if value.count >= 2,
       ((value.hasPrefix("\"") && value.hasSuffix("\"")) ||
        (value.hasPrefix("'") && value.hasSuffix("'")))
    {
      value.removeFirst()
      value.removeLast()
    }
    result[key] = value
  }
  return result
}

private func normalizedBaseURL(_ value: String) -> URL? {
  let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
  guard var components = URLComponents(string: trimmed),
        components.scheme == "http" || components.scheme == "https",
        components.host != nil
  else { return nil }
  components.path = ""
  components.query = nil
  components.fragment = nil
  return components.url
}

private func exporterScriptURL() -> URL? {
  Bundle.main.resourceURL?.appendingPathComponent("portable-export-macos.sh")
}

private func verifierScriptURL() -> URL? {
  Bundle.main.resourceURL?.appendingPathComponent("verify-time-machine-volumes.py")
}

@discardableResult
private func runExporterAndWait() -> Int32 {
  guard let script = exporterScriptURL(),
        FileManager.default.isExecutableFile(atPath: script.path)
  else {
    FileHandle.standardError.write(
      Data("PiRouter Exporter is missing its portable-export script.\n".utf8)
    )
    return 1
  }

  let exporter = Process()
  exporter.executableURL = URL(fileURLWithPath: "/bin/bash")
  exporter.arguments = [script.path]
  exporter.standardInput = FileHandle.nullDevice
  exporter.standardOutput = FileHandle.standardOutput
  exporter.standardError = FileHandle.standardError

  signal(SIGINT, SIG_IGN)
  signal(SIGTERM, SIG_IGN)
  let interruptSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .global())
  let terminateSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .global())
  interruptSource.setEventHandler { exporter.interrupt() }
  terminateSource.setEventHandler { exporter.terminate() }
  interruptSource.resume()
  terminateSource.resume()

  do {
    try exporter.run()
    exporter.waitUntilExit()
  } catch {
    FileHandle.standardError.write(
      Data("PiRouter Exporter could not start: \(error.localizedDescription)\n".utf8)
    )
    return 1
  }

  switch exporter.terminationReason {
  case .exit:
    return exporter.terminationStatus
  case .uncaughtSignal:
    return 128 + exporter.terminationStatus
  @unknown default:
    return 1
  }
}

@discardableResult
private func runVerifierAndWait() -> Int32 {
  guard let script = verifierScriptURL(),
        FileManager.default.fileExists(atPath: script.path)
  else {
    FileHandle.standardError.write(
      Data("PiRouter Exporter is missing its Time Machine verifier.\n".utf8)
    )
    return 1
  }

  let verifier = Process()
  verifier.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
  verifier.arguments = [script.path, "Armstrong", "Development"]
  verifier.standardInput = FileHandle.nullDevice
  verifier.standardOutput = FileHandle.standardOutput
  verifier.standardError = FileHandle.standardError

  signal(SIGINT, SIG_IGN)
  signal(SIGTERM, SIG_IGN)
  let interruptSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .global())
  let terminateSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .global())
  interruptSource.setEventHandler { verifier.interrupt() }
  terminateSource.setEventHandler { verifier.terminate() }
  interruptSource.resume()
  terminateSource.resume()

  do {
    try verifier.run()
    verifier.waitUntilExit()
  } catch {
    FileHandle.standardError.write(
      Data("PiRouter Exporter could not start verification: \(error.localizedDescription)\n".utf8)
    )
    return 1
  }

  switch verifier.terminationReason {
  case .exit:
    return verifier.terminationStatus
  case .uncaughtSignal:
    return 128 + verifier.terminationStatus
  @unknown default:
    return 1
  }
}

private final class DashboardViewController: NSViewController, WKNavigationDelegate {
  private let configuration = AppConfiguration.load()
  private let webView: WKWebView
  private let statusLabel = NSTextField(labelWithString: "Finding PiRouter…")
  private let progressIndicator = NSProgressIndicator()
  private let reloadButton = NSButton(
    image: NSImage(named: NSImage.refreshTemplateName) ?? NSImage(),
    target: nil,
    action: nil
  )
  private var currentEndpoint: Endpoint?
  private var isResolving = false
  private var monitorTimer: Timer?

  init() {
    let webConfiguration = WKWebViewConfiguration()
    webConfiguration.websiteDataStore = .default()
    webView = WKWebView(frame: .zero, configuration: webConfiguration)
    super.init(nibName: nil, bundle: nil)
  }

  required init?(coder: NSCoder) {
    nil
  }

  override func loadView() {
    let root = NSView()
    root.wantsLayer = true

    let connectionBar = NSVisualEffectView()
    connectionBar.material = .headerView
    connectionBar.blendingMode = .withinWindow
    connectionBar.state = .active
    connectionBar.translatesAutoresizingMaskIntoConstraints = false

    statusLabel.font = .systemFont(ofSize: 12, weight: .medium)
    statusLabel.textColor = .secondaryLabelColor
    statusLabel.lineBreakMode = .byTruncatingTail
    statusLabel.translatesAutoresizingMaskIntoConstraints = false

    progressIndicator.style = .spinning
    progressIndicator.controlSize = .small
    progressIndicator.isDisplayedWhenStopped = false
    progressIndicator.translatesAutoresizingMaskIntoConstraints = false

    reloadButton.target = self
    reloadButton.action = #selector(reloadDashboard)
    reloadButton.isBordered = false
    reloadButton.toolTip = "Reconnect and reload"
    reloadButton.translatesAutoresizingMaskIntoConstraints = false

    webView.navigationDelegate = self
    webView.allowsMagnification = true
    webView.translatesAutoresizingMaskIntoConstraints = false

    root.addSubview(connectionBar)
    connectionBar.addSubview(progressIndicator)
    connectionBar.addSubview(statusLabel)
    connectionBar.addSubview(reloadButton)
    root.addSubview(webView)

    NSLayoutConstraint.activate([
      connectionBar.topAnchor.constraint(equalTo: root.topAnchor),
      connectionBar.leadingAnchor.constraint(equalTo: root.leadingAnchor),
      connectionBar.trailingAnchor.constraint(equalTo: root.trailingAnchor),
      connectionBar.heightAnchor.constraint(equalToConstant: 36),

      progressIndicator.leadingAnchor.constraint(equalTo: connectionBar.leadingAnchor, constant: 13),
      progressIndicator.centerYAnchor.constraint(equalTo: connectionBar.centerYAnchor),
      progressIndicator.widthAnchor.constraint(equalToConstant: 16),
      progressIndicator.heightAnchor.constraint(equalToConstant: 16),

      statusLabel.leadingAnchor.constraint(equalTo: progressIndicator.trailingAnchor, constant: 8),
      statusLabel.centerYAnchor.constraint(equalTo: connectionBar.centerYAnchor),
      statusLabel.trailingAnchor.constraint(lessThanOrEqualTo: reloadButton.leadingAnchor, constant: -8),

      reloadButton.trailingAnchor.constraint(equalTo: connectionBar.trailingAnchor, constant: -12),
      reloadButton.centerYAnchor.constraint(equalTo: connectionBar.centerYAnchor),
      reloadButton.widthAnchor.constraint(equalToConstant: 24),
      reloadButton.heightAnchor.constraint(equalToConstant: 24),

      webView.topAnchor.constraint(equalTo: connectionBar.bottomAnchor),
      webView.leadingAnchor.constraint(equalTo: root.leadingAnchor),
      webView.trailingAnchor.constraint(equalTo: root.trailingAnchor),
      webView.bottomAnchor.constraint(equalTo: root.bottomAnchor),
    ])

    view = root
  }

  override func viewDidAppear() {
    super.viewDidAppear()
    guard currentEndpoint == nil else { return }
    resolveAndLoad()
    monitorTimer = Timer.scheduledTimer(
      timeInterval: 20,
      target: self,
      selector: #selector(checkConnection),
      userInfo: nil,
      repeats: true
    )
  }

  deinit {
    monitorTimer?.invalidate()
  }

  @objc func reloadDashboard() {
    resolveAndLoad(forceReload: true)
  }

  @objc private func checkConnection() {
    resolveAndLoad()
  }

  private func resolveAndLoad(forceReload: Bool = false) {
    guard !isResolving else { return }
    guard !configuration.endpoints.isEmpty else {
      showOffline("PiRouter configuration is missing. Reinstall the app to pair it.")
      return
    }

    isResolving = true
    progressIndicator.startAnimation(nil)
    if currentEndpoint == nil {
      statusLabel.stringValue = "Finding PiRouter on the local network and tailnet…"
    }

    probe(configuration.endpoints, at: 0) { [weak self] endpoint in
      guard let self else { return }
      self.isResolving = false
      self.progressIndicator.stopAnimation(nil)

      guard let endpoint else {
        self.showOffline("PiRouter is unavailable. Check the network or Tailscale, then retry.")
        return
      }

      self.statusLabel.textColor = .secondaryLabelColor
      self.statusLabel.stringValue = endpoint.statusText
      let shouldLoad = forceReload || endpoint != self.currentEndpoint || self.webView.url == nil
      self.currentEndpoint = endpoint
      self.view.window?.title = "PiRouter Exporter — \(self.connectionName(for: endpoint))"
      if shouldLoad {
        self.webView.load(URLRequest(
          url: endpoint.baseURL,
          cachePolicy: .reloadRevalidatingCacheData,
          timeoutInterval: 15
        ))
      }
    }
  }

  private func probe(
    _ endpoints: [Endpoint],
    at index: Int,
    completion: @escaping (Endpoint?) -> Void
  ) {
    guard index < endpoints.count else {
      DispatchQueue.main.async { completion(nil) }
      return
    }

    let endpoint = endpoints[index]
    var request = URLRequest(
      url: endpoint.baseURL,
      cachePolicy: .reloadIgnoringLocalAndRemoteCacheData,
      timeoutInterval: endpoint.kind == .local ? 1.5 : 5
    )
    request.httpMethod = "GET"
    request.setValue("text/html", forHTTPHeaderField: "Accept")

    URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
      guard self != nil else { return }
      let code = (response as? HTTPURLResponse)?.statusCode ?? 0
      if (200..<500).contains(code) {
        DispatchQueue.main.async { completion(endpoint) }
      } else {
        self?.probe(endpoints, at: index + 1, completion: completion)
      }
    }.resume()
  }

  private func showOffline(_ message: String) {
    statusLabel.textColor = .systemOrange
    statusLabel.stringValue = message
    view.window?.title = "PiRouter Exporter — Disconnected"
  }

  private func connectionName(for endpoint: Endpoint) -> String {
    switch endpoint.kind {
    case .local: return "Local"
    case .tailscale: return "Tailscale"
    case .remote: return "Remote"
    }
  }

  func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
    progressIndicator.startAnimation(nil)
  }

  func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
    progressIndicator.stopAnimation(nil)
    if let endpoint = currentEndpoint {
      statusLabel.textColor = .secondaryLabelColor
      statusLabel.stringValue = endpoint.statusText
    }
  }

  func webView(
    _ webView: WKWebView,
    didFailProvisionalNavigation navigation: WKNavigation!,
    withError error: Error
  ) {
    progressIndicator.stopAnimation(nil)
    currentEndpoint = nil
    showOffline("The dashboard connection was interrupted; reconnecting…")
    DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
      self?.resolveAndLoad(forceReload: true)
    }
  }
}

private final class AppDelegate: NSObject, NSApplicationDelegate {
  private var window: NSWindow?
  private var dashboardController: DashboardViewController?

  func applicationDidFinishLaunching(_ notification: Notification) {
    let controller = DashboardViewController()
    let window = NSWindow(
      contentRect: NSRect(x: 0, y: 0, width: 1180, height: 780),
      styleMask: [.titled, .closable, .miniaturizable, .resizable],
      backing: .buffered,
      defer: false
    )
    window.title = "PiRouter Exporter"
    window.minSize = NSSize(width: 800, height: 560)
    window.contentViewController = controller
    window.center()
    window.setFrameAutosaveName("PiRouterExporterWindow")
    window.tabbingMode = .disallowed
    window.makeKeyAndOrderFront(nil)

    dashboardController = controller
    self.window = window
    installMenus(for: controller)
    kickstartBackgroundExporter()
    NSApp.activate(ignoringOtherApps: true)
  }

  func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
    true
  }

  func applicationShouldHandleReopen(
    _ sender: NSApplication,
    hasVisibleWindows flag: Bool
  ) -> Bool {
    window?.makeKeyAndOrderFront(nil)
    return true
  }

  private func kickstartBackgroundExporter() {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
    process.arguments = [
      "kickstart",
      "gui/\(getuid())/com.pirouter.portable-export",
    ]
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    try? process.run()
  }

  private func installMenus(for controller: DashboardViewController) {
    let mainMenu = NSMenu()
    let appMenuItem = NSMenuItem()
    let viewMenuItem = NSMenuItem()
    mainMenu.addItem(appMenuItem)
    mainMenu.addItem(viewMenuItem)

    let appMenu = NSMenu()
    appMenu.addItem(withTitle: "About PiRouter Exporter", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
    appMenu.addItem(.separator())
    appMenu.addItem(withTitle: "Quit PiRouter Exporter", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
    appMenuItem.submenu = appMenu

    let viewMenu = NSMenu(title: "View")
    let reloadItem = NSMenuItem(title: "Reconnect and Reload", action: #selector(DashboardViewController.reloadDashboard), keyEquivalent: "r")
    reloadItem.target = controller
    viewMenu.addItem(reloadItem)
    viewMenu.addItem(.separator())
    viewMenu.addItem(withTitle: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
      .keyEquivalentModifierMask = [.control, .command]
    viewMenuItem.submenu = viewMenu

    NSApp.mainMenu = mainMenu
  }
}

if CommandLine.arguments.contains("--background") {
  exit(runExporterAndWait())
}

if CommandLine.arguments.contains("--verify-volumes") {
  exit(runVerifierAndWait())
}

let application = NSApplication.shared
private let delegate = AppDelegate()
application.setActivationPolicy(.regular)
application.delegate = delegate
application.run()
