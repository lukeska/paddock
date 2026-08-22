// Headless singleton owning the one call to `paddock report`.
//
// The shell instantiates a `service` kind once, while a bar widget is
// instantiated once per monitor. Polling here rather than in the widget keeps
// the cost at one subprocess per refresh no matter how many screens exist.
//
// Everything is asynchronous. Plugins run unsandboxed inside the shell
// process, so a blocking call would freeze the bar, notifications and lock
// screen along with it.

import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root

  // Injected by the shell for any plugin root declaring them.
  property var shell: null
  property var manifest: null

  // Set by the bar widget from its inline shell.json settings, so the
  // interval stays user-tunable even though services receive no settings of
  // their own.
  property int intervalSeconds: 15

  // Last good snapshot. Deliberately retained across a failed refresh: a
  // panel showing slightly stale truth beats one that blanks whenever the
  // command hiccups.
  property var snapshot: null
  property bool loaded: false

  // "ok" | "degraded" | "down" come from the CLI. "unknown" is ours alone and
  // means the report could not be produced or parsed.
  readonly property string health: available && snapshot ? String(snapshot.health) : "unknown"
  property bool available: false
  property string error: ""
  property string lastRefresh: ""

  readonly property var units: snapshot && snapshot.units ? snapshot.units : []
  readonly property var runtimes: snapshot && snapshot.php ? snapshot.php.runtimes : []
  readonly property var services: snapshot && snapshot.services ? snapshot.services : []
  readonly property var sites: snapshot && snapshot.sites ? snapshot.sites : []
  readonly property bool lingering: snapshot ? snapshot.linger === true : false
  readonly property string defaultPhp:
    snapshot && snapshot.php && snapshot.php.default ? String(snapshot.php.default) : ""

  // The schema this plugin was written against. A newer CLI is assumed
  // compatible; an older one is reported rather than misread.
  readonly property int expectedSchema: 1
  readonly property bool schemaSupported:
    !snapshot || Number(snapshot.schema_version) >= expectedSchema

  signal updated()

  function refresh() {
    if (reportProcess.running) return
    reportProcess.running = true
  }

  function _fail(message) {
    available = false
    error = String(message)
    loaded = true
    updated()
  }

  Process {
    id: reportProcess
    command: ["paddock", "report"]
    stdout: StdioCollector { id: collector; waitForEnd: true }
    stderr: StdioCollector { id: errors; waitForEnd: true }

    onExited: function (exitCode) {
      if (exitCode !== 0) {
        // Exit 78 is Paddock's own "expected failure" code and carries a
        // single readable line on stderr; anything else is reported raw.
        var detail = String(errors.text || "").trim()
        root._fail(detail !== "" ? detail : "paddock report exited " + exitCode)
        return
      }
      try {
        var parsed = JSON.parse(String(collector.text || ""))
        if (!parsed || typeof parsed !== "object") throw new Error("not an object")
        root.snapshot = parsed
        root.available = true
        root.error = ""
        root.loaded = true
        root.lastRefresh = Qt.formatDateTime(new Date(), "HH:mm:ss")
        root.updated()
      } catch (e) {
        root._fail("unreadable report: " + e)
      }
    }

    // A missing `paddock` is the ordinary case on a machine without it
    // installed, not a crash. ADR 0008 requires the shell to stay usable.
    onStarted: root.error = ""
  }

  Timer {
    id: poll
    interval: Math.max(5, root.intervalSeconds) * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  // `omarchy-shell -q dev.paddock.status refresh` forces an update, so a
  // Paddock command can push a change instead of waiting out the interval.
  IpcHandler {
    target: "dev.paddock.status"
    function refresh(): void { root.refresh() }
    function health(): string { return root.health }
  }
}
