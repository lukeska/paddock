"""Paddock's native GTK 4 and Libadwaita application shell."""

from __future__ import annotations

import sys
from pathlib import Path

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
    LinkedSitesSnapshot,
    LinkedSitesOperationResult,
    OperationResult,
    ParkingOperationResult,
    PaddockController,
    PhpInstallResult,
    PhpVersionsSnapshot,
    NodeInstallResult,
    NodeVersionsSnapshot,
    RedisConfigCandidate,
    RedisSnapshot,
    ServiceInstanceOperationResult,
    ServiceInstancesSnapshot,
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
from .site_presenter import (
    error_clipboard_text,
    site_name_matches,
    site_worker_summary,
    terminal_command,
    zed_command,
)
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
        self.service_instances_snapshot: ServiceInstancesSnapshot | None = None
        self.linked_sites_snapshot: LinkedSitesSnapshot | None = None
        self.php_versions_snapshot: PhpVersionsSnapshot | None = None
        self.php_install_buttons: dict[str, Gtk.Button] = {}
        self.node_versions_snapshot: NodeVersionsSnapshot | None = None
        self.node_install_buttons: dict[str, Gtk.Button] = {}
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
        self.sites_page = self._build_sites()
        self.php_page = self._build_php_versions()
        self.node_page = self._build_node_versions()
        self.stack.add_titled(self.dashboard_page, "dashboard", "Dashboard")
        self.stack.add_titled(self.services_page, "services", "Services")
        self.stack.add_titled(self.sites_page, "sites", "Sites")
        self.stack.add_titled(self.php_page, "php", "PHP")
        self.stack.add_titled(self.node_page, "node", "Node.js")

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
            ("sites", "Sites", "web-browser-symbolic"),
            ("php", "PHP", "application-x-executable-symbolic"),
            ("node", "Node.js", "utilities-terminal-symbolic"),
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

    def _build_php_versions(self) -> Gtk.Widget:
        page = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=600)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.add_css_class("paddock-page")
        clamp.set_child(content)
        page.set_child(clamp)

        title = Gtk.Label(label="PHP", xalign=0)
        title.add_css_class("paddock-dashboard-heading")
        content.append(title)
        self.php_versions_section = PaddockSection("Available versions")
        content.append(self.php_versions_section)
        return page

    def _build_node_versions(self) -> Gtk.Widget:
        page = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=600)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.add_css_class("paddock-page"); clamp.set_child(content); page.set_child(clamp)
        title = Gtk.Label(label="Node.js", xalign=0); title.add_css_class("paddock-dashboard-heading"); content.append(title)
        self.node_versions_section = PaddockSection("Available LTS versions"); content.append(self.node_versions_section)
        return page

    def _build_services(self) -> Gtk.Widget:
        self.services_view = Gtk.Stack()
        self.services_view.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.services_view.add_named(self._build_service_catalog(), "catalog")
        self.services_view.add_named(self._build_instance_detail(), "detail")
        return self.services_view

    def _build_sites(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        rail = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        rail.add_css_class("paddock-sites-rail")
        self.site_search = Gtk.SearchEntry(
            placeholder_text="Search sites by name…",
            hexpand=True,
        )
        self.site_search.set_max_width_chars(48)
        self.site_search.connect("search-changed", self._site_search_changed)
        rail.append(self.site_search)
        self.parking_button = Gtk.Button(label="Parking Folders")
        self.parking_button.connect("clicked", self._open_parking_folders)
        rail.append(self.parking_button)
        page.append(rail)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(250)
        paned.set_wide_handle(True)
        paned.set_vexpand(True)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.add_css_class("paddock-page")
        left.set_size_request(220, -1)
        title = Gtk.Label(label="Sites", xalign=0)
        title.add_css_class("paddock-dashboard-heading")
        left.append(title)
        self.linked_sites_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.linked_sites_list.add_css_class("paddock-card")
        self.linked_sites_list.connect("row-selected", self._site_selected)
        left_scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        left_scroller.set_child(self.linked_sites_list)
        left_scroller.set_vexpand(True)
        left.append(left_scroller)
        paned.set_start_child(left)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        right.add_css_class("paddock-page")
        self.site_detail_stack = Gtk.Stack()
        self.site_detail_stack.set_vexpand(True)
        empty = Adw.StatusPage(
            title="Select a site",
            description="Choose a linked site to see its configuration and actions.",
            icon_name="web-browser-symbolic",
        )
        self.site_detail_stack.add_named(empty, "empty")

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.site_detail_title = Gtk.Label(label="Site", xalign=0)
        self.site_detail_title.add_css_class("paddock-dashboard-heading")
        details.append(self.site_detail_title)
        configuration = PaddockSection("Details")
        self.site_secured_row = Adw.ActionRow(title="Secured")
        self.site_secured_button = Gtk.Button(label="No")
        self.site_secured_button.set_valign(Gtk.Align.CENTER)
        self.site_secured_button.connect("clicked", self._toggle_site_secured)
        self.site_secured_row.add_suffix(self.site_secured_button)
        self.site_php_row = Adw.ActionRow(title="PHP version")
        self.site_php_button = Gtk.Button(label="Select")
        self.site_php_button.set_valign(Gtk.Align.CENTER)
        self.site_php_button.connect("clicked", self._toggle_site_php_options)
        self.site_php_row.add_suffix(self.site_php_button)
        self.site_php_options_row = Gtk.ListBoxRow(selectable=False, activatable=False)
        self.site_php_revealer = Gtk.Revealer()
        self.site_php_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.site_php_options = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4,
            margin_top=6, margin_bottom=6, margin_start=10, margin_end=10,
        )
        self.site_php_revealer.set_child(self.site_php_options)
        self.site_php_options_row.set_child(self.site_php_revealer)
        self.site_node_row = Adw.ActionRow(title="Node.js version")
        self.site_node_button = Gtk.Button(label="Select"); self.site_node_button.set_valign(Gtk.Align.CENTER)
        self.site_node_button.connect("clicked", self._toggle_site_node_options); self.site_node_row.add_suffix(self.site_node_button)
        self.site_node_options_row = Gtk.ListBoxRow(selectable=False, activatable=False)
        self.site_node_revealer = Gtk.Revealer(); self.site_node_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.site_node_options = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6, margin_bottom=6, margin_start=10, margin_end=10)
        self.site_node_revealer.set_child(self.site_node_options); self.site_node_options_row.set_child(self.site_node_revealer)
        self.site_reverb_row = Adw.ActionRow(title="Reverb")
        self.site_reverb_toggle = Gtk.Button(label="Start")
        self.site_reverb_toggle.set_valign(Gtk.Align.CENTER)
        self.site_reverb_toggle.connect("clicked", self._toggle_site_reverb)
        self.site_reverb_logs = Gtk.Button(label="Logs")
        self.site_reverb_logs.set_valign(Gtk.Align.CENTER)
        self.site_reverb_logs.connect("clicked", self._open_site_reverb_logs)
        self.site_reverb_autostart = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        self.site_reverb_autostart.set_tooltip_text("Start Reverb automatically")
        self.site_reverb_autostart.connect("toggled", self._toggle_site_reverb_autostart)
        self.site_reverb_row.add_suffix(self.site_reverb_autostart)
        self.site_reverb_row.add_suffix(self.site_reverb_logs)
        self.site_reverb_row.add_suffix(self.site_reverb_toggle)
        self.site_queue_row = Adw.ActionRow(title="Queue")
        self.site_queue_toggle = Gtk.Button(label="Start")
        self.site_queue_toggle.set_valign(Gtk.Align.CENTER)
        self.site_queue_toggle.connect("clicked", self._toggle_site_queue)
        self.site_queue_logs = Gtk.Button(label="Logs")
        self.site_queue_logs.set_valign(Gtk.Align.CENTER)
        self.site_queue_logs.connect("clicked", self._open_site_queue_logs)
        self.site_queue_autostart = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        self.site_queue_autostart.set_tooltip_text("Start the queue worker automatically")
        self.site_queue_autostart.connect("toggled", self._toggle_site_queue_autostart)
        self.site_queue_row.add_suffix(self.site_queue_autostart)
        self.site_queue_row.add_suffix(self.site_queue_logs)
        self.site_queue_row.add_suffix(self.site_queue_toggle)
        self.site_url_row = Adw.ActionRow(title="URL")
        self.site_url_link = Gtk.LinkButton(uri="http://localhost", label="Open")
        self.site_url_link.add_css_class("paddock-text-link")
        self.site_url_row.add_suffix(self.site_url_link)
        self.site_terminal_row = Adw.ActionRow(title="Terminal")
        self.site_terminal_button = Gtk.Button(label="Open")
        self.site_terminal_button.set_valign(Gtk.Align.CENTER)
        self.site_terminal_button.connect("clicked", self._open_site_terminal)
        self.site_terminal_row.add_suffix(self.site_terminal_button)
        self.site_path_row = Adw.ActionRow(title="Path")
        self.site_path_link = Gtk.LinkButton(uri="file:///", label="/")
        self.site_path_link.add_css_class("paddock-text-link")
        self.site_path_row.add_suffix(self.site_path_link)
        # Read-only: both follow from the project's own files, and changing
        # one is `paddock link --type`, not a click in a dashboard.
        self.site_type_row = Adw.ActionRow(title="Type")
        self.site_zed_row = Adw.ActionRow(title="Zed")
        self.site_zed_button = Gtk.Button(label="Open")
        self.site_zed_button.set_valign(Gtk.Align.CENTER)
        self.site_zed_button.connect("clicked", self._open_site_in_zed)
        self.site_zed_row.add_suffix(self.site_zed_button)
        for row in (
            self.site_secured_row,
            self.site_php_row,
            self.site_php_options_row,
            self.site_node_row,
            self.site_node_options_row,
            self.site_type_row,
            self.site_url_row,
            self.site_terminal_row,
            self.site_path_row,
            self.site_zed_row,
        ):
            if isinstance(row, Adw.ActionRow):
                row.set_subtitle_selectable(True)
            configuration.add(row)
        details.append(configuration)

        self.site_nginx_section = PaddockSection("Nginx")
        self.site_config_row = Adw.ActionRow(title="Your configuration")
        self.site_config_row.set_subtitle_selectable(True)
        self.site_config_button = Gtk.Button(label="Edit")
        self.site_config_button.set_valign(Gtk.Align.CENTER)
        self.site_config_button.connect("clicked", self._edit_site_configuration)
        self.site_config_row.add_suffix(self.site_config_button)
        self.site_project_config_row = Adw.ActionRow(title="From the project")
        self.site_project_config_row.set_subtitle_selectable(True)
        self.site_project_config_button = Gtk.Button(label="Trust")
        self.site_project_config_button.set_valign(Gtk.Align.CENTER)
        self.site_project_config_button.connect(
            "clicked", self._toggle_project_configuration
        )
        self.site_project_config_row.add_suffix(self.site_project_config_button)
        self.site_reload_row = Adw.ActionRow(
            title="Apply edits",
            subtitle="Re-check every fragment and reload nginx",
        )
        self.site_reload_button = Gtk.Button(label="Reload")
        self.site_reload_button.set_valign(Gtk.Align.CENTER)
        self.site_reload_button.connect("clicked", self._reload_web_configuration)
        self.site_reload_row.add_suffix(self.site_reload_button)
        for row in (
            self.site_config_row,
            self.site_project_config_row,
            self.site_reload_row,
        ):
            self.site_nginx_section.add(row)
        details.append(self.site_nginx_section)

        self.site_workers_section = PaddockSection("Workers")
        self.site_workers_section.add(self.site_queue_row)
        self.site_workers_section.add(self.site_reverb_row)
        details.append(self.site_workers_section)
        self.site_detail_stack.add_named(details, "details")
        right.append(self.site_detail_stack)
        paned.set_end_child(right)
        page.append(paned)
        return page

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

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        title = Gtk.Label(label="Services", xalign=0)
        title.set_hexpand(True)
        title.add_css_class("paddock-dashboard-heading")
        heading.append(title)
        self.add_service_button = Gtk.Button(label="Add Service")
        self.add_service_button.add_css_class("suggested-action")
        self.add_service_button.connect("clicked", self._open_add_service)
        heading.append(self.add_service_button)
        content.append(heading)

        self.service_cache_section = PaddockSection("Cache")
        self.service_database_section = PaddockSection("Databases")
        self.service_mail_section = PaddockSection("Mail")
        self.service_search_section = PaddockSection("Search")
        self.service_storage_section = PaddockSection("Storage")
        content.append(self.service_cache_section)
        content.append(self.service_database_section)
        content.append(self.service_mail_section)
        content.append(self.service_search_section)
        content.append(self.service_storage_section)

        self.service_catalog_rows: dict[str, PaddockServiceRow] = {}
        self.service_toggle_buttons: dict[str, Gtk.Button] = {}

        self.services_split = Adw.OverlaySplitView()
        self.services_split.set_sidebar_position(Gtk.PackType.END)
        self.services_split.set_min_sidebar_width(280)
        self.services_split.set_max_sidebar_width(380)
        self.services_split.set_content(page)
        self.services_split.set_sidebar(self._build_service_info_sidebar())
        self.services_split.set_show_sidebar(False)
        return self.services_split

    def _build_instance_detail(self) -> Gtk.Widget:
        page = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=600)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.add_css_class("paddock-page")
        clamp.set_child(content)
        page.set_child(clamp)

        header = Adw.HeaderBar()
        self.instance_detail_title = Gtk.Label(label="Service")
        header.set_title_widget(self.instance_detail_title)
        back = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        back.set_tooltip_text("Back to all services")
        back.connect("clicked", self._show_service_catalog)
        header.pack_start(back)
        content.append(header)

        settings = PaddockSection("Settings")
        name_row = Adw.ActionRow(title="Display name")
        self.instance_name_entry = Gtk.Entry(max_length=80, valign=Gtk.Align.CENTER)
        self.instance_name_entry.set_width_chars(24)
        name_row.add_suffix(self.instance_name_entry)
        settings.add(name_row)
        port_row = Adw.ActionRow(title="Port")
        self.instance_port_entry = Gtk.Entry(max_length=5, valign=Gtk.Align.CENTER)
        self.instance_port_entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.instance_port_entry.set_width_chars(8)
        port_row.add_suffix(self.instance_port_entry)
        settings.add(port_row)
        autostart_row = Adw.ActionRow(title="Start automatically with Paddock")
        self.instance_autostart_check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        autostart_row.add_suffix(self.instance_autostart_check)
        settings.add(autostart_row)
        content.append(settings)
        self.instance_save_button = Gtk.Button(label="Save")
        self.instance_save_button.set_halign(Gtk.Align.END)
        self.instance_save_button.add_css_class("suggested-action")
        self.instance_save_button.connect("clicked", self._save_instance_settings)
        content.append(self.instance_save_button)

        danger = PaddockSection("Danger zone", danger=True)
        removal = Adw.ActionRow(
            title="Remove service",
            subtitle="Permanently remove this instance and its data volume.",
        )
        self.instance_remove_button = Gtk.Button(label="Remove")
        self.instance_remove_button.add_css_class("destructive-action")
        self.instance_remove_button.connect("clicked", self._confirm_remove_instance)
        removal.add_suffix(self.instance_remove_button)
        danger.add(removal)
        content.append(danger)
        return page

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

        logs = PaddockSection("Recent logs")
        log_row = Gtk.ListBoxRow(selectable=False, activatable=False)
        self.service_log_preview = Gtk.TextView(
            editable=False, cursor_visible=False, monospace=True
        )
        self.service_log_preview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.service_log_preview.get_buffer().set_text("Select a service to load logs.")
        preview = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=150,
        )
        preview.set_child(self.service_log_preview)
        log_row.set_child(preview)
        logs.add(log_row)
        content.append(logs)
        self.service_open_logs = Gtk.Button(label="Open Logs")
        self.service_open_logs.connect("clicked", self._open_selected_service_logs)
        service_actions = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        self.service_open_dashboard = Gtk.Button(label="Open Dashboard")
        self.service_open_dashboard.connect(
            "clicked", self._open_selected_service_dashboard
        )
        self.service_open_dashboard.set_visible(False)
        service_actions.append(self.service_open_dashboard)
        service_actions.append(self.service_open_logs)
        content.append(service_actions)
        sidebar.append(content)
        return sidebar

    def _build_pending_service_detail(
        self, key: str, title: str, kind: str, icon_name: str
    ) -> Gtk.Widget:
        page = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=600)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.add_css_class("paddock-page")
        clamp.set_child(content)
        page.set_child(clamp)
        content.append(self._service_detail_header(title))
        content.append(self._build_service_settings_form(key))
        return page

    def _build_service_settings_form(self, key: str) -> Gtk.Widget:
        section = PaddockSection("Settings")
        name_row = Adw.ActionRow(title="Display name")
        name = Gtk.Entry(max_length=80, valign=Gtk.Align.CENTER)
        name.set_hexpand(False)
        name.set_width_chars(24)
        name_row.add_suffix(name)
        section.add(name_row)

        port_row = Adw.ActionRow(title="Port")
        port = Gtk.Entry(max_length=5, valign=Gtk.Align.CENTER)
        port.set_input_purpose(Gtk.InputPurpose.DIGITS)
        port.set_width_chars(8)
        port.set_valign(Gtk.Align.CENTER)
        port_row.add_suffix(port)
        section.add(port_row)

        autostart_row = Adw.ActionRow(title="Start automatically with Paddock")
        autostart = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        autostart_row.add_suffix(autostart)
        section.add(autostart_row)

        save = Gtk.Button(label="Save")
        save.set_halign(Gtk.Align.END)
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save_service_settings, key)
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        container.append(section)
        container.append(save)
        self.service_name_entries[key] = name
        self.service_port_entries[key] = port
        self.service_autostart_checks[key] = autostart
        self.service_save_buttons[key] = save
        return container

    def _service_detail_header(self, title: str) -> Gtk.Widget:
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label=title))
        back = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        back.set_tooltip_text("Back to all services")
        back.connect("clicked", self._show_service_catalog)
        header.pack_start(back)
        return header

    def _open_service_settings(self, _button, key: str) -> None:
        service = self._service_instance(key)
        if service is None:
            return
        self.selected_service_key = key
        self.instance_detail_title.set_label(service.label)
        self.instance_name_entry.set_text(service.label)
        self.instance_port_entry.set_text(str(service.port))
        self.instance_autostart_check.set_active(service.autostart)
        self.services_view.set_visible_child_name("detail")

    def _save_instance_settings(self, _button) -> None:
        if self.mutation_busy:
            return
        key = getattr(self, "selected_service_key", None)
        if key is None:
            return
        label = self.instance_name_entry.get_text()
        port_text = self.instance_port_entry.get_text().strip()
        if not port_text.isdigit():
            self.show_error("Invalid port", "Enter a numeric port between 1024 and 65535.")
            return
        port = int(port_text)
        autostart = self.instance_autostart_check.get_active()
        self.operation_spinner.set_tooltip_text(f"Saving {label.strip()} settings")
        self._set_mutation_busy(True)
        self.tasks.submit(
            f"service-instance-settings-{key}",
            lambda: self.controller.update_service_instance(
                key, label, port, autostart
            ),
            self._instance_operation_finished,
            self._instance_operation_failed,
        )

    def _show_service_info(self, _row, key: str) -> None:
        self.selected_service_key = key
        service = self._service_instance(key)
        if service is not None:
            self._update_service_info(service)
        self.services_split.set_show_sidebar(True)
        self._load_service_log_preview(key)

    def _update_service_info(self, service) -> None:
        self.service_info_title.set_label(service.label)
        self.service_info_env.set_label("\n".join(service.connection))
        self.service_open_logs.set_sensitive(not self.mutation_busy)
        self.service_open_dashboard.set_visible(service.dashboard_url is not None)
        self.service_open_dashboard.set_sensitive(not self.mutation_busy)

    def _open_selected_service_dashboard(self, _button=None) -> None:
        key = getattr(self, "selected_service_key", None)
        service = self._service_instance(key) if key else None
        if service is not None and service.dashboard_url:
            Gio.AppInfo.launch_default_for_uri(service.dashboard_url, None)

    def _load_service_log_preview(self, key: str) -> None:
        self.service_log_preview.get_buffer().set_text("Loading recent logs…")
        self.tasks.submit(
            f"service-log-preview-{key}",
            lambda: self.controller.service_instance_logs(key, 12),
            lambda result: self._show_service_log_preview(key, result),
            lambda error: self._show_service_log_preview_error(key, error),
        )

    def _show_service_log_preview(self, key: str, result: LogResult) -> None:
        if getattr(self, "selected_service_key", None) != key:
            return
        if result.ok:
            text = "\n".join(result.lines) or "No log entries were found."
        else:
            text = result.detail or "Logs are unavailable."
        self.service_log_preview.get_buffer().set_text(text)

    def _show_service_log_preview_error(self, key: str, error: BaseException) -> None:
        if getattr(self, "selected_service_key", None) == key:
            self.service_log_preview.get_buffer().set_text(str(error))

    def _open_selected_service_logs(self, _button=None) -> None:
        key = getattr(self, "selected_service_key", None)
        if key is None or self.mutation_busy:
            return
        service = self._service_instance(key)
        if service is None:
            return
        self.operation_spinner.set_tooltip_text(f"Loading {service.label} logs")
        self._set_mutation_busy(True)
        self.tasks.submit(
            f"service-logs-{key}",
            lambda: self.controller.service_instance_logs(key, 200),
            lambda result: self._show_service_logs(key, service.label, result),
            lambda error: self._service_logs_failed(service.label, error),
        )

    def _service_logs_failed(self, title: str, error: BaseException) -> None:
        self._set_mutation_busy(False)
        self.show_error(f"{title} logs unavailable", str(error))

    def _show_service_logs(self, key: str, title: str, result: LogResult) -> None:
        self._set_mutation_busy(False)
        if not result.ok:
            self.show_error(f"{title} logs unavailable", result.detail or result.code)
            return
        text = "\n".join(result.lines)
        text = f"{text}\n" if text else f"No {title} log entries were found.\n"
        dialog = Adw.AlertDialog(
            heading=f"{title} Logs",
            body="Latest 200 journal lines. Log content is displayed as plain text.",
        )
        dialog.add_css_class("paddock-dialog")
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
                self._open_selected_service_logs()
            elif response_name == "copy":
                self.get_clipboard().set(text)
                self.show_toast(f"{title} logs copied")

        dialog.connect("response", response)
        dialog.present(self)

    def _toggle_service(self, _button, key: str) -> None:
        if self.mutation_busy:
            return
        service = self._service_instance(key)
        if service is None:
            return
        active = not service.active
        self.operation_spinner.set_tooltip_text(
            f"{'Starting' if active else 'Stopping'} {service.label}"
        )
        self._set_mutation_busy(True)
        self.tasks.submit(
            f"service-mutation-{key}",
            lambda: self.controller.set_service_instance_active(key, active),
            self._instance_operation_finished,
            self._instance_operation_failed,
        )

    def _service_instance(self, instance_id: str):
        if self.service_instances_snapshot is None:
            return None
        return next(
            (item for item in self.service_instances_snapshot.instances if item.id == instance_id),
            None,
        )

    def _open_add_service(self, _button=None) -> None:
        if self.mutation_busy:
            return
        kinds = ("redis", "mysql", "postgres", "mailpit", "meilisearch", "rustfs")
        labels = ("Redis", "MySQL", "PostgreSQL", "Mailpit", "Meilisearch", "RustFS")
        ports = (6379, 3306, 5432, 1025, 7700, 9000)
        dialog = Adw.AlertDialog(
            heading="Add Service",
            body="Create an independent service instance with its own port and data volume.",
        )
        dialog.add_css_class("paddock-dialog")
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        form.set_size_request(440, -1)
        form.append(Gtk.Label(label="Service type", xalign=0))
        kind = Gtk.DropDown.new_from_strings(labels)
        form.append(kind)
        form.append(Gtk.Label(label="Display name", xalign=0))
        name = Gtk.Entry(max_length=80)
        form.append(name)
        form.append(Gtk.Label(label="Loopback port", xalign=0))
        port = Gtk.Entry(input_purpose=Gtk.InputPurpose.DIGITS)
        form.append(port)
        autostart = Gtk.CheckButton(label="Start automatically with Paddock")
        form.append(autostart)

        def selected_changed(dropdown, _property=None) -> None:
            index = dropdown.get_selected()
            existing = self.service_instances_snapshot.instances if self.service_instances_snapshot else ()
            used_labels = {item.label.casefold() for item in existing}
            ordinal = 1
            candidate = labels[index]
            while candidate.casefold() in used_labels:
                ordinal += 1
                candidate = f"{labels[index]} {ordinal}"
            used_ports = {item.port for item in existing}
            candidate_port = ports[index]
            while candidate_port in used_ports:
                candidate_port += 1
            name.set_text(candidate)
            port.set_text(str(candidate_port))

        kind.connect("notify::selected", selected_changed)
        selected_changed(kind)
        dialog.set_extra_child(form)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
        dialog.set_default_response("add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)

        def response(_dialog, response_name: str) -> None:
            if response_name != "add":
                return
            port_text = port.get_text().strip()
            if not port_text.isdigit():
                self.show_error("Invalid port", "Enter a numeric port between 1024 and 65535.")
                return
            index = kind.get_selected()
            self.operation_spinner.set_tooltip_text(f"Adding {name.get_text().strip()}")
            self._set_mutation_busy(True)
            self.tasks.submit(
                "service-instance-add",
                lambda: self.controller.create_service_instance(
                    kinds[index], name.get_text(), int(port_text), autostart.get_active()
                ),
                self._instance_operation_finished,
                self._instance_operation_failed,
            )

        dialog.connect("response", response)
        dialog.present(self)

    def _confirm_remove_instance(self, _button=None) -> None:
        key = getattr(self, "selected_service_key", None)
        service = self._service_instance(key) if key else None
        if service is None or self.mutation_busy:
            return
        dialog = Adw.AlertDialog(
            heading=f"Remove {service.label}?",
            body=(
                f"This permanently removes the instance and deletes its data volume "
                f"{service.volume}. This cannot be undone."
            ),
        )
        dialog.add_css_class("paddock-dialog")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

        def response(_dialog, response_name: str) -> None:
            if response_name != "remove":
                return
            self._set_mutation_busy(True)
            self.tasks.submit(
                f"service-instance-remove-{service.id}",
                lambda: self.controller.remove_service_instance(service.id),
                self._instance_removed,
                self._instance_operation_failed,
            )

        dialog.connect("response", response)
        dialog.present(self)

    def _instance_removed(self, result: ServiceInstanceOperationResult) -> None:
        self.services_view.set_visible_child_name("catalog")
        self.services_split.set_show_sidebar(False)
        self.selected_service_key = None
        self._instance_operation_finished(result)

    def _instance_operation_finished(self, result: ServiceInstanceOperationResult) -> None:
        self._set_mutation_busy(False)
        self._show_service_instances(result.snapshot)
        if result.ok:
            self.show_toast(result.summary)
        else:
            self.show_error(result.summary, result.detail or "Unknown service error")
        self.refresh()

    def _instance_operation_failed(self, error: BaseException) -> None:
        self._set_mutation_busy(False)
        self.show_error("Service operation failed", str(error))
        self.refresh()

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
        content.append(self._build_service_settings_form("redis"))
        self.redis_hero = PaddockHero(
            "content-loading-symbolic", "Redis", "Shared service · loopback only"
        )

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

        actions = PaddockSection("Manage")
        configuration = Adw.ActionRow(
            title="Redis configuration",
            subtitle="Change the pinned container image or loopback host port.",
        )
        self.redis_configure = Gtk.Button(label="Configure")
        self.redis_configure.connect("clicked", self._open_redis_editor)
        configuration.add_suffix(self.redis_configure)

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

        danger = PaddockSection("Danger zone", danger=True)
        removal = Adw.ActionRow(
            title="Remove Redis",
            subtitle="Permanently remove the service and its data volume.",
        )
        removal_buttons = Gtk.Box(spacing=6)
        self.redis_remove = Gtk.Button(label="Remove")
        self.redis_remove.set_valign(Gtk.Align.CENTER)
        self.redis_remove.add_css_class("destructive-action")
        self.redis_remove.connect("clicked", self._confirm_redis_data_deletion)
        self.redis_delete = Gtk.Button(label="Delete Data…")
        self.redis_delete.add_css_class("destructive-action")
        self.redis_delete.connect("clicked", self._confirm_redis_data_deletion)
        removal_buttons.append(self.redis_remove)
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
            "service-instances-snapshot",
            self.controller.service_instances_snapshot,
            self._show_service_instances,
            lambda error: self.show_error("Service refresh failed", str(error)),
        )
        self.tasks.submit(
            "linked-sites-snapshot",
            self.controller.linked_sites_snapshot,
            self._show_linked_sites,
            lambda error: self.show_error("Sites refresh failed", str(error)),
        )
        self.tasks.submit(
            "php-versions-snapshot",
            self.controller.php_versions_snapshot,
            self._show_php_versions,
            lambda error: self.show_error("PHP refresh failed", str(error)),
        )
        self.tasks.submit(
            "node-versions-snapshot", self.controller.node_versions_snapshot,
            self._show_node_versions,
            lambda error: self.show_error("Node.js refresh failed", str(error)),
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

    def _show_service_instances(self, snapshot: ServiceInstancesSnapshot) -> None:
        self.service_instances_snapshot = snapshot
        self.service_cache_section.clear()
        self.service_database_section.clear()
        self.service_mail_section.clear()
        self.service_search_section.clear()
        self.service_storage_section.clear()
        self.service_catalog_rows.clear()
        self.service_toggle_buttons.clear()
        for service in snapshot.instances:
            section = self.service_storage_section if service.type == "rustfs" else (
                self.service_search_section if service.type == "meilisearch" else (
                self.service_mail_section if service.type == "mailpit" else (
                self.service_cache_section if service.type == "redis"
                else self.service_database_section
            )))
            row = PaddockServiceRow(
                service.label,
                f"{service.version} · Port: {service.port}",
                service.state,
                show_detail=True,
            )
            row.set_activatable(True)
            settings = Gtk.Button(label="Settings")
            settings.set_valign(Gtk.Align.CENTER)
            settings.connect("clicked", self._open_service_settings, service.id)
            toggle = Gtk.Button(label="Stop" if service.active else "Start")
            toggle.set_valign(Gtk.Align.CENTER)
            if not service.active:
                toggle.add_css_class("suggested-action")
            toggle.connect("clicked", self._toggle_service, service.id)
            row.add_suffix(settings)
            row.add_suffix(toggle)
            row.connect("activated", self._show_service_info, service.id)
            section.add(row)
            self.service_catalog_rows[service.id] = row
            self.service_toggle_buttons[service.id] = toggle
            if getattr(self, "selected_service_key", None) == service.id:
                self._update_service_info(service)
                if self.services_view.get_visible_child_name() == "detail":
                    if not self.instance_name_entry.has_focus():
                        self.instance_name_entry.set_text(service.label)
                    if not self.instance_port_entry.has_focus():
                        self.instance_port_entry.set_text(str(service.port))
                    self.instance_autostart_check.set_active(service.autostart)
        if not any(item.type == "redis" for item in snapshot.instances):
            self.service_cache_section.add(Adw.ActionRow(title="No cache services added"))
        if not any(item.type in {"mysql", "postgres"} for item in snapshot.instances):
            self.service_database_section.add(Adw.ActionRow(title="No databases added"))
        if not any(item.type == "mailpit" for item in snapshot.instances):
            self.service_mail_section.add(Adw.ActionRow(title="No mail services added"))
        if not any(item.type == "meilisearch" for item in snapshot.instances):
            self.service_search_section.add(Adw.ActionRow(title="No search services added"))
        if not any(item.type == "rustfs" for item in snapshot.instances):
            self.service_storage_section.add(Adw.ActionRow(title="No storage services added"))
        self._update_action_sensitivity()

    def _show_linked_sites(self, snapshot: LinkedSitesSnapshot) -> None:
        self.linked_sites_snapshot = snapshot
        self._render_linked_sites()

    def _show_php_versions(self, snapshot: PhpVersionsSnapshot) -> None:
        self.php_versions_snapshot = snapshot
        self.php_versions_section.clear()
        self.php_install_buttons.clear()
        for version in snapshot.versions:
            if version.installed:
                subtitle = f"{version.architecture} · Installed"
            elif version.available:
                subtitle = f"{version.architecture} · Available to install"
            else:
                subtitle = f"{version.architecture} · Locally installed"
            row = Adw.ActionRow(title=f"PHP {version.release}", subtitle=subtitle)
            if version.path:
                row.set_tooltip_text(version.path)
            if version.available and not version.installed:
                install = Gtk.Button(label="Install")
                install.set_valign(Gtk.Align.CENTER)
                install.add_css_class("suggested-action")
                install.set_sensitive(not self.mutation_busy)
                install.connect("clicked", self._install_php, version.minor)
                row.add_suffix(install)
                self.php_install_buttons[version.minor] = install
            self.php_versions_section.add(row)
        if not snapshot.versions:
            self.php_versions_section.add(
                Adw.ActionRow(title="No PHP versions available")
            )

    def _install_php(self, _button, minor: str) -> None:
        if self.mutation_busy:
            return
        self.operation_spinner.set_tooltip_text(f"Installing PHP {minor}")
        self._set_mutation_busy(True)
        self.tasks.submit(
            f"php-install-{minor}",
            lambda: self.controller.install_php(minor),
            self._php_install_finished,
            lambda error: self._php_install_failed(minor, error),
        )

    def _show_node_versions(self, snapshot: NodeVersionsSnapshot) -> None:
        self.node_versions_snapshot = snapshot
        self.node_versions_section.clear(); self.node_install_buttons.clear()
        for version in snapshot.versions:
            subtitle = f"{version.architecture} · " + ("Installed" if version.installed else "Available to install")
            row = Adw.ActionRow(title=f"Node.js {version.release}", subtitle=subtitle)
            if version.path: row.set_tooltip_text(version.path)
            if version.available and not version.installed:
                install = Gtk.Button(label="Install"); install.set_valign(Gtk.Align.CENTER)
                install.add_css_class("suggested-action"); install.set_sensitive(not self.mutation_busy)
                install.connect("clicked", self._install_node, version.major); row.add_suffix(install)
                self.node_install_buttons[version.major] = install
            self.node_versions_section.add(row)
        if not snapshot.versions: self.node_versions_section.add(Adw.ActionRow(title="No Node.js versions available"))

    def _install_node(self, _button, major: str) -> None:
        if self.mutation_busy: return
        self.operation_spinner.set_tooltip_text(f"Installing Node.js {major}"); self._set_mutation_busy(True)
        self.tasks.submit(f"node-install-{major}", lambda: self.controller.install_node(major), self._node_install_finished, lambda error: self._node_install_failed(major, error))

    def _node_install_finished(self, result: NodeInstallResult) -> None:
        self._set_mutation_busy(False); self._show_node_versions(result.snapshot)
        if result.ok: self.show_toast(result.summary); self.refresh()
        else: self.show_error(result.summary, result.detail or "Unknown installation error")

    def _node_install_failed(self, major: str, error: BaseException) -> None:
        self._set_mutation_busy(False); self.show_error(f"Node {major} could not be installed", str(error)); self.refresh()

    def _php_install_finished(self, result: PhpInstallResult) -> None:
        self._set_mutation_busy(False)
        self._show_php_versions(result.snapshot)
        if result.ok:
            self.show_toast(result.summary)
            self.refresh()
        else:
            self.show_error(result.summary, result.detail or "Unknown installation error")

    def _php_install_failed(self, minor: str, error: BaseException) -> None:
        self._set_mutation_busy(False)
        self.show_error(f"PHP {minor} could not be installed", str(error))

    def _site_search_changed(self, _entry=None) -> None:
        if self.linked_sites_snapshot is not None:
            self._render_linked_sites()

    def _render_linked_sites(self) -> None:
        previous = getattr(self, "selected_site_name", None)
        query = self.site_search.get_text().strip().casefold()
        sites = tuple(
            site for site in self.linked_sites_snapshot.sites
            if site_name_matches(site.name, query)
        )
        while child := self.linked_sites_list.get_first_child():
            self.linked_sites_list.remove(child)
        selected_row = None
        for site in sites:
            row = Adw.ActionRow(
                title=site.host,
                subtitle=f"PHP {site.php} · {site_worker_summary(site)}",
            )
            row.set_name(site.name)
            row.set_activatable(True)
            self.linked_sites_list.append(row)
            if site.name == previous:
                selected_row = row
        if sites:
            self.linked_sites_list.select_row(
                selected_row or self.linked_sites_list.get_row_at_index(0)
            )
        else:
            self.selected_site_name = None
            self.site_detail_stack.set_visible_child_name("empty")

    def _site_selected(self, _list, row) -> None:
        if row is None or self.linked_sites_snapshot is None:
            self.selected_site_name = None
            self.site_detail_stack.set_visible_child_name("empty")
            return
        name = row.get_name()
        site = next(
            (item for item in self.linked_sites_snapshot.sites if item.name == name),
            None,
        )
        if site is None:
            return
        self.selected_site_name = site.name
        self.site_detail_title.set_label(site.host)
        self.site_secured_button.set_label("Yes" if site.secured else "No")
        self.site_secured_button.set_sensitive(not self.mutation_busy)
        versions = list(self.linked_sites_snapshot.php_versions)
        if site.php not in versions:
            versions.append(site.php)
            versions.sort()
        self.site_php_button.set_label(site.php)
        self.site_php_button.set_sensitive(not self.mutation_busy and bool(versions))
        self.site_php_revealer.set_reveal_child(False)
        while child := self.site_php_options.get_first_child():
            self.site_php_options.remove(child)
        for version in versions:
            choice = Gtk.Button(label=f"{version}{'  ✓' if version == site.php else ''}")
            choice.connect("clicked", self._choose_site_php, version)
            self.site_php_options.append(choice)
        node_versions = list(self.linked_sites_snapshot.node_versions)
        if site.node and site.node not in node_versions: node_versions.append(site.node); node_versions.sort(key=int)
        self.site_node_button.set_label(site.node or "Default")
        self.site_node_button.set_sensitive(not self.mutation_busy and bool(node_versions))
        self.site_node_revealer.set_reveal_child(False)
        while child := self.site_node_options.get_first_child(): self.site_node_options.remove(child)
        for version in node_versions:
            choice = Gtk.Button(label=f"{version}{'  ✓' if version == site.node else ''}")
            choice.connect("clicked", self._choose_site_node, version); self.site_node_options.append(choice)
        self.site_reverb_row.set_visible(site.reverb_available or site.reverb_configured)
        self.site_workers_section.set_visible(
            site.reverb_available or site.reverb_configured
        )
        if site.reverb_configured:
            detail = site.reverb_state.replace("-", " ").capitalize()
            if site.reverb_port is not None:
                detail += f" · Proxied through {site.host} · Internal port {site.reverb_port}"
            self.site_reverb_row.set_subtitle(detail)
        else:
            self.site_reverb_row.set_subtitle("Available · not running")
        active = site.reverb_state == "active"
        self.site_reverb_toggle.set_label("Stop" if active else "Start")
        self.site_reverb_toggle.set_sensitive(not self.mutation_busy)
        self.site_reverb_logs.set_sensitive(not self.mutation_busy and site.reverb_configured)
        self._updating_reverb_autostart = True
        self.site_reverb_autostart.set_active(site.reverb_autostart)
        self._updating_reverb_autostart = False
        self.site_reverb_autostart.set_sensitive(not self.mutation_busy)
        self.site_queue_row.set_visible(site.queue_available or site.queue_configured)
        self.site_queue_row.set_subtitle(
            site.queue_state.replace("-", " ").capitalize()
            if site.queue_configured else "Available · not running"
        )
        queue_active = site.queue_state == "active"
        self.site_queue_toggle.set_label("Stop" if queue_active else "Start")
        self.site_queue_toggle.set_sensitive(not self.mutation_busy)
        self.site_queue_logs.set_sensitive(not self.mutation_busy and site.queue_configured)
        self._updating_queue_autostart = True
        self.site_queue_autostart.set_active(site.queue_autostart)
        self._updating_queue_autostart = False
        self.site_queue_autostart.set_sensitive(not self.mutation_busy)
        self.site_workers_section.set_visible(
            site.reverb_available or site.reverb_configured
            or site.queue_available or site.queue_configured
        )
        self.site_url_link.set_uri(site.url)
        self.site_url_link.set_label(site.url)
        self.site_path_link.set_uri(Path(site.root).as_uri())
        self.site_path_link.set_label(site.root)
        self._show_site_configuration(site)
        self.site_detail_stack.set_visible_child_name("details")

    def _toggle_site_php_options(self, _button=None) -> None:
        if self.mutation_busy:
            return
        self.site_php_revealer.set_reveal_child(
            not self.site_php_revealer.get_reveal_child()
        )

    def _toggle_site_node_options(self, _button=None) -> None:
        if not self.mutation_busy: self.site_node_revealer.set_reveal_child(not self.site_node_revealer.get_reveal_child())

    def _selected_site(self):
        if self.linked_sites_snapshot is None:
            return None
        selected = getattr(self, "selected_site_name", None)
        return next(
            (item for item in self.linked_sites_snapshot.sites if item.name == selected),
            None,
        )

    def _toggle_site_reverb(self, _button=None) -> None:
        if self.mutation_busy:
            return
        site = self._selected_site()
        if site is None:
            return
        active = site.reverb_state != "active"
        self.operation_spinner.set_tooltip_text(
            f"{'Starting' if active else 'Stopping'} Reverb for {site.host}"
        )
        self._set_mutation_busy(True)
        self.tasks.submit(
            f"site-reverb-{site.name}",
            lambda: self.controller.set_reverb_active(site.name, active),
            self._site_reverb_finished,
            self._site_reverb_failed,
        )

    def _toggle_site_reverb_autostart(self, button) -> None:
        if getattr(self, "_updating_reverb_autostart", False) or self.mutation_busy:
            return
        site = self._selected_site()
        if site is None:
            return
        enabled = button.get_active()
        self._set_mutation_busy(True)
        self.tasks.submit(
            f"site-reverb-autostart-{site.name}",
            lambda: self.controller.set_reverb_autostart(site.name, enabled),
            self._site_reverb_finished,
            self._site_reverb_failed,
        )

    def _open_site_reverb_logs(self, _button=None) -> None:
        if self.mutation_busy:
            return
        site = self._selected_site()
        if site is None:
            return
        self.tasks.submit(
            f"site-reverb-logs-{site.name}",
            lambda: self.controller.reverb_logs(site.name, 200),
            lambda lines: self.show_error(
                f"Reverb logs · {site.host}",
                "\n".join(lines) or "No log entries were found.",
            ),
            lambda error: self.show_error("Reverb logs unavailable", str(error)),
        )

    def _site_reverb_finished(self, result: LinkedSitesOperationResult) -> None:
        self._set_mutation_busy(False)
        self._show_linked_sites(result.snapshot)
        if result.ok:
            self.show_toast(result.summary)
        else:
            self.show_error(result.summary, result.detail or "Unknown Reverb error")

    def _site_reverb_failed(self, error: BaseException) -> None:
        self._set_mutation_busy(False)
        self.show_error("Reverb could not be changed", str(error))
        self.refresh()

    def _toggle_site_queue(self, _button=None) -> None:
        if self.mutation_busy:
            return
        site = self._selected_site()
        if site is None:
            return
        active = site.queue_state != "active"
        self.operation_spinner.set_tooltip_text(
            f"{'Starting' if active else 'Stopping'} queue worker for {site.host}"
        )
        self._set_mutation_busy(True)
        self.tasks.submit(
            f"site-queue-{site.name}",
            lambda: self.controller.set_queue_active(site.name, active),
            self._site_queue_finished,
            self._site_queue_failed,
        )

    def _toggle_site_queue_autostart(self, button) -> None:
        if getattr(self, "_updating_queue_autostart", False) or self.mutation_busy:
            return
        site = self._selected_site()
        if site is None:
            return
        enabled = button.get_active()
        self._set_mutation_busy(True)
        self.tasks.submit(
            f"site-queue-autostart-{site.name}",
            lambda: self.controller.set_queue_autostart(site.name, enabled),
            self._site_queue_finished,
            self._site_queue_failed,
        )

    def _open_site_queue_logs(self, _button=None) -> None:
        if self.mutation_busy:
            return
        site = self._selected_site()
        if site is None:
            return
        self.tasks.submit(
            f"site-queue-logs-{site.name}",
            lambda: self.controller.queue_logs(site.name, 200),
            lambda lines: self.show_error(
                f"Queue logs · {site.host}",
                "\n".join(lines) or "No log entries were found.",
            ),
            lambda error: self.show_error("Queue logs unavailable", str(error)),
        )

    def _site_queue_finished(self, result: LinkedSitesOperationResult) -> None:
        self._set_mutation_busy(False)
        self._show_linked_sites(result.snapshot)
        if result.ok:
            self.show_toast(result.summary)
        else:
            self.show_error(result.summary, result.detail or "Unknown queue error")

    def _site_queue_failed(self, error: BaseException) -> None:
        self._set_mutation_busy(False)
        self.show_error("Queue worker could not be changed", str(error))
        self.refresh()

    def _choose_site_node(self, _button, version: str) -> None:
        if self.mutation_busy: return
        self.site_node_revealer.set_reveal_child(False)
        selected = getattr(self, "selected_site_name", None)
        site = next((item for item in self.linked_sites_snapshot.sites if item.name == selected), None) if self.linked_sites_snapshot else None
        if site is None or version == site.node: return
        self.operation_spinner.set_tooltip_text(f"Switching {site.host} to Node {version}"); self._set_mutation_busy(True)
        self.tasks.submit(f"site-node-{site.name}", lambda: self.controller.set_linked_site_node(site.name, version), self._site_node_finished, self._site_node_failed)

    def _site_node_finished(self, result: LinkedSitesOperationResult) -> None:
        self._set_mutation_busy(False); self._show_linked_sites(result.snapshot)
        if result.ok: self.show_toast(result.summary)
        else: self.show_error(result.summary, result.detail or "Unknown site error")

    def _site_node_failed(self, error: BaseException) -> None:
        self._set_mutation_busy(False); self.show_error("Node version could not be changed", str(error)); self.refresh()

    def _choose_site_php(self, _button, version: str) -> None:
        if self.mutation_busy:
            return
        self.site_php_revealer.set_reveal_child(False)
        selected = getattr(self, "selected_site_name", None)
        site = next(
            (
                candidate for candidate in self.linked_sites_snapshot.sites
                if candidate.name == selected
            ),
            None,
        ) if self.linked_sites_snapshot else None
        if site is None:
            return
        if version == site.php:
            return
        self.operation_spinner.set_tooltip_text(
            f"Switching {site.host} to PHP {version}"
        )
        self._set_mutation_busy(True)
        self.tasks.submit(
            f"site-php-{site.name}",
            lambda: self.controller.set_linked_site_php(site.name, version),
            self._site_php_finished,
            self._site_php_failed,
        )

    def _site_php_finished(self, result: LinkedSitesOperationResult) -> None:
        self._set_mutation_busy(False)
        self._show_linked_sites(result.snapshot)
        if result.ok:
            self.show_toast(result.summary)
        else:
            self.show_error(result.summary, result.detail or "Unknown site error")

    def _site_php_failed(self, error: BaseException) -> None:
        self._set_mutation_busy(False)
        self.show_error("PHP version could not be changed", str(error))
        self.refresh()

    def _toggle_site_secured(self, _button=None) -> None:
        if self.mutation_busy or self.linked_sites_snapshot is None:
            return
        selected = getattr(self, "selected_site_name", None)
        site = next(
            (item for item in self.linked_sites_snapshot.sites if item.name == selected),
            None,
        )
        if site is None:
            return
        secured = not site.secured
        self.operation_spinner.set_tooltip_text(
            f"Switching {site.host} to {'HTTPS' if secured else 'HTTP'}"
        )
        self._set_mutation_busy(True)
        self.tasks.submit(
            f"site-security-{site.name}",
            lambda: self.controller.set_linked_site_secured(site.name, secured),
            self._site_security_finished,
            self._site_security_failed,
        )

    def _site_security_finished(self, result: LinkedSitesOperationResult) -> None:
        self._set_mutation_busy(False)
        self._show_linked_sites(result.snapshot)
        if result.ok:
            self.show_toast(result.summary)
        else:
            self.show_error(result.summary, result.detail or "Unknown site error")

    def _site_security_failed(self, error: BaseException) -> None:
        self._set_mutation_busy(False)
        self.show_error("Site security could not be changed", str(error))
        self.refresh()

    def _open_site_terminal(self, _button=None) -> None:
        if self.linked_sites_snapshot is None:
            return
        selected = getattr(self, "selected_site_name", None)
        site = next(
            (item for item in self.linked_sites_snapshot.sites if item.name == selected),
            None,
        )
        if site is None:
            return
        if not Path(site.root).is_dir():
            self.show_error("Project path unavailable", f"Directory does not exist: {site.root}")
            return
        try:
            Gio.Subprocess.new(terminal_command(site.root), Gio.SubprocessFlags.NONE)
        except GLib.Error as error:
            self.show_error("Could not open terminal", str(error))

    def _show_site_configuration(self, site) -> None:
        """Say plainly whether each fragment is being served.

        A project fragment that is present but untrusted serves nothing, and
        that safe outcome is the one most likely to be mistaken for a bug, so
        the row states it rather than only naming the file.
        """
        self.site_type_row.set_subtitle(
            f"{site.type} · document root: "
            f"{'the project root' if site.document_root == '.' else site.document_root}"
        )
        self.site_config_row.set_subtitle(
            site.custom_config
            + ("" if site.custom_config_present else "  (not created yet)")
        )
        self.site_config_button.set_label(
            "Edit" if site.custom_config_present else "Create"
        )
        self.site_config_button.set_sensitive(not self.mutation_busy)

        declared = site.project_config is not None and site.project_config_status != "none"
        self.site_project_config_row.set_visible(declared)
        self.site_project_config_button.set_sensitive(
            not self.mutation_busy and site.project_config_status != "missing"
        )
        if declared:
            explanation = {
                "trusted": "applied",
                "pending": "not reviewed yet, so nothing from it is served",
                "changed": "changed since you trusted it, so it is not served",
                "missing": "declared but not present",
            }.get(site.project_config_status, site.project_config_status)
            self.site_project_config_row.set_subtitle(
                f"{site.project_config} · {explanation}"
            )
            self.site_project_config_button.set_label(
                "Stop using" if site.project_config_status == "trusted" else "Trust"
            )
        self.site_reload_button.set_sensitive(not self.mutation_busy)

    def _edit_site_configuration(self, _button=None) -> None:
        site = self._selected_site()
        if site is None or self.mutation_busy:
            return
        self.operation_spinner.set_tooltip_text(f"Preparing {site.host} configuration")
        self._set_mutation_busy(True)
        self.tasks.submit(
            f"site-config-{site.name}",
            lambda: self.controller.ensure_site_configuration(site.name),
            self._site_configuration_prepared,
            lambda error: self._site_configuration_failed(error),
        )

    def _site_configuration_prepared(self, result: LinkedSitesOperationResult) -> None:
        self._set_mutation_busy(False)
        self._show_linked_sites(result.snapshot)
        if not result.ok:
            self.show_error(result.summary, result.detail or "Unknown error")
            return
        # `detail` carries the path the controller made sure exists.
        try:
            Gio.AppInfo.launch_default_for_uri(Path(result.detail).as_uri(), None)
        except GLib.Error as error:
            self.show_error("Could not open the configuration", str(error))
            return
        self.show_toast(f"Opened {result.detail}")

    def _site_configuration_failed(self, error: BaseException) -> None:
        self._set_mutation_busy(False)
        self.show_error("Site configuration could not be prepared", str(error))
        self.refresh()

    def _toggle_project_configuration(self, _button=None) -> None:
        site = self._selected_site()
        if site is None or self.mutation_busy:
            return
        trusted = site.project_config_status != "trusted"
        self.operation_spinner.set_tooltip_text(
            f"{'Trusting' if trusted else 'Withdrawing trust in'} "
            f"{site.project_config}"
        )
        self._set_mutation_busy(True)
        self.tasks.submit(
            f"site-trust-{site.name}",
            lambda: self.controller.set_site_configuration_trusted(site.name, trusted),
            self._site_security_finished,
            self._site_configuration_failed,
        )

    def _reload_web_configuration(self, _button=None) -> None:
        if self.mutation_busy:
            return
        self.operation_spinner.set_tooltip_text("Reloading the web configuration")
        self._set_mutation_busy(True)
        self.tasks.submit(
            "web-reload",
            self.controller.reload_web,
            self._web_reload_finished,
            self._site_configuration_failed,
        )

    def _web_reload_finished(self, result) -> None:
        self._set_mutation_busy(False)
        if result.ok:
            self.show_toast(result.summary)
        else:
            # Verbatim: `nginx: [emerg] ... in /path/to/file:12` names the file
            # and the line, and paraphrasing it throws that away.
            self.show_error(result.summary, result.detail or "Unknown nginx error")
        self.refresh()

    def _open_site_in_zed(self, _button=None) -> None:
        if self.linked_sites_snapshot is None:
            return
        selected = getattr(self, "selected_site_name", None)
        site = next(
            (item for item in self.linked_sites_snapshot.sites if item.name == selected),
            None,
        )
        if site is None:
            return
        if not Path(site.root).is_dir():
            self.show_error("Project path unavailable", f"Directory does not exist: {site.root}")
            return
        try:
            Gio.Subprocess.new(zed_command(site.root), Gio.SubprocessFlags.NONE)
        except GLib.Error as error:
            self.show_error("Could not open Zed", str(error))

    def _open_parking_folders(self, _button=None) -> None:
        if self.mutation_busy:
            return
        snapshot = self.controller.parking_snapshot()
        dialog = Adw.AlertDialog(
            heading="Parking Folders",
            body="Every immediate subfolder is served as <folder-name>.test.",
        )
        dialog.add_css_class("paddock-dialog")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_size_request(560, -1)
        folders = PaddockSection("Folders")
        for path in snapshot.paths:
            row = Adw.ActionRow(title=path)
            row.set_title_selectable(True)
            remove = Gtk.Button(label="Remove")
            remove.set_valign(Gtk.Align.CENTER)
            remove.add_css_class("destructive-action")
            remove.connect("clicked", self._remove_parking_folder, path, dialog)
            row.add_suffix(remove)
            folders.add(row)
        if not snapshot.paths:
            folders.add(Adw.ActionRow(title="No parking folders"))
        content.append(folders)
        if snapshot.conflicts:
            warning = Gtk.Label(
                label="\n".join(snapshot.conflicts), xalign=0, wrap=True, selectable=True
            )
            warning.add_css_class("error")
            content.append(warning)
        add = Gtk.Button(label="Add Folder")
        add.set_halign(Gtk.Align.END)
        add.add_css_class("suggested-action")
        add.connect("clicked", self._choose_parking_folder, dialog)
        content.append(add)
        dialog.set_extra_child(content)
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.present(self)

    def _choose_parking_folder(self, _button, parent_dialog) -> None:
        chooser = Gtk.FileDialog(title="Add Parking Folder")

        def selected(file_dialog, result) -> None:
            try:
                folder = file_dialog.select_folder_finish(result)
            except GLib.Error as error:
                if not error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    self.show_error("Folder could not be selected", str(error))
                return
            path = folder.get_path()
            if path is None:
                self.show_error("Folder could not be selected", "Choose a local folder.")
                return
            parent_dialog.close()
            self._set_mutation_busy(True)
            self.tasks.submit(
                "parking-folder-add",
                lambda: self.controller.add_parking_path(path),
                self._parking_operation_finished,
                self._parking_operation_failed,
            )

        chooser.select_folder(self, None, selected)

    def _remove_parking_folder(self, _button, path: str, parent_dialog) -> None:
        parent_dialog.close()
        self._set_mutation_busy(True)
        self.tasks.submit(
            "parking-folder-remove",
            lambda: self.controller.remove_parking_path(path),
            self._parking_operation_finished,
            self._parking_operation_failed,
        )

    def _parking_operation_finished(self, result: ParkingOperationResult) -> None:
        self._set_mutation_busy(False)
        if result.ok:
            self.show_toast(result.summary)
        else:
            self.show_error(result.summary, result.detail or "Unknown parking error")
        self.refresh()

    def _parking_operation_failed(self, error: BaseException) -> None:
        self._set_mutation_busy(False)
        self.show_error("Parking folder operation failed", str(error))
        self.refresh()

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
        self.redis_remove.set_sensitive(snapshot.configured)
        self.redis_delete.set_sensitive(snapshot.configured)
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
        sensitive = not self.mutation_busy
        self.dashboard_toggle.set_sensitive(sensitive)
        self.add_service_button.set_sensitive(sensitive)
        self.instance_save_button.set_sensitive(sensitive)
        self.instance_remove_button.set_sensitive(sensitive)
        self.parking_button.set_sensitive(sensitive)
        self.site_php_button.set_sensitive(
            sensitive and bool(
                self.linked_sites_snapshot and self.linked_sites_snapshot.php_versions
            )
        )
        self.site_secured_button.set_sensitive(
            sensitive and getattr(self, "selected_site_name", None) is not None
        )
        self.site_node_button.set_sensitive(sensitive and bool(self.linked_sites_snapshot and self.linked_sites_snapshot.node_versions))
        for button in self.service_toggle_buttons.values():
            button.set_sensitive(sensitive)
        for button in self.php_install_buttons.values():
            button.set_sensitive(sensitive)
        for button in self.node_install_buttons.values():
            button.set_sensitive(sensitive)
        if hasattr(self, "service_open_logs"):
            selected = getattr(self, "selected_service_key", None)
            service = self._service_instance(selected) if selected else None
            self.service_open_logs.set_sensitive(
                sensitive and service is not None
            )

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
        dialog.add_css_class("paddock-dialog")
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
        dialog.add_css_class("paddock-dialog")
        dialog.add_response("copy", "Copy Error")
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")

        def response(_dialog, response_name: str) -> None:
            if response_name == "copy":
                self.get_clipboard().set(error_clipboard_text(heading, detail))

        dialog.connect("response", response)
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
            controller = PaddockController(store)
            controller.parking.ensure_default(Path.home())
            self.window = PaddockWindow(self, controller)
        self.window.present()

    def _refresh(self, *_args) -> None:
        if self.window is not None:
            self.window.refresh()


def main(argv: list[str] | None = None) -> int:
    application = PaddockApplication()
    return application.run(sys.argv if argv is None else argv)
