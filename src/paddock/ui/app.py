"""Paddock's native GTK 4 and Libadwaita application shell."""

from __future__ import annotations

import sys

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from paddock import __version__
from paddock.application import (
    DashboardOperationResult,
    DashboardSnapshot,
    FieldError,
    LogResult,
    OperationResult,
    PaddockController,
    RedisConfigCandidate,
    RedisSnapshot,
)
from paddock.paths import Paths
from paddock.state import StateStore

from . import APPLICATION_ID
from .components import PaddockHero, PaddockSection, PaddockServiceRow
from .redis_form import (
    describe_apply_failure,
    describe_lifecycle_failure,
    describe_plan,
    describe_remove_failure,
)
from .redis_view import present_redis
from .tasks import AsyncOperationRunner
from .theme_gtk import OmarchyThemeAdapter


def dispatch(callback):
    return GLib.idle_add(lambda: (callback(), GLib.SOURCE_REMOVE)[1])


class PaddockWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, controller: PaddockController):
        super().__init__(application=application, title="Paddock")
        self.set_default_size(920, 650)
        self.controller = controller
        self.tasks = AsyncOperationRunner(dispatch)
        self.redis_snapshot: RedisSnapshot | None = None
        self.dashboard_snapshot: DashboardSnapshot | None = None
        self.mutation_busy = False
        self.redis_transitioning = False
        self.refresh_source = 0
        self.theme = OmarchyThemeAdapter(self.get_display())
        self.theme.start()

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.add_css_class("paddock-shell")
        self.set_content(self.toast_overlay)
        self.split_view = Adw.NavigationSplitView()
        self.toast_overlay.set_child(self.split_view)

        self.stack = Adw.ViewStack()
        self.dashboard_page = self._build_dashboard()
        self.services_page = self._build_services()
        self.stack.add_titled(self.dashboard_page, "dashboard", "Dashboard")
        self.stack.add_titled(self.services_page, "services", "Services")

        self.split_view.set_sidebar(
            Adw.NavigationPage(child=self._build_sidebar(), title="Paddock")
        )
        self.split_view.set_content(
            Adw.NavigationPage(child=self._build_content(), title="Paddock")
        )
        self.connect("close-request", self._closed)
        self.connect("notify::is-active", self._window_activity_changed)
        self.refresh()
        self.refresh_source = GLib.timeout_add_seconds(5, self._periodic_refresh)

    def _build_sidebar(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Adw.HeaderBar(title_widget=Gtk.Label(label="Paddock")))
        navigation = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        navigation.add_css_class("navigation-sidebar")
        for name, title, icon in (
            ("dashboard", "Dashboard", "view-grid-symbolic"),
            ("services", "Services", "network-server-symbolic"),
        ):
            row = Adw.ActionRow(title=title)
            row.set_name(name)
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            navigation.append(row)
        navigation.connect("row-selected", self._navigate)
        navigation.select_row(navigation.get_row_at_index(0))
        box.append(navigation)
        return box

    def _build_content(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        self.operation_spinner = Gtk.Spinner()
        self.operation_spinner.set_tooltip_text("A Paddock operation is running")
        self.operation_spinner.set_visible(False)
        header.pack_end(self.operation_spinner)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Refresh")
        refresh.set_action_name("app.refresh")
        header.pack_end(refresh)
        box.append(header)
        self.stack.set_vexpand(True)
        box.append(self.stack)
        return box

    def _build_dashboard(self) -> Gtk.Widget:
        page = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=600)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.add_css_class("paddock-page")
        clamp.set_child(content)
        page.set_child(clamp)

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        heading.set_valign(Gtk.Align.CENTER)
        title = Gtk.Label(label="Dashboard", xalign=0)
        title.set_hexpand(True)
        title.add_css_class("paddock-dashboard-heading")
        heading.append(title)
        self.dashboard_toggle = Gtk.Button(label="Start All")
        self.dashboard_toggle.add_css_class("suggested-action")
        self.dashboard_toggle.set_tooltip_text("Start every configured Paddock service")
        self.dashboard_toggle.connect("clicked", self._toggle_dashboard_services)
        heading.append(self.dashboard_toggle)
        content.append(heading)

        self.dashboard_infrastructure = PaddockSection("Infrastructure")
        self.dashboard_php = PaddockSection("PHP runtimes")
        self.dashboard_services = PaddockSection("Services")
        content.append(self.dashboard_infrastructure)
        content.append(self.dashboard_php)
        content.append(self.dashboard_services)
        return page

    def _build_services(self) -> Gtk.Widget:
        self.services_view = Gtk.Stack()
        self.services_view.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.pending_service_status: dict[str, Adw.StatusPage] = {}
        self.services_view.add_named(self._build_service_catalog(), "catalog")
        self.services_view.add_named(self._build_redis_detail(), "redis")
        self.services_view.add_named(
            self._build_pending_service_detail(
                "mysql", "MySQL", "relational database", "network-server-symbolic"
            ),
            "mysql",
        )
        self.services_view.add_named(
            self._build_pending_service_detail(
                "postgres",
                "PostgreSQL",
                "relational database",
                "network-server-symbolic",
            ),
            "postgres",
        )
        return self.services_view

    def _build_service_catalog(self) -> Gtk.Widget:
        page = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=600)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.add_css_class("paddock-page")
        clamp.set_child(content)
        page.set_child(clamp)

        title = Gtk.Label(label="Services", xalign=0)
        title.add_css_class("paddock-dashboard-heading")
        content.append(title)

        cache = PaddockSection("Cache")
        databases = PaddockSection("Databases")
        content.append(cache)
        content.append(databases)

        self.service_catalog_rows: dict[str, PaddockServiceRow] = {}
        self.service_toggle_buttons: dict[str, Gtk.Button] = {}
        for key, name, detail, section in (
            ("redis", "Redis", "8.10.1 · Port: 6379", cache),
            ("mysql", "MySQL", "8.4.11 · Port: 3306", databases),
            ("postgres", "PostgreSQL", "17.11 · Port: 5432", databases),
        ):
            row = PaddockServiceRow(
                name, detail, "not-configured", show_detail=True
            )
            row.set_activatable(True)
            settings = Gtk.Button(label="Settings")
            settings.set_valign(Gtk.Align.CENTER)
            settings.connect("clicked", self._open_service_settings, key)
            toggle = Gtk.Button(label="Start")
            toggle.set_valign(Gtk.Align.CENTER)
            toggle.add_css_class("suggested-action")
            toggle.connect("clicked", self._toggle_service, key)
            row.add_suffix(settings)
            row.add_suffix(toggle)
            row.connect("activated", self._show_service_info, key)
            section.add(row)
            self.service_catalog_rows[key] = row
            self.service_toggle_buttons[key] = toggle

        self.services_split = Adw.OverlaySplitView()
        self.services_split.set_sidebar_position(Gtk.PackType.END)
        self.services_split.set_min_sidebar_width(280)
        self.services_split.set_max_sidebar_width(380)
        self.services_split.set_content(page)
        self.services_split.set_sidebar(self._build_service_info_sidebar())
        self.services_split.set_show_sidebar(False)
        return self.services_split

    def _build_service_info_sidebar(self) -> Gtk.Widget:
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        self.service_info_title = Gtk.Label(label="Service")
        header.set_title_widget(self.service_info_title)
        close = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close.set_tooltip_text("Close service information")
        close.connect("clicked", lambda _button: self.services_split.set_show_sidebar(False))
        header.pack_end(close)
        sidebar.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.add_css_class("paddock-page")
        section = PaddockSection("Laravel .env")
        environment = Gtk.ListBoxRow(selectable=False, activatable=False)
        self.service_info_env = Gtk.Label(xalign=0, selectable=True)
        self.service_info_env.set_wrap(True)
        self.service_info_env.add_css_class("paddock-env-block")
        environment.set_child(self.service_info_env)
        section.add(environment)
        content.append(section)
        sidebar.append(content)
        return sidebar

    def _build_pending_service_detail(
        self, key: str, title: str, kind: str, icon_name: str
    ) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(self._service_detail_header(title))
        status = Adw.StatusPage(
            title=title,
            description=(
                f"Paddock can run this {kind}, but native configuration controls "
                "will be added in the next service-management slice."
            ),
            icon_name=icon_name,
        )
        status.set_vexpand(True)
        page.append(status)
        self.pending_service_status[key] = status
        return page

    def _service_detail_header(self, title: str) -> Gtk.Widget:
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label=title))
        back = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        back.set_tooltip_text("Back to all services")
        back.connect("clicked", self._show_service_catalog)
        header.pack_start(back)
        return header

    def _open_service_settings(self, _button, key: str) -> None:
        self.services_view.set_visible_child_name(key)

    def _show_service_info(self, _row, key: str) -> None:
        self.selected_service_key = key
        if self.dashboard_snapshot is not None:
            service = next(
                (
                    service
                    for service in self.dashboard_snapshot.services
                    if service.key == key
                ),
                None,
            )
            if service is not None:
                self._update_service_info(service)
        self.services_split.set_show_sidebar(True)

    def _update_service_info(self, service) -> None:
        self.service_info_title.set_label(service.title)
        self.service_info_env.set_label("\n".join(service.connection))

    def _toggle_service(self, _button, key: str) -> None:
        if self.mutation_busy or self.dashboard_snapshot is None:
            return
        service = next(
            (service for service in self.dashboard_snapshot.services if service.key == key),
            None,
        )
        if service is None:
            return
        active = not service.active
        self.operation_spinner.set_tooltip_text(
            f"{'Starting' if active else 'Stopping'} {service.title}"
        )
        self._set_mutation_busy(True)
        self.tasks.submit(
            f"service-mutation-{key}",
            lambda: self.controller.set_service_active(key, active),
            self._dashboard_operation_finished,
            self._dashboard_operation_failed,
        )

    def _show_service_catalog(self, _button=None) -> None:
        self.services_view.set_visible_child_name("catalog")

    def _build_redis_detail(self) -> Gtk.Widget:
        self.redis_view = Gtk.Stack()
        self.redis_view.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        redis_empty_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        redis_empty_page.append(self._service_detail_header("Redis"))
        self.redis_empty = Adw.StatusPage(
            title="Redis",
            description="Loading Redis status…",
            icon_name="network-server-symbolic",
        )
        self.redis_add = Gtk.Button(label="Add Redis")
        self.redis_add.add_css_class("suggested-action")
        self.redis_add.connect("clicked", self._open_redis_editor)
        self.redis_empty.set_child(self.redis_add)
        self.redis_empty.set_vexpand(True)
        redis_empty_page.append(self.redis_empty)
        self.redis_view.add_named(redis_empty_page, "status")

        page = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=600)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.add_css_class("paddock-page")
        clamp.set_child(content)
        page.set_child(clamp)

        content.append(self._service_detail_header("Redis"))
        self.redis_hero = PaddockHero(
            "content-loading-symbolic", "Redis", "Shared service · loopback only"
        )
        content.append(self.redis_hero)

        hero_actions = self.redis_hero.actions
        self.redis_start = Gtk.Button(label="Start")
        self.redis_start.add_css_class("suggested-action")
        self.redis_start.set_tooltip_text("Start Redis and enable it at boot")
        self.redis_start.connect("clicked", lambda _button: self._run_redis_action("start"))
        self.redis_stop = Gtk.Button(label="Stop")
        self.redis_stop.set_tooltip_text("Stop Redis temporarily without disabling it")
        self.redis_stop.connect("clicked", lambda _button: self._run_redis_action("stop"))
        self.redis_restart = Gtk.Button(label="Restart")
        self.redis_restart.set_tooltip_text("Restart Redis and wait until it is ready")
        self.redis_restart.connect(
            "clicked", lambda _button: self._run_redis_action("restart")
        )
        for button in (self.redis_start, self.redis_stop, self.redis_restart):
            hero_actions.append(button)

        details = PaddockSection("Configuration")
        self.redis_address_row = Adw.ActionRow(title="Address")
        self.redis_container_port_row = Adw.ActionRow(title="Container port")
        self.redis_image_row = Adw.ActionRow(title="Container image")
        self.redis_volume_row = Adw.ActionRow(title="Data volume")
        self.redis_enabled_row = Adw.ActionRow(title="Starts automatically")
        self.redis_linger_row = Adw.ActionRow(title="Available after logout")
        for row in (
            self.redis_address_row,
            self.redis_container_port_row,
            self.redis_image_row,
            self.redis_volume_row,
            self.redis_enabled_row,
            self.redis_linger_row,
        ):
            row.set_subtitle_selectable(True)
            details.add(row)
        content.append(details)

        actions = PaddockSection("Manage")
        configuration = Adw.ActionRow(
            title="Redis configuration",
            subtitle="Change the pinned container image or loopback host port.",
        )
        self.redis_configure = Gtk.Button(label="Configure")
        self.redis_configure.connect("clicked", self._open_redis_editor)
        configuration.add_suffix(self.redis_configure)
        actions.add(configuration)

        environment = Adw.ActionRow(
            title="Laravel environment",
            subtitle="Copy REDIS_HOST and REDIS_PORT",
        )
        copy = Gtk.Button(label="Copy")
        copy.set_tooltip_text("Copy Laravel Redis environment variables")
        copy.connect("clicked", self._copy_redis_environment)
        environment.add_suffix(copy)
        actions.add(environment)

        logs = Adw.ActionRow(
            title="Redis logs",
            subtitle="View the latest 200 journal lines as plain text.",
        )
        self.redis_logs_button = Gtk.Button(label="Open Logs")
        self.redis_logs_button.connect("clicked", self._load_redis_logs)
        logs.add_suffix(self.redis_logs_button)
        actions.add(logs)
        content.append(actions)

        danger = PaddockSection("Danger zone", danger=True)
        removal = Adw.ActionRow(
            title="Remove Redis",
            subtitle="Remove the service while preserving its data, or explicitly delete both.",
        )
        removal_buttons = Gtk.Box(spacing=6)
        self.redis_remove = Gtk.Button(label="Remove")
        self.redis_remove.connect("clicked", self._confirm_redis_removal)
        self.redis_delete = Gtk.Button(label="Delete Data…")
        self.redis_delete.add_css_class("destructive-action")
        self.redis_delete.connect("clicked", self._confirm_redis_data_deletion)
        removal_buttons.append(self.redis_remove)
        removal_buttons.append(self.redis_delete)
        removal.add_suffix(removal_buttons)
        danger.add(removal)
        content.append(danger)

        self.redis_view.add_named(page, "configured")
        return self.redis_view

    def _navigate(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is not None:
            if row.get_name() == "services":
                self._show_service_catalog()
            self.stack.set_visible_child_name(row.get_name())
            self.split_view.set_show_content(True)

    def refresh(self) -> None:
        self.tasks.submit(
            "dashboard-snapshot",
            self.controller.dashboard_snapshot,
            self._show_dashboard,
            lambda error: self.show_error("Dashboard refresh failed", str(error)),
        )
        self.tasks.submit(
            "snapshot",
            self.controller.redis_snapshot,
            self._show_snapshot,
            lambda error: self.show_error("Status refresh failed", str(error)),
        )

    def _show_dashboard(self, snapshot: DashboardSnapshot) -> None:
        self.dashboard_snapshot = snapshot
        sections = {
            "Infrastructure": self.dashboard_infrastructure,
            "PHP": self.dashboard_php,
            "Services": self.dashboard_services,
        }
        for section in sections.values():
            section.clear()
        for service in snapshot.services:
            sections[service.group].add(
                PaddockServiceRow(
                    service.title, service.detail, service.state
                )
            )
            catalog_row = self.service_catalog_rows.get(service.key)
            if catalog_row is not None:
                catalog_row.set_state(service.state)
                catalog_row.set_subtitle(service.detail)
            toggle = self.service_toggle_buttons.get(service.key)
            if toggle is not None:
                toggle.set_label("Stop" if service.active else "Start")
                if service.active:
                    toggle.remove_css_class("suggested-action")
                else:
                    toggle.add_css_class("suggested-action")
            if getattr(self, "selected_service_key", None) == service.key:
                self._update_service_info(service)
            pending_status = self.pending_service_status.get(service.key)
            if pending_status is not None:
                state = "Running" if service.active else "Not configured"
                if service.configured and not service.active:
                    state = service.state.replace("-", " ").title()
                pending_status.set_description(
                    f"{state} · {service.detail}. Native configuration controls "
                    "will be added in the next service-management slice."
                )
        if not any(service.group == "PHP" for service in snapshot.services):
            self.dashboard_php.add(
                Adw.ActionRow(
                    title="No PHP runtimes installed",
                    subtitle="Install a PHP runtime to make it available here.",
                )
            )
        if snapshot.all_active:
            self.dashboard_toggle.set_label("Stop All")
            self.dashboard_toggle.remove_css_class("suggested-action")
            self.dashboard_toggle.set_tooltip_text(
                "Temporarily stop every configured Paddock service"
            )
        else:
            self.dashboard_toggle.set_label("Start All")
            self.dashboard_toggle.add_css_class("suggested-action")
            self.dashboard_toggle.set_tooltip_text(
                "Start every configured Paddock service"
            )

    def _toggle_dashboard_services(self, _button) -> None:
        if self.mutation_busy or self.dashboard_snapshot is None:
            return
        active = not self.dashboard_snapshot.all_active
        self.operation_spinner.set_tooltip_text(
            "Starting all configured services" if active else "Stopping all services"
        )
        self._set_mutation_busy(True)
        self.tasks.submit(
            "dashboard-mutation",
            lambda: self.controller.set_dashboard_active(active),
            self._dashboard_operation_finished,
            self._dashboard_operation_failed,
        )

    def _dashboard_operation_finished(self, result: DashboardOperationResult) -> None:
        self._set_mutation_busy(False)
        self._show_dashboard(result.snapshot)
        if result.ok:
            self.show_toast(result.summary)
        else:
            self.show_error(result.summary, result.detail or "Unknown lifecycle error")
        self.refresh()

    def _dashboard_operation_failed(self, error: BaseException) -> None:
        self._set_mutation_busy(False)
        self.show_error("Dashboard operation failed", str(error))
        self.refresh()

    def _periodic_refresh(self) -> bool:
        if not self.mutation_busy:
            self.refresh()
        return GLib.SOURCE_CONTINUE

    def _window_activity_changed(self, window, _property) -> None:
        if window.is_active() and not self.mutation_busy:
            self.refresh()

    def _show_snapshot(self, snapshot: RedisSnapshot) -> None:
        self.redis_snapshot = snapshot
        presentation = present_redis(snapshot)
        if presentation.view != "configured":
            self.redis_transitioning = False
            self.redis_empty.set_title(presentation.title)
            self.redis_empty.set_description(presentation.description)
            self.redis_empty.set_icon_name(presentation.icon_name)
            self.redis_add.set_visible(presentation.view == "unconfigured")
            self.redis_view.set_visible_child_name("status")
            return

        self.redis_view.set_visible_child_name("configured")
        self.redis_hero.set_presentation(
            presentation.title,
            presentation.description,
            presentation.icon_name,
            presentation.tone,
        )
        self.redis_hero.meta.set_label(
            f"SHARED SERVICE · {snapshot.address} · LOOPBACK ONLY"
        )
        self.redis_address_row.set_subtitle(snapshot.address or "Unavailable")
        self.redis_container_port_row.set_subtitle(str(snapshot.container_port))
        self.redis_image_row.set_subtitle(snapshot.image or "Unavailable")
        self.redis_volume_row.set_subtitle(snapshot.volume or "Unavailable")
        self.redis_enabled_row.set_subtitle(
            "Yes" if snapshot.enabled_state == "enabled" else snapshot.enabled_state.title()
        )
        self.redis_linger_row.set_subtitle("Yes" if snapshot.lingering else "No")
        self.redis_start.set_visible(presentation.can_start)
        self.redis_stop.set_visible(presentation.can_stop)
        self.redis_restart.set_visible(presentation.can_restart)
        self.redis_transitioning = presentation.transitioning
        self._update_action_sensitivity()

    def _run_redis_action(self, action: str) -> None:
        if self.mutation_busy or self.redis_transitioning:
            return
        progress = {
            "start": "Starting Redis and checking readiness",
            "stop": "Stopping Redis",
            "restart": "Restarting Redis and checking readiness",
        }
        self.operation_spinner.set_tooltip_text(progress[action])
        self._set_mutation_busy(True)
        operation = getattr(self.controller, f"{action}_redis")
        self.tasks.submit(
            "redis-mutation",
            operation,
            self._operation_finished,
            self._operation_failed,
        )

    def _operation_finished(self, result: OperationResult) -> None:
        self._set_mutation_busy(False)
        self._show_snapshot(result.snapshot)
        if result.ok:
            self.show_toast(result.summary)
        else:
            self.show_error(
                result.summary,
                describe_lifecycle_failure(result.code, result.detail),
            )

    def _operation_failed(self, error: BaseException) -> None:
        self._set_mutation_busy(False)
        self.show_error("Redis operation failed", str(error))
        self.refresh()

    def _set_mutation_busy(self, busy: bool) -> None:
        self.mutation_busy = busy
        self.operation_spinner.set_visible(busy)
        if busy:
            self.operation_spinner.start()
        else:
            self.operation_spinner.stop()
        self._update_action_sensitivity()

    def _update_action_sensitivity(self) -> None:
        sensitive = not (self.mutation_busy or self.redis_transitioning)
        for button in (self.redis_start, self.redis_stop, self.redis_restart):
            button.set_sensitive(sensitive)
        self.redis_add.set_sensitive(sensitive)
        self.redis_configure.set_sensitive(sensitive)
        self.dashboard_toggle.set_sensitive(sensitive)
        for button in self.service_toggle_buttons.values():
            button.set_sensitive(sensitive)
        for button in (
            self.redis_logs_button,
            self.redis_remove,
            self.redis_delete,
        ):
            button.set_sensitive(sensitive)

    def _open_redis_editor(
        self,
        _button=None,
        candidate: RedisConfigCandidate | None = None,
        errors: tuple[FieldError, ...] = (),
    ) -> None:
        if self.mutation_busy or self.redis_transitioning:
            return
        defaults = self.controller.redis_candidate_defaults()
        current = candidate or RedisConfigCandidate(
            image=(self.redis_snapshot.image if self.redis_snapshot else None)
            or defaults.image,
            port=(self.redis_snapshot.port if self.redis_snapshot else None)
            or defaults.port,
        )

        configured = bool(self.redis_snapshot and self.redis_snapshot.configured)
        dialog = Adw.AlertDialog(
            heading="Configure Redis" if configured else "Add Redis",
            body="Review the proposed machine changes before anything is applied.",
        )
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        form.set_size_request(480, -1)
        image_label = Gtk.Label(label="Container image", xalign=0)
        image_entry = Gtk.Entry(text=current.image)
        image_entry.set_activates_default(True)
        image_error = Gtk.Label(xalign=0, wrap=True)
        image_error.add_css_class("error")
        port_label = Gtk.Label(label="Loopback host port", xalign=0)
        port_entry = Gtk.Entry(
            text=str(current.port), input_purpose=Gtk.InputPurpose.DIGITS
        )
        port_entry.set_activates_default(True)
        port_error = Gtk.Label(xalign=0, wrap=True)
        port_error.add_css_class("error")
        safety = Gtk.Label(
            label=(
                "Redis remains bound to 127.0.0.1. Its container port is fixed at "
                "6379, and the existing data volume is preserved."
            ),
            xalign=0,
            wrap=True,
        )
        safety.add_css_class("dim-label")
        for widget in (
            image_label,
            image_entry,
            image_error,
            port_label,
            port_entry,
            port_error,
            safety,
        ):
            form.append(widget)

        for error in errors:
            label = image_error if error.field == "image" else port_error
            label.set_label(error.message)
        dialog.set_extra_child(form)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("preview", "Preview Changes")
        dialog.set_default_response("preview")
        dialog.set_response_appearance("preview", Adw.ResponseAppearance.SUGGESTED)

        def response(_dialog, response_name: str) -> None:
            if response_name != "preview":
                return
            port_text = port_entry.get_text().strip()
            if not port_text.isdecimal():
                self._open_redis_editor(
                    candidate=RedisConfigCandidate(image_entry.get_text(), 0),
                    errors=(
                        FieldError(
                            "port", "invalid_port", "The port must be an integer."
                        ),
                    ),
                )
                return
            proposed = RedisConfigCandidate(image_entry.get_text(), int(port_text))
            self._set_mutation_busy(True)
            self.tasks.submit(
                "redis-plan",
                lambda: self.controller.plan_redis(proposed),
                lambda plan: self._redis_plan_ready(proposed, plan),
                self._operation_failed,
            )

        dialog.connect("response", response)
        dialog.present(self)

    def _redis_plan_ready(self, candidate, plan) -> None:
        self._set_mutation_busy(False)
        if not plan.valid:
            self._open_redis_editor(candidate=candidate, errors=plan.errors)
            return
        if not plan.changed:
            self.show_toast("Redis is already configured this way")
            return

        preview = Adw.AlertDialog(
            heading="Apply Redis changes?",
            body="Nothing has changed yet. Applying will perform these actions.",
        )
        lines = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        lines.set_size_request(480, -1)
        for text in describe_plan(plan):
            line = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
            lines.append(line)
        preview.set_extra_child(lines)
        preview.add_response("back", "Back")
        preview.add_response("apply", "Apply")
        preview.set_default_response("apply")
        preview.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)

        def response(_dialog, response_name: str) -> None:
            if response_name == "back":
                self._open_redis_editor(candidate=candidate)
            elif response_name == "apply":
                self._set_mutation_busy(True)
                self.tasks.submit(
                    "redis-mutation",
                    lambda: self.controller.apply_redis(candidate),
                    self._redis_apply_finished,
                    self._operation_failed,
                )

        preview.connect("response", response)
        preview.present(self)

    def _redis_apply_finished(self, result: OperationResult) -> None:
        self._set_mutation_busy(False)
        self._show_snapshot(result.snapshot)
        if result.ok:
            self.show_toast(result.summary)
            return
        self.show_error(
            result.summary, describe_apply_failure(result.code, result.detail)
        )

    def _load_redis_logs(self, _button=None) -> None:
        if self.mutation_busy:
            return
        self.operation_spinner.set_tooltip_text("Loading Redis logs")
        self._set_mutation_busy(True)
        self.tasks.submit(
            "redis-logs",
            lambda: self.controller.redis_logs(200),
            self._show_redis_logs,
            self._operation_failed,
        )

    def _show_redis_logs(self, result: LogResult) -> None:
        self._set_mutation_busy(False)
        if not result.ok:
            self.show_error("Redis logs unavailable", result.detail or result.code)
            return
        text = "\n".join(result.lines)
        if text:
            text += "\n"
        else:
            text = "No Redis log entries were found.\n"

        dialog = Adw.AlertDialog(
            heading="Redis Logs",
            body="Latest 200 journal lines. Log content is displayed as plain text.",
        )
        view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
        view.set_wrap_mode(Gtk.WrapMode.NONE)
        view.get_buffer().set_text(text)
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_width=640,
            min_content_height=360,
        )
        scroller.set_child(view)
        dialog.set_extra_child(scroller)
        dialog.add_response("close", "Close")
        dialog.add_response("refresh", "Refresh")
        dialog.add_response("copy", "Copy")
        dialog.set_default_response("close")

        def response(_dialog, response_name: str) -> None:
            if response_name == "refresh":
                self._load_redis_logs()
            elif response_name == "copy":
                self.get_clipboard().set(text)
                self.show_toast("Redis logs copied")

        dialog.connect("response", response)
        dialog.present(self)

    def _confirm_redis_removal(self, _button=None) -> None:
        if self.mutation_busy or not self.redis_snapshot:
            return
        volume = self.redis_snapshot.volume or "paddock-redis"
        dialog = Adw.AlertDialog(
            heading="Remove Redis?",
            body=(
                f"Paddock will stop and remove the Redis service. Data volume {volume} "
                "will be preserved so it can be reused later."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove Service")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

        def response(_dialog, response_name: str) -> None:
            if response_name == "remove":
                self._run_redis_removal(delete_data=False, volume=volume)

        dialog.connect("response", response)
        dialog.present(self)

    def _confirm_redis_data_deletion(self, _button=None, error: str = "") -> None:
        if self.mutation_busy or not self.redis_snapshot:
            return
        volume = self.redis_snapshot.volume or "paddock-redis"
        dialog = Adw.AlertDialog(
            heading="Delete Redis and its data?",
            body=(
                "This permanently removes the service and its recorded data volume. "
                f"Type {volume} to confirm."
            ),
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        entry = Gtk.Entry(placeholder_text=volume)
        entry.set_activates_default(True)
        error_label = Gtk.Label(label=error, xalign=0, wrap=True)
        error_label.add_css_class("error")
        box.append(entry)
        box.append(error_label)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete Service and Data")
        dialog.set_default_response("delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def response(_dialog, response_name: str) -> None:
            if response_name != "delete":
                return
            if entry.get_text() != volume:
                self._confirm_redis_data_deletion(
                    error=f"Enter the exact volume name: {volume}"
                )
                return
            self._run_redis_removal(delete_data=True, volume=volume)

        dialog.connect("response", response)
        dialog.present(self)

    def _run_redis_removal(self, *, delete_data: bool, volume: str) -> None:
        self.operation_spinner.set_tooltip_text(
            "Removing Redis and its data" if delete_data else "Removing Redis"
        )
        self._set_mutation_busy(True)
        self.tasks.submit(
            "redis-mutation",
            lambda: self.controller.remove_redis(delete_data=delete_data),
            lambda result: self._redis_removal_finished(result, volume),
            self._operation_failed,
        )

    def _redis_removal_finished(self, result: OperationResult, volume: str) -> None:
        self._set_mutation_busy(False)
        self._show_snapshot(result.snapshot)
        if result.ok:
            self.show_toast(result.summary)
            return
        self.show_error(
            result.summary,
            describe_remove_failure(result.code, result.detail, volume),
        )

    def _copy_redis_environment(self, _button) -> None:
        if self.redis_snapshot is None or not self.redis_snapshot.connection:
            return
        self.get_clipboard().set("\n".join(self.redis_snapshot.connection) + "\n")
        self.show_toast("Laravel Redis environment copied")

    def show_toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message))

    def show_error(self, heading: str, detail: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=detail)
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.present(self)

    def _closed(self, _window) -> bool:
        if self.refresh_source:
            GLib.source_remove(self.refresh_source)
            self.refresh_source = 0
        self.theme.close()
        self.tasks.close()
        return False


class PaddockApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APPLICATION_ID)
        self.window: PaddockWindow | None = None
        self.connect("activate", self._activate)
        self._add_action("quit", lambda *_: self.quit(), ["<Primary>q"])
        self._add_action("refresh", self._refresh, ["<Primary>r", "F5"])

    def _add_action(self, name: str, callback, shortcuts: list[str]) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        self.set_accels_for_action(f"app.{name}", shortcuts)

    def _activate(self, _application) -> None:
        if self.window is None:
            store = StateStore(Paths.from_environment())
            store.initialize()
            self.window = PaddockWindow(self, PaddockController(store))
        self.window.present()

    def _refresh(self, *_args) -> None:
        if self.window is not None:
            self.window.refresh()


def main(argv: list[str] | None = None) -> int:
    application = PaddockApplication()
    return application.run(sys.argv if argv is None else argv)
