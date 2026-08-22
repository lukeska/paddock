// The popout: everything Paddock knows, in four sections.
//
// Reads the same `service` singleton the bar widget does, so opening the panel
// costs no extra subprocess; it only asks for a refresh so what you look at is
// current.
//
// `manageIpc` is false on purpose. The Panel base would register a handler on
// this plugin's IPC target, but Service.qml already owns it, and the shell
// refuses a second handler for the same target. Summoning is routed instead
// through the bar widget's open/close/opened, which is what Bar.findPanelWidget
// looks for.

import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "dev.paddock.status"
  ipcTarget: "dev.paddock.status"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  // The bar tracks the widget in its slot, not this nested panel, so the
  // popout coordinator has to be given that identity.
  readonly property var barIdentity: hostWidget || root

  readonly property var paddock:
    bar && bar.shell && typeof bar.shell.serviceFor === "function"
      ? bar.shell.serviceFor("dev.paddock.status") : null

  readonly property bool available: paddock ? paddock.available : false
  readonly property string health: paddock ? paddock.health : "unknown"

  function refresh() { if (paddock) paddock.refresh() }

  function open() {
    root.controller.show()
    refresh()               // never show a stale panel
  }
  function openFromHotkey() { open() }
  function close() { root.controller.hide() }
  function toggle() { root.opened ? root.close() : root.open() }
  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function shellRun(command) {
    if (root.bar && typeof root.bar.run === "function") root.bar.run(command)
  }
  function quoted(value) {
    return root.bar && typeof root.bar.shellQuote === "function"
      ? root.bar.shellQuote(String(value)) : "'" + String(value) + "'"
  }
  function inTerminal(command) {
    shellRun("omarchy-launch-floating-terminal-with-presentation " + command)
  }

  readonly property color okColor: Color.accent
  readonly property color badColor: Color.urgent
  readonly property color dimColor: Qt.darker(Color.popups.text, 1.5)

  function stateColor(state) { return state === "active" ? okColor : badColor }

  KeyboardPanel {
    id: surface
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keys
    contentWidth: surface.fittedContentWidth(Style.space(420))
    contentHeight: surface.fittedContentHeight(body.implicitHeight)

    PanelKeyCatcher {
      id: keys
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }

      Flickable {
        id: scroll
        anchors.fill: parent
        contentWidth: width
        contentHeight: body.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        Column {
          id: body
          width: scroll.width
          spacing: Style.space(10)

          // ---- Heading: the rollup, in words as well as colour.
          Item {
            width: parent.width
            height: title.implicitHeight

            Text {
              id: title
              text: "Paddock"
              color: Color.popups.text
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              font.bold: true
            }
            Row {
              anchors.right: parent.right
              anchors.verticalCenter: title.verticalCenter
              spacing: Style.space(6)

              Rectangle {
                width: Style.space(8); height: width; radius: width / 2
                anchors.verticalCenter: parent.verticalCenter
                color: root.health === "ok" ? root.okColor
                     : root.health === "unknown" ? root.dimColor : root.badColor
              }
              Text {
                text: root.health
                color: root.dimColor
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
            }
          }

          // ---- Unavailable: say why, rather than showing empty sections.
          Text {
            width: parent.width
            visible: !root.available
            wrapMode: Text.WordWrap
            color: root.badColor
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            text: paddock && paddock.error !== ""
              ? "Cannot read Paddock:\n" + paddock.error
              : "Reading Paddock…"
          }

          // ---- Too old to trust: the report predates what this plugin reads.
          Text {
            width: parent.width
            visible: root.available && paddock && !paddock.schemaSupported
            wrapMode: Text.WordWrap
            color: root.badColor
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            text: paddock
              ? "Paddock is older than this widget expects (report schema "
                + paddock.reportedSchema + ", needs " + paddock.expectedSchema
                + "). Update Paddock."
              : ""
          }

          PanelSeparator { width: parent.width; visible: root.available }

          // ---- Status ------------------------------------------------------
          PanelSectionHeader { text: "STATUS"; visible: root.available }
          Flow {
            width: parent.width
            spacing: Style.space(6)
            visible: root.available

            Repeater {
              model: paddock ? paddock.units : []
              Text {
                // Unit names are long and identical up front; the tail is the
                // only part worth reading at a glance.
                text: String(modelData.name)
                        .replace("paddock-", "").replace(".service", "")
                        .replace("paddock.target", "target")
                color: modelData.ok ? root.okColor : root.badColor
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
            }
          }

          // ---- PHP ---------------------------------------------------------
          PanelSectionHeader { text: "PHP"; visible: root.available }
          Repeater {
            model: paddock ? paddock.runtimes : []
            Row {
              width: body.width
              spacing: Style.space(8)
              visible: root.available

              Text {
                text: modelData.minor
                color: Color.popups.text
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                font.bold: true
              }
              Text {
                text: modelData.release ? modelData.release : ""
                color: root.dimColor
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
              Text {
                text: paddock && paddock.defaultPhp === modelData.minor ? "default" : ""
                color: root.okColor
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
              Text {
                text: modelData.state === "active" ? "" : "fpm " + modelData.state
                color: root.badColor
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
            }
          }

          // ---- Services ----------------------------------------------------
          PanelSectionHeader { text: "SERVICES"; visible: root.available }
          Text {
            width: parent.width
            visible: root.available && paddock && paddock.services.length === 0
            text: "none configured"
            color: root.dimColor
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
          Repeater {
            model: paddock ? paddock.services : []
            Row {
              width: body.width
              spacing: Style.space(8)

              Text {
                text: modelData.name
                color: Color.popups.text
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                font.bold: true
              }
              Text {
                text: modelData.state
                color: root.stateColor(modelData.state)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
              Text {
                text: modelData.address
                color: root.dimColor
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
            }
          }
          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            visible: root.available && paddock && paddock.services.length > 0
                     && !paddock.lingering
            // Up now, gone at logout. Worth saying out loud.
            text: "Lingering is disabled — services stop at logout. Re-run paddock setup."
            color: root.badColor
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          // ---- Sites -------------------------------------------------------
          PanelSectionHeader { text: "SITES"; visible: root.available }
          Text {
            width: parent.width
            visible: root.available && paddock && paddock.sites.length === 0
            text: "none linked"
            color: root.dimColor
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
          Repeater {
            model: paddock ? paddock.sites : []
            Item {
              width: body.width
              height: siteRow.implicitHeight

              Row {
                id: siteRow
                spacing: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter

                Text {
                  text: modelData.host
                  color: Color.popups.text
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
                Text {
                  text: modelData.php
                  color: root.dimColor
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
                Text {
                  text: modelData.secured ? "TLS" : ""
                  color: root.okColor
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }

              PanelActionButton {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                iconText: "󰖟"
                tooltipText: "Open " + modelData.url
                onClicked: {
                  root.shellRun("xdg-open " + root.quoted(modelData.url))
                  root.close()
                }
              }
            }
          }

          PanelSeparator { width: parent.width; visible: root.available }

          // ---- Footer: the two things worth a terminal.
          Row {
            width: parent.width
            spacing: Style.space(8)

            Button {
              text: "doctor"
              fontSize: Style.font.caption
              foreground: Color.popups.text
              tooltipText: "Run paddock doctor in a terminal"
              onClicked: { root.inTerminal("paddock doctor"); root.close() }
            }
            Button {
              text: "logs"
              fontSize: Style.font.caption
              foreground: Color.popups.text
              tooltipText: "Follow the Paddock journal in a terminal"
              onClicked: { root.inTerminal("paddock logs --follow"); root.close() }
            }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: paddock && paddock.lastRefresh !== "" ? paddock.lastRefresh : ""
              color: root.dimColor
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }
      }
    }
  }
}
