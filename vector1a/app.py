from __future__ import annotations

import json
import tkinter as tk
import time
import queue
import math
import threading
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .engine import OutputSample, VectorEngine
from .events import AXIS_AUTHORED, EventEngine, EventError
from .motion import MotionMode, MotionParameters
from .network import MFPListener, ReStimWebSocketClient
from .routing import AuthoredAxisRouter
from .orchestration import SessionOrchestrator, port_is_open, wait_for_port
from .settings import load_settings, save_settings, settings_path
from .timeline import (EXTRA_RAMP_LEVEL_KEYS, RAMP_CURVE_NAMES, RAMP_LEVEL_LABELS,
                       TIMELINE_HOLD_SECONDS, TIMELINE_SCALE_SECONDS,
                       MediaTimeline, RampWaypoint,
                       decode_timeline_seconds, export_ramp_funscript,
                       export_ramp_waypoints_payload, format_media_time,
                       import_ramp_funscript, import_ramp_waypoints_payload,
                       media_volume_gain, media_volume_gain_waypoints,
                       normalize_curve_name, normalize_level_key,
                       normalize_waypoints, parse_media_time, ramp_curve,
                       waypoint_levels_used)
from .controller import (A, B, X, Y, START, LEFT_SHOULDER, RIGHT_SHOULDER, DPAD_UP, DPAD_DOWN,
                         DPAD_LEFT, DPAD_RIGHT, XInputController)
from .variety import fit_range_for_travel, rolling_offset, rolling_value
from .fourphase import (ELECTRODE_ORDERS, SPATIAL_MODELS, adaptive_crossover_width,
                        apply_group_delay, depth_spread, directed_signed,
                        directional_crossover_profile, map_electrode_order,
                        morph_electrode_order, moving_sequence_window, potential_roles, sequence_cycle_stage,
                        proportional_reversal_boost, reversal_emphasis_envelope,
                        stroke_phase_crossover, restim_crossfade, vertical_crossfade)
from . import __version__
from .control_api import (ACTIONS, CONTROL_META_FIELDS, PANEL_FIELDS, STATUS_KEYS,
                          ControlApiServer, run_on_ui, writable_fields)


THEME_COLORS = {
    "light": {
        "bg": "#f0f0f0",
        "fg": "#1a1a1a",
        "muted": "#555555",
        "warning": "#9b4b00",
        "canvas_bg": "#e6e6e6",
        "canvas_highlight": "#999999",
        "bar_fill": "#08ae2a",
        "bar_marker": "#173b8f",
        "preview_bg": "#f7f7f7",
        "preview_outline": "#dddddd",
        "preview_highlight": "#bbbbbb",
        "preview_line": "#2a6fdb",
        "preview_marker": "#cc4444",
        "text_bg": "#f5f5f5",
        "text_fg": "#1a1a1a",
        "entry_bg": "#ffffff",
        "button_bg": "#e1e1e1",
        "select_bg": "#0078d7",
    },
    "dark": {
        "bg": "#1e1e1e",
        "fg": "#e0e0e0",
        "muted": "#a0a0a0",
        "warning": "#e0a060",
        "canvas_bg": "#2d2d2d",
        "canvas_highlight": "#555555",
        "bar_fill": "#2ecc71",
        "bar_marker": "#6db3f2",
        "preview_bg": "#2a2a2a",
        "preview_outline": "#555555",
        "preview_highlight": "#666666",
        "preview_line": "#5b9bd5",
        "preview_marker": "#e07070",
        "text_bg": "#2a2a2a",
        "text_fg": "#e0e0e0",
        "entry_bg": "#2d2d2d",
        "button_bg": "#3a3a3a",
        "select_bg": "#0a64a8",
    },
}


class RangeBar(tk.Canvas):
    def __init__(self, parent, width=520, height=24):
        colors = THEME_COLORS["light"]
        super().__init__(parent, width=width, height=height, highlightthickness=1,
                         highlightbackground=colors["canvas_highlight"],
                         background=colors["canvas_bg"])
        self._width, self._height = width, height
        self._colors = colors
        self._last: tuple[float, float, float] | None = None

    def apply_theme(self, colors: dict) -> None:
        self._colors = colors
        self.configure(background=colors["canvas_bg"],
                       highlightbackground=colors["canvas_highlight"])
        if self._last is not None:
            self.set(*self._last)

    def set(self, minimum: float, maximum: float, value: float) -> None:
        minimum, maximum, value = (min(1.0, max(0.0, x)) for x in (minimum, maximum, value))
        self._last = (minimum, maximum, value)
        self.delete("all")
        self.create_rectangle(minimum * self._width, 1, maximum * self._width,
                              self._height - 1, fill=self._colors["bar_fill"], outline="")
        x = value * self._width
        self.create_line(x, 0, x, self._height, fill=self._colors["bar_marker"], width=3)


class CollapsibleSection(ttk.Frame):
    def __init__(self, parent, title: str, collapsed: bool = False, on_toggle=None):
        super().__init__(parent, padding=(2, 2))
        self.title = title
        self.collapsed = collapsed
        self.on_toggle = on_toggle
        self.summary = tk.StringVar(value="")
        self.button = ttk.Button(self, width=3, command=self.toggle)
        self.button.grid(row=0, column=0, padx=(2, 5), sticky="nw")
        ttk.Label(self, text=title, font=("TkDefaultFont", 10, "bold")) \
            .grid(row=0, column=1, sticky="nw")
        self._summary_label = ttk.Label(self, textvariable=self.summary, style="Muted.TLabel")
        self._summary_label.grid(row=0, column=2, sticky="ew", padx=12)
        self.columnconfigure(2, weight=1)
        self.rowconfigure(1, weight=1)
        self.body = ttk.Frame(self, padding=(10, 6))
        self.body.grid(row=1, column=0, columnspan=3, sticky="nsew")
        ttk.Separator(self, orient="horizontal").grid(row=2, column=0, columnspan=3, sticky="ew")
        self._render()

    def toggle(self) -> None:
        self.collapsed = not self.collapsed
        self._render()
        if self.on_toggle is not None:
            self.on_toggle()

    def _render(self) -> None:
        self.button.configure(text="▶" if self.collapsed else "▼")
        if self.collapsed:
            self.body.grid_remove()
        else:
            self.body.grid()


