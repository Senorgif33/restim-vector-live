from __future__ import annotations

import tkinter as tk
import time
import queue
import math
from collections import deque
from tkinter import messagebox, ttk

from .engine import OutputSample, VectorEngine
from .motion import MotionMode, MotionParameters
from .network import MFPListener, ReStimWebSocketClient
from .settings import load_settings, save_settings, settings_path
from .controller import (A, B, X, Y, START, LEFT_SHOULDER, RIGHT_SHOULDER, DPAD_UP, DPAD_DOWN,
                         DPAD_LEFT, DPAD_RIGHT, XInputController)
from .variety import fit_range_for_travel, rolling_offset, rolling_value
from .fourphase import (ELECTRODE_ORDERS, adaptive_crossover_width, apply_group_delay, directed_signed,
                        directional_crossover_profile, map_electrode_order,
                        morph_electrode_order, moving_sequence_window, potential_roles, sequence_cycle_stage,
                        proportional_reversal_boost, reversal_emphasis_envelope,
                        stroke_phase_crossover, restim_crossfade, vertical_crossfade)
from . import __version__


class RangeBar(tk.Canvas):
    def __init__(self, parent, width=520, height=24):
        super().__init__(parent, width=width, height=height, highlightthickness=1,
                         highlightbackground="#999", background="#e6e6e6")
        self._width, self._height = width, height

    def set(self, minimum: float, maximum: float, value: float) -> None:
        minimum, maximum, value = (min(1.0, max(0.0, x)) for x in (minimum, maximum, value))
        self.delete("all")
        self.create_rectangle(minimum * self._width, 1, maximum * self._width,
                              self._height - 1, fill="#08ae2a", outline="")
        x = value * self._width
        self.create_line(x, 0, x, self._height, fill="#173b8f", width=3)


