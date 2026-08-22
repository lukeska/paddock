// Paddock's bar presence: one dot whose colour is the whole stack's health.
//
// Reaches its polling singleton through `bar.shell.serviceFor(...)`. Bar
// widgets are not given the `service` property directly — only panel entries
// are (shell.qml) — so this lookup is the supported route.

import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "dev.paddock.status"

  readonly property var paddock:
    bar && bar.shell && typeof bar.shell.serviceFor === "function"
      ? bar.shell.serviceFor("dev.paddock.status") : null

  readonly property string health: paddock ? paddock.health : "unknown"

  // Services receive no settings of their own, so the widget forwards the
  // user's interval to the singleton it shares with every other monitor.
  function applySettings() {
    if (paddock) paddock.intervalSeconds = Number(setting("refreshIntervalSec", 15))
  }
  onSettingsChanged: applySettings()
  onPaddockChanged: applySettings()
  Component.onCompleted: applySettings()

  function refresh() { if (paddock) paddock.refresh() }

  // ---- Panel hosting -------------------------------------------------------
  // The panel is a nested Loader rather than a `panel` kind, so it lives and
  // dies with this widget and needs no second enable step.
  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  onBarChanged: { injectPanel(); applySettings() }

  // Bar.findPanelWidget looks for opened/open/close on the *widget*, which is
  // how `omarchy-shell shell toggle dev.paddock.status` reaches the panel
  // without a second IPC handler competing with the service's.
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }

  // Forwarded so this widget can stand in for the panel as the bar's popout
  // identity: requestPopout prefers closeForPopoutSwitch over close.
  readonly property bool popoutSwitchClosing:
    panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  readonly property color statusColor: {
    if (health === "ok") return Color.accent
    if (health === "degraded" || health === "down") return Color.urgent
    return Color.muted            // unknown: not installed, or report failed
  }

  readonly property string summary: {
    if (!paddock || !paddock.loaded) return "Paddock: checking…"
    if (health === "unknown") return "Paddock unavailable\n" + paddock.error
    var parts = ["Paddock: " + health]
    parts.push(paddock.sites.length + " site" + (paddock.sites.length === 1 ? "" : "s"))
    if (paddock.runtimes.length > 0) {
      var minors = []
      for (var i = 0; i < paddock.runtimes.length; i++) minors.push(paddock.runtimes[i].minor)
      parts.push("PHP " + minors.join(", "))
    }
    for (var j = 0; j < paddock.services.length; j++) {
      parts.push(paddock.services[j].name + " " + paddock.services[j].state)
    }
    return parts.join(" · ")
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // Nerd Font "server" glyph; the colour carries the meaning.
    text: "󰆨"
    foreground: root.statusColor
    slotSize: Style.bar.statusSlot
    fontSize: Style.font.caption
    tooltipText: root.summary
    onPressed: root.togglePanel()

    // Bindings on the singletons re-theme live when omarchy-theme-set pushes
    // a new palette; no plugin code runs.
    Behavior on foreground { ColorAnimation { duration: 150 } }
  }
}