class VectorApp:
    FOUR_PHASE_PRESET_FIELDS = (
        "four_phase_return_depth", "four_phase_invert", "four_phase_volume_ceiling",
        "four_phase_volume_modulation", "four_phase_volume_headroom",
        "four_phase_volume_cycle", "four_phase_crossover_width",
        "four_phase_crossover_curve", "four_phase_crossover_sharpness",
        "four_phase_adaptive_crossover", "four_phase_slow_crossover_width",
        "four_phase_fast_crossover_width", "four_phase_directional_trajectory",
        "four_phase_reverse_width_scale", "four_phase_reverse_curve",
        "four_phase_reverse_sharpness", "four_phase_spatial_curve",
        "four_phase_spatial_blend", "four_phase_reversal_emphasis",
        "four_phase_reversal_window", "four_phase_reversal_strength",
        "four_phase_stroke_phase_texture", "four_phase_acceleration_width_scale",
        "four_phase_deceleration_width_scale", "motion_rising_volume_multiplier",
        "motion_falling_volume_multiplier", "four_phase_group_delay",
        "four_phase_group_delay_ms", "four_phase_group_delay_transition", "electrode_order",
        "four_phase_moving_sequence", "four_phase_moving_sequence_depth",
        "four_phase_moving_sequence_width",
        "four_phase_spatial_model", "four_phase_tip_retention",
        "four_phase_spread_softness", "four_phase_full_depth_capture",
    )
    SETTINGS_FIELDS = (
        "mfp_host", "mfp_port", "restim_host", "restim_port", "prostate_host", "prostate_port",
        "four_phase_host", "four_phase_port",
        "auto_start_mfp", "auto_start_restim", "auto_start_prostate",
        "mfp_launch_target", "restim_launch_target", "prostate_launch_target",
        "rate", "lookahead", "volume", "dynamic_volume", "volume_rest_level", "volume_ratio",
        "volume_ramp_up", "frequency_ramp_level", "frequency_ratio", "send_frequency",
        "pulse_frequency_ratio", "pulse_frequency_min", "pulse_frequency_max", "send_pulse_frequency",
        "pulse_rise_ratio", "pulse_rise_min", "pulse_rise_max", "send_pulse_rise",
        "pulse_width_ratio", "pulse_width_min", "pulse_width_max", "send_pulse_width",
        "prostate_narrow_ratio", "prostate_arc_depth", "prostate_threshold",
        "prostate_volume_multiplier", "prostate_rest_level", "prostate_phase_degrees",
        "four_phase_return_depth",
        "four_phase_invert", "four_phase_volume_ceiling", "four_phase_volume_modulation",
        "four_phase_volume_headroom", "four_phase_volume_cycle",
        "four_phase_crossover_width", "four_phase_crossover_curve",
        "four_phase_crossover_sharpness", "four_phase_adaptive_crossover",
        "four_phase_slow_crossover_width", "four_phase_fast_crossover_width",
        "four_phase_directional_trajectory", "four_phase_reverse_width_scale",
        "four_phase_reverse_curve", "four_phase_reverse_sharpness",
        "four_phase_spatial_curve", "four_phase_spatial_blend",
        "four_phase_reversal_emphasis", "four_phase_reversal_window",
        "four_phase_reversal_strength",
        "four_phase_stroke_phase_texture", "four_phase_acceleration_width_scale",
        "four_phase_deceleration_width_scale", "motion_rising_volume_multiplier",
        "motion_falling_volume_multiplier", "four_phase_group_delay",
        "four_phase_group_delay_ms", "four_phase_group_delay_transition",
        "four_phase_moving_sequence", "four_phase_moving_sequence_depth",
        "four_phase_moving_sequence_width",
        "four_phase_spatial_model", "four_phase_tip_retention",
        "four_phase_spread_softness", "four_phase_full_depth_capture",
        "preset_a_name", "preset_b_name", "preset_transition_seconds",
        "electrode_order", "variety_electrode_morph", "variety_electrode_morph_cycle",
        "variety_electrode_morph_transition_seconds",
        "jitter_enabled", "jitter_amplitude", "jitter_cycle_seconds",
        "speed_linked_variation", "variation_full_speed_percent", "variation_fade_seconds",
        "prostate_phase_step", "controller_enabled", "controller_fine_step", "minimum_radius",
        "speed_threshold", "direction_probability", "mode", "direct_controller_enabled",
        "variety_enabled", "variety_frequency_cycle", "variety_pulse_frequency_cycle",
        "variety_pulse_rise_cycle", "variety_pulse_width_cycle", "variety_phase_cycle",
        "variety_frequency", "variety_pulse_frequency",
        "variety_pulse_rise", "variety_pulse_width", "variety_phase",
        "timeline_position_axis", "timeline_duration_axis", "timeline_scale_seconds",
        "media_volume_ramp_enabled", "media_volume_ramp_floor",
        "media_volume_ramp_floor2", "media_volume_ramp_floor3",
        "media_volume_ramp_ceiling", "media_volume_ramp_ceiling2",
        "media_volume_ramp_ceiling3", "media_volume_ramp_curve",
        "media_volume_ramp_waypoints_enabled",
        "events_enabled", "events_file_path", "events_definitions_path",
        "control_api_enabled", "control_api_host", "control_api_port",
        "ui_dark_mode",
    )

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(f"Vector 1A {__version__} - MFP to ReStim")
        root.geometry("1480x900")
        root.minsize(980, 720)

        self.mfp_status = tk.StringVar(value="Disconnected")
        self.restim_status = tk.StringVar(value="Disconnected")
        self.prostate_status = tk.StringVar(value="Disconnected")
        self.four_phase_status = tk.StringVar(value="Disconnected")
        self.mfp_host = tk.StringVar(value="127.0.0.1")
        self.mfp_port = tk.IntVar(value=12345)
        self.restim_host = tk.StringVar(value="127.0.0.1")
        self.restim_port = tk.IntVar(value=12346)
        self.prostate_host = tk.StringVar(value="127.0.0.1")
        self.prostate_port = tk.IntVar(value=12350)
        self.four_phase_host = tk.StringVar(value="127.0.0.1")
        self.four_phase_port = tk.IntVar(value=12351)
        self.auto_start_mfp = tk.BooleanVar(value=False)
        self.auto_start_restim = tk.BooleanVar(value=False)
        self.auto_start_prostate = tk.BooleanVar(value=False)
        self.mfp_launch_target = tk.StringVar(value="")
        self.restim_launch_target = tk.StringVar(value="")
        self.prostate_launch_target = tk.StringVar(value="")
        self.startup_status = tk.StringVar(value="Manual startup")
        self.session_ready_status = tk.StringVar(value="SESSION: MANUAL")
        self._startup_in_progress = False
        self.authored_axes_status = tk.StringVar(value="No authored axes detected")
        self.authored_routing_mode = tk.StringVar(value="Manual selected axes")
        self.timeline_status = tk.StringVar(value="Media timeline: none")
        self.media_ramp_status = tk.StringVar(value="Media ramp: off")
        self.timeline_position_axis = tk.StringVar(value="T0")
        self.timeline_duration_axis = tk.StringVar(value="T1")
        self.timeline_scale_seconds = tk.DoubleVar(value=TIMELINE_SCALE_SECONDS)
        self.media_volume_ramp_enabled = tk.BooleanVar(value=False)
        self.media_volume_ramp_floor = tk.DoubleVar(value=0.40)  # Floor 1
        self.media_volume_ramp_floor2 = tk.DoubleVar(value=0.40)
        self.media_volume_ramp_floor3 = tk.DoubleVar(value=0.40)
        self.media_volume_ramp_ceiling = tk.DoubleVar(value=1.0)  # Ceiling 1
        self.media_volume_ramp_ceiling2 = tk.DoubleVar(value=1.0)
        self.media_volume_ramp_ceiling3 = tk.DoubleVar(value=1.0)
        self.media_volume_ramp_curve = tk.StringVar(value="Linear")
        self.media_volume_ramp_waypoints_enabled = tk.BooleanVar(value=False)
        self._media_ramp_waypoints: list[RampWaypoint] = []
        self._media_ramp_extra_level_widgets: dict[str, list[tk.Widget]] = {}
        self.events_enabled = tk.BooleanVar(value=False)
        self.events_file_path = tk.StringVar(value="")
        self.events_definitions_path = tk.StringVar(value="")
        self.events_status = tk.StringVar(value="Events: off")
        self.control_api_enabled = tk.BooleanVar(value=False)
        self.control_api_host = tk.StringVar(value="0.0.0.0")
        self.control_api_port = tk.IntVar(value=8787)
        self.control_api_status = tk.StringVar(value="Off")
        self.ui_dark_mode = tk.BooleanVar(value=False)
        self._control_api: ControlApiServer | None = None
        self._theme = THEME_COLORS["light"]
        self._range_bars: list[RangeBar] = []
        self.rate = tk.IntVar(value=50)
        self.lookahead = tk.DoubleVar(value=2.0)
        self.volume = tk.DoubleVar(value=0.70)
        self.dynamic_volume = tk.BooleanVar(value=True)
        self.volume_rest_level = tk.DoubleVar(value=0.40)
        self.volume_ratio = tk.DoubleVar(value=20.0)
        self.volume_ramp_up = tk.DoubleVar(value=1.0)
        self.frequency_ramp_level = tk.DoubleVar(value=1.0)
        self.frequency_ratio = tk.DoubleVar(value=2.0)
        self.send_frequency = tk.BooleanVar(value=True)
        self.pulse_frequency_ratio = tk.DoubleVar(value=3.0)
        self.pulse_frequency_min = tk.DoubleVar(value=0.40)
        self.pulse_frequency_max = tk.DoubleVar(value=0.95)
        self.send_pulse_frequency = tk.BooleanVar(value=True)
        self.pulse_rise_ratio = tk.DoubleVar(value=2.0)
        self.pulse_rise_min = tk.DoubleVar(value=0.0)
        self.pulse_rise_max = tk.DoubleVar(value=0.80)
        self.send_pulse_rise = tk.BooleanVar(value=True)
        self.pulse_width_ratio = tk.DoubleVar(value=3.0)
        self.pulse_width_min = tk.DoubleVar(value=0.10)
        self.pulse_width_max = tk.DoubleVar(value=0.45)
        self.send_pulse_width = tk.BooleanVar(value=True)
        self.prostate_narrow_ratio = tk.DoubleVar(value=1.0)
        self.prostate_arc_depth = tk.DoubleVar(value=0.25)
        self.prostate_threshold = tk.DoubleVar(value=0.25)
        self.prostate_volume_multiplier = tk.DoubleVar(value=1.5)
        self.prostate_rest_level = tk.DoubleVar(value=0.7)
        self.prostate_phase_degrees = tk.DoubleVar(value=0.0)
        self.prostate_phase_step = tk.DoubleVar(value=15.0)
        self.four_phase_return_depth = tk.DoubleVar(value=0.30)
        self.four_phase_invert = tk.BooleanVar(value=False)
        self.four_phase_volume_ceiling = tk.DoubleVar(value=0.85)
        self.four_phase_volume_modulation = tk.BooleanVar(value=False)
        self.four_phase_volume_headroom = tk.DoubleVar(value=0.15)
        self.four_phase_volume_cycle = tk.DoubleVar(value=4.0)
        self.four_phase_crossover_width = tk.DoubleVar(value=1.0)
        self.four_phase_crossover_curve = tk.StringVar(value="Cosine")
        self.four_phase_crossover_sharpness = tk.DoubleVar(value=1.0)
        self.four_phase_adaptive_crossover = tk.BooleanVar(value=False)
        self.four_phase_slow_crossover_width = tk.DoubleVar(value=.90)
        self.four_phase_fast_crossover_width = tk.DoubleVar(value=.35)
        self.four_phase_effective_crossover_width = tk.StringVar(value="1.000")
        self.four_phase_directional_trajectory = tk.BooleanVar(value=False)
        self.four_phase_reverse_width_scale = tk.DoubleVar(value=.75)
        self.four_phase_reverse_curve = tk.StringVar(value="Ease Out")
        self.four_phase_reverse_sharpness = tk.DoubleVar(value=.6)
        self.four_phase_spatial_curve = tk.StringVar(value="Linear")
        self.four_phase_spatial_blend = tk.DoubleVar(value=.5)
        self.four_phase_spatial_live = tk.StringVar(value="live 0.500")
        self.four_phase_reversal_emphasis = tk.BooleanVar(value=False)
        self.four_phase_reversal_window = tk.DoubleVar(value=.35)
        self.four_phase_reversal_strength = tk.DoubleVar(value=.20)
        self.four_phase_reversal_live = tk.StringVar(value="live 0.000")
        self.four_phase_stroke_phase_texture = tk.BooleanVar(value=False)
        self.four_phase_acceleration_width_scale = tk.DoubleVar(value=.70)
        self.four_phase_deceleration_width_scale = tk.DoubleVar(value=1.20)
        self.motion_rising_volume_multiplier = tk.DoubleVar(value=1.0)
        self.motion_falling_volume_multiplier = tk.DoubleVar(value=1.0)
        self.four_phase_stroke_phase_live = tk.StringVar(value="off")
        self.four_phase_group_delay = tk.BooleanVar(value=False)
        self.four_phase_group_delay_ms = tk.DoubleVar(value=0.0)
        self.four_phase_group_delay_transition = tk.DoubleVar(value=1.0)
        self.four_phase_group_delay_live = tk.StringVar(value="live 0 ms")
        self.four_phase_moving_sequence = tk.BooleanVar(value=False)
        self.four_phase_moving_sequence_depth = tk.DoubleVar(value=.50)
        self.four_phase_moving_sequence_width = tk.DoubleVar(value=1.0)
        self.four_phase_moving_sequence_live = tk.StringVar(value="off")
        self.four_phase_spatial_model = tk.StringVar(value="Moving focus")
        self.four_phase_tip_retention = tk.DoubleVar(value=.80)
        self.four_phase_spread_softness = tk.DoubleVar(value=.20)
        self.four_phase_full_depth_capture = tk.DoubleVar(value=.05)
        self.four_phase_model_live = tk.StringVar(value="Moving focus")
        self.preset_a_name = tk.StringVar(value="A")
        self.preset_b_name = tk.StringVar(value="B")
        self.preset_transition_seconds = tk.DoubleVar(value=2.5)
        self.preset_status = tk.StringVar(value="No preset active")
        self._preset_slots: dict[str, dict] = {}
        self._preset_active: str | None = None
        self._preset_transition = None
        self._preset_window = None
        self.electrode_order = tk.StringVar(value="ABCD")
        self.variety_electrode_morph = tk.BooleanVar(value=False)
        self.variety_electrode_morph_cycle = tk.DoubleVar(value=6.0)
        self.variety_electrode_morph_transition_seconds = tk.DoubleVar(value=3.0)
        self.jitter_enabled = tk.BooleanVar(value=False)
        self.jitter_amplitude = tk.DoubleVar(value=0.02)
        self.jitter_cycle_seconds = tk.DoubleVar(value=1.0)
        self.speed_linked_variation = tk.BooleanVar(value=True)
        self.variation_full_speed_percent = tk.DoubleVar(value=35.0)
        self.variation_fade_seconds = tk.DoubleVar(value=.75)
        self.variation_depth_live = tk.StringVar(value="Effect depth 0%")
        self.send_four_phase_visual = tk.BooleanVar(value=False)
        self.controller_enabled = tk.BooleanVar(value=True)
        self.controller_target = tk.IntVar(value=0)
        self.controller_fine_step = tk.DoubleVar(value=0.05)
        self.controller_status = tk.StringVar(value="Disabled")
        self.direct_controller_enabled = tk.BooleanVar(value=True)
        self.variety_enabled = tk.BooleanVar(value=False)
        self.variety_frequency_cycle = tk.DoubleVar(value=4.0)
        self.variety_pulse_frequency_cycle = tk.DoubleVar(value=3.0)
        self.variety_pulse_rise_cycle = tk.DoubleVar(value=2.0)
        self.variety_pulse_width_cycle = tk.DoubleVar(value=1.0)
        self.variety_phase_cycle = tk.DoubleVar(value=5.0)
        self.variety_frequency = tk.BooleanVar(value=True)
        self.variety_pulse_frequency = tk.BooleanVar(value=False)
        self.variety_pulse_rise = tk.BooleanVar(value=False)
        self.variety_pulse_width = tk.BooleanVar(value=False)
        self.variety_phase = tk.BooleanVar(value=False)
        self.variety_status = tk.StringVar(value="Off")
        self._variety_started = time.monotonic()
        self._variety_baseline = {}
        self._controller_events: queue.SimpleQueue[tuple[str, object]] = queue.SimpleQueue()
        self.minimum_radius = tk.DoubleVar(value=0.10)
        self.speed_threshold = tk.DoubleVar(value=50.0)
        self.direction_probability = tk.DoubleVar(value=0.10)
        self.mode = tk.StringVar(value=MotionMode.TOP_LEFT_BOTTOM_RIGHT.value)
        self.diag_vars = {name: tk.StringVar(value="--") for name in (
            "raw_l0", "output_l0", "speed", "alpha", "beta", "buffer",
            "lookahead", "actual_delay", "input_count", "output_count", "state",
            "active_mode",
            "output_mode",
            "output_volume",
            "frequency",
            "pulse_frequency",
            "pulse_rise_time",
            "pulse_width",
            "alpha_prostate", "beta_prostate", "volume_prostate",
            "variation_depth",
        )}

        self._connection_events: deque[str] = deque(maxlen=200)
        self._last_connection_event: dict[str, str] = {}

        self.axis_router = AuthoredAxisRouter()
        self.media_timeline = MediaTimeline()
        self.event_engine = EventEngine()
        self._events_last_position_ms: int | None = None
        self._events_last_due_at: float | None = None
        self._events_last_s1: float | None = None
        self.orchestrator = SessionOrchestrator(self._set_startup_status)
        self.restim = ReStimWebSocketClient(self._set_restim_status)
        self.prostate_restim = ReStimWebSocketClient(self._set_prostate_status)
        self.engine = VectorEngine(self._send_sample)
        self._four_phase_last_l0 = 0.5
        self._four_phase_direction = 1
        self._four_phase_send_last_l0 = 0.5
        self._four_phase_send_direction = 1
        self._motion_send_last_l0 = 0.5
        self._motion_send_direction = 1
        self._four_phase_history = deque(maxlen=128)
        self._four_phase_effective_group_delay = 0.0
        self._four_phase_group_delay_last_time = None
        self._four_phase_live_lock = threading.Lock()
        self._four_phase_live_output = (
            (0.5, 0.5, 0.5, 0.5), "ABCD", "ABCD", 0.0, "stable")
        self.listener = MFPListener(
            self.engine.receive_l0, self._set_mfp_status, self._on_mfp_command,
            on_evt=self._on_evt_trigger)
        self.xinput = XInputController(self._xinput_buttons_threaded, self._xinput_status_threaded)
        self.sections = {}
        self._first_run = self._load_settings()
        self._build()
        self._bind_controller_keys()
        self._controller_enabled_changed()
        self.xinput.start()
        self.engine.start()
        self.root.after(100, self._refresh)
        self.root.after(350, self._auto_start_session)
        self.root.after(0, self._sync_control_api_server)
        if self._first_run:
            self.root.after(500, self.show_setup_guide)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _frame(self, title: str, row: int, column: int = 0, span: int = 1,
               parent: ttk.Frame | None = None) -> ttk.Frame:
        collapsed_titles = {
            "Frequency", "Pulse frequency", "Pulse rise time", "Pulse width",
            "Prostate controls", "Four-phase primary motion",
            "Xbox controller", "Rolling Variety", "Live diagnostics",
            "Remote control API",
        }
        host = parent if parent is not None else self._scroll_body
        section = CollapsibleSection(
            host, title, collapsed=(title in collapsed_titles),
            on_toggle=self._refresh_scroll_region)
        section.grid(row=row, column=column, columnspan=span, sticky="nsew", padx=10, pady=3)
        self.sections[title] = section
        return section.body

    def _refresh_scroll_region(self) -> None:
        canvas = getattr(self, "_scroll_canvas", None)
        if canvas is None:
            return
        self.root.update_idletasks()
        bbox = canvas.bbox("all")
        if bbox is not None:
            canvas.configure(scrollregion=bbox)

    def _on_mousewheel(self, event) -> None:
        canvas = getattr(self, "_scroll_canvas", None)
        shell = getattr(self, "_scroll_shell", None)
        if canvas is None or shell is None:
            return
        try:
            x, y = event.x_root, event.y_root
            if not (shell.winfo_rootx() <= x < shell.winfo_rootx() + shell.winfo_width()
                    and shell.winfo_rooty() <= y < shell.winfo_rooty() + shell.winfo_height()):
                return
        except tk.TclError:
            return
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is not None and widget.winfo_class() in (
                "Text", "Listbox", "Treeview", "TCombobox", "TSpinbox", "Spinbox"):
            return
        if getattr(event, "delta", 0):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        elif getattr(event, "num", None) == 4:
            canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            canvas.yview_scroll(1, "units")

    def _on_theme_toggle(self) -> None:
        self._apply_theme()
        self._save_settings()

    def _apply_theme(self) -> None:
        mode = "dark" if self.ui_dark_mode.get() else "light"
        colors = THEME_COLORS[mode]
        self._theme = colors
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.configure(background=colors["bg"])
        style.configure(".", background=colors["bg"], foreground=colors["fg"],
                        fieldbackground=colors["entry_bg"], bordercolor=colors["canvas_highlight"],
                        troughcolor=colors["canvas_bg"], darkcolor=colors["bg"],
                        lightcolor=colors["bg"], focuscolor=colors["select_bg"])
        style.configure("TFrame", background=colors["bg"])
        style.configure("TLabel", background=colors["bg"], foreground=colors["fg"])
        style.configure("TButton", background=colors["button_bg"], foreground=colors["fg"])
        style.configure("TCheckbutton", background=colors["bg"], foreground=colors["fg"],
                        indicatorcolor=colors["entry_bg"])
        style.configure("TRadiobutton", background=colors["bg"], foreground=colors["fg"],
                        indicatorcolor=colors["entry_bg"])
        style.configure("TEntry", fieldbackground=colors["entry_bg"], foreground=colors["fg"],
                        insertcolor=colors["fg"])
        style.configure("TSpinbox", fieldbackground=colors["entry_bg"], foreground=colors["fg"],
                        insertcolor=colors["fg"], arrowcolor=colors["fg"])
        style.configure("TCombobox", fieldbackground=colors["entry_bg"], foreground=colors["fg"],
                        arrowcolor=colors["fg"])
        style.configure("TScrollbar", background=colors["button_bg"], troughcolor=colors["canvas_bg"],
                        arrowcolor=colors["fg"])
        style.configure("TSeparator", background=colors["muted"])
        style.configure("TLabelframe", background=colors["bg"], foreground=colors["fg"])
        style.configure("TLabelframe.Label", background=colors["bg"], foreground=colors["fg"])
        style.configure("Muted.TLabel", background=colors["bg"], foreground=colors["muted"])
        style.configure("Warning.TLabel", background=colors["bg"], foreground=colors["warning"])
        style.configure(
            "Treeview",
            background=colors["entry_bg"],
            foreground=colors["fg"],
            fieldbackground=colors["entry_bg"],
            bordercolor=colors["canvas_highlight"],
            lightcolor=colors["entry_bg"],
            darkcolor=colors["entry_bg"])
        style.configure(
            "Treeview.Heading",
            background=colors["button_bg"],
            foreground=colors["fg"],
            bordercolor=colors["canvas_highlight"],
            relief="flat")
        style.map(
            "Treeview",
            background=[("selected", colors["select_bg"])],
            foreground=[("selected", "#ffffff")])
        style.map(
            "Treeview.Heading",
            background=[("active", colors["canvas_highlight"])],
            foreground=[("active", colors["fg"])])
        style.map("TButton",
                  background=[("active", colors["canvas_highlight"]), ("pressed", colors["select_bg"])],
                  foreground=[("disabled", colors["muted"])])
        style.map("TCheckbutton",
                  background=[("active", colors["bg"])],
                  foreground=[("disabled", colors["muted"])])
        style.map("TCombobox",
                  fieldbackground=[("readonly", colors["entry_bg"])],
                  foreground=[("readonly", colors["fg"])],
                  selectbackground=[("readonly", colors["select_bg"])],
                  selectforeground=[("readonly", "#ffffff")])
        canvas = getattr(self, "_scroll_canvas", None)
        if canvas is not None:
            canvas.configure(background=colors["bg"], highlightbackground=colors["bg"])
        preview = getattr(self, "media_ramp_curve_preview", None)
        if preview is not None:
            preview.configure(background=colors["preview_bg"],
                              highlightbackground=colors["preview_highlight"])
            self._redraw_media_ramp_curve_preview()
        events_text = getattr(self, "events_status_text", None)
        if events_text is not None:
            events_text.configure(background=colors["text_bg"], foreground=colors["text_fg"],
                                  insertbackground=colors["text_fg"],
                                  highlightbackground=colors["canvas_highlight"])
        for bar in getattr(self, "_range_bars", []):
            bar.apply_theme(colors)
        tree = getattr(self, "media_ramp_waypoint_tree", None)
        if tree is not None:
            tree.tag_configure(
                "row", background=colors["entry_bg"], foreground=colors["fg"])
            self._refresh_media_ramp_waypoint_tree()

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self.root, padding=(12, 6))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Label(toolbar, text="ReStim Vector Live", font=("TkDefaultFont", 10, "bold")).pack(side="left")
        ttk.Button(toolbar, text="START / RESUME", command=self.resume).pack(side="left", padx=(24, 6))
        ttk.Button(toolbar, text="Neutral", command=self.neutral).pack(side="left", padx=6)
        ttk.Button(toolbar, text="STOP", command=self.stop).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Setup guide", command=self.show_setup_guide).pack(side="left", padx=(18, 6))
        ttk.Button(toolbar, text="Rolling Variety", command=self.show_variety_window).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Presets A/B", command=self.show_preset_window).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Connection log", command=self.show_connection_log).pack(side="left", padx=6)
        ttk.Button(toolbar, text="MFP axes", command=self.show_axis_routing).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Session startup", command=self.show_session_startup).pack(side="left", padx=6)
        right_bar = ttk.Frame(toolbar)
        right_bar.pack(side="right")
        ttk.Checkbutton(
            right_bar, text="Dark mode", width=11, variable=self.ui_dark_mode,
            command=self._on_theme_toggle).pack(side="left", padx=(8, 4))
        ttk.Label(
            right_bar, textvariable=self.session_ready_status,
            font=("TkDefaultFont", 9, "bold")).pack(side="left", padx=8)
        ttk.Label(right_bar, textvariable=self.diag_vars["state"]).pack(side="left", padx=(4, 8))

        self._scroll_shell = ttk.Frame(self.root)
        self._scroll_shell.grid(row=1, column=0, sticky="nsew")
        self._scroll_shell.columnconfigure(0, weight=1)
        self._scroll_shell.rowconfigure(0, weight=1)

        self._scroll_canvas = tk.Canvas(self._scroll_shell, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(
            self._scroll_shell, orient="vertical", command=self._scroll_canvas.yview)
        self._scroll_canvas.configure(yscrollcommand=scrollbar.set)
        self._scroll_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self._scroll_body = ttk.Frame(self._scroll_canvas)
        self._scroll_body.columnconfigure(0, weight=1, uniform="main")
        self._scroll_body.columnconfigure(1, weight=1, uniform="main")
        self._scroll_window = self._scroll_canvas.create_window(
            (0, 0), window=self._scroll_body, anchor="nw")

        def _on_body_configure(_event=None) -> None:
            self._refresh_scroll_region()

        def _on_canvas_configure(event) -> None:
            self._scroll_canvas.itemconfigure(self._scroll_window, width=event.width)

        self._scroll_body.bind("<Configure>", _on_body_configure)
        self._scroll_canvas.bind("<Configure>", _on_canvas_configure)
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        self.root.bind_all("<Button-4>", self._on_mousewheel)
        self.root.bind_all("<Button-5>", self._on_mousewheel)

        io_row = ttk.Frame(self._scroll_body)
        io_row.grid(row=0, column=0, columnspan=2, sticky="ew")
        io_row.columnconfigure(0, weight=1, uniform="io")
        io_row.columnconfigure(1, weight=1, uniform="io")
        io_row.rowconfigure(0, weight=1)

        mfp = self._frame("MultiFunPlayer input", 0, 0, parent=io_row)
        mfp.columnconfigure(0, minsize=88)
        ttk.Label(mfp, text="Bind address").grid(row=0, column=0, sticky="w")
        ttk.Entry(mfp, textvariable=self.mfp_host, width=16).grid(row=0, column=1, padx=5)
        ttk.Label(mfp, text="Port").grid(row=0, column=2)
        ttk.Spinbox(mfp, from_=1, to=65535, textvariable=self.mfp_port, width=7).grid(row=0, column=3, padx=5)
        ttk.Button(mfp, text="Start listener", command=self.start_listener).grid(row=1, column=0, pady=8)
        ttk.Button(mfp, text="Stop listener", command=self.listener.stop).grid(row=1, column=1, pady=8)
        ttk.Label(mfp, textvariable=self.mfp_status).grid(row=1, column=2, columnspan=2, sticky="w")
        ttk.Label(mfp, textvariable=self.authored_axes_status, style="Muted.TLabel").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(0, 4))
        ttk.Label(mfp, textvariable=self.timeline_status, style="Muted.TLabel").grid(
            row=3, column=0, columnspan=4, sticky="nw", pady=(0, 4))

        restim = self._frame("ReStim output", 0, 1, parent=io_row)
        restim.columnconfigure(0, minsize=88)
        ttk.Label(restim, text="Primary WS").grid(row=0, column=0, sticky="w")
        ttk.Entry(restim, textvariable=self.restim_host, width=16).grid(row=0, column=1, padx=5)
        ttk.Label(restim, text="Port").grid(row=0, column=2)
        ttk.Spinbox(restim, from_=1, to=65535, textvariable=self.restim_port, width=7).grid(row=0, column=3, padx=5)
        ttk.Button(restim, text="Connect", command=self.connect_restim).grid(row=1, column=0, pady=8)
        ttk.Button(restim, text="Disconnect", command=self.restim.disconnect).grid(row=1, column=1, pady=8)
        ttk.Label(restim, textvariable=self.restim_status).grid(row=1, column=2, columnspan=3, sticky="w")
        ttk.Label(restim, text="Prostate").grid(row=2, column=0, sticky="w")
        ttk.Entry(restim, textvariable=self.prostate_host, width=16).grid(row=2, column=1, padx=5)
        ttk.Label(restim, text="Port").grid(row=2, column=2, sticky="e")
        ttk.Spinbox(restim, from_=1, to=65535, textvariable=self.prostate_port, width=7).grid(row=2, column=3, padx=5)
        ttk.Button(restim, text="Connect", command=self.connect_prostate).grid(row=3, column=0, pady=6)
        ttk.Button(restim, text="Disconnect", command=self.prostate_restim.disconnect).grid(row=3, column=1, pady=6)
        ttk.Label(restim, textvariable=self.prostate_status).grid(row=3, column=2, columnspan=4, sticky="w")

        motion = self._frame("Motion", 1, 0, 2)
        ttk.Label(motion, text="Mode").grid(row=0, column=0, sticky="w")
        mode_box = ttk.Combobox(motion, textvariable=self.mode, state="readonly", width=38,
                                values=[mode.value for mode in MotionMode])
        mode_box.grid(row=0, column=1, columnspan=3, sticky="w", padx=6)
        mode_box.bind("<<ComboboxSelected>>", lambda _event: self.apply_config())
        fields = (
            ("Points per second", self.rate, 1, 200, 1),
            ("Look-ahead / delay (s)", self.lookahead, 0.05, 10, 0.05),
            ("Base volume", self.volume, 0, 1, 0.01),
            ("Minimum distance from center", self.minimum_radius, 0, 0.9, 0.05),
            ("Speed threshold (%)", self.speed_threshold, 0, 100, 1),
            ("Direction change probability", self.direction_probability, 0, 1, 0.05),
        )
        for index, (label, variable, low, high, step) in enumerate(fields):
            row = 1 + index // 3
            col = (index % 3) * 2
            ttk.Label(motion, text=label).grid(row=row, column=col, sticky="w", pady=4)
            ttk.Spinbox(motion, from_=low, to=high, increment=step, textvariable=variable,
                        width=10, command=self.apply_config).grid(row=row, column=col + 1, padx=6, sticky="w")
        ttk.Button(motion, text="Apply", command=self.apply_config).grid(row=3, column=5, sticky="e")
        ttk.Checkbutton(motion, text="Add smooth L0 position variation", variable=self.jitter_enabled,
                        command=self.apply_config).grid(row=3, column=0, sticky="w", pady=(5, 2))
        ttk.Label(motion, text="Maximum shift (±L0)").grid(row=3, column=1, sticky="e")
        ttk.Spinbox(motion, from_=0, to=.20, increment=.005,
                    textvariable=self.jitter_amplitude, width=8,
                    command=self.apply_config).grid(row=3, column=2, sticky="w")
        ttk.Label(motion, text="Variation cycle (s)").grid(row=3, column=3, sticky="e")
        ttk.Spinbox(motion, from_=.05, to=30, increment=.05,
                    textvariable=self.jitter_cycle_seconds, width=8,
                    command=self.apply_config).grid(row=3, column=4, sticky="w")
        ttk.Checkbutton(motion, text="Scale optional effects with speed",
                        variable=self.speed_linked_variation,
                        command=self.apply_config).grid(row=4, column=0, sticky="w", pady=(3, 0))
        ttk.Label(motion, text="Full effects at speed (%)").grid(row=4, column=1, sticky="e")
        ttk.Spinbox(motion, from_=1, to=100, increment=1,
                    textvariable=self.variation_full_speed_percent, width=8,
                    command=self.apply_config).grid(row=4, column=2, sticky="w")
        ttk.Label(motion, text="Response time (s)").grid(row=4, column=3, sticky="e")
        ttk.Spinbox(motion, from_=.05, to=10, increment=.05,
                    textvariable=self.variation_fade_seconds, width=8,
                    command=self.apply_config).grid(row=4, column=4, sticky="w")
        ttk.Label(motion, textvariable=self.variation_depth_live,
                  style="Muted.TLabel").grid(row=4, column=5, sticky="w", padx=8)
        ttk.Label(motion, text="Spatial response").grid(row=5, column=0, sticky="w", pady=(4, 0))
        ttk.Combobox(motion, textvariable=self.four_phase_spatial_curve,
                     values=("Linear", "S-curve", "Endpoint emphasis", "Centre emphasis"),
                     state="readonly", width=18).grid(row=5, column=1, sticky="w")
        ttk.Label(motion, text="Blend (1 = 100%)").grid(row=5, column=3, sticky="e")
        ttk.Spinbox(motion, from_=0, to=1, increment=.05,
                    textvariable=self.four_phase_spatial_blend, width=8,
                    command=self.apply_config).grid(row=5, column=4, sticky="w")
        ttk.Checkbutton(motion, text="Boost volume near stroke reversal",
                        variable=self.four_phase_reversal_emphasis).grid(row=6, column=0, sticky="w")
        ttk.Label(motion, text="Window either side (s)").grid(row=6, column=1, sticky="e")
        ttk.Spinbox(motion, from_=.05, to=1.5, increment=.05,
                    textvariable=self.four_phase_reversal_window, width=8).grid(row=6, column=2, sticky="w")
        ttk.Label(motion, text="Current-volume boost (+×)").grid(row=6, column=3, sticky="e")
        ttk.Spinbox(motion, from_=0, to=1, increment=.05,
                    textvariable=self.four_phase_reversal_strength, width=8).grid(row=6, column=4, sticky="w")
        ttk.Label(motion, textvariable=self.four_phase_reversal_live,
                  style="Muted.TLabel").grid(row=6, column=5, sticky="w", padx=8)
        ttk.Checkbutton(motion, text="Stroke-phase texture",
                        variable=self.four_phase_stroke_phase_texture).grid(row=7, column=0, sticky="w")
        ttk.Label(motion, text="L0 rising volume ×").grid(row=7, column=1, sticky="e")
        ttk.Spinbox(motion, from_=.8, to=1, increment=.01,
                    textvariable=self.motion_rising_volume_multiplier, width=8).grid(row=7, column=2, sticky="w")
        ttk.Label(motion, text="L0 falling volume ×").grid(row=7, column=3, sticky="e")
        ttk.Spinbox(motion, from_=.8, to=1, increment=.01,
                    textvariable=self.motion_falling_volume_multiplier, width=8).grid(row=7, column=4, sticky="w")
        ttk.Label(motion, text="Shared by 3-phase and 4-phase",
                   style="Muted.TLabel").grid(row=7, column=5, sticky="w", padx=8)
        ttk.Button(motion, text="Explain these controls", command=self.show_motion_guide).grid(
            row=8, column=5, sticky="e", pady=(5, 0))

        volume_frame = self._frame("Volume response", 2, 0, 2)
        ttk.Checkbutton(volume_frame, text="Reduce volume when motion stops",
                        variable=self.dynamic_volume, command=self.apply_config).grid(row=0, column=0, sticky="w")
        volume_fields = (("Rest level", self.volume_rest_level, 0, 1, 0.05),
                         ("Ramp | Speed ratio", self.volume_ratio, 10, 40, 1),
                         ("Return ramp (s)", self.volume_ramp_up, 0, 10, 0.1))
        for index, (label, variable, low, high, step) in enumerate(volume_fields):
            col = 1 + index * 2
            ttk.Label(volume_frame, text=label).grid(row=0, column=col, padx=(18, 4))
            ttk.Spinbox(volume_frame, from_=low, to=high, increment=step,
                        textvariable=variable, width=8, command=self.apply_config).grid(row=0, column=col + 1)
        ttk.Label(volume_frame, text="Primary volume ceiling").grid(
            row=0, column=7, padx=(18, 4))
        ttk.Spinbox(volume_frame, from_=0, to=1, increment=.05,
                    textvariable=self.four_phase_volume_ceiling, width=8).grid(
                        row=0, column=8)

        ramp_frame = self._frame("Media volume ramp", 3, 0, 2)
        ttk.Checkbutton(ramp_frame, text="Enable media volume ramp",
                        variable=self.media_volume_ramp_enabled,
                        command=self._save_settings).grid(row=0, column=0, sticky="w")
        ttk.Label(ramp_frame, text="Floor 1").grid(row=0, column=1, padx=(18, 4))
        ttk.Spinbox(ramp_frame, from_=0, to=1, increment=.05,
                    textvariable=self.media_volume_ramp_floor, width=8,
                    command=self._on_media_ramp_levels_changed).grid(
                        row=0, column=2, sticky="w")
        ttk.Label(ramp_frame, text="Ceiling 1").grid(row=0, column=3, padx=(18, 4))
        ttk.Spinbox(ramp_frame, from_=0, to=1, increment=.05,
                    textvariable=self.media_volume_ramp_ceiling, width=8,
                    command=self._on_media_ramp_levels_changed).grid(
                        row=0, column=4, sticky="w")
        ttk.Label(ramp_frame, text="Curve").grid(row=0, column=5, padx=(18, 4))
        ramp_curve_box = ttk.Combobox(ramp_frame, textvariable=self.media_volume_ramp_curve,
                     values=RAMP_CURVE_NAMES, state="readonly", width=14)
        ramp_curve_box.grid(row=0, column=6, sticky="w")
        ramp_curve_box.bind("<<ComboboxSelected>>", self._on_media_ramp_curve_selected)
        self.media_ramp_curve_preview = tk.Canvas(
            ramp_frame, width=280, height=96, highlightthickness=1,
            highlightbackground=THEME_COLORS["light"]["preview_highlight"],
            background=THEME_COLORS["light"]["preview_bg"])
        self.media_ramp_curve_preview.grid(row=0, column=7, rowspan=3, sticky="nw",
                                           padx=(12, 0))

        ttk.Checkbutton(
            ramp_frame, text="Use extra time waypoints",
            variable=self.media_volume_ramp_waypoints_enabled,
            command=self._on_media_ramp_waypoints_toggled).grid(
                row=1, column=0, sticky="w", pady=(4, 0))

        extra_levels = (
            ("floor2", "Floor 2", self.media_volume_ramp_floor2, 1, 1),
            ("floor3", "Floor 3", self.media_volume_ramp_floor3, 1, 3),
            ("ceiling2", "Ceiling 2", self.media_volume_ramp_ceiling2, 1, 5),
            ("ceiling3", "Ceiling 3", self.media_volume_ramp_ceiling3, 2, 1),
        )
        self._media_ramp_extra_level_widgets = {}
        for key, label_text, variable, row, col in extra_levels:
            label = ttk.Label(ramp_frame, text=label_text)
            label.grid(row=row, column=col, padx=(18, 4), pady=(4, 0))
            spin = ttk.Spinbox(
                ramp_frame, from_=0, to=1, increment=.05,
                textvariable=variable, width=8,
                command=self._on_media_ramp_levels_changed)
            spin.grid(row=row, column=col + 1, sticky="w", pady=(4, 0))
            self._media_ramp_extra_level_widgets[key] = [label, spin]

        waypoint_box = ttk.Frame(ramp_frame)
        waypoint_box.grid(row=3, column=0, columnspan=8, sticky="we", pady=(6, 0))
        self.media_ramp_waypoint_tree = ttk.Treeview(
            waypoint_box, columns=("time", "level", "curve"), show="headings", height=4)
        self.media_ramp_waypoint_tree.heading("time", text="Time")
        self.media_ramp_waypoint_tree.heading("level", text="Level")
        self.media_ramp_waypoint_tree.heading("curve", text="Curve")
        self.media_ramp_waypoint_tree.column("time", width=80, anchor="w")
        self.media_ramp_waypoint_tree.column("level", width=90, anchor="w")
        self.media_ramp_waypoint_tree.column("curve", width=110, anchor="w")
        self.media_ramp_waypoint_tree.pack(side="left", fill="x", expand=True)
        wp_buttons = ttk.Frame(waypoint_box)
        wp_buttons.pack(side="left", padx=(8, 0))
        ttk.Button(wp_buttons, text="Add…",
                   command=self._add_media_ramp_waypoint).pack(fill="x", pady=1)
        ttk.Button(wp_buttons, text="Edit…",
                   command=self._edit_media_ramp_waypoint).pack(fill="x", pady=1)
        ttk.Button(wp_buttons, text="Remove",
                   command=self._remove_media_ramp_waypoint).pack(fill="x", pady=1)
        ttk.Button(wp_buttons, text="Move up",
                   command=lambda: self._move_media_ramp_waypoint(-1)).pack(
                       fill="x", pady=1)
        ttk.Button(wp_buttons, text="Move down",
                   command=lambda: self._move_media_ramp_waypoint(1)).pack(
                       fill="x", pady=1)
        ttk.Button(wp_buttons, text="Import…",
                   command=self._import_media_ramp_waypoints).pack(fill="x", pady=1)
        ttk.Button(wp_buttons, text="Export…",
                   command=self._export_media_ramp_waypoints).pack(fill="x", pady=1)

        ttk.Label(ramp_frame, textvariable=self.media_ramp_status,
                  style="Muted.TLabel").grid(row=4, column=0, columnspan=8, sticky="w",
                                          pady=(4, 0))
        ttk.Label(ramp_frame, text=(
            "Scales primary and prostate volume from Timeline Absolute (T0/T1). "
            "Simple mode uses Floor 1 → Ceiling 1 over full media progress with the "
            "global Curve. Extra time waypoints target Floor 1–3 / Ceiling 1–3 at "
            "absolute media times; each waypoint's Curve shapes the segment arriving "
            "at that point. After the last waypoint, gain holds. Floor 2/3 and "
            "Ceiling 2/3 controls appear when used. Import/Export supports Vector JSON "
            "or OFS volume .funscript (baked actions + bookmarks + re-edit metadata). "
            "Distinct from motion rest-volume."),
            style="Muted.TLabel", wraplength=900).grid(
                row=5, column=0, columnspan=8, sticky="w", pady=(2, 0))
        self._refresh_media_ramp_waypoint_tree()
        self._refresh_extra_level_controls()
        self._redraw_media_ramp_curve_preview()

        events_frame = self._frame("Custom events", 4, 0, 2)
        ttk.Checkbutton(events_frame, text="Enable custom events",
                        variable=self.events_enabled,
                        command=self._on_events_enabled_changed).grid(
                            row=0, column=0, sticky="w")
        ttk.Entry(events_frame, textvariable=self.events_file_path,
                  width=56).grid(row=0, column=1, sticky="we", padx=8)
        ttk.Button(events_frame, text="Browse…",
                   command=self._browse_events_file).grid(row=0, column=2, sticky="w")
        ttk.Button(events_frame, text="Reload",
                   command=self._reload_events_file).grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(events_frame, text="Definitions").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(events_frame, textvariable=self.events_definitions_path,
                  width=56).grid(row=1, column=1, sticky="we", padx=8, pady=(4, 0))
        ttk.Button(events_frame, text="Browse…",
                   command=self._browse_events_definitions).grid(
                       row=1, column=2, sticky="w", pady=(4, 0))
        status_row = ttk.Frame(events_frame)
        status_row.grid(row=2, column=0, columnspan=4, sticky="we", pady=(4, 0))
        self.events_status_text = tk.Text(
            status_row, height=2, wrap="word", relief="solid", borderwidth=1,
            font=("TkDefaultFont", 9),
            background=THEME_COLORS["light"]["text_bg"],
            foreground=THEME_COLORS["light"]["text_fg"])
        self.events_status_text.pack(side="left", fill="x", expand=True)
        self.events_status_text.insert("1.0", self.events_status.get())
        self.events_status_text.configure(state="disabled")
        ttk.Button(status_row, text="Copy",
                   command=self._copy_events_status).pack(side="right", padx=(6, 0))
        ttk.Label(events_frame, text=(
            "Plays funscript-tools .events.yml on the delayed media timeline (T0/T1) "
            "after the media volume ramp, and/or live EVT triggers on the same "
            "MFP TCP/UDP port (schedule = receive + look-ahead; no T0 needed). "
            "For Journey/Fap-Hero leave the events file empty and keep definitions loaded. "
            "Axes: volume, volume-prostate, "
            "pulse_frequency, pulse_width, frequency, alpha, beta, e1–e4, "
            "sensor_suppression (S1). While enabled, S1 defaults to 0% "
            "(sensors active, or authored S1 if routed); edge mutes to 50%, "
            "cum/ruin to 100%. "
            "Leave Definitions blank to use Vector's bundled defs (includes S1). "
            "3P defs (alpha/beta) and 4P defs (e1–e4) require the matching ReStim mode. "
            "Do not also bake the same events offline into authored funscripts (double apply)."),
            style="Muted.TLabel", wraplength=900).grid(
                row=3, column=0, columnspan=4, sticky="w", pady=(2, 0))
        events_frame.columnconfigure(1, weight=1)

        frequency_frame = self._frame("Frequency", 5, 0, 2)
        ttk.Label(frequency_frame, text="0").grid(row=0, column=0)
        self.frequency_bar = ttk.Progressbar(frequency_frame, orient="horizontal", mode="determinate",
                                             maximum=1.0, length=520)
        self.frequency_bar.grid(row=0, column=1, padx=8)
        ttk.Label(frequency_frame, text="1").grid(row=0, column=2)
        self.frequency_value = tk.StringVar(value="0.0000")
        ttk.Label(frequency_frame, textvariable=self.frequency_value,
                  font=("TkDefaultFont", 10, "bold")).grid(row=0, column=3, padx=8)
        ttk.Label(frequency_frame, text="Ramp level").grid(row=0, column=4, padx=(16, 4))
        ttk.Spinbox(frequency_frame, from_=0, to=1, increment=0.05,
                    textvariable=self.frequency_ramp_level, width=7,
                    command=self.apply_config).grid(row=0, column=5)
        ttk.Label(frequency_frame, text="Ramp | Speed ratio").grid(row=0, column=6, padx=(16, 4))
        ttk.Spinbox(frequency_frame, from_=1, to=10, increment=1,
                    textvariable=self.frequency_ratio, width=7,
                    command=self.apply_config).grid(row=0, column=7)

        pulse_frame = self._frame("Pulse frequency", 6, 0, 2)
        ttk.Label(pulse_frame, text="0").grid(row=0, column=0)
        self.pulse_frequency_bar = RangeBar(pulse_frame)
        self._range_bars.append(self.pulse_frequency_bar)
        self.pulse_frequency_bar.grid(row=0, column=1, padx=8)
        ttk.Label(pulse_frame, text="1").grid(row=0, column=2)
        self.pulse_frequency_value = tk.StringVar(value="0.0000")
        ttk.Label(pulse_frame, textvariable=self.pulse_frequency_value,
                  font=("TkDefaultFont", 10, "bold")).grid(row=0, column=3, padx=8)
        pulse_fields = (("Min", self.pulse_frequency_min),
                        ("Max", self.pulse_frequency_max),
                        ("Speed | Alpha ratio", self.pulse_frequency_ratio))
        for index, (label, variable) in enumerate(pulse_fields):
            col = 4 + index * 2
            ttk.Label(pulse_frame, text=label).grid(row=0, column=col, padx=(12, 4))
            ttk.Spinbox(pulse_frame, from_=0 if index < 2 else 1,
                        to=1 if index < 2 else 10, increment=0.05 if index < 2 else 1,
                        textvariable=variable, width=7,
                        command=self.apply_config).grid(row=0, column=col + 1)

        rise_frame = self._frame("Pulse rise time", 7, 0, 2)
        ttk.Label(rise_frame, text="0 sharp").grid(row=0, column=0)
        self.pulse_rise_bar = RangeBar(rise_frame)
        self._range_bars.append(self.pulse_rise_bar)
        self.pulse_rise_bar.grid(row=0, column=1, padx=8)
        ttk.Label(rise_frame, text="1 soft").grid(row=0, column=2)
        self.pulse_rise_value = tk.StringVar(value="0.0000")
        ttk.Label(rise_frame, textvariable=self.pulse_rise_value,
                  font=("TkDefaultFont", 10, "bold")).grid(row=0, column=3, padx=8)
        rise_fields = (("Min", self.pulse_rise_min), ("Max", self.pulse_rise_max),
                       ("Inv Ramp | Inv Speed", self.pulse_rise_ratio))
        for index, (label, variable) in enumerate(rise_fields):
            col = 4 + index * 2
            ttk.Label(rise_frame, text=label).grid(row=0, column=col, padx=(12, 4))
            ttk.Spinbox(rise_frame, from_=0 if index < 2 else 1,
                        to=1 if index < 2 else 10, increment=0.05 if index < 2 else 1,
                        textvariable=variable, width=7,
                        command=self.apply_config).grid(row=0, column=col + 1)
        width_frame = self._frame("Pulse width", 8, 0, 2)
        ttk.Label(width_frame, text="0 narrow").grid(row=0, column=0)
        self.pulse_width_bar = RangeBar(width_frame)
        self._range_bars.append(self.pulse_width_bar)
        self.pulse_width_bar.grid(row=0, column=1, padx=8)
        ttk.Label(width_frame, text="1 wide").grid(row=0, column=2)
        self.pulse_width_value = tk.StringVar(value="0.0000")
        ttk.Label(width_frame, textvariable=self.pulse_width_value,
                  font=("TkDefaultFont", 10, "bold")).grid(row=0, column=3, padx=8)
        width_fields = (("Min", self.pulse_width_min), ("Max", self.pulse_width_max),
                        ("Speed | Inv L0 ratio", self.pulse_width_ratio))
        for index, (label, variable) in enumerate(width_fields):
            col = 4 + index * 2
            ttk.Label(width_frame, text=label).grid(row=0, column=col, padx=(12, 4))
            ttk.Spinbox(width_frame, from_=0 if index < 2 else 1,
                        to=1 if index < 2 else 10, increment=0.05 if index < 2 else 1,
                        textvariable=variable, width=7,
                        command=self.apply_config).grid(row=0, column=col + 1)

        prostate = self._frame("Prostate controls", 9, 0, 2)
        self.prostate_bars = {}
        self.prostate_values = {}
        for row, (label, key) in enumerate((("Alpha-prostate", "alpha_prostate"),
                                             ("Beta-prostate", "beta_prostate"),
                                             ("Volume-prostate", "volume_prostate"))):
            ttk.Label(prostate, text=label, width=18).grid(row=row, column=0, sticky="w")
            ttk.Label(prostate, text="0").grid(row=row, column=1)
            bar = ttk.Progressbar(prostate, orient="horizontal", mode="determinate",
                                  maximum=1.0, length=520)
            bar.grid(row=row, column=2, padx=8)
            ttk.Label(prostate, text="1").grid(row=row, column=3)
            value = tk.StringVar(value="0.0000")
            ttk.Label(prostate, textvariable=value, width=8,
                      font=("TkDefaultFont", 10, "bold")).grid(row=row, column=4, padx=8)
            self.prostate_bars[key] = bar
            self.prostate_values[key] = value
        ttk.Label(prostate, text="Return arc scale").grid(row=0, column=5, padx=(14, 4))
        ttk.Spinbox(prostate, from_=0, to=1, increment=.05, textvariable=self.prostate_narrow_ratio,
                    width=7, command=self.apply_config).grid(row=0, column=6)
        ttk.Label(prostate, text="Side arc depth").grid(row=2, column=5, padx=(14, 4))
        ttk.Spinbox(prostate, from_=0, to=1, increment=.05, textvariable=self.prostate_arc_depth,
                    width=7, command=self.apply_config).grid(row=2, column=6)
        ttk.Label(prostate, text="Stroke threshold").grid(row=1, column=5, padx=(14, 4))
        ttk.Spinbox(prostate, from_=0, to=1, increment=.05, textvariable=self.prostate_threshold,
                    width=7, command=self.apply_config).grid(row=1, column=6)
        ttk.Label(prostate, text="Volume ratio multiplier").grid(row=0, column=7, padx=(14, 4))
        ttk.Spinbox(prostate, from_=1, to=3, increment=.1, textvariable=self.prostate_volume_multiplier,
                    width=7, command=self.apply_config).grid(row=0, column=8)
        ttk.Label(prostate, text="Volume rest level").grid(row=1, column=7, padx=(14, 4))
        ttk.Spinbox(prostate, from_=0, to=1, increment=.05, textvariable=self.prostate_rest_level,
                    width=7, command=self.apply_config).grid(row=1, column=8)
        ttk.Label(prostate, text="Timing phase (degrees)").grid(row=2, column=7, padx=(14, 4))
        ttk.Spinbox(prostate, from_=-90, to=90, increment=5,
                    textvariable=self.prostate_phase_degrees, width=7,
                    command=self.apply_config).grid(row=2, column=8)

        four_phase = self._frame("Four-phase primary motion", 10, 0, 2)
        self.four_phase_bars, self.four_phase_values = [], []
        for row, label in enumerate(("A — top", "B", "C", "D — bottom")):
            ttk.Label(four_phase, text=label, width=18).grid(row=row, column=0, sticky="w")
            ttk.Label(four_phase, text="0").grid(row=row, column=1)
            bar = ttk.Progressbar(four_phase, orient="horizontal", mode="determinate",
                                  maximum=1.0, length=520)
            bar.grid(row=row, column=2, padx=8)
            ttk.Label(four_phase, text="1").grid(row=row, column=3)
            value = tk.StringVar(value="0.0000")
            ttk.Label(four_phase, textvariable=value, width=8,
                      font=("TkDefaultFont", 10, "bold")).grid(row=row, column=4, padx=8)
            self.four_phase_bars.append(bar); self.four_phase_values.append(value)
        ttk.Label(four_phase,
                  text="Last transmitted E1-E4 Primary output.",
                  style="Warning.TLabel").grid(row=0, column=5, rowspan=4, sticky="w", padx=18)
        ttk.Label(four_phase, text="Return depth").grid(row=4, column=0, sticky="w")
        ttk.Spinbox(four_phase, from_=0, to=1, increment=.05,
                    textvariable=self.four_phase_return_depth, width=7).grid(
                        row=4, column=1, sticky="w")
        ttk.Label(four_phase, text="Signed: primary +1.00 | return −depth | unused 0.00") \
            .grid(row=4, column=2, columnspan=4, sticky="w", padx=8)
        ttk.Checkbutton(four_phase, text="Reverse L0 direction", variable=self.four_phase_invert).grid(row=5, column=0, sticky="w")
        ttk.Checkbutton(four_phase, text="Add slow volume variation", variable=self.four_phase_volume_modulation).grid(row=5, column=3, sticky="w")
        ttk.Label(four_phase, text="Maximum addition").grid(row=5, column=4, sticky="e")
        ttk.Spinbox(four_phase, from_=0, to=1, increment=.05, textvariable=self.four_phase_volume_headroom, width=7).grid(row=5, column=5, sticky="w")
        ttk.Label(four_phase, text="Volume cycle (min)").grid(row=6, column=4, sticky="e")
        ttk.Spinbox(four_phase, from_=.5, to=30, increment=.5, textvariable=self.four_phase_volume_cycle, width=7).grid(row=6, column=5, sticky="w")
        ttk.Label(four_phase, text="Base crossover width").grid(row=6, column=0, sticky="w")
        ttk.Spinbox(four_phase, from_=.05, to=1, increment=.05,
                    textvariable=self.four_phase_crossover_width, width=7).grid(row=6, column=1, sticky="w")
        ttk.Label(four_phase, text="Crossover curve").grid(row=6, column=2, sticky="e", padx=(8, 4))
        ttk.Combobox(four_phase, textvariable=self.four_phase_crossover_curve,
                     values=("Cosine", "Linear", "Ease In", "Ease Out", "S-curve"),
                     state="readonly", width=10).grid(row=6, column=3, sticky="w")
        ttk.Label(four_phase, text="Crossover sharpness").grid(row=7, column=0, sticky="w")
        ttk.Spinbox(four_phase, from_=.2, to=5, increment=.1,
                    textvariable=self.four_phase_crossover_sharpness, width=7).grid(row=7, column=1, sticky="w")
        ttk.Label(four_phase, text="Signalling sequence").grid(row=7, column=2, sticky="e", padx=(8, 4))
        order_box = ttk.Combobox(four_phase, textvariable=self.electrode_order,
                                 values=ELECTRODE_ORDERS, state="readonly", width=7)
        order_box.grid(row=7, column=3, sticky="w")
        order_box.bind("<<ComboboxSelected>>", lambda _event: self._electrode_order_changed())
        ttk.Label(four_phase, text="Sequence blend (live)").grid(row=7, column=4, sticky="e", padx=(8, 4))
        self.electrode_morph_bar = ttk.Progressbar(
            four_phase, orient="horizontal", mode="determinate", maximum=1.0, length=120)
        self.electrode_morph_bar.grid(row=7, column=5, sticky="w")
        ttk.Checkbutton(four_phase, text="Change crossover width with speed",
                        variable=self.four_phase_adaptive_crossover).grid(
                            row=8, column=0, sticky="w")
        ttk.Label(four_phase, text="Low-speed width").grid(row=8, column=1, sticky="e")
        ttk.Spinbox(four_phase, from_=.05, to=1, increment=.05,
                    textvariable=self.four_phase_slow_crossover_width,
                    width=7).grid(row=8, column=2, sticky="w")
        ttk.Label(four_phase, text="High-speed width").grid(row=8, column=3, sticky="e")
        ttk.Spinbox(four_phase, from_=.05, to=1, increment=.05,
                    textvariable=self.four_phase_fast_crossover_width,
                    width=7).grid(row=8, column=4, sticky="w")
        ttk.Label(four_phase, textvariable=self.four_phase_effective_crossover_width,
                  width=12).grid(row=8, column=5, sticky="w", padx=8)
        ttk.Checkbutton(four_phase, text="Use different return-stroke crossover",
                        variable=self.four_phase_directional_trajectory).grid(
                            row=9, column=0, sticky="w")
        ttk.Label(four_phase, text="Return width ×").grid(row=9, column=1, sticky="e")
        ttk.Spinbox(four_phase, from_=.2, to=3, increment=.05,
                    textvariable=self.four_phase_reverse_width_scale,
                    width=7).grid(row=9, column=2, sticky="w")
        ttk.Label(four_phase, text="Return curve").grid(row=9, column=3, sticky="e")
        ttk.Combobox(four_phase, textvariable=self.four_phase_reverse_curve,
                     values=("Cosine", "Linear", "Ease In", "Ease Out", "S-curve"),
                     state="readonly", width=10).grid(row=9, column=4, sticky="w")
        reverse_sharpness = ttk.Frame(four_phase)
        reverse_sharpness.grid(row=9, column=5, sticky="w", padx=8)
        ttk.Label(reverse_sharpness, text="Sharpness").pack(side="left")
        ttk.Spinbox(reverse_sharpness, from_=.2, to=5, increment=.1,
                    textvariable=self.four_phase_reverse_sharpness,
                    width=7).pack(side="left", padx=(4, 0))
        ttk.Label(four_phase, text="Spatial model").grid(row=10, column=0, sticky="w")
        ttk.Combobox(four_phase, textvariable=self.four_phase_spatial_model,
                     values=SPATIAL_MODELS, state="readonly", width=14).grid(
                         row=10, column=1, sticky="w")
        ttk.Label(four_phase, text="Tip retention at full depth").grid(
            row=10, column=2, sticky="e", padx=(8, 4))
        ttk.Spinbox(four_phase, from_=0, to=1, increment=.05,
                    textvariable=self.four_phase_tip_retention, width=7).grid(
                        row=10, column=3, sticky="w")
        ttk.Label(four_phase, text="Spread softness").grid(
            row=10, column=4, sticky="e")
        ttk.Spinbox(four_phase, from_=0, to=1, increment=.05,
                    textvariable=self.four_phase_spread_softness, width=7).grid(
                        row=10, column=5, sticky="w")
        ttk.Label(four_phase, text="Full-depth capture").grid(
            row=11, column=0, sticky="w")
        ttk.Spinbox(four_phase, from_=0, to=.20, increment=.01,
                    textvariable=self.four_phase_full_depth_capture, width=7).grid(
                        row=11, column=1, sticky="w")
        ttk.Label(four_phase, textvariable=self.four_phase_model_live,
                  style="Warning.TLabel").grid(
                      row=11, column=2, columnspan=4, sticky="w", pady=(2, 4))
        ttk.Label(four_phase, text="Change width through each stroke").grid(row=12, column=0, sticky="w")
        ttk.Label(four_phase, text="Accelerating width ×").grid(row=12, column=1, sticky="e")
        ttk.Spinbox(four_phase, from_=.2, to=3, increment=.05,
                    textvariable=self.four_phase_acceleration_width_scale,
                    width=7).grid(row=12, column=2, sticky="w")
        ttk.Label(four_phase, text="Deceleration width ×").grid(row=12, column=3, sticky="e")
        ttk.Spinbox(four_phase, from_=.2, to=3, increment=.05,
                    textvariable=self.four_phase_deceleration_width_scale,
                    width=7).grid(row=12, column=4, sticky="w")
        ttk.Label(four_phase, textvariable=self.four_phase_stroke_phase_live,
                  width=20).grid(row=12, column=5, sticky="w", padx=8)
        ttk.Checkbutton(four_phase, text="Offset A/B versus C/D timing",
                        variable=self.four_phase_group_delay).grid(row=13, column=0, sticky="w")
        ttk.Label(four_phase, text="Group delay (ms; +A/B later)").grid(row=13, column=1, sticky="e")
        ttk.Spinbox(four_phase, from_=-300, to=300, increment=10,
                    textvariable=self.four_phase_group_delay_ms, width=7).grid(
                        row=13, column=2, sticky="w")
        ttk.Label(four_phase, text="Transition (s)").grid(row=13, column=3, sticky="e")
        ttk.Spinbox(four_phase, from_=.1, to=5, increment=.1,
                    textvariable=self.four_phase_group_delay_transition, width=7).grid(
                        row=13, column=4, sticky="w")
        ttk.Label(four_phase, textvariable=self.four_phase_group_delay_live,
                  style="Muted.TLabel").grid(row=13, column=5, sticky="w")
        ttk.Checkbutton(four_phase, text="Bias sequence within each stroke",
                        variable=self.four_phase_moving_sequence).grid(row=14, column=0, sticky="w")
        ttk.Label(four_phase, text="Maximum blend").grid(row=14, column=1, sticky="e")
        ttk.Spinbox(four_phase, from_=0, to=1, increment=.05,
                    textvariable=self.four_phase_moving_sequence_depth, width=7).grid(
                        row=14, column=2, sticky="w")
        ttk.Label(four_phase, text="Stroke portion").grid(row=14, column=3, sticky="e")
        ttk.Spinbox(four_phase, from_=.1, to=1, increment=.05,
                    textvariable=self.four_phase_moving_sequence_width, width=7).grid(
                        row=14, column=4, sticky="w")
        ttk.Label(four_phase, textvariable=self.four_phase_moving_sequence_live,
                   style="Muted.TLabel").grid(row=14, column=5, sticky="w")
        ttk.Button(four_phase, text="Explain these controls",
                   command=self.show_four_phase_guide).grid(
                       row=5, column=1, columnspan=2, sticky="w", padx=(8, 0))
        self.four_phase_signed_values = []
        for offset, label in enumerate(("A signed", "B signed", "C signed", "D signed")):
            row = offset + 15
            ttk.Label(four_phase, text=label, width=18).grid(row=row, column=0, sticky="w")
            value = tk.StringVar(value="+0.0000")
            ttk.Label(four_phase, textvariable=value, width=10,
                      font=("TkDefaultFont", 10, "bold")).grid(row=row, column=1, sticky="w")
            self.four_phase_signed_values.append(value)
        ttk.Label(four_phase, text="Conceptual relative potentials (−1 to +1); not T-code") \
            .grid(row=15, column=2, columnspan=4, sticky="w", padx=8)
        ttk.Separator(four_phase, orient="horizontal").grid(
            row=19, column=0, columnspan=6, sticky="ew", pady=7)
        self.four_phase_potential_bars, self.four_phase_potential_values = [], []
        for offset, label in enumerate(("E1 / A potential", "E2 / B potential",
                                        "E3 / C potential", "E4 / D potential")):
            row = offset + 20
            ttk.Label(four_phase, text=label, width=18).grid(row=row, column=0, sticky="w")
            ttk.Label(four_phase, text="0").grid(row=row, column=1)
            bar = ttk.Progressbar(four_phase, orient="horizontal", mode="determinate",
                                  maximum=1.0, length=520)
            bar.grid(row=row, column=2, padx=8)
            ttk.Label(four_phase, text="1").grid(row=row, column=3)
            value = tk.StringVar(value="0.0000")
            ttk.Label(four_phase, textvariable=value, width=8,
                      font=("TkDefaultFont", 10, "bold")).grid(row=row, column=4, padx=8)
            self.four_phase_potential_bars.append(bar)
            self.four_phase_potential_values.append(value)
        self.four_phase_roles = tk.StringVar(value="Primary -- | preferred return --")
        ttk.Label(four_phase, textvariable=self.four_phase_roles,
                  style="Warning.TLabel").grid(row=20, column=5, rowspan=4, sticky="w", padx=18)
        ttk.Checkbutton(four_phase,
                        text="Send E1–E4 visual test (FOC-Stim hardware MUST be disconnected)",
                        variable=self.send_four_phase_visual,
                        command=self._four_phase_send_toggle).grid(
                            row=24, column=0, columnspan=4, sticky="w", pady=(8, 2))
        ttk.Label(four_phase, textvariable=self.four_phase_status).grid(
            row=24, column=4, columnspan=2, sticky="w", padx=8)
        # The internal commissioning plots remain available to the refresh code,
        # but are intentionally hidden in the publication UI.
        for hidden_row in (*range(0, 5), *range(15, 20), 24):
            for widget in four_phase.grid_slaves(row=hidden_row):
                widget.grid_remove()

        controller = self._frame("Xbox controller", 11, 0, 2)
        ttk.Checkbutton(controller, text="Enable controller controls",
                        variable=self.controller_enabled,
                        command=self._controller_enabled_changed).grid(row=0, column=0, sticky="w", padx=6)
        ttk.Label(controller, textvariable=self.controller_status,
                  font=("TkDefaultFont", 10, "bold"), width=22).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(controller, text="Direct Xbox input (works without focus)",
                        variable=self.direct_controller_enabled).grid(row=0, column=6, padx=12)
        ttk.Label(controller, text="Step").grid(row=0, column=2, padx=(12, 4))
        ttk.Spinbox(controller, from_=0.005, to=.25, increment=.005,
                    textvariable=self.controller_fine_step, width=7).grid(row=0, column=3)
        ttk.Label(controller, text="Phase step (degrees)").grid(row=0, column=4, padx=(18, 4))
        ttk.Spinbox(controller, from_=1, to=45, increment=1,
                    textvariable=self.prostate_phase_step, width=7).grid(row=0, column=5)
        ttk.Label(controller, text="X / Page Up: prostate phase ahead | Y / Page Down: prostate phase behind") \
            .grid(row=2, column=0, columnspan=6, sticky="w", padx=6, pady=(2, 2))
        ttk.Label(controller,
                  text="Direct Xbox: D-pad frequency/pulse frequency | LB + D-pad rise/width | RB cycle signalling sequence | X/Y phase | A Resume | B Neutral | Menu Stop") \
            .grid(row=3, column=0, columnspan=7, sticky="w", padx=6, pady=(2, 2))
        ttk.Label(controller,
                  text="W/S Frequency ramp ±  •  A/D Pulse frequency range −/+  •  I/K Rise range +/−  •  J/L Width range −/+  •  Enter Resume  •  Space Neutral  •  Esc Stop") \
            .grid(row=1, column=0, columnspan=6, sticky="w", padx=6, pady=(6, 2))

        variety = self._frame("Rolling Variety", 12, 0, 2)
        ttk.Checkbutton(variety, text="Enable rolling variety", variable=self.variety_enabled,
                        command=self._variety_toggle).grid(row=0, column=0, padx=6)
        for column, (label, variable, cycle) in enumerate((
                ("Frequency 1.0-0.5", self.variety_frequency, self.variety_frequency_cycle),
                ("Pulse frequency +/-0.20", self.variety_pulse_frequency, self.variety_pulse_frequency_cycle),
                ("Rise +/-0.20", self.variety_pulse_rise, self.variety_pulse_rise_cycle),
                ("Width +/-0.20", self.variety_pulse_width, self.variety_pulse_width_cycle),
                ("Phase -45/+45", self.variety_phase, self.variety_phase_cycle),
                ("Sequence carousel (hold min)", self.variety_electrode_morph,
                 self.variety_electrode_morph_cycle)), start=0):
            ttk.Checkbutton(variety, text=label, variable=variable,
                            command=self._variety_toggle).grid(row=1, column=column, padx=8)
            ttk.Spinbox(variety, from_=0.5, to=30, increment=.5, textvariable=cycle,
                        width=6).grid(row=2, column=column, pady=(2, 0))
        ttk.Label(variety, text="Transition sec").grid(row=3, column=5, pady=(3, 0))
        ttk.Spinbox(variety, from_=1, to=15, increment=.5,
                    textvariable=self.variety_electrode_morph_transition_seconds,
                    width=6).grid(row=4, column=5)
        ttk.Label(variety, textvariable=self.variety_status,
                  font=("TkDefaultFont", 10, "bold")).grid(row=0, column=3, columnspan=3, padx=18)

        controls = self._frame("Commissioning controls", 13, 0, 2)
        ttk.Button(controls, text="Neutral", command=self.neutral, width=18).pack(side="left", padx=12)
        ttk.Button(controls, text="Resume", command=self.resume, width=18).pack(side="left", padx=12)
        ttk.Button(controls, text="STOP", command=self.stop, width=18).pack(side="left", padx=12)
        ttk.Label(controls, text="Test without FOCstim hardware connected.").pack(side="right", padx=12)
        self.sections["Commissioning controls"].grid_remove()

        diagnostics = self._frame("Live diagnostics", 14, 0, 2)
        diagnostics.columnconfigure(1, weight=1)
        diagnostics.columnconfigure(3, weight=1)
        labels = (
            ("Raw incoming L0", "raw_l0"), ("Buffered/current-output L0", "output_l0"),
            ("Calculated speed", "speed"), ("Alpha", "alpha"),
            ("Beta", "beta"), ("Buffer fill", "buffer"),
            ("Configured look-ahead", "lookahead"), ("Measured queue delay", "actual_delay"),
            ("Incoming L0 commands", "input_count"), ("Output samples", "output_count"),
            ("Engine state", "state"),
            ("Active calculation", "active_mode"),
            ("Released sample mode", "output_mode"),
            ("Released volume", "output_volume"),
            ("Frequency (0-1)", "frequency"),
            ("Pulse frequency (0-1)", "pulse_frequency"),
            ("Pulse rise time (0-1)", "pulse_rise_time"),
            ("Pulse width (0-1)", "pulse_width"),
            ("Alpha-prostate", "alpha_prostate"),
            ("Beta-prostate", "beta_prostate"),
            ("Volume-prostate", "volume_prostate"),
        )
        for index, (label, key) in enumerate(labels):
            row = index // 2
            col = (index % 2) * 2
            ttk.Label(diagnostics, text=label).grid(row=row, column=col, sticky="w", padx=8, pady=5)
            ttk.Label(diagnostics, textvariable=self.diag_vars[key], font=("TkDefaultFont", 10, "bold")) \
                .grid(row=row, column=col + 1, sticky="w", padx=8, pady=5)

        remote = self._frame("Remote control API", 15, 0, 2)
        ttk.Checkbutton(
            remote, text="Enable LAN control API (no auth — trusted LAN only)",
            variable=self.control_api_enabled,
            command=self._sync_control_api_server).grid(row=0, column=0, columnspan=4, sticky="w", padx=6)
        ttk.Label(remote, text="Bind address").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(remote, textvariable=self.control_api_host, width=16).grid(row=1, column=1, padx=4)
        ttk.Label(remote, text="Port").grid(row=1, column=2, sticky="e")
        ttk.Spinbox(remote, from_=1, to=65535, textvariable=self.control_api_port, width=7).grid(
            row=1, column=3, padx=4, sticky="w")
        ttk.Button(remote, text="Apply bind", command=self._sync_control_api_server).grid(
            row=1, column=4, padx=8)
        ttk.Label(remote, textvariable=self.control_api_status).grid(
            row=2, column=0, columnspan=5, sticky="w", padx=6, pady=(2, 4))
        ttk.Label(
            remote,
            text="Phone browser: GET/POST http://<pc-lan-ip>:<port>/v1/state  ·  WS /v1/stream",
            style="Muted.TLabel").grid(row=3, column=0, columnspan=5, sticky="w", padx=6, pady=(0, 4))

        self._apply_theme()
        self.root.after_idle(self._refresh_scroll_region)

    def _set_mfp_status(self, text: str) -> None:
        self._record_connection_event("MFP", text)
        self.root.after(0, self.mfp_status.set, text)

    @staticmethod
    def _brief_conn_status(text: str) -> str:
        value = (text or "").strip()
        lower = value.lower()
        if lower.startswith("connected"):
            return "Connected"
        if lower.startswith("disconnected"):
            return "Disconnected"
        if lower.startswith("connecting"):
            return "Connecting"
        if lower.startswith("error"):
            return "Error"
        return value.split(" ", 1)[0] if value else "—"

    def _set_restim_status(self, text: str) -> None:
        self._record_connection_event("Primary", text)
        self.root.after(0, self.restim_status.set, text)

    def _set_prostate_status(self, text: str) -> None:
        self._record_connection_event("Prostate", text)
        self.root.after(0, self.prostate_status.set, text)

    def _record_connection_event(self, source: str, text: str) -> None:
        if self._last_connection_event.get(source) == text:
            return
        self._last_connection_event[source] = text
        stamp = time.strftime("%H:%M:%S")
        self._connection_events.append(f"{stamp}  {source}: {text}")

    def show_connection_log(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Connection and recovery log")
        window.geometry("760x360")
        window.minsize(560, 260)
        body = ttk.Frame(window, padding=10)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=(
            "Recent connection changes. A quiet script is reported separately from a failed listener."
        )).pack(anchor="w", pady=(0, 8))
        text_box = tk.Text(body, wrap="word", height=14, state="normal")
        text_box.pack(fill="both", expand=True)
        text_box.insert("1.0", "\n".join(self._connection_events) or "No connection events yet.")
        text_box.configure(state="disabled")
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(8, 0))

        def copy_log() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(self._connection_events))

        def clear_log() -> None:
            self._connection_events.clear()
            self._last_connection_event.clear()
            text_box.configure(state="normal")
            text_box.delete("1.0", "end")
            text_box.insert("1.0", "Connection log cleared.")
            text_box.configure(state="disabled")

        ttk.Button(buttons, text="Copy", command=copy_log).pack(side="left")
        ttk.Button(buttons, text="Clear", command=clear_log).pack(side="left", padx=6)
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side="right")

    def _on_mfp_command(self, command, received_at: float) -> None:
        """Capture all MFP axes; L0 still enters the proven engine separately."""
        self.axis_router.receive(command, received_at)
        self.media_timeline.receive(command, received_at)

    def _on_evt_trigger(self, trigger, received_at: float) -> None:
        """Schedule a live EVT onto the same look-ahead clock as L0 samples."""
        if not self.events_enabled.get():
            return
        activate_at = received_at + float(self.engine.lookahead_seconds)
        self.event_engine.schedule_trigger(
            trigger.name, dict(trigger.params), activate_at)

    def _configure_media_timeline(self) -> None:
        self.media_timeline.configure(
            self.timeline_position_axis.get().strip() or "T0",
            self.timeline_duration_axis.get().strip() or "T1",
            self.timeline_scale_seconds.get())

    def _authored_overrides_for_sample(self, calculated_at: float) -> dict[str, float]:
        if self.authored_routing_mode.get() == "Auto authored ReStim set":
            overrides = self.axis_router.snapshot_auto(calculated_at)
        else:
            overrides = self.axis_router.snapshot(calculated_at)
        blocked = self.media_timeline.timeline_axes()
        if blocked:
            overrides = {axis: value for axis, value in overrides.items()
                         if axis not in blocked}
        return overrides


    def _on_events_enabled_changed(self) -> None:
        if not self.events_enabled.get():
            self.event_engine.clear_triggers()
        self._save_settings()
        self._update_events_status()

    def _browse_events_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select .events.yml",
            filetypes=(("Event YAML", "*.events.yml *.events.yaml *.yml *.yaml"),
                       ("All files", "*.*")))
        if selected:
            self.events_file_path.set(selected)
            self._reload_events_file()

    def _browse_events_definitions(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select event_definitions.yml",
            filetypes=(("YAML", "*.yml *.yaml"), ("All files", "*.*")))
        if selected:
            self.events_definitions_path.set(selected)
            self._reload_events_file()

    def _reload_events_file(self) -> None:
        defs = self.events_definitions_path.get().strip()
        try:
            if defs:
                self.event_engine.reload_definitions(defs)
            else:
                self.event_engine.reload_definitions()
            path = self.events_file_path.get().strip()
            if path:
                self.event_engine.load_events_file(path)
            else:
                self.event_engine.clear()
        except EventError as exc:
            self._set_events_status_text(f"Events: error - {exc}")
            self._save_settings()
            return
        self._save_settings()
        self._update_events_status()

    def _copy_events_status(self) -> None:
        text = self.events_status.get()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _set_events_status_text(self, line: str) -> None:
        self.events_status.set(line)
        widget = getattr(self, "events_status_text", None)
        if widget is None:
            return
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", line)
        widget.configure(state="disabled")

    def _update_events_status(self, position_ms: int | None = None) -> None:
        if position_ms is None:
            position_ms = self._events_last_position_ms
        line = self.event_engine.status_line(
            position_ms, enabled=bool(self.events_enabled.get()),
            due_at=self._events_last_due_at)
        if self.events_enabled.get():
            s1_steps = sum(1 for step in self.event_engine.loaded.steps
                           if step.axis == "sensor_suppression")
            if self._events_last_s1 is None:
                line = f"{line} | out S1=--"
            else:
                line = f"{line} | out S1={self._events_last_s1 * 100:.0f}%"
            if s1_steps == 0 and self.event_engine.step_count:
                line += " | WARN: no sensor_suppression steps loaded"
            else:
                line += f" | S1 steps={s1_steps}"
        self._set_events_status_text(line)

    def _apply_events_to_sample(
            self, at_time: float, primary_volume: float,
            prostate_volume: float, frequency: float, pulse_frequency: float,
            pulse_width: float, alpha: float, beta: float,
            electrodes: tuple[float, float, float, float],
            authored_overrides: dict[str, float]
            ) -> tuple[float, float, float, float, float, float, float,
                       tuple[float, float, float, float], dict[str, float]]:
        """Apply file events (T0) and/or scheduled EVT triggers at sample.due_at.

        File-event media time must stay usable for the full look-ahead window.
        Snapshotting only at ``due_at`` with the default 2 s T0 hold drops
        ``position_ms`` (so cum never writes S1=100) while the Events status
        line — which snapshots ``now`` — still shows the event as active.
        """
        unchanged = (primary_volume, prostate_volume, frequency, pulse_frequency,
                     pulse_width, alpha, beta, electrodes, authored_overrides)
        if not self.events_enabled.get():
            self._events_last_position_ms = None
            self._events_last_due_at = None
            self._events_last_s1 = None
            return unchanged
        self._configure_media_timeline()
        # Match the Events status clock (send-time / now) and keep T0 held for
        # at least look-ahead so file steps still apply when packets are sparse.
        hold = max(TIMELINE_HOLD_SECONDS, float(self.engine.lookahead_seconds) + 1.0)
        state = self.media_timeline.snapshot(self.engine.clock(), hold_seconds=hold)
        if state.position_ms is None:
            state = self.media_timeline.snapshot(at_time, hold_seconds=hold)
        position_ms = state.position_ms
        self._events_last_position_ms = position_ms
        self._events_last_due_at = at_time
        values = {
            "volume": authored_overrides.get("V0", primary_volume),
            "volume-prostate": prostate_volume,
            "frequency": authored_overrides.get("C0", frequency),
            "pulse_frequency": authored_overrides.get("P0", pulse_frequency),
            "pulse_width": authored_overrides.get("P1", pulse_width),
            "alpha": authored_overrides.get("L0", alpha),
            "beta": authored_overrides.get("L1", beta),
            "e1": authored_overrides.get("E1", electrodes[0]),
            "e2": authored_overrides.get("E2", electrodes[1]),
            "e3": authored_overrides.get("E3", electrodes[2]),
            "e4": authored_overrides.get("E4", electrodes[3]),
            # Authored S1 when routed; otherwise sensors fully active while events are on.
            "sensor_suppression": authored_overrides.get("S1", 0.0),
        }
        if self.event_engine.step_count and position_ms is not None:
            values = self.event_engine.apply(position_ms, values)
        result = self.event_engine.apply_triggers(at_time, values)
        primary_volume = result["volume"]
        prostate_volume = result["volume-prostate"]
        frequency = result["frequency"]
        pulse_frequency = result["pulse_frequency"]
        pulse_width = result["pulse_width"]
        alpha = result["alpha"]
        beta = result["beta"]
        electrodes = (result["e1"], result["e2"], result["e3"], result["e4"])
        authored_overrides = dict(authored_overrides)
        for axis, key in AXIS_AUTHORED.items():
            if key in authored_overrides:
                authored_overrides[key] = result[axis]
        # Always emit S1 while events are active (baseline or event override).
        authored_overrides["S1"] = result["sensor_suppression"]
        self._events_last_s1 = float(result["sensor_suppression"])
        return (primary_volume, prostate_volume, frequency, pulse_frequency,
                pulse_width, alpha, beta, electrodes, authored_overrides)

    def _on_media_ramp_curve_selected(self, _event=None) -> None:
        self._redraw_media_ramp_curve_preview()
        self._save_settings()

    def _on_media_ramp_levels_changed(self) -> None:
        self._redraw_media_ramp_curve_preview()
        self._save_settings()

    def _on_media_ramp_waypoints_toggled(self) -> None:
        if self.media_volume_ramp_waypoints_enabled.get() and not self._media_ramp_waypoints:
            self._media_ramp_waypoints = [RampWaypoint(
                0.0, "floor1",
                normalize_curve_name(self.media_volume_ramp_curve.get()))]
        self._refresh_media_ramp_waypoint_tree()
        self._refresh_extra_level_controls()
        self._redraw_media_ramp_curve_preview()
        self._save_settings()

    def _refresh_extra_level_controls(self) -> None:
        """Show Floor 2/3 and Ceiling 2/3 spinboxes only when waypoints use them."""
        used = waypoint_levels_used(self._media_ramp_waypoints)
        for key in EXTRA_RAMP_LEVEL_KEYS:
            widgets = self._media_ramp_extra_level_widgets.get(key, [])
            if key in used:
                for widget in widgets:
                    widget.grid()
            else:
                for widget in widgets:
                    widget.grid_remove()

    def _media_ramp_level_args(self) -> tuple[float, float, float, float, float, float]:
        return (
            self.media_volume_ramp_floor.get(),
            self.media_volume_ramp_ceiling.get(),
            self.media_volume_ramp_floor2.get(),
            self.media_volume_ramp_ceiling2.get(),
            self.media_volume_ramp_floor3.get(),
            self.media_volume_ramp_ceiling3.get(),
        )

    def _refresh_media_ramp_waypoint_tree(self) -> None:
        tree = getattr(self, "media_ramp_waypoint_tree", None)
        if tree is None:
            return
        for item in tree.get_children():
            tree.delete(item)
        default_curve = normalize_curve_name(self.media_volume_ramp_curve.get())
        self._media_ramp_waypoints = normalize_waypoints(
            self._media_ramp_waypoints, default_curve)
        for point in self._media_ramp_waypoints:
            tree.insert(
                "", "end",
                values=(format_media_time(point.time_s),
                        RAMP_LEVEL_LABELS.get(point.level, point.level),
                        point.curve),
                tags=("row",))

    def _default_add_waypoint_time(self) -> float:
        if not self._media_ramp_waypoints:
            return 0.0
        return self._media_ramp_waypoints[-1].time_s + 60.0

    def _waypoint_dialog(self, title: str, time_s: float, level: str,
                         curve: str, on_accept) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="Time (h:mm:ss, m:ss, or seconds)").grid(
            row=0, column=0, sticky="w", padx=8, pady=6)
        time_var = tk.StringVar(value=format_media_time(time_s))
        ttk.Entry(dialog, textvariable=time_var, width=16).grid(
            row=0, column=1, sticky="w", padx=8, pady=6)
        ttk.Label(dialog, text="Level").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        level_labels = list(RAMP_LEVEL_LABELS.values())
        level_var = tk.StringVar(
            value=RAMP_LEVEL_LABELS.get(normalize_level_key(level), "Ceiling 1"))
        ttk.Combobox(dialog, textvariable=level_var, values=level_labels,
                     state="readonly", width=14).grid(
                         row=1, column=1, sticky="w", padx=8, pady=6)
        ttk.Label(dialog, text="Curve (to this point)").grid(
            row=2, column=0, sticky="w", padx=8, pady=6)
        curve_var = tk.StringVar(
            value=normalize_curve_name(curve, self.media_volume_ramp_curve.get()))
        ttk.Combobox(dialog, textvariable=curve_var, values=list(RAMP_CURVE_NAMES),
                     state="readonly", width=14).grid(
                         row=2, column=1, sticky="w", padx=8, pady=6)

        def accept() -> None:
            try:
                parsed_time = parse_media_time(time_var.get())
            except ValueError as exc:
                messagebox.showerror("Invalid time", str(exc), parent=dialog)
                return
            label_to_key = {label: key for key, label in RAMP_LEVEL_LABELS.items()}
            parsed_level = label_to_key.get(level_var.get(), "ceiling1")
            parsed_curve = normalize_curve_name(
                curve_var.get(), self.media_volume_ramp_curve.get())
            on_accept(parsed_time, parsed_level, parsed_curve)
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.grid(row=3, column=0, columnspan=2, pady=8)
        ttk.Button(buttons, text="OK", command=accept).pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)

    def _add_media_ramp_waypoint(self) -> None:
        def accept(time_s: float, level: str, curve: str) -> None:
            self._media_ramp_waypoints = normalize_waypoints(
                list(self._media_ramp_waypoints) + [
                    RampWaypoint(time_s, level, curve)],
                self.media_volume_ramp_curve.get())
            if not self.media_volume_ramp_waypoints_enabled.get():
                self.media_volume_ramp_waypoints_enabled.set(True)
            self._refresh_media_ramp_waypoint_tree()
            self._refresh_extra_level_controls()
            self._redraw_media_ramp_curve_preview()
            self._save_settings()

        self._waypoint_dialog(
            "Add media ramp waypoint",
            self._default_add_waypoint_time(),
            "ceiling1",
            self.media_volume_ramp_curve.get(),
            accept)

    def _edit_media_ramp_waypoint(self) -> None:
        tree = getattr(self, "media_ramp_waypoint_tree", None)
        if tree is None:
            return
        selected = tree.selection()
        if len(selected) != 1:
            messagebox.showinfo(
                "Edit waypoint", "Select a single waypoint to edit.", parent=self.root)
            return
        index = tree.index(selected[0])
        if not (0 <= index < len(self._media_ramp_waypoints)):
            return
        point = self._media_ramp_waypoints[index]

        def accept(time_s: float, level: str, curve: str) -> None:
            points = list(self._media_ramp_waypoints)
            points[index] = RampWaypoint(time_s, level, curve)
            self._media_ramp_waypoints = normalize_waypoints(
                points, self.media_volume_ramp_curve.get())
            self._refresh_media_ramp_waypoint_tree()
            self._refresh_extra_level_controls()
            self._redraw_media_ramp_curve_preview()
            self._save_settings()

        self._waypoint_dialog(
            "Edit media ramp waypoint", point.time_s, point.level, point.curve, accept)

    def _remove_media_ramp_waypoint(self) -> None:
        tree = getattr(self, "media_ramp_waypoint_tree", None)
        if tree is None:
            return
        selected = tree.selection()
        if not selected:
            return
        indexes = sorted((tree.index(item) for item in selected), reverse=True)
        points = list(self._media_ramp_waypoints)
        for index in indexes:
            if 0 <= index < len(points):
                points.pop(index)
        self._media_ramp_waypoints = normalize_waypoints(points)
        self._refresh_media_ramp_waypoint_tree()
        self._refresh_extra_level_controls()
        self._redraw_media_ramp_curve_preview()
        self._save_settings()

    def _move_media_ramp_waypoint(self, direction: int) -> None:
        """Swap selected waypoint with neighbor (useful for same-time ties)."""
        tree = getattr(self, "media_ramp_waypoint_tree", None)
        if tree is None:
            return
        selected = tree.selection()
        if len(selected) != 1:
            return
        index = tree.index(selected[0])
        points = list(self._media_ramp_waypoints)
        target = index + int(direction)
        if not (0 <= index < len(points) and 0 <= target < len(points)):
            return
        points[index], points[target] = points[target], points[index]
        self._media_ramp_waypoints = normalize_waypoints(points)
        self._refresh_media_ramp_waypoint_tree()
        children = tree.get_children()
        if 0 <= target < len(children):
            tree.selection_set(children[target])
            tree.focus(children[target])
        self._redraw_media_ramp_curve_preview()
        self._save_settings()

    def _export_media_ramp_waypoints(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export media ramp",
            defaultextension=".funscript",
            filetypes=(
                ("Funscript / volume", "*.funscript"),
                ("JSON waypoints", "*.json"),
                ("All files", "*.*"),
            ))
        if not path:
            return
        floor1, ceiling1, floor2, ceiling2, floor3, ceiling3 = self._media_ramp_level_args()
        curve = self.media_volume_ramp_curve.get()
        end_s = None
        self._configure_media_timeline()
        duration = self.media_timeline.snapshot(time.monotonic()).duration_s
        if duration is not None and duration > 0:
            end_s = float(duration)
        if path.lower().endswith(".funscript"):
            payload = export_ramp_funscript(
                self._media_ramp_waypoints,
                floor1, floor2, floor3, ceiling1, ceiling2, ceiling3,
                curve, end_s=end_s)
        else:
            payload = export_ramp_waypoints_payload(
                self._media_ramp_waypoints,
                floor1, floor2, floor3, ceiling1, ceiling2, ceiling3, curve)
        try:
            Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self.root)

    def _import_media_ramp_waypoints(self) -> None:
        path = filedialog.askopenfilename(
            title="Import media ramp",
            filetypes=(
                ("Funscript / JSON", "*.funscript *.json"),
                ("Funscript", "*.funscript"),
                ("JSON waypoints", "*.json"),
                ("All files", "*.*"),
            ))
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            waypoints, settings = import_ramp_funscript(data)
        except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
            messagebox.showerror("Import failed", str(exc), parent=self.root)
            return
        self._media_ramp_waypoints = waypoints
        if not settings.get("gains_from_bookmarks_only"):
            self.media_volume_ramp_floor.set(settings["floor1"])
            self.media_volume_ramp_floor2.set(settings["floor2"])
            self.media_volume_ramp_floor3.set(settings["floor3"])
            self.media_volume_ramp_ceiling.set(settings["ceiling1"])
            self.media_volume_ramp_ceiling2.set(settings["ceiling2"])
            self.media_volume_ramp_ceiling3.set(settings["ceiling3"])
        curve = settings["curve"]
        if curve in RAMP_CURVE_NAMES:
            self.media_volume_ramp_curve.set(curve)
        if waypoints and not self.media_volume_ramp_waypoints_enabled.get():
            self.media_volume_ramp_waypoints_enabled.set(True)
        self._refresh_media_ramp_waypoint_tree()
        self._refresh_extra_level_controls()
        self._redraw_media_ramp_curve_preview()
        self._save_settings()

    def _redraw_media_ramp_curve_preview(self) -> None:
        """Draw selected curve shape, or waypoint gain path when enabled."""
        canvas = getattr(self, "media_ramp_curve_preview", None)
        if canvas is None:
            return
        colors = getattr(self, "_theme", THEME_COLORS["light"])
        width = int(canvas.winfo_reqwidth() or 280)
        height = int(canvas.winfo_reqheight() or 96)
        pad = 6
        canvas.delete("all")
        canvas.create_rectangle(
            pad, pad, width - pad, height - pad,
            outline=colors["preview_outline"], fill=colors["preview_bg"])
        inner_w = max(1, width - 2 * pad)
        inner_h = max(1, height - 2 * pad)
        samples = 96
        points: list[float] = []
        floor1, ceiling1, floor2, ceiling2, floor3, ceiling3 = self._media_ramp_level_args()
        if (self.media_volume_ramp_waypoints_enabled.get()
                and self._media_ramp_waypoints):
            waypoints = normalize_waypoints(self._media_ramp_waypoints)
            end_s = max(waypoints[-1].time_s * 1.15, waypoints[-1].time_s + 1.0, 1.0)
            for index in range(samples + 1):
                position_s = (index / samples) * end_s
                gain = media_volume_gain_waypoints(
                    position_s, waypoints,
                    floor1, ceiling1, floor2, ceiling2, floor3, ceiling3,
                    self.media_volume_ramp_curve.get())
                x = pad + (index / samples) * inner_w
                y = pad + (1.0 - gain) * inner_h
                points.extend((x, y))
            if len(points) >= 4:
                canvas.create_line(*points, fill=colors["preview_line"], width=1, smooth=True)
            for point in waypoints:
                frac = min(1.0, max(0.0, point.time_s / end_s))
                x = pad + frac * inner_w
                y = pad + (1.0 - media_volume_gain_waypoints(
                    point.time_s, waypoints,
                    floor1, ceiling1, floor2, ceiling2, floor3, ceiling3,
                    self.media_volume_ramp_curve.get())) * inner_h
                # Small tick + dot (not full-height bars).
                canvas.create_line(x, y - 5, x, y + 5, fill=colors["preview_marker"], width=1)
                canvas.create_oval(
                    x - 2, y - 2, x + 2, y + 2, fill=colors["preview_marker"], outline="")
            return
        name = self.media_volume_ramp_curve.get()
        for index in range(samples + 1):
            progress = index / samples
            shaped = ramp_curve(progress, name)
            x = pad + progress * inner_w
            y = pad + (1.0 - shaped) * inner_h
            points.extend((x, y))
        if len(points) >= 4:
            canvas.create_line(*points, fill=colors["preview_line"], width=1, smooth=True)

    def _media_volume_gain_at(self, calculated_at: float) -> float | None:
        if not self.media_volume_ramp_enabled.get():
            return None
        self._configure_media_timeline()
        state = self.media_timeline.snapshot(calculated_at)
        if (self.media_volume_ramp_waypoints_enabled.get()
                and self._media_ramp_waypoints):
            floor1, ceiling1, floor2, ceiling2, floor3, ceiling3 = (
                self._media_ramp_level_args())
            return media_volume_gain_waypoints(
                state.position_s if state.usable else None,
                self._media_ramp_waypoints,
                floor1, ceiling1, floor2, ceiling2, floor3, ceiling3,
                self.media_volume_ramp_curve.get())
        # Use held progress between quiet T0 packets; floor only when unusable.
        return media_volume_gain(
            state.progress,
            self.media_volume_ramp_floor.get(),
            self.media_volume_ramp_ceiling.get(),
            self.media_volume_ramp_curve.get())

    def _set_startup_status(self, text: str) -> None:
        self._record_connection_event("Startup", text)
        try:
            self.root.after(0, self.startup_status.set, text)
        except (RuntimeError, tk.TclError):
            pass

    def _browse_launch_target(self, variable: tk.StringVar) -> None:
        selected = filedialog.askopenfilename(
            title="Select application or shortcut",
            filetypes=(("Applications and shortcuts", "*.exe *.lnk *.bat *.cmd"),
                       ("All files", "*.*")))
        if selected:
            variable.set(selected)

    def show_session_startup(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Session startup")
        window.transient(self.root)
        window.geometry("820x300")
        body = ttk.Frame(window, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=(
            "Optional standalone session coordinator. Vector can launch the selected applications, "
            "wait for ReStim services to become available, connect them, and report one session-ready state. "
            "Signal generation remains entirely inside Vector."), wraplength=760).grid(
                row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        rows = (
            ("MultiFunPlayer", self.auto_start_mfp, self.mfp_launch_target),
            ("Primary ReStim", self.auto_start_restim, self.restim_launch_target),
            ("Prostate ReStim", self.auto_start_prostate, self.prostate_launch_target),
        )
        for row, (label, enabled, target) in enumerate(rows, 1):
            ttk.Checkbutton(body, text=f"Auto-start {label}", variable=enabled).grid(
                row=row, column=0, sticky="w", pady=5)
            ttk.Entry(body, textvariable=target, width=62).grid(
                row=row, column=1, columnspan=2, sticky="ew", padx=8)
            ttk.Button(body, text="Browse…",
                       command=lambda v=target: self._browse_launch_target(v)).grid(
                           row=row, column=3, sticky="e")
        ttk.Label(body, textvariable=self.startup_status, style="Muted.TLabel").grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(12, 6))
        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=4, sticky="ew")
        ttk.Button(buttons, text="Start selected session", command=self._launch_session_apps).pack(side="left")
        ttk.Button(buttons, text="Save", command=self._save_settings).pack(side="left", padx=6)
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side="right")
        body.columnconfigure(1, weight=1)

    def _launch_session_apps(self) -> None:
        if self._startup_in_progress:
            self._set_startup_status("Session startup already in progress")
            return

        selected = {
            "mfp": bool(self.auto_start_mfp.get()),
            "restim": bool(self.auto_start_restim.get()),
            "prostate": bool(self.auto_start_prostate.get()),
        }
        targets = {
            "mfp": self.mfp_launch_target.get(),
            "restim": self.restim_launch_target.get(),
            "prostate": self.prostate_launch_target.get(),
        }
        hosts = {
            "restim": self.restim_host.get().strip(),
            "prostate": self.prostate_host.get().strip(),
        }
        ports = {
            "restim": int(self.restim_port.get()),
            "prostate": int(self.prostate_port.get()),
        }
        if not any(selected.values()):
            self.session_ready_status.set("SESSION: MANUAL")
            self._set_startup_status("No auto-start applications selected")
            self._save_settings()
            return

        self._startup_in_progress = True
        self.session_ready_status.set("SESSION: STARTING")
        self._set_startup_status("Starting selected session components…")
        self._save_settings()

        # MFP sends into Vector, so Vector can prepare the listener immediately.
        if selected["mfp"]:
            self.start_listener()

        def worker() -> None:
            messages: list[str] = []
            failed: list[str] = []
            if selected["mfp"]:
                result = self.orchestrator.launch("MultiFunPlayer", targets["mfp"])
                messages.append(result.message)
                if targets["mfp"].strip() and not result.launched and "already launched" not in result.message:
                    failed.append("MultiFunPlayer")

            for key, label in (("restim", "Primary ReStim"), ("prostate", "Prostate ReStim")):
                if not selected[key]:
                    continue
                host, port = hosts[key], ports[key]
                if port_is_open(host, port):
                    messages.append(f"{label}: already listening on {host}:{port}")
                else:
                    result = self.orchestrator.launch(label, targets[key])
                    messages.append(result.message)
                    if not wait_for_port(host, port, timeout=12.0):
                        failed.append(label)
                        messages.append(f"{label}: port {host}:{port} not ready after 12 s")

            def finish() -> None:
                # Only attempt WebSocket handshakes after the corresponding TCP service is ready.
                if selected["restim"] and "Primary ReStim" not in failed:
                    self.connect_restim()
                if selected["prostate"] and "Prostate ReStim" not in failed:
                    self.connect_prostate()
                self._startup_in_progress = False
                if failed:
                    self.session_ready_status.set("SESSION: ATTENTION")
                    messages.append("Attention: " + ", ".join(failed))
                else:
                    self.session_ready_status.set("SESSION: READY")
                    messages.append("READY")
                self._set_startup_status(" | ".join(messages))

            self.root.after(0, finish)

        threading.Thread(target=worker, name="vector-session-startup", daemon=True).start()

    def _auto_start_session(self) -> None:
        if self.auto_start_mfp.get() or self.auto_start_restim.get() or self.auto_start_prostate.get():
            self._launch_session_apps()

    def show_axis_routing(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("MFP authored-axis routing")
        window.transient(self.root)
        window.geometry("900x680")
        outer = ttk.Frame(window, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=(
            "Choose manual per-axis routing, or Auto authored ReStim set. Auto mode only takes over "
            "when MFP is clearly supplying ReStim-semantic axes (for example V0/C0/P0/P1/P3/E1-E4). "
            "It then passes the complete authored set, including L0/L1, on Vector's delayed timeline; "
            "any missing ReStim axes remain Vector-generated."),
            wraplength=840).pack(anchor="w", pady=(0, 8))
        policy = ttk.Frame(outer)
        policy.pack(fill="x", pady=(0, 8))
        ttk.Label(policy, text="Routing policy:", font=("TkDefaultFont", 9, "bold")).pack(side="left")
        ttk.Radiobutton(policy, text="Manual selected axes", variable=self.authored_routing_mode,
                        value="Manual selected axes", command=self._save_settings).pack(side="left", padx=(10, 4))
        ttk.Radiobutton(policy, text="Auto authored ReStim set", variable=self.authored_routing_mode,
                        value="Auto authored ReStim set", command=self._save_settings).pack(side="left", padx=4)

        state_frame = ttk.LabelFrame(outer, text="Live routing state", padding=(10, 7))
        state_frame.pack(fill="x", pady=(0, 8))
        discovered_text = tk.StringVar(value="Detected this session: none")
        live_text = tk.StringVar(value="Currently live: none")
        mode_text = tk.StringVar(value="Routing mode: VECTOR GENERATION")
        timeline_text = tk.StringVar(value="Media timeline: none")
        ttk.Label(state_frame, textvariable=discovered_text).pack(anchor="w")
        ttk.Label(state_frame, textvariable=live_text).pack(anchor="w", pady=(2, 0))
        ttk.Label(state_frame, textvariable=mode_text, font=("TkDefaultFont", 9, "bold")).pack(anchor="w", pady=(2, 0))
        ttk.Label(state_frame, textvariable=timeline_text, style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(state_frame, text=(
            "T0/T1 (or configured timeline axes) are media clock only and cannot be routed to ReStim."),
            style="Muted.TLabel", wraplength=840).pack(anchor="w", pady=(4, 0))

        frame = ttk.Frame(outer)
        frame.pack(fill="x", expand=False)
        controls: dict[str, tuple[tk.BooleanVar, ttk.Label, ttk.Label]] = {}

        ttk.Separator(outer).pack(fill="x", pady=(12, 8))
        ttk.Label(outer, text="Recent MFP packets (raw input → parsed axes)",
                  font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        packet_box = tk.Text(outer, height=12, wrap="none")
        packet_box.pack(fill="both", expand=True, pady=(4, 6))
        packet_box.configure(state="disabled")
        packet_signature = [None]

        def copy_packets() -> None:
            packets = self.listener.recent_packets(40)
            lines = []
            now = time.monotonic()
            for packet in packets:
                age = max(0.0, now - float(packet["time"]))
                axes = ",".join(packet["axes"]) or "none"
                lines.append(f'{packet["transport"]} {age:5.2f}s  parsed=[{axes}]  raw={packet["raw"]}')
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(lines))

        packet_buttons = ttk.Frame(outer)
        packet_buttons.pack(fill="x")
        ttk.Button(packet_buttons, text="Copy packet diagnostics",
                   command=copy_packets).pack(side="left")
        generated = {"L0", "L1", "E1", "E2", "E3", "E4", "V0", "C0", "P0", "P1", "P3"}
        axis_names = {
            "L0": "Alpha / primary position", "L1": "Beta",
            "V0": "Primary volume", "C0": "Frequency",
            "P0": "Pulse frequency", "P1": "Pulse width", "P3": "Pulse rise time",
            "E1": "Electrode A", "E2": "Electrode B", "E3": "Electrode C", "E4": "Electrode D",
            "V1": "Additional volume axis",
            "S1": "Sensor suppression",
            "T0": "Media absolute position (clock)", "T1": "Media absolute duration (clock)",
        }

        def toggle(axis: str, variable: tk.BooleanVar) -> None:
            if axis in self.media_timeline.timeline_axes():
                variable.set(False)
                return
            self.axis_router.set_enabled(axis, variable.get())
            self._save_settings()

        def refresh_routes() -> None:
            if not window.winfo_exists():
                return
            self._configure_media_timeline()
            now = time.monotonic()
            blocked = self.media_timeline.timeline_axes()
            status = self.axis_router.axis_status(now)
            axes = sorted(set(status) | self.axis_router.enabled_axes() | blocked)
            discovered = sorted(status)
            live_axes = sorted(self.axis_router.live_axes(now))
            discovered_text.set("Detected this session: " + (", ".join(discovered) if discovered else "none"))
            live_text.set("Currently live: " + (", ".join(live_axes) if live_axes else "none"))
            timeline = self.media_timeline.snapshot(now)
            if timeline.usable and timeline.position_s is not None:
                duration = ("unknown" if timeline.duration_s is None
                            else f"{timeline.duration_s:.1f} s")
                progress = ("--" if timeline.progress is None
                            else f"{timeline.progress * 100:.1f}%")
                suffix = " (held)" if timeline.held else ""
                timeline_text.set(
                    f"Media timeline: {timeline.position_s:.1f} s / {duration}  "
                    f"progress={progress}{suffix}")
            else:
                timeline_text.set("Media timeline: none / stale")
            if not axes:
                self.authored_axes_status.set("No authored axes detected")
                mode_text.set("Routing mode: VECTOR GENERATION")
            else:
                enabled = sorted(self.axis_router.enabled_axes() - blocked)
                if self.authored_routing_mode.get() == "Auto authored ReStim set":
                    auto_active = self.axis_router.auto_authored_active(now)
                    if auto_active:
                        suffix = " | AUTO full authored set"
                        mode_text.set("Routing mode: AUTO AUTHORED RESTIM")
                    else:
                        suffix = " | AUTO waiting; Vector generation"
                        mode_text.set("Routing mode: VECTOR GENERATION (auto waiting)")
                else:
                    suffix = (" | routed: " + ", ".join(enabled) if enabled else " | Vector generation active")
                    mode_text.set("Routing mode: MANUAL PASSTHROUGH" if enabled else "Routing mode: VECTOR GENERATION")
                self.authored_axes_status.set("Authored axes: " + ", ".join(axes) + suffix)
            packets = self.listener.recent_packets(12)
            signature = tuple((p["transport"], p["raw"], tuple(p["axes"])) for p in packets)
            if signature != packet_signature[0]:
                packet_signature[0] = signature
                packet_now = time.monotonic()
                lines = []
                for packet in packets:
                    age = max(0.0, packet_now - float(packet["time"]))
                    parsed = ",".join(packet["axes"]) or "none"
                    raw = str(packet["raw"]).replace("\r", "\\r").replace("\n", "\\n")
                    if len(raw) > 180:
                        raw = raw[:177] + "..."
                    lines.append(f'{packet["transport"]} {age:5.2f}s  parsed=[{parsed}]  raw={raw}')
                packet_box.configure(state="normal")
                packet_box.delete("1.0", "end")
                packet_box.insert("1.0", "\n".join(lines) if lines else "Waiting for MFP packets…")
                packet_box.configure(state="disabled")
            for axis in axes:
                if axis not in controls:
                    row = len(controls)
                    var = tk.BooleanVar(value=axis in self.axis_router.enabled_axes())
                    label = axis_names.get(axis, axis)
                    ttk.Checkbutton(frame, text=f"{axis}  {label}", variable=var,
                                    command=lambda a=axis, v=var: toggle(a, v)).grid(
                                        row=row, column=0, sticky="w", pady=4)
                    mode = "Vector candidate" if axis in generated else "additional axis"
                    owner = ttk.Label(frame, text=mode)
                    owner.grid(row=row, column=1, sticky="w", padx=12)
                    live = ttk.Label(frame, text="waiting")
                    live.grid(row=row, column=2, sticky="w", padx=12)
                    controls[axis] = (var, live, owner)
                info = status.get(axis)
                live = controls[axis][1]
                owner = controls[axis][2]
                is_live = axis in live_axes
                if axis in blocked:
                    owner.configure(text="MEDIA CLOCK")
                    controls[axis][0].set(False)
                    if axis in self.axis_router.enabled_axes():
                        self.axis_router.set_enabled(axis, False)
                else:
                    if self.authored_routing_mode.get() == "Auto authored ReStim set":
                        authored = self.axis_router.auto_authored_active(now) and is_live
                    else:
                        authored = axis in self.axis_router.enabled_axes() and is_live
                    if authored:
                        owner.configure(text="AUTHORED")
                    elif axis in generated:
                        owner.configure(text="VECTOR")
                    elif is_live:
                        owner.configure(text="MFP (not routed)")
                    else:
                        owner.configure(text="inactive")
                if axis in blocked:
                    timeline_info = self.media_timeline.snapshot(now)
                    scale = self.timeline_scale_seconds.get()
                    if axis == self.media_timeline.position_axis:
                        seconds = timeline_info.position_s
                        if seconds is None and info is not None and info.get("value") is not None:
                            seconds = decode_timeline_seconds(float(info["value"]), scale)
                        live.configure(text=("waiting" if seconds is None
                                             else f"{seconds:.1f} s"))
                    elif axis == self.media_timeline.duration_axis:
                        seconds = timeline_info.duration_s
                        if seconds is None and info is not None and info.get("value") is not None:
                            decoded = decode_timeline_seconds(float(info["value"]), scale)
                            seconds = None if decoded <= 0.0 else decoded
                        if seconds is None:
                            live.configure(text="unknown")
                        else:
                            live.configure(text=f"{seconds:.1f} s")
                    elif info is None:
                        live.configure(text="configured; not seen this session")
                    else:
                        age = float(info.get("last_seen_age", 0.0))
                        live.configure(text=f"seen {age:.1f}s ago")
                elif info is None:
                    live.configure(text="configured; not seen this session")
                else:
                    value = info.get("value")
                    age = float(info.get("last_seen_age", 0.0))
                    live.configure(text=f"{value:.3f} | {age:.1f}s ago" if value is not None else f"{age:.1f}s ago")
            window.after(300, refresh_routes)

        ttk.Separator(outer).pack(fill="x", pady=8)
        ttk.Button(outer, text="Close", command=window.destroy).pack(anchor="e")
        refresh_routes()

    def start_listener(self) -> None:
        try:
            self.listener.start(self.mfp_host.get().strip(), self.mfp_port.get())
        except Exception as exc:
            messagebox.showerror("MFP listener", str(exc))

    def connect_restim(self) -> None:
        try:
            self.restim.connect(self.restim_host.get().strip(), self.restim_port.get())
        except OSError as exc:
            self._set_restim_status(f"Connection failed: {exc}")

    def connect_prostate(self) -> None:
        try:
            self.prostate_restim.connect(self.prostate_host.get().strip(), self.prostate_port.get())
        except OSError as exc:
            self._set_prostate_status(f"Connection failed: {exc}")

    def _four_phase_send_toggle(self) -> None:
        if not self.send_four_phase_visual.get():
            return
        if not messagebox.askyesno(
                "Enable four-phase visual test",
                "Confirm FOC-Stim stimulation hardware is disconnected.\n\n"
                "Vector will send E1–E4 commands to the connected four-phase ReStim "
                "instance for visual commissioning only."):
            self.send_four_phase_visual.set(False)

    def _bind_controller_keys(self) -> None:
        for key, action in (("<w>", lambda: self._adjust_frequency_ramp(1)),
                            ("<s>", lambda: self._adjust_frequency_ramp(-1)),
                            ("<a>", lambda: self._shift_control_range("pulse_frequency", -1)),
                            ("<d>", lambda: self._shift_control_range("pulse_frequency", 1)),
                            ("<i>", lambda: self._shift_control_range("pulse_rise", 1)),
                            ("<k>", lambda: self._shift_control_range("pulse_rise", -1)),
                            ("<j>", lambda: self._shift_control_range("pulse_width", -1)),
                            ("<l>", lambda: self._shift_control_range("pulse_width", 1)),
                            ("<o>", self._cycle_electrode_order),
                            ("<bracketleft>", lambda: self._apply_preset("A")),
                            ("<bracketright>", lambda: self._apply_preset("B")),
                            ("<Prior>", lambda: self._adjust_prostate_phase(1)),
                            ("<Next>", lambda: self._adjust_prostate_phase(-1)),
                            ("<Return>", self.resume), ("<space>", self.neutral),
                            ("<Escape>", self.stop)):
            self.root.bind(key, lambda event, fn=action: self._controller_key(event, fn))

    def _controller_key(self, event, action):
        if not self.controller_enabled.get():
            return None
        if self.direct_controller_enabled.get() and self.xinput.connected:
            return "break"
        # Numeric/text editing retains ordinary keyboard behaviour.
        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox)):
            return None
        action()
        return "break"

    def _controller_enabled_changed(self) -> None:
        if self.controller_enabled.get():
            self.controller_status.set("Controller enabled; detecting Xbox input")
        else:
            self.controller_status.set("Disabled")
        self.apply_config()

    def _adjust_frequency_ramp(self, direction: int) -> None:
        self.variety_frequency.set(False)
        step = self.controller_fine_step.get()
        self.frequency_ramp_level.set(round(min(1.0, max(0.0,
            self.frequency_ramp_level.get() + direction * step)), 4))
        self.apply_config()

    def _shift_control_range(self, target: str, direction: int) -> None:
        {"pulse_frequency": self.variety_pulse_frequency,
         "pulse_rise": self.variety_pulse_rise,
         "pulse_width": self.variety_pulse_width}[target].set(False)
        pairs = {
            "pulse_frequency": (self.pulse_frequency_min, self.pulse_frequency_max),
            "pulse_rise": (self.pulse_rise_min, self.pulse_rise_max),
            "pulse_width": (self.pulse_width_min, self.pulse_width_max),
        }
        minimum, maximum = pairs[target]
        low, high = minimum.get(), maximum.get()
        delta = direction * self.controller_fine_step.get()
        if low + delta < 0.0:
            delta = -low
        if high + delta > 1.0:
            delta = 1.0 - high
        minimum.set(round(low + delta, 4))
        maximum.set(round(high + delta, 4))
        self.apply_config()

    def _adjust_prostate_phase(self, direction: int) -> None:
        self.variety_phase.set(False)
        phase = self.prostate_phase_degrees.get() + direction * self.prostate_phase_step.get()
        self.prostate_phase_degrees.set(round(min(90.0, max(-90.0, phase)), 1))
        self.apply_config()

    def _electrode_order_changed(self) -> None:
        self.variety_electrode_morph.set(False)

    def _cycle_electrode_order(self) -> None:
        self.variety_electrode_morph.set(False)
        try:
            index = ELECTRODE_ORDERS.index(self.electrode_order.get())
        except ValueError:
            index = -1
        self.electrode_order.set(ELECTRODE_ORDERS[(index + 1) % len(ELECTRODE_ORDERS)])

    def _xinput_status_threaded(self, status: str) -> None:
        self._controller_events.put(("status", status))

    def _set_xinput_status(self, status: str) -> None:
        if self.controller_enabled.get() and self.direct_controller_enabled.get():
            self.controller_status.set(status)

    def _xinput_buttons_threaded(self, buttons: int) -> None:
        self._controller_events.put(("buttons", buttons))

    def _drain_controller_events(self) -> None:
        while True:
            try:
                kind, value = self._controller_events.get_nowait()
            except queue.Empty:
                return
            if kind == "status":
                self._set_xinput_status(str(value))
            else:
                self._handle_xinput_buttons(int(value))

    def _handle_xinput_buttons(self, buttons: int) -> None:
        if not self.controller_enabled.get() or not self.direct_controller_enabled.get():
            return
        modified = bool(buttons & LEFT_SHOULDER)
        if buttons & A: self.resume()
        if buttons & B: self.neutral()
        if buttons & START: self.stop()
        if buttons & X: self._adjust_prostate_phase(1)
        if buttons & Y: self._adjust_prostate_phase(-1)
        if buttons & RIGHT_SHOULDER:
            if modified:
                self._toggle_ab_preset()
            else:
                self._cycle_electrode_order()
        if modified:
            if buttons & DPAD_UP: self._shift_control_range("pulse_rise", 1)
            if buttons & DPAD_DOWN: self._shift_control_range("pulse_rise", -1)
            if buttons & DPAD_LEFT: self._shift_control_range("pulse_width", -1)
            if buttons & DPAD_RIGHT: self._shift_control_range("pulse_width", 1)
        else:
            if buttons & DPAD_UP: self._adjust_frequency_ramp(1)
            if buttons & DPAD_DOWN: self._adjust_frequency_ramp(-1)
            if buttons & DPAD_LEFT: self._shift_control_range("pulse_frequency", -1)
            if buttons & DPAD_RIGHT: self._shift_control_range("pulse_frequency", 1)

    def _variety_toggle(self) -> None:
        self._variety_started = time.monotonic()
        for enabled, variables in (
                (self.variety_pulse_frequency, (self.pulse_frequency_min, self.pulse_frequency_max)),
                (self.variety_pulse_rise, (self.pulse_rise_min, self.pulse_rise_max)),
                (self.variety_pulse_width, (self.pulse_width_min, self.pulse_width_max))):
            if enabled.get() and self.variety_enabled.get():
                low, high = fit_range_for_travel(variables[0].get(), variables[1].get())
                variables[0].set(low); variables[1].set(high)
        self._variety_baseline = {
            "frequency": self.frequency_ramp_level.get(),
            "pf": (self.pulse_frequency_min.get(), self.pulse_frequency_max.get()),
            "rise": (self.pulse_rise_min.get(), self.pulse_rise_max.get()),
            "width": (self.pulse_width_min.get(), self.pulse_width_max.get()),
            "phase": self.prostate_phase_degrees.get(),
        }
        self.variety_status.set("Running" if self.variety_enabled.get() else "Off")

    def show_variety_window(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Rolling Variety")
        window.resizable(False, False)
        body = ttk.Frame(window, padding=16); body.pack(fill="both", expand=True)
        ttk.Checkbutton(body, text="Enable rolling variety", variable=self.variety_enabled,
                        command=self._variety_toggle).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text="Item").grid(row=1, column=0, sticky="w", pady=8)
        ttk.Label(body, text="Cycle minutes").grid(row=1, column=1, pady=8)
        options = (("Frequency ramp 1.0 to 0.5", self.variety_frequency, self.variety_frequency_cycle),
                   ("Pulse-frequency range +/-0.20", self.variety_pulse_frequency, self.variety_pulse_frequency_cycle),
                   ("Pulse-rise range +/-0.20", self.variety_pulse_rise, self.variety_pulse_rise_cycle),
                   ("Pulse-width range +/-0.20", self.variety_pulse_width, self.variety_pulse_width_cycle),
                   ("Prostate timing phase +/-45 degrees", self.variety_phase, self.variety_phase_cycle),
                   ("Sequence carousel (hold minutes)", self.variety_electrode_morph,
                    self.variety_electrode_morph_cycle))
        for row, (label, variable, cycle) in enumerate(options, start=2):
            ttk.Checkbutton(body, text=label, variable=variable,
                            command=self._variety_toggle).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Spinbox(body, from_=0.5, to=30, increment=.5, textvariable=cycle,
                        width=8).grid(row=row, column=1, padx=12)
        ttk.Label(body, text="Sequence transition seconds").grid(
            row=8, column=0, sticky="w", pady=3)
        ttk.Spinbox(body, from_=1, to=15, increment=.5,
                    textvariable=self.variety_electrode_morph_transition_seconds,
                    width=8).grid(row=8, column=1, padx=12)
        ttk.Label(body, textvariable=self.variety_status,
                  font=("TkDefaultFont", 10, "bold")).grid(row=9, column=0, sticky="w", pady=(12, 0))
        ttk.Button(body, text="Restart cycle from current settings",
                   command=self._variety_toggle).grid(row=9, column=1, padx=12, pady=(12, 0))

    @staticmethod
    def _bounded_shift(pair: tuple[float, float], offset: float) -> tuple[float, float]:
        low, high = pair
        offset = max(-low, min(1.0 - high, offset))
        return round(low + offset, 4), round(high + offset, 4)

    def _update_variety(self) -> None:
        if not self.variety_enabled.get():
            return
        if not self._variety_baseline:
            self._variety_toggle()
        elapsed = time.monotonic() - self._variety_started
        depth = (self.engine.diagnostics().variation_depth
                 if self.speed_linked_variation.get() else 1.0)
        if self.variety_frequency.get():
            baseline = self._variety_baseline.get("frequency", 1.0)
            target = rolling_value(elapsed, self.variety_frequency_cycle.get(), .5, 1.0)
            self.frequency_ramp_level.set(round(baseline + (target - baseline) * depth, 4))
        for enabled, key, variables, travel, cycle in (
                (self.variety_pulse_frequency, "pf", (self.pulse_frequency_min, self.pulse_frequency_max), .20, self.variety_pulse_frequency_cycle),
                (self.variety_pulse_rise, "rise", (self.pulse_rise_min, self.pulse_rise_max), .20, self.variety_pulse_rise_cycle),
                (self.variety_pulse_width, "width", (self.pulse_width_min, self.pulse_width_max), .20, self.variety_pulse_width_cycle)):
            if enabled.get():
                offset = rolling_offset(elapsed, cycle.get())
                low, high = self._bounded_shift(
                    self._variety_baseline[key], offset * depth * travel)
                variables[0].set(low); variables[1].set(high)
        if self.variety_phase.get():
            baseline = self._variety_baseline["phase"]
            offset = rolling_offset(elapsed, self.variety_phase_cycle.get())
            self.prostate_phase_degrees.set(round(
                max(-90, min(90, baseline + offset * 45 * depth)), 1))
        self.variety_status.set(
            f"Running | {elapsed / 60:.1f} min | depth {depth * 100:.0f}% | independent cycles")
        self.apply_config()

    def _electrode_morph_state(self, at_time: float | None = None
                               ) -> tuple[str, str, float]:
        base = self.electrode_order.get()
        if not (self.variety_enabled.get() and self.variety_electrode_morph.get()):
            return base, base, 0.0
        elapsed = (time.monotonic() if at_time is None else at_time) - self._variety_started
        stage_seconds = max(1.0, self.variety_electrode_morph_cycle.get() * 60.0)
        transition_seconds = min(stage_seconds,
            max(.1, self.variety_electrode_morph_transition_seconds.get()))
        full_cycle = stage_seconds * len(ELECTRODE_ORDERS)
        return sequence_cycle_stage(
            base, (elapsed % full_cycle) / full_cycle,
            transition_seconds / stage_seconds)

    def _sequence_morph_state(self, direction: int, stroke_progress: float,
                              variation_depth: float, at_time: float | None = None
                              ) -> tuple[str, str, float, str]:
        if self.four_phase_spatial_model.get() == "Depth spread":
            order = self.electrode_order.get()
            return order, order, 0.0, "depth spread"
        carousel_active = self.variety_enabled.get() and self.variety_electrode_morph.get()
        if self.four_phase_moving_sequence.get() and not carousel_active:
            source, target, amount = moving_sequence_window(
                self.electrode_order.get(), direction, stroke_progress,
                self.four_phase_moving_sequence_depth.get() * variation_depth,
                self.four_phase_moving_sequence_width.get())
            return source, target, amount, "window"
        source, target, amount = self._electrode_morph_state(at_time)
        return source, target, amount, ("carousel" if carousel_active else "stable")

    def _effective_crossover_width(self, speed_percent: float) -> float:
        if not self.four_phase_adaptive_crossover.get():
            return min(1.0, max(.05, self.four_phase_crossover_width.get()))
        return adaptive_crossover_width(
            speed_percent, self.four_phase_slow_crossover_width.get(),
            self.four_phase_fast_crossover_width.get())

    def _crossover_profile(self, speed_percent: float, direction: int,
                           stroke_progress: float = .5, variation_depth: float = 1.0
                           ) -> tuple[float, str, float, str]:
        variation_depth = min(1.0, max(0.0, variation_depth))
        base_width = min(1.0, max(.05, self.four_phase_crossover_width.get()))
        adaptive_width = self._effective_crossover_width(speed_percent)
        adaptive_width = base_width + (adaptive_width - base_width) * variation_depth
        reverse_scale = 1.0 + (self.four_phase_reverse_width_scale.get() - 1.0) * variation_depth
        width, curve, sharpness, direction_name = directional_crossover_profile(
            direction, adaptive_width,
            self.four_phase_crossover_curve.get(),
            self.four_phase_crossover_sharpness.get(),
            self.four_phase_directional_trajectory.get() and variation_depth > .001,
            reverse_scale,
            self.four_phase_reverse_curve.get(),
            (self.four_phase_crossover_sharpness.get() +
             (self.four_phase_reverse_sharpness.get() -
              self.four_phase_crossover_sharpness.get()) * variation_depth))
        acceleration_scale = 1.0 + (
            self.four_phase_acceleration_width_scale.get() - 1.0) * variation_depth
        deceleration_scale = 1.0 + (
            self.four_phase_deceleration_width_scale.get() - 1.0) * variation_depth
        width, phase_name = stroke_phase_crossover(
            width, stroke_progress, self.four_phase_stroke_phase_texture.get(),
            acceleration_scale, deceleration_scale)
        if self.four_phase_stroke_phase_texture.get():
            direction_name = f"{direction_name} {phase_name}"
        return width, curve, sharpness, direction_name

    def _spatial_path(self, output_l0: float, variation_depth: float = 1.0) -> float:
        return 1.0 - output_l0 if self.four_phase_invert.get() else output_l0

    def _four_phase_profile(
            self, path_l0: float, direction: int, speed_percent: float,
            stroke_progress: float, variation_depth: float,
            at_time: float | None = None
            ) -> tuple[tuple[float, float, float, float], str, str, float, str]:
        """Build logical E1-E4 values, then apply physical sequence mapping."""
        if self.four_phase_spatial_model.get() == "Depth spread":
            logical = depth_spread(
                path_l0, self.four_phase_tip_retention.get(),
                self.four_phase_spread_softness.get(),
                self.four_phase_full_depth_capture.get())
            order = self.electrode_order.get()
            return map_electrode_order(logical, order), order, order, 0.0, "depth spread"

        width, curve, sharpness, _ = self._crossover_profile(
            speed_percent, direction, stroke_progress, variation_depth)
        logical = restim_crossfade(
            path_l0, direction, self.four_phase_return_depth.get(),
            width, curve, sharpness)
        source, target, amount, kind = self._sequence_morph_state(
            direction, stroke_progress, variation_depth, at_time)
        if source == target:
            return map_electrode_order(logical, source), source, target, amount, kind
        return (morph_electrode_order(logical, source, target, amount),
                source, target, amount, kind)

    def apply_config(self) -> None:
        try:
            selected = MotionMode(self.mode.get())
            params = MotionParameters(self.minimum_radius.get(), self.speed_threshold.get(),
                                      self.direction_probability.get())
            pf_min, pf_max = self.pulse_frequency_min.get(), self.pulse_frequency_max.get()
            rise_min, rise_max = self.pulse_rise_min.get(), self.pulse_rise_max.get()
            width_min, width_max = self.pulse_width_min.get(), self.pulse_width_max.get()
            self.engine.configure(rate_hz=self.rate.get(), lookahead_seconds=self.lookahead.get(),
                                  volume=self.volume.get(), mode=selected, params=params,
                                  dynamic_volume=self.dynamic_volume.get(),
                                  volume_rest_level=self.volume_rest_level.get(),
                                  volume_ramp_speed_ratio=self.volume_ratio.get(),
                                  volume_ramp_up_seconds=self.volume_ramp_up.get(),
                                  frequency_ramp_level=self.frequency_ramp_level.get(),
                                  frequency_ramp_speed_ratio=self.frequency_ratio.get(),
                                  pulse_frequency_ratio=self.pulse_frequency_ratio.get(),
                                  pulse_frequency_min=pf_min,
                                  pulse_frequency_max=pf_max,
                                  pulse_rise_ratio=self.pulse_rise_ratio.get(),
                                  pulse_rise_min=rise_min,
                                  pulse_rise_max=rise_max,
                                  pulse_width_ratio=self.pulse_width_ratio.get(),
                                  pulse_width_min=width_min,
                                  pulse_width_max=width_max,
                                  prostate_narrow_ratio=self.prostate_narrow_ratio.get(),
                                  prostate_arc_depth=self.prostate_arc_depth.get(),
                                  prostate_stroke_threshold=self.prostate_threshold.get(),
                                  prostate_volume_multiplier=self.prostate_volume_multiplier.get(),
                                  prostate_rest_level=self.prostate_rest_level.get(),
                                  prostate_phase_degrees=self.prostate_phase_degrees.get(),
                                  jitter_enabled=self.jitter_enabled.get(),
                                  jitter_amplitude=self.jitter_amplitude.get(),
                                  jitter_cycle_seconds=self.jitter_cycle_seconds.get(),
                                  speed_linked_variation=self.speed_linked_variation.get(),
                                  variation_full_speed_percent=self.variation_full_speed_percent.get(),
                                  variation_fade_seconds=self.variation_fade_seconds.get(),
                                  spatial_curve=self.four_phase_spatial_curve.get(),
                                  spatial_blend=self.four_phase_spatial_blend.get())
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("Invalid settings", str(exc))

    def _load_settings(self) -> bool:
        saved = load_settings()
        for name in self.SETTINGS_FIELDS:
            if name in saved:
                try:
                    getattr(self, name).set(saved[name])
                except tk.TclError:
                    pass
        self._configure_media_timeline()
        try:
            defs = self.events_definitions_path.get().strip()
            if defs:
                self.event_engine.reload_definitions(defs)
            path = self.events_file_path.get().strip()
            if path:
                self.event_engine.load_events_file(path)
            else:
                self.event_engine.clear()
        except EventError as exc:
            self._set_events_status_text(f"Events: error - {exc}")
        else:
            self._update_events_status()
        routes = saved.get("authored_axis_routes", [])
        if isinstance(routes, list):
            blocked = self.media_timeline.timeline_axes()
            self.axis_router.set_enabled_axes(
                [axis for axis in routes if str(axis).upper() not in blocked])
        mode = saved.get("authored_routing_mode")
        if mode in ("Manual selected axes", "Auto authored ReStim set"):
            self.authored_routing_mode.set(mode)
        slots = saved.get("four_phase_presets", {})
        if isinstance(slots, dict):
            for slot in ("A", "B"):
                if isinstance(slots.get(slot), dict):
                    self._preset_slots[slot] = slots[slot]
        waypoints = saved.get("media_volume_ramp_waypoints", [])
        default_curve = normalize_curve_name(self.media_volume_ramp_curve.get())
        self._media_ramp_waypoints = normalize_waypoints(
            waypoints if isinstance(waypoints, list) else [], default_curve)
        if (self.media_volume_ramp_waypoints_enabled.get()
                and not self._media_ramp_waypoints):
            self._media_ramp_waypoints = [
                RampWaypoint(0.0, "floor1", default_curve)]
        return not bool(saved.get("first_run_complete"))

    def _save_settings(self) -> None:
        values = {name: getattr(self, name).get() for name in self.SETTINGS_FIELDS}
        values["four_phase_presets"] = self._preset_slots
        blocked = self.media_timeline.timeline_axes()
        values["authored_axis_routes"] = sorted(
            axis for axis in self.axis_router.enabled_axes() if axis not in blocked)
        values["authored_routing_mode"] = self.authored_routing_mode.get()
        default_curve = normalize_curve_name(self.media_volume_ramp_curve.get())
        values["media_volume_ramp_waypoints"] = [
            {"time_s": point.time_s, "level": point.level, "curve": point.curve}
            for point in normalize_waypoints(self._media_ramp_waypoints, default_curve)
        ]
        values["first_run_complete"] = True
        self._configure_media_timeline()
        save_settings(values)
    def _preset_snapshot(self) -> dict:
        return {name: getattr(self, name).get()
                for name in self.FOUR_PHASE_PRESET_FIELDS}

    def _baseline_preset(self) -> dict:
        baseline = self._preset_snapshot()
        baseline.update({
            "four_phase_spatial_model": "Moving focus",
            "four_phase_tip_retention": .80,
            "four_phase_spread_softness": .20,
            "four_phase_full_depth_capture": .05,
            "four_phase_return_depth": .30,
            "four_phase_invert": False,
            "four_phase_volume_ceiling": .85,
            "four_phase_volume_modulation": False,
            "four_phase_crossover_width": .50,
            "four_phase_crossover_curve": "Linear",
            "four_phase_crossover_sharpness": 1.0,
            "four_phase_adaptive_crossover": False,
            "four_phase_directional_trajectory": False,
            "four_phase_spatial_curve": "Linear",
            "four_phase_spatial_blend": 0.0,
            "four_phase_reversal_emphasis": False,
            "four_phase_stroke_phase_texture": False,
            "electrode_order": "ABCD",
        })
        return baseline

    def _capture_preset(self, slot: str) -> None:
        self._preset_slots[slot] = self._preset_snapshot()
        self._preset_active = slot
        name = self.preset_a_name.get() if slot == "A" else self.preset_b_name.get()
        self.preset_status.set(f"Captured and active: {slot} — {name}")
        self._save_settings()

    def _apply_preset(self, slot: str) -> None:
        target = self._baseline_preset() if slot == "Baseline" else self._preset_slots.get(slot)
        if not target:
            self.preset_status.set(f"Preset {slot} is empty; capture it first")
            return
        duration = max(.1, min(10.0, self.preset_transition_seconds.get()))
        self._preset_transition = (self._preset_snapshot(), target.copy(),
                                   time.monotonic(), duration, slot)
        self._preset_active = slot
        self.preset_status.set(f"Transitioning to {slot}")
        self._preset_transition_tick()

    def _preset_transition_tick(self) -> None:
        if self._preset_transition is None:
            return
        start, target, started, duration, slot = self._preset_transition
        progress = min(1.0, max(0.0, (time.monotonic() - started) / duration))
        eased = progress * progress * (3.0 - 2.0 * progress)
        for name in self.FOUR_PHASE_PRESET_FIELDS:
            old, new = start.get(name), target.get(name)
            if old is None or new is None:
                continue
            if isinstance(old, bool) or isinstance(new, bool) or isinstance(old, str):
                value = new if progress >= .5 else old
            else:
                value = old + (new - old) * eased
            getattr(self, name).set(value)
        self.preset_status.set(f"Transitioning to {slot}: {progress * 100:.0f}%")
        if progress < 1.0:
            self.root.after(20, self._preset_transition_tick)
        else:
            self._preset_transition = None
            label = ("Baseline" if slot == "Baseline" else
                     (self.preset_a_name.get() if slot == "A" else self.preset_b_name.get()))
            self.preset_status.set(f"Active: {slot} — {label}")

    def _toggle_ab_preset(self) -> None:
        self._apply_preset("B" if self._preset_active == "A" else "A")

    def _preset_matches(self, target: dict) -> bool:
        current = self._preset_snapshot()
        for name in self.FOUR_PHASE_PRESET_FIELDS:
            left, right = current.get(name), target.get(name)
            if isinstance(left, (int, float)) and not isinstance(left, bool):
                if abs(float(left) - float(right)) > 1e-4:
                    return False
            elif left != right:
                return False
        return True

    def show_preset_window(self) -> None:
        if self._preset_window is not None and self._preset_window.winfo_exists():
            self._preset_window.lift()
            return
        window = tk.Toplevel(self.root)
        self._preset_window = window
        window.title("Four-phase presets A/B")
        window.resizable(False, False)
        body = ttk.Frame(window, padding=14)
        body.grid(sticky="nsew")
        ttk.Label(body, text="Name").grid(row=0, column=1, sticky="w")
        for row, (slot, variable) in enumerate((("A", self.preset_a_name),
                                                ("B", self.preset_b_name)), start=1):
            ttk.Label(body, text=f"Preset {slot}").grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(body, textvariable=variable, width=24).grid(row=row, column=1, padx=6)
            ttk.Button(body, text="Capture current",
                       command=lambda s=slot: self._capture_preset(s)).grid(row=row, column=2, padx=4)
            ttk.Button(body, text="Apply",
                       command=lambda s=slot: self._apply_preset(s)).grid(row=row, column=3, padx=4)
        ttk.Button(body, text="Apply clean Baseline",
                   command=lambda: self._apply_preset("Baseline")).grid(
                       row=3, column=0, columnspan=2, sticky="w", pady=(10, 4))
        ttk.Label(body, text="Transition (s)").grid(row=3, column=2, sticky="e")
        ttk.Spinbox(body, from_=.1, to=10, increment=.1,
                    textvariable=self.preset_transition_seconds, width=7).grid(row=3, column=3)
        ttk.Label(body, textvariable=self.preset_status,
                  style="Muted.TLabel").grid(row=4, column=0, columnspan=4, sticky="w", pady=(10, 2))
        ttk.Label(body, text="Keyboard: [ applies A, ] applies B. Direct Xbox: hold LB and press RB to toggle A/B.",
                  style="Muted.TLabel").grid(row=5, column=0, columnspan=4, sticky="w")

    def show_setup_guide(self) -> None:
        messagebox.showinfo(
            "Vector 1A setup",
            "Safe visual commissioning\n\n"
            "1. Leave stimulation hardware disconnected.\n"
            "2. In MFP, send L0 by UDP or TCP to 127.0.0.1:12345.\n"
            "3. Set the MFP script offset to -2.00 seconds.\n"
            "4. Enable the primary ReStim WebSocket server and enter its port in Vector "
            "(normally 12346). Do NOT use ReStim's TCP port (commonly 12347).\n"
            "5. For prostate output, run a second ReStim WebSocket server on port 12350.\n"
            "6. Start the listener, connect both outputs, then select Start / Resume.\n\n"
            "Fine-tune MFP around -2.00 seconds while leaving Vector's delay at 2.00 seconds.\n\n"
            f"Settings are saved in:\n{settings_path()}"
        )

    def show_motion_guide(self) -> None:
        messagebox.showinfo(
            "Motion controls explained",
            "Motion modes\n\n"
            "Circular 0-180 follows a semicircle. Top-Left to Bottom-Right follows "
            "a 240-degree A-to-C arc with Vector's internal -30-degree alignment. "
            "Top-Right to Bottom-Left uses the corresponding +30-degree alignment. "
            "ReStim Original builds one arc per detected stroke.\n\n"
            "Base volume is the normal volume before the Volume response section "
            "and direction texture are applied.\n\n"
            "Smooth L0 position variation shifts the motion coordinate, not volume. "
            "Maximum shift 0.10 means up to approximately 0.10 either side of the "
            "scripted L0 position, clipped to the 0-1 range.\n\n"
            "Scale optional effects with speed fades variation out at rest and brings "
            "it in as motion accelerates. Full effects at speed is the calculated "
            "speed where the configured effects reach 100%; Response time controls "
            "how gently that depth changes. The live Effect depth readout shows the "
            "amount currently applied.\n\n"
            "Spatial response reshapes progress along the path without changing its "
            "endpoints. Blend 0 is linear; Blend 1 applies the selected curve fully.\n\n"
            "Boost volume near stroke reversal applies a short proportional increase "
            "around each known endpoint. A boost of 0.20 means up to 20% of the current "
            "volume, subject to the absolute 100% limit.\n\n"
            "Stroke-phase texture applies the selected volume multipliers according "
            "to whether L0 is rising or falling."
        )

    def show_four_phase_guide(self) -> None:
        messagebox.showinfo(
            "Four-phase controls explained",
            "Signal path\n\n"
            "The four green bars are the live E1-E4 commands sent to the Primary "
            "ReStim. Signalling sequence maps the logical path onto the physical "
            "electrodes. Moving focus replaces each electrode with the next as depth "
            "changes. Depth spread progressively retains A, then B and C as D joins; "
            "Tip retention controls how much A remains at full depth and Spread "
            "softness rounds each accumulating transition. Full-depth capture sets "
            "how much of the deepest L0 range holds D at 100%; 0.05 means the last "
            "5%. Reverse L0 direction "
            "swaps which end corresponds to low "
            "and high script positions. Return depth sets the preferred return "
            "electrode's relative negative contribution.\n\n"
            "Depth spread precedence\n\n"
            "Depth spread uses the selected static signalling sequence, but bypasses "
            "the sequence carousel, within-stroke sequence bias, crossover and "
            "direction textures, stroke-phase width changes, and A/B versus C/D "
            "timing separation. This keeps every transmitted profile inside ReStim's "
            "four-phase constraints. Spatial response and volume-only reversal "
            "emphasis remain active; speed-linked depth still scales compatible "
            "spatial and volume effects.\n\n"
            "Electrode crossover\n\n"
            "Crossover width controls how much of each transition is shared by two "
            "adjacent electrodes: smaller is more focused, larger is broader and "
            "smoother. Curve controls how that handover develops; Sharpness mainly "
            "changes the S-curve. Change crossover width with speed interpolates "
            "between the low- and high-speed widths.\n\n"
            "Stroke texture\n\n"
            "Use different return-stroke crossover gives falling/reverse motion its "
            "own width multiplier, curve and sharpness. Change width through each "
            "stroke moves smoothly from the accelerating multiplier to the "
            "decelerating multiplier.\n\n"
            "Timing and sequence movement\n\n"
            "A positive group delay makes A/B later; a negative value makes C/D "
            "later. Transition controls how gently a changed delay is introduced. "
            "Bias sequence within each stroke temporarily blends toward the next or "
            "previous signalling sequence near mid-stroke, returning to the selected "
            "sequence at the endpoints. Maximum blend is its strength; Stroke portion "
            "is how much of the stroke contains the effect.\n\n"
            "Slow volume variation uses up to Maximum addition over the selected "
            "cycle, never exceeding 100%. The live Sequence blend bar is a readout, "
            "not another setting."
        )

    def _send_sample(self, sample: OutputSample) -> None:
        variation_depth = (sample.variation_depth
                           if self.speed_linked_variation.get() else 1.0)
        motion_delta = sample.output_l0 - self._motion_send_last_l0
        if abs(motion_delta) > 0.0005:
            self._motion_send_direction = 1 if motion_delta > 0 else -1
        self._motion_send_last_l0 = sample.output_l0
        path_l0 = self._spatial_path(sample.output_l0, variation_depth)
        delta = path_l0 - self._four_phase_send_last_l0
        if abs(delta) > 0.0005:
            self._four_phase_send_direction = 1 if delta > 0 else -1
        self._four_phase_send_last_l0 = path_l0
        electrodes, morph_source, morph_target, morph_amount, profile_kind = \
            self._four_phase_profile(
            path_l0, self._four_phase_send_direction, sample.speed_percent,
            sample.stroke_progress, variation_depth, sample.due_at)
        self._four_phase_history.append((sample.due_at, electrodes))
        target_delay = 0.0
        if self.four_phase_group_delay.get() and profile_kind != "depth spread":
            target_delay = min(.300, max(-.300,
                self.four_phase_group_delay_ms.get() / 1000.0)) * variation_depth
        previous_time = self._four_phase_group_delay_last_time
        self._four_phase_group_delay_last_time = sample.due_at
        dt = max(0.0, sample.due_at - previous_time) if previous_time is not None else 0.0
        transition = max(.1, self.four_phase_group_delay_transition.get())
        blend = 1.0 - math.exp(-dt / transition) if dt > 0 else 0.0
        self._four_phase_effective_group_delay += (
            target_delay - self._four_phase_effective_group_delay) * blend
        if abs(target_delay) <= 1e-9 and abs(self._four_phase_effective_group_delay) < 1e-5:
            self._four_phase_effective_group_delay = 0.0
        if profile_kind == "depth spread":
            self._four_phase_effective_group_delay = 0.0
        else:
            electrodes = apply_group_delay(
                electrodes, list(self._four_phase_history), sample.due_at,
                self._four_phase_effective_group_delay)
        ceiling = min(1.0, max(0.0, self.four_phase_volume_ceiling.get()))
        if self.four_phase_volume_modulation.get():
            cycle = max(.5, self.four_phase_volume_cycle.get()) * 60.0
            wave = (1.0 - math.cos((sample.calculated_at % cycle)
                                   * 2.0 * math.pi / cycle)) / 2.0
            ceiling = min(1.0, ceiling +
                          self.four_phase_volume_headroom.get() * wave * variation_depth)
        primary_volume = min(1.0, max(0.0,
            ceiling * sample.volume / max(self.volume.get(), 1e-9)))
        if self.four_phase_stroke_phase_texture.get():
            configured = (self.motion_rising_volume_multiplier.get()
                          if self._motion_send_direction > 0
                          else self.motion_falling_volume_multiplier.get())
            multiplier = 1.0 + (min(1.0, max(.8, configured)) - 1.0) * variation_depth
            primary_volume *= multiplier
        if self.four_phase_reversal_emphasis.get():
            reversal = reversal_emphasis_envelope(
                sample.reversal_distance_seconds, self.four_phase_reversal_window.get())
            boost = min(1.0, max(0.0,
                self.four_phase_reversal_strength.get() * variation_depth))
            primary_volume = proportional_reversal_boost(
                primary_volume, reversal, boost)
        primary_volume = min(1.0, max(0.0, primary_volume))
        authored_overrides = self._authored_overrides_for_sample(sample.calculated_at)
        prostate_volume = min(1.0, max(0.0, sample.volume_prostate))
        ramp_gain = self._media_volume_gain_at(sample.calculated_at)
        if ramp_gain is not None:
            primary_volume = min(1.0, max(0.0, primary_volume * ramp_gain))
            prostate_volume = min(1.0, max(0.0, prostate_volume * ramp_gain))
            if "V0" in authored_overrides:
                authored_overrides = dict(authored_overrides)
                authored_overrides["V0"] = min(
                    1.0, max(0.0, authored_overrides["V0"] * ramp_gain))
        frequency = sample.frequency
        pulse_frequency = sample.pulse_frequency
        pulse_width = sample.pulse_width
        alpha = sample.alpha
        beta = sample.beta
        (primary_volume, prostate_volume, frequency, pulse_frequency, pulse_width,
         alpha, beta, electrodes, authored_overrides) = self._apply_events_to_sample(
            sample.due_at, primary_volume, prostate_volume, frequency,
            pulse_frequency, pulse_width, alpha, beta, electrodes,
            authored_overrides)
        with self._four_phase_live_lock:
            self._four_phase_live_output = (
                electrodes, morph_source, morph_target, morph_amount, profile_kind)
        self.restim.send_primary(
            alpha, beta, electrodes, primary_volume, frequency,
            pulse_frequency, sample.pulse_rise_time, pulse_width,
            overrides=authored_overrides)
        self.prostate_restim.send_prostate(
            sample.alpha_prostate, sample.beta_prostate, prostate_volume,
            frequency, pulse_frequency, pulse_width,
            sample.pulse_rise_time)

    def neutral(self) -> None:
        self.apply_config()
        self._reset_four_phase_group_delay()
        self._motion_send_last_l0 = 0.5
        self._motion_send_direction = 1
        self.engine.neutral()
        self.restim.send_primary(0.5, 0.5, (0.5, 0.5, 0.5, 0.5),
                                 self.volume.get(), 0.5, 0.5, 0.5, 0.5)
        with self._four_phase_live_lock:
            order = self.electrode_order.get()
            self._four_phase_live_output = (
                (0.5, 0.5, 0.5, 0.5), order, order, 0.0, "neutral")
        self.prostate_restim.send_prostate(0.5, 0.5, self.volume.get(), 0.5, 0.5, 0.5, 0.5)

    def resume(self) -> None:
        self.apply_config()
        self._reset_four_phase_group_delay()
        self.engine.resume()

    def stop(self) -> None:
        self._reset_four_phase_group_delay()
        self._motion_send_last_l0 = 0.5
        self._motion_send_direction = 1
        self.engine.stop()
        self.restim.send_primary(0.5, 0.5, (0.5, 0.5, 0.5, 0.5),
                                 0.0, 0.5, 0.5, 0.5, 0.5)
        with self._four_phase_live_lock:
            order = self.electrode_order.get()
            self._four_phase_live_output = (
                (0.5, 0.5, 0.5, 0.5), order, order, 0.0, "stopped")
        self.prostate_restim.send_prostate(0.5, 0.5, 0.0, 0.5, 0.5, 0.5, 0.5)

    def _reset_four_phase_group_delay(self) -> None:
        self._four_phase_history.clear()
        self._four_phase_effective_group_delay = 0.0
        self._four_phase_group_delay_last_time = None

    def _refresh(self) -> None:
        self._drain_controller_events()
        if not self._startup_in_progress and self.session_ready_status.get() == "SESSION: READY":
            missing = []
            if self.auto_start_restim.get() and not self.restim.connected:
                missing.append("Primary")
            if self.auto_start_prostate.get() and not self.prostate_restim.connected:
                missing.append("Prostate")
            if missing:
                self.session_ready_status.set("SESSION: ATTENTION")
                self._set_startup_status("Connection lost after READY: " + ", ".join(missing))
        self._update_variety()
        self._configure_media_timeline()
        timeline = self.media_timeline.snapshot(time.monotonic())
        if timeline.usable and timeline.position_s is not None:
            duration = ("unknown" if timeline.duration_s is None
                        else f"{timeline.duration_s:.1f} s")
            progress = ("--" if timeline.progress is None
                        else f"{timeline.progress * 100:.1f}%")
            suffix = " (held)" if timeline.held else ""
            self.timeline_status.set(
                f"Media timeline: {timeline.position_s:.1f} s / {duration}  "
                f"progress={progress}{suffix}")
        else:
            self.timeline_status.set("Media timeline: none")
        if self.media_volume_ramp_enabled.get():
            if (self.media_volume_ramp_waypoints_enabled.get()
                    and self._media_ramp_waypoints):
                floor1, ceiling1, floor2, ceiling2, floor3, ceiling3 = (
                    self._media_ramp_level_args())
                gain = media_volume_gain_waypoints(
                    timeline.position_s if timeline.usable else None,
                    self._media_ramp_waypoints,
                    floor1, ceiling1, floor2, ceiling2, floor3, ceiling3,
                    self.media_volume_ramp_curve.get())
                if timeline.usable and timeline.position_s is not None:
                    time_text = format_media_time(timeline.position_s)
                    if timeline.held:
                        time_text += " held"
                else:
                    time_text = "--"
                self.media_ramp_status.set(
                    f"Media ramp: waypoints @ {time_text} → gain {gain * 100:.1f}% "
                    f"({self.media_volume_ramp_curve.get()}, "
                    f"{len(self._media_ramp_waypoints)} pts)")
            else:
                gain = media_volume_gain(
                    timeline.progress,
                    self.media_volume_ramp_floor.get(),
                    self.media_volume_ramp_ceiling.get(),
                    self.media_volume_ramp_curve.get())
                if timeline.progress is None:
                    progress_text = "--"
                else:
                    progress_text = f"{timeline.progress * 100:.1f}%"
                    if timeline.held:
                        progress_text += " held"
                self.media_ramp_status.set(
                    f"Media ramp: media {progress_text} → gain {gain * 100:.1f}% "
                    f"({self.media_volume_ramp_curve.get()})")
        else:
            self.media_ramp_status.set("Media ramp: off")
        self._update_events_status(getattr(timeline, "position_ms", None))
        if self._preset_transition is None and self._preset_active:
            target = (self._baseline_preset() if self._preset_active == "Baseline"
                      else self._preset_slots.get(self._preset_active))
            if target:
                label = ("Baseline" if self._preset_active == "Baseline" else
                         (self.preset_a_name.get() if self._preset_active == "A"
                          else self.preset_b_name.get()))
                suffix = "" if self._preset_matches(target) else " (modified)"
                self.preset_status.set(
                    f"Active: {self._preset_active} — {label}{suffix}")
        diag = self.engine.diagnostics()
        values = {
            "raw_l0": f"{diag.raw_l0:.4f}", "output_l0": f"{diag.output_l0:.4f}",
            "speed": f"{diag.speed_percent:.2f}%", "alpha": f"{diag.alpha:.4f}",
            "beta": f"{diag.beta:.4f}", "buffer": f"{diag.buffer_fill} samples",
            "lookahead": f"{diag.lookahead_seconds:.3f} s",
            "actual_delay": f"{diag.actual_queue_delay:.4f} s" if diag.output_samples else "--",
            "input_count": str(diag.input_samples), "output_count": str(diag.output_samples),
            "state": diag.state,
            "active_mode": self.engine.mode.value,
            "output_mode": diag.output_mode,
            "output_volume": f"{diag.output_volume * 100:.1f}%",
            "frequency": f"{diag.frequency:.4f}",
            "pulse_frequency": f"{diag.pulse_frequency:.4f}",
            "pulse_rise_time": f"{diag.pulse_rise_time:.4f}",
            "pulse_width": f"{diag.pulse_width:.4f}",
            "alpha_prostate": f"{diag.alpha_prostate:.4f}",
            "beta_prostate": f"{diag.beta_prostate:.4f}",
            "volume_prostate": f"{diag.volume_prostate * 100:.1f}%",
            "variation_depth": f"{diag.variation_depth * 100:.1f}%",
        }
        for key, value in values.items():
            self.diag_vars[key].set(value)
        self.variation_depth_live.set(f"Effect depth {diag.variation_depth * 100:.0f}%")
        self.frequency_bar["value"] = diag.frequency
        self.frequency_value.set(f"{diag.frequency:.4f}")
        self.pulse_frequency_bar.set(self.pulse_frequency_min.get(),
                                     self.pulse_frequency_max.get(), diag.pulse_frequency)
        self.pulse_frequency_value.set(f"{diag.pulse_frequency:.4f}")
        self.pulse_rise_bar.set(self.pulse_rise_min.get(), self.pulse_rise_max.get(),
                                diag.pulse_rise_time)
        self.pulse_rise_value.set(f"{diag.pulse_rise_time:.4f}")
        self.pulse_width_bar.set(self.pulse_width_min.get(), self.pulse_width_max.get(),
                                 diag.pulse_width)
        self.pulse_width_value.set(f"{diag.pulse_width:.4f}")
        for key, value in (("alpha_prostate", diag.alpha_prostate),
                           ("beta_prostate", diag.beta_prostate),
                           ("volume_prostate", diag.volume_prostate)):
            self.prostate_bars[key]["value"] = value
            self.prostate_values[key].set(f"{value:.4f}")
        variation_depth = (diag.variation_depth
                           if self.speed_linked_variation.get() else 1.0)
        path_l0 = self._spatial_path(diag.output_l0, variation_depth)
        self.four_phase_spatial_live.set(f"live {path_l0:.3f}")
        reversal = (reversal_emphasis_envelope(
            diag.reversal_distance_seconds, self.four_phase_reversal_window.get())
            * variation_depth
            if self.four_phase_reversal_emphasis.get() else 0.0)
        self.four_phase_reversal_live.set(f"live {reversal:.3f}")
        delay_ms = self._four_phase_effective_group_delay * 1000.0
        delayed_group = "A/B later" if delay_ms > .5 else ("C/D later" if delay_ms < -.5 else "aligned")
        self.four_phase_group_delay_live.set(f"live {delay_ms:+.0f} ms | {delayed_group}")
        four_phase = vertical_crossfade(path_l0)
        for bar, variable, value in zip(self.four_phase_bars, self.four_phase_values,
                                        four_phase):
            bar["value"] = value
            variable.set(f"{value:.4f}")
        delta = path_l0 - self._four_phase_last_l0
        if abs(delta) > 0.0005:
            self._four_phase_direction = 1 if delta > 0 else -1
        self._four_phase_last_l0 = path_l0
        depth_mode = self.four_phase_spatial_model.get() == "Depth spread"
        if depth_mode:
            signed = depth_spread(
                path_l0, self.four_phase_tip_retention.get(),
                self.four_phase_spread_softness.get(),
                self.four_phase_full_depth_capture.get())
            self.four_phase_effective_crossover_width.set("bypassed")
            self.four_phase_stroke_phase_live.set("bypassed by Depth spread")
            self.four_phase_model_live.set(
                "Depth spread: static sequence only; sequence bias, crossover, "
                "direction, width texture and AB/CD delay are bypassed")
        else:
            signed = directed_signed(
                path_l0, self._four_phase_direction,
                self.four_phase_return_depth.get())[0]
            effective_crossover, _, _, direction_name = self._crossover_profile(
                diag.speed_percent, self._four_phase_direction,
                diag.stroke_progress, variation_depth)
            self.four_phase_effective_crossover_width.set(
                f"{direction_name} {effective_crossover:.3f}")
            if self.four_phase_stroke_phase_texture.get():
                phase_name = "accelerating" if diag.stroke_progress < .5 else "decelerating"
                self.four_phase_stroke_phase_live.set(
                    f"{phase_name} {diag.stroke_progress:.2f} | live {effective_crossover:.3f}")
            else:
                self.four_phase_stroke_phase_live.set("off")
            self.four_phase_model_live.set("Moving focus: crossover and sequence textures available")
        with self._four_phase_live_lock:
            (potentials, morph_source, morph_target,
             morph_amount, morph_kind) = self._four_phase_live_output
        self.electrode_morph_bar["value"] = morph_amount
        if morph_kind == "window":
            self.four_phase_moving_sequence_live.set(
                f"{morph_source}→{morph_target} | {morph_amount * 100:.0f}%")
        elif morph_kind == "carousel" and self.four_phase_moving_sequence.get():
            self.four_phase_moving_sequence_live.set("carousel priority")
        elif morph_kind == "depth spread":
            self.four_phase_moving_sequence_live.set("bypassed by Depth spread")
        else:
            self.four_phase_moving_sequence_live.set("off")
        for variable, value in zip(self.four_phase_signed_values, signed):
            variable.set(f"{value:+.4f}")
        for bar, variable, value in zip(self.four_phase_potential_bars,
                                        self.four_phase_potential_values, potentials):
            bar["value"] = value
            variable.set(f"{value:.4f}")
        primary, preferred_return = potential_roles(potentials)
        if morph_kind == "depth spread":
            sequence_status = f"Depth spread | static sequence {morph_source}"
        elif morph_source == morph_target:
            sequence_status = f"Current sequence {morph_source}"
        elif morph_amount <= .001:
            sequence_status = (f"Current sequence {morph_source} | next "
                               f"{morph_target}")
        elif morph_amount >= .999:
            sequence_status = (f"Current sequence {morph_target} | next stage pending")
        else:
            sequence_status = (f"{morph_source} morphing toward {morph_target} | "
                               f"{morph_amount * 100:.0f}%")
        self.four_phase_roles.set(sequence_status)
        current = self.listener.connection_label()
        if current.startswith(("Receiving", "Listening")):
            if not self.mfp_status.get().startswith("MFP "):
                self.mfp_status.set(current)
        summaries = {
            "MultiFunPlayer input": f"{self.mfp_status.get()} | {self.mfp_host.get()}:{self.mfp_port.get()}",
            "ReStim output": (
                f"Primary: {self._brief_conn_status(self.restim_status.get())} | "
                f"Prostate: {self._brief_conn_status(self.prostate_status.get())}"),
            "Motion": f"{self.mode.get()} | {self.rate.get()} Hz | {self.lookahead.get():.2f} s delay",
            "Volume response": (
                f"Base {self.volume.get() * 100:.0f}% | "
                f"primary ceiling {self.four_phase_volume_ceiling.get() * 100:.0f}% | "
                f"rest {self.volume_rest_level.get() * 100:.0f}%"
            ),
            "Frequency": f"{diag.frequency:.3f} | ramp {self.frequency_ramp_level.get():.2f}",
            "Pulse frequency": f"{diag.pulse_frequency:.3f} | range {self.pulse_frequency_min.get():.2f}-{self.pulse_frequency_max.get():.2f}",
            "Pulse rise time": f"{diag.pulse_rise_time:.3f} | range {self.pulse_rise_min.get():.2f}-{self.pulse_rise_max.get():.2f}",
            "Pulse width": f"{diag.pulse_width:.3f} | range {self.pulse_width_min.get():.2f}-{self.pulse_width_max.get():.2f}",
            "Prostate controls": f"alpha {diag.alpha_prostate:.3f} beta {diag.beta_prostate:.3f} volume {diag.volume_prostate * 100:.0f}% | phase {self.prostate_phase_degrees.get():+.0f} degrees",
            "Four-phase primary motion": (
                f"{sequence_status} | "
                f"E1 {potentials[0]:.2f} E2 {potentials[1]:.2f} "
                f"E3 {potentials[2]:.2f} E4 {potentials[3]:.2f}"),
            "Xbox controller": f"{self.controller_status.get()} | step {self.controller_fine_step.get():.2f}",
            "Rolling Variety": self.variety_status.get(),
            "Commissioning controls": diag.state,
            "Live diagnostics": (f"{diag.state} | buffer {diag.buffer_fill} | delay {diag.actual_queue_delay:.4f} s"
                                 if diag.output_samples else
                                 f"{diag.state} | buffer {diag.buffer_fill} | waiting for output"),
            "Remote control API": self.control_api_status.get(),
        }
        for title, summary in summaries.items():
            section = self.sections.get(title)
            if section is not None:
                section.summary.set(summary)
        self.root.after(100, self._refresh)

    def _sync_control_api_server(self) -> None:
        if self._control_api is not None:
            self._control_api.stop()
            self._control_api = None
        if not self.control_api_enabled.get():
            self.control_api_status.set("Off")
            return
        host = self.control_api_host.get().strip() or "0.0.0.0"
        try:
            port = int(self.control_api_port.get())
        except (tk.TclError, TypeError, ValueError):
            self.control_api_status.set("Invalid port")
            return
        try:
            server = ControlApiServer(self, host, port)
            server.start()
            self._control_api = server
            self.control_api_status.set(f"Listening on {host}:{port}")
            self._save_settings()
        except OSError as exc:
            self.control_api_status.set(f"Bind failed: {exc}")

    def _writable_control_fields(self) -> tuple[str, ...]:
        return writable_fields(self.SETTINGS_FIELDS)

    def _coerce_control_value(self, var: tk.Variable, value: object) -> object:
        if isinstance(var, tk.BooleanVar):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if isinstance(var, tk.IntVar):
            return int(value)
        if isinstance(var, tk.DoubleVar):
            return float(value)
        return str(value)

    def _control_state_ui(self) -> dict:
        settings = {}
        for name in self._writable_control_fields():
            try:
                settings[name] = getattr(self, name).get()
            except (tk.TclError, AttributeError):
                continue
        status = {}
        for name in STATUS_KEYS:
            try:
                status[name] = getattr(self, name).get()
            except (tk.TclError, AttributeError):
                status[name] = None
        diag = self.engine.diagnostics()
        diagnostics = {
            "raw_l0": diag.raw_l0,
            "output_l0": diag.output_l0,
            "speed_percent": diag.speed_percent,
            "alpha": diag.alpha,
            "beta": diag.beta,
            "buffer_fill": diag.buffer_fill,
            "lookahead_seconds": diag.lookahead_seconds,
            "actual_queue_delay": diag.actual_queue_delay,
            "input_samples": diag.input_samples,
            "output_samples": diag.output_samples,
            "state": diag.state,
            "output_mode": diag.output_mode,
            "output_volume": diag.output_volume,
            "frequency": diag.frequency,
            "pulse_frequency": diag.pulse_frequency,
            "pulse_rise_time": diag.pulse_rise_time,
            "pulse_width": diag.pulse_width,
            "alpha_prostate": diag.alpha_prostate,
            "beta_prostate": diag.beta_prostate,
            "volume_prostate": diag.volume_prostate,
            "variation_depth": diag.variation_depth,
            "active_mode": self.engine.mode.value,
        }
        with self._four_phase_live_lock:
            electrodes, morph_source, morph_target, morph_amount, profile_kind = (
                self._four_phase_live_output)
        meters = {
            "frequency": diag.frequency,
            "pulse_frequency": diag.pulse_frequency,
            "pulse_rise_time": diag.pulse_rise_time,
            "pulse_width": diag.pulse_width,
            "alpha": diag.alpha,
            "beta": diag.beta,
            "output_volume": diag.output_volume,
            "alpha_prostate": diag.alpha_prostate,
            "beta_prostate": diag.beta_prostate,
            "volume_prostate": diag.volume_prostate,
            "e1": electrodes[0],
            "e2": electrodes[1],
            "e3": electrodes[2],
            "e4": electrodes[3],
            "four_phase_morph_source": morph_source,
            "four_phase_morph_target": morph_target,
            "four_phase_morph_amount": morph_amount,
            "four_phase_profile": profile_kind,
        }
        return {
            "version": __version__,
            "settings": settings,
            "status": status,
            "diagnostics": diagnostics,
            "meters": meters,
            "presets": {
                "active": self._preset_active,
                "a_filled": "A" in self._preset_slots,
                "b_filled": "B" in self._preset_slots,
                "a_name": self.preset_a_name.get(),
                "b_name": self.preset_b_name.get(),
                "transition_seconds": self.preset_transition_seconds.get(),
                "status": self.preset_status.get(),
            },
        }

    def _control_schema_ui(self) -> dict:
        writable = list(self._writable_control_fields())
        return {
            "version": 1,
            "app_version": __version__,
            "writable": writable,
            "readonly_status": list(STATUS_KEYS),
            "actions": list(ACTIONS),
            "panels": {name: list(fields) for name, fields in PANEL_FIELDS.items()},
        }

    def _control_patch_ui(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            return {"ok": False, "error": "body must be a JSON object"}
        # Accept either flat {field: value} or {settings: {...}}
        values = patch.get("settings") if isinstance(patch.get("settings"), dict) else patch
        allowed = set(self._writable_control_fields())
        applied: list[str] = []
        unknown: list[str] = []
        errors: list[str] = []
        for key, value in values.items():
            if key in ("settings", "status", "diagnostics", "meters", "version"):
                continue
            if key not in allowed:
                unknown.append(key)
                continue
            var = getattr(self, key, None)
            if var is None:
                unknown.append(key)
                continue
            try:
                var.set(self._coerce_control_value(var, value))
                applied.append(key)
            except (tk.TclError, TypeError, ValueError) as exc:
                errors.append(f"{key}: {exc}")
        if applied:
            self.apply_config()
        if any(name.startswith("variety_") for name in applied):
            self._variety_toggle()
        if "controller_enabled" in applied or "direct_controller_enabled" in applied:
            self._controller_enabled_changed()
        if any(name.startswith("media_volume_ramp_") for name in applied):
            self._on_media_ramp_levels_changed()
            self._save_settings()
        if "events_enabled" in applied:
            self._on_events_enabled_changed()
        if any(name in CONTROL_META_FIELDS for name in applied):
            # Defer restart — must not shutdown the HTTP server from inside a request.
            self.root.after(50, self._sync_control_api_server)
        ok = not errors
        return {
            "ok": ok,
            "applied": applied,
            "unknown": unknown,
            "errors": errors,
            "state": self._control_state_ui(),
        }

    def _control_action_ui(self, name: str) -> dict:
        actions = {
            "neutral": self.neutral,
            "stop": self.stop,
            "resume": self.resume,
            "start_listener": self.start_listener,
            "stop_listener": self.listener.stop,
            "connect_restim": self.connect_restim,
            "disconnect_restim": self.restim.disconnect,
            "connect_prostate": self.connect_prostate,
            "disconnect_prostate": self.prostate_restim.disconnect,
            "apply_preset_a": lambda: self._apply_preset("A"),
            "apply_preset_b": lambda: self._apply_preset("B"),
            "apply_preset_baseline": lambda: self._apply_preset("Baseline"),
            "capture_preset_a": lambda: self._capture_preset("A"),
            "capture_preset_b": lambda: self._capture_preset("B"),
            "toggle_preset_ab": self._toggle_ab_preset,
        }
        handler = actions.get(name)
        if handler is None:
            return {"ok": False, "error": f"unknown action: {name}", "actions": list(ACTIONS)}
        handler()
        return {"ok": True, "action": name, "state": self._control_state_ui()}

    def control_schema(self) -> dict:
        return run_on_ui(self.root, self._control_schema_ui)

    def control_state(self) -> dict:
        return run_on_ui(self.root, self._control_state_ui)

    def control_patch(self, patch: dict) -> dict:
        return run_on_ui(self.root, lambda: self._control_patch_ui(patch))

    def control_action(self, name: str) -> dict:
        return run_on_ui(self.root, lambda: self._control_action_ui(name))

    def close(self) -> None:
        self._save_settings()
        if self._control_api is not None:
            self._control_api.stop()
            self._control_api = None
        self.xinput.close()
        self.engine.close()
        self.listener.stop()
        self.restim.close()
        self.prostate_restim.close()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    VectorApp(root)
    root.mainloop()
