import QtQuick
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "dev.paddock.status"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "L"
    slotSize: Style.bar.statusSlot
    fontSize: Style.font.caption
    tooltipText: "Paddock status"
    onPressed: root.bar.run("omarchy-launch-floating-terminal-with-presentation paddock status")
  }
}