class CollapsibleSection(ttk.Frame):
    def __init__(self, parent, title: str, collapsed: bool = False):
        super().__init__(parent, padding=(2, 2))
        self.title = title
        self.collapsed = collapsed
        self.summary = tk.StringVar(value="")
        self.button = ttk.Button(self, width=3, command=self.toggle)
        self.button.grid(row=0, column=0, padx=(2, 5))
        ttk.Label(self, text=title, font=("TkDefaultFont", 10, "bold")) \
            .grid(row=0, column=1, sticky="w")
        ttk.Label(self, textvariable=self.summary, foreground="#555") \
            .grid(row=0, column=2, sticky="w", padx=12)
        self.columnconfigure(2, weight=1)
        self.body = ttk.Frame(self, padding=(10, 6))
        self.body.grid(row=1, column=0, columnspan=3, sticky="nsew")
        ttk.Separator(self, orient="horizontal").grid(row=2, column=0, columnspan=3, sticky="ew")
        self._render()

    def toggle(self) -> None:
        self.collapsed = not self.collapsed
        self._render()

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
    )
    SETTINGS_FIELDS = (
        "mfp_host", "mfp_port", "restim_host", "restim_port", "prostate_host", "prostate_port",
        "four_phase_host", "four_phase_port",
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
    )

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(f"Vector 1A {__version__} - MFP to ReStim")
        root.geometry("1360x900")
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
        self.listener = MFPListener(self.engine.receive_l0, self._set_mfp_status)
        self.xinput = XInputController(self._xinput_buttons_threaded, self._xinput_status_threaded)
        self.sections = {}
        self._first_run = self._load_settings()
        self._build()
        self._bind_controller_keys()
        self._controller_enabled_changed()
        self.xinput.start()
        self.engine.start()
        self.root.after(100, self._refresh)
        if self._first_run:
            self.root.after(500, self.show_setup_guide)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _frame(self, title: str, row: int, column: int = 0, span: int = 1) -> ttk.Frame:
        collapsed_titles = {
            "Frequency", "Pulse frequency", "Pulse rise time", "Pulse width",
            "Prostate controls", "Four-phase primary motion",
            "Xbox controller", "Rolling Variety", "Live diagnostics",
        }
        section = CollapsibleSection(self.root, title, collapsed=(title in collapsed_titles))
        section.grid(row=row + 1, column=column, columnspan=span, sticky="nsew", padx=10, pady=3)
        self.sections[title] = section
        return section.body

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(12, weight=1)

        toolbar = ttk.Frame(self.root, padding=(12, 6))
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(toolbar, text="ReStim Vector Live", font=("TkDefaultFont", 10, "bold")).pack(side="left")
        ttk.Button(toolbar, text="START / RESUME", command=self.resume).pack(side="left", padx=(24, 6))
        ttk.Button(toolbar, text="Neutral", command=self.neutral).pack(side="left", padx=6)
        ttk.Button(toolbar, text="STOP", command=self.stop).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Setup guide", command=self.show_setup_guide).pack(side="left", padx=(18, 6))
        ttk.Button(toolbar, text="Rolling Variety", command=self.show_variety_window).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Presets A/B", command=self.show_preset_window).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Connection log", command=self.show_connection_log).pack(side="left", padx=6)
        ttk.Label(toolbar, textvariable=self.diag_vars["state"]).pack(side="right", padx=8)

        mfp = self._frame("MultiFunPlayer input", 0, 0)
        ttk.Label(mfp, text="Bind address").grid(row=0, column=0, sticky="w")
        ttk.Entry(mfp, textvariable=self.mfp_host, width=16).grid(row=0, column=1, padx=5)
        ttk.Label(mfp, text="Port").grid(row=0, column=2)
        ttk.Spinbox(mfp, from_=1, to=65535, textvariable=self.mfp_port, width=7).grid(row=0, column=3, padx=5)
        ttk.Button(mfp, text="Start listener", command=self.start_listener).grid(row=1, column=0, pady=8)
        ttk.Button(mfp, text="Stop listener", command=self.listener.stop).grid(row=1, column=1, pady=8)
        ttk.Label(mfp, textvariable=self.mfp_status).grid(row=1, column=2, columnspan=2, sticky="w")

        restim = self._frame("ReStim output", 0, 1)
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
                  foreground="#555").grid(row=4, column=5, sticky="w", padx=8)
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
                  foreground="#555").grid(row=6, column=5, sticky="w", padx=8)
        ttk.Checkbutton(motion, text="Stroke-phase texture",
                        variable=self.four_phase_stroke_phase_texture).grid(row=7, column=0, sticky="w")
        ttk.Label(motion, text="L0 rising volume ×").grid(row=7, column=1, sticky="e")
        ttk.Spinbox(motion, from_=.8, to=1, increment=.01,
                    textvariable=self.motion_rising_volume_multiplier, width=8).grid(row=7, column=2, sticky="w")
        ttk.Label(motion, text="L0 falling volume ×").grid(row=7, column=3, sticky="e")
        ttk.Spinbox(motion, from_=.8, to=1, increment=.01,
                    textvariable=self.motion_falling_volume_multiplier, width=8).grid(row=7, column=4, sticky="w")
        ttk.Label(motion, text="Shared by 3-phase and 4-phase",
                   foreground="#555").grid(row=7, column=5, sticky="w", padx=8)
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

        frequency_frame = self._frame("Frequency", 3, 0, 2)
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

        pulse_frame = self._frame("Pulse frequency", 4, 0, 2)
        ttk.Label(pulse_frame, text="0").grid(row=0, column=0)
        self.pulse_frequency_bar = RangeBar(pulse_frame)
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

        rise_frame = self._frame("Pulse rise time", 5, 0, 2)
        ttk.Label(rise_frame, text="0 sharp").grid(row=0, column=0)
        self.pulse_rise_bar = RangeBar(rise_frame)
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
        width_frame = self._frame("Pulse width", 6, 0, 2)
        ttk.Label(width_frame, text="0 narrow").grid(row=0, column=0)
        self.pulse_width_bar = RangeBar(width_frame)
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

        prostate = self._frame("Prostate controls", 7, 0, 2)
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

        four_phase = self._frame("Four-phase primary motion", 8, 0, 2)
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
                  text="Live E1-E4 Primary output; adjacent electrodes blend continuously.",
                  foreground="#9b4b00").grid(row=0, column=5, rowspan=4, sticky="w", padx=18)
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
                  foreground="#555").grid(row=13, column=5, sticky="w")
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
                   foreground="#555").grid(row=14, column=5, sticky="w")
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
                  foreground="#9b4b00").grid(row=20, column=5, rowspan=4, sticky="w", padx=18)
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

        controller = self._frame("Xbox controller", 9, 0, 2)
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

        variety = self._frame("Rolling Variety", 10, 0, 2)
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

        controls = self._frame("Commissioning controls", 11, 0, 2)
        ttk.Button(controls, text="Neutral", command=self.neutral, width=18).pack(side="left", padx=12)
        ttk.Button(controls, text="Resume", command=self.resume, width=18).pack(side="left", padx=12)
        ttk.Button(controls, text="STOP", command=self.stop, width=18).pack(side="left", padx=12)
        ttk.Label(controls, text="Test without FOCstim hardware connected.").pack(side="right", padx=12)
        self.sections["Commissioning controls"].grid_remove()

        diagnostics = self._frame("Live diagnostics", 12, 0, 2)
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

    def _set_mfp_status(self, text: str) -> None:
        self._record_connection_event("MFP", text)
        self.root.after(0, self.mfp_status.set, text)

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
        slots = saved.get("four_phase_presets", {})
        if isinstance(slots, dict):
            for slot in ("A", "B"):
                if isinstance(slots.get(slot), dict):
                    self._preset_slots[slot] = slots[slot]
        return not bool(saved.get("first_run_complete"))

    def _save_settings(self) -> None:
        values = {name: getattr(self, name).get() for name in self.SETTINGS_FIELDS}
        values["four_phase_presets"] = self._preset_slots
        values["first_run_complete"] = True
        save_settings(values)

    def _preset_snapshot(self) -> dict:
        return {name: getattr(self, name).get()
                for name in self.FOUR_PHASE_PRESET_FIELDS}

    def _baseline_preset(self) -> dict:
        baseline = self._preset_snapshot()
        baseline.update({
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
                  foreground="#555").grid(row=4, column=0, columnspan=4, sticky="w", pady=(10, 2))
        ttk.Label(body, text="Keyboard: [ applies A, ] applies B. Direct Xbox: hold LB and press RB to toggle A/B.",
                  foreground="#555").grid(row=5, column=0, columnspan=4, sticky="w")

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
            "electrodes. Reverse L0 direction swaps which end corresponds to low "
            "and high script positions. Return depth sets the preferred return "
            "electrode's relative negative contribution.\n\n"
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
        crossover_width, crossover_curve, crossover_sharpness, _ = \
            self._crossover_profile(sample.speed_percent,
                                    self._four_phase_send_direction,
                                    sample.stroke_progress, variation_depth)
        electrodes = restim_crossfade(
            path_l0, self._four_phase_send_direction,
            self.four_phase_return_depth.get(), crossover_width,
            crossover_curve, crossover_sharpness)
        morph_source, morph_target, morph_amount, _ = self._sequence_morph_state(
            self._four_phase_send_direction, sample.stroke_progress,
            variation_depth, sample.due_at)
        if morph_source == morph_target:
            electrodes = map_electrode_order(electrodes, morph_source)
        else:
            electrodes = morph_electrode_order(
                electrodes, morph_source, morph_target, morph_amount)
        self._four_phase_history.append((sample.due_at, electrodes))
        target_delay = 0.0
        if self.four_phase_group_delay.get():
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
        self.restim.send_primary(
            sample.alpha, sample.beta, electrodes, primary_volume, sample.frequency,
            sample.pulse_frequency, sample.pulse_rise_time, sample.pulse_width)
        self.prostate_restim.send_prostate(
            sample.alpha_prostate, sample.beta_prostate, sample.volume_prostate,
            sample.frequency, sample.pulse_frequency, sample.pulse_width,
            sample.pulse_rise_time)

    def neutral(self) -> None:
        self.apply_config()
        self._reset_four_phase_group_delay()
        self._motion_send_last_l0 = 0.5
        self._motion_send_direction = 1
        self.engine.neutral()
        self.restim.send_primary(0.5, 0.5, (0.5, 0.5, 0.5, 0.5),
                                 self.volume.get(), 0.5, 0.5, 0.5, 0.5)
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
        self.prostate_restim.send_prostate(0.5, 0.5, 0.0, 0.5, 0.5, 0.5, 0.5)

    def _reset_four_phase_group_delay(self) -> None:
        self._four_phase_history.clear()
        self._four_phase_effective_group_delay = 0.0
        self._four_phase_group_delay_last_time = None

    def _refresh(self) -> None:
        self._drain_controller_events()
        self._update_variety()
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
        signed, primary_index, return_index = directed_signed(
            path_l0, self._four_phase_direction,
            self.four_phase_return_depth.get())
        effective_crossover, effective_curve, effective_sharpness, direction_name = \
            self._crossover_profile(diag.speed_percent, self._four_phase_direction,
                                    diag.stroke_progress, variation_depth)
        self.four_phase_effective_crossover_width.set(
            f"{direction_name} {effective_crossover:.3f}")
        if self.four_phase_stroke_phase_texture.get():
            phase_name = "accelerating" if diag.stroke_progress < .5 else "decelerating"
            self.four_phase_stroke_phase_live.set(
                f"{phase_name} {diag.stroke_progress:.2f} | live {effective_crossover:.3f}")
        else:
            self.four_phase_stroke_phase_live.set("off")
        potentials = restim_crossfade(path_l0, self._four_phase_direction,
                                      self.four_phase_return_depth.get(),
                                      effective_crossover,
                                      effective_curve, effective_sharpness)
        morph_source, morph_target, morph_amount, morph_kind = self._sequence_morph_state(
            self._four_phase_direction, diag.stroke_progress, variation_depth)
        if morph_source == morph_target:
            potentials = map_electrode_order(potentials, morph_source)
        else:
            potentials = morph_electrode_order(
                potentials, morph_source, morph_target, morph_amount)
        self.electrode_morph_bar["value"] = morph_amount
        if morph_kind == "window":
            self.four_phase_moving_sequence_live.set(
                f"{morph_source}→{morph_target} | {morph_amount * 100:.0f}%")
        elif morph_kind == "carousel" and self.four_phase_moving_sequence.get():
            self.four_phase_moving_sequence_live.set("carousel priority")
        else:
            self.four_phase_moving_sequence_live.set("off")
        for variable, value in zip(self.four_phase_signed_values, signed):
            variable.set(f"{value:+.4f}")
        for bar, variable, value in zip(self.four_phase_potential_bars,
                                        self.four_phase_potential_values, potentials):
            bar["value"] = value
            variable.set(f"{value:.4f}")
        primary, preferred_return = potential_roles(potentials)
        if morph_source == morph_target:
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
            "ReStim output": f"Primary: {self.restim_status.get()} | Prostate: {self.prostate_status.get()}",
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
        }
        for title, summary in summaries.items():
            self.sections[title].summary.set(summary)
        self.root.after(100, self._refresh)

    def close(self) -> None:
        self._save_settings()
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
