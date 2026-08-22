"""Paddock's native GTK 4 and Libadwaita application shell."""

from __future__ import annotations

import sys

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from paddock import __version__
from paddock.application import (
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
from .components import PaddockHero, PaddockSection
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
        self.overview_page = self._build_overview()
        self.services_page = self._build_services()
        self.stack.add_titled(self.overview_page, "overview", "Overview")
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
            ("overview", "Overview", "view-grid-symbolic"),
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

    def _build_overview(self) -> Gtk.Widget:
        page = Adw.StatusPage(
            title="Paddock",
            description="Your local Laravel development environment.",
            icon_name="applications-development-symbolic",
        )
        self.overview_status = Gtk.Label(label="Loading Redis status…")
        self.overview_status.add_css_class("dim-label")
        page.set_child(self.overview_status)
        return page

    def _build_services(self) -> Gtk.Widget:
        self.redis_view = Gtk.Stack()
        self.redis_view.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        self.redis_empty = Adw.StatusPage(
            title="Services",
            description="Loading Redis status…",
            icon_name="network-server-symbolic",
        )
        self.redis_add = Gtk.Button(label="Add Redis")
        self.redis_add.add_css_class("suggested-action")
        self.redis_add.connect("clicked", self._open_redis_editor)
        self.redis_empty.set_child(self.redis_add)
        self.redis_view.add_named(self.redis_empty, "status")

        page = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=600)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.add_css_class("paddock-page")
        clamp.set_child(content)
        page.set_child(clamp)

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
            self.stack.set_visible_child_name(row.get_name())
            self.split_view.set_show_content(True)

    def refresh(self) -> None:
        self.tasks.submit(
            "snapshot",
            self.controller.redis_snapshot,
            self._show_snapshot,
            lambda error: self.show_error("Status refresh failed", str(error)),
        )

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
        self.overview_status.set_label(f"Redis: {presentation.title}")
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
