from __future__ import annotations

import tkinter as tk
import time
import queue
from tkinter import messagebox, ttk

from .engine import OutputSample, VectorEngine
from .motion import MotionMode, MotionParameters
from .network import MFPListener, ReStimClient, ReStimWebSocketClient
from .settings import load_settings, save_settings, settings_path
from .controller import (A, B, X, Y, START, LEFT_SHOULDER, DPAD_UP, DPAD_DOWN,
                         DPAD_LEFT, DPAD_RIGHT, XInputController)
from .variety import fit_range_for_travel, rolling_offset, rolling_value
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
    SETTINGS_FIELDS = (
        "mfp_host", "mfp_port", "restim_host", "restim_port", "prostate_host", "prostate_port",
        "rate", "lookahead", "volume", "dynamic_volume", "volume_rest_level", "volume_ratio",
        "volume_ramp_up", "frequency_ramp_level", "frequency_ratio", "send_frequency",
        "pulse_frequency_ratio", "pulse_frequency_min", "pulse_frequency_max", "send_pulse_frequency",
        "pulse_rise_ratio", "pulse_rise_min", "pulse_rise_max", "send_pulse_rise",
        "pulse_width_ratio", "pulse_width_min", "pulse_width_max", "send_pulse_width",
        "prostate_narrow_ratio", "prostate_arc_depth", "prostate_threshold",
        "prostate_volume_multiplier", "prostate_rest_level", "prostate_phase_degrees",
        "prostate_phase_step", "controller_enabled", "controller_fine_step", "minimum_radius",
        "speed_threshold", "direction_probability", "mode", "direct_controller_enabled",
        "variety_enabled", "variety_cycle_minutes", "variety_frequency", "variety_pulse_frequency",
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
        self.mfp_host = tk.StringVar(value="127.0.0.1")
        self.mfp_port = tk.IntVar(value=12345)
        self.restim_host = tk.StringVar(value="127.0.0.1")
        self.restim_port = tk.IntVar(value=12347)
        self.prostate_host = tk.StringVar(value="127.0.0.1")
        self.prostate_port = tk.IntVar(value=12350)
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
        self.controller_enabled = tk.BooleanVar(value=True)
        self.controller_target = tk.IntVar(value=0)
        self.controller_fine_step = tk.DoubleVar(value=0.05)
        self.controller_status = tk.StringVar(value="Disabled")
        self.direct_controller_enabled = tk.BooleanVar(value=True)
        self.variety_enabled = tk.BooleanVar(value=False)
        self.variety_cycle_minutes = tk.DoubleVar(value=4.0)
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
        )}

        self.restim = ReStimClient(self._set_restim_status)
        self.prostate_restim = ReStimWebSocketClient(self._set_prostate_status)
        self.engine = VectorEngine(self._send_sample)
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
        section = CollapsibleSection(self.root, title, collapsed=(title == "Live diagnostics"))
        section.grid(row=row + 1, column=column, columnspan=span, sticky="nsew", padx=10, pady=3)
        self.sections[title] = section
        return section.body

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(12, weight=1)

        toolbar = ttk.Frame(self.root, padding=(12, 6))
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(toolbar, text="Vector commissioning", font=("TkDefaultFont", 10, "bold")).pack(side="left")
        ttk.Button(toolbar, text="START / RESUME", command=self.resume).pack(side="left", padx=(24, 6))
        ttk.Button(toolbar, text="Neutral", command=self.neutral).pack(side="left", padx=6)
        ttk.Button(toolbar, text="STOP", command=self.stop).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Setup guide", command=self.show_setup_guide).pack(side="left", padx=(18, 6))
        ttk.Button(toolbar, text="Rolling Variety", command=self.show_variety_window).pack(side="left", padx=6)
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
        ttk.Label(restim, text="Host").grid(row=0, column=0, sticky="w")
        ttk.Entry(restim, textvariable=self.restim_host, width=16).grid(row=0, column=1, padx=5)
        ttk.Label(restim, text="Port").grid(row=0, column=2)
        ttk.Spinbox(restim, from_=1, to=65535, textvariable=self.restim_port, width=7).grid(row=0, column=3, padx=5)
        ttk.Button(restim, text="Connect", command=self.connect_restim).grid(row=1, column=0, pady=8)
        ttk.Button(restim, text="Disconnect", command=self.restim.disconnect).grid(row=1, column=1, pady=8)
        ttk.Label(restim, textvariable=self.restim_status).grid(row=1, column=2, columnspan=2, sticky="w")
        ttk.Button(restim, text="START / RESUME", command=self.resume).grid(row=0, column=4, padx=12)
        ttk.Button(restim, text="STOP", command=self.stop).grid(row=1, column=4, padx=12)
        ttk.Label(restim, text="Prostate WS").grid(row=2, column=0, sticky="w")
        ttk.Entry(restim, textvariable=self.prostate_host, width=16).grid(row=2, column=1, padx=5)
        ttk.Spinbox(restim, from_=1, to=65535, textvariable=self.prostate_port, width=7).grid(row=2, column=2, padx=5)
        ttk.Button(restim, text="Connect", command=self.connect_prostate).grid(row=3, column=0, pady=6)
        ttk.Button(restim, text="Disconnect", command=self.prostate_restim.disconnect).grid(row=3, column=1, pady=6)
        ttk.Label(restim, textvariable=self.prostate_status).grid(row=3, column=2, columnspan=2, sticky="w")

        motion = self._frame("Motion", 1, 0, 2)
        ttk.Label(motion, text="Mode").grid(row=0, column=0, sticky="w")
        mode_box = ttk.Combobox(motion, textvariable=self.mode, state="readonly", width=38,
                                values=[mode.value for mode in MotionMode])
        mode_box.grid(row=0, column=1, columnspan=3, sticky="w", padx=6)
        mode_box.bind("<<ComboboxSelected>>", lambda _event: self.apply_config())
        fields = (
            ("Points per second", self.rate, 1, 200, 1),
            ("Look-ahead / delay (s)", self.lookahead, 0.05, 10, 0.05),
            ("Fixed volume", self.volume, 0, 1, 0.01),
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

        frequency_frame = self._frame("Frequency (Vector 1B commissioning)", 3, 0, 2)
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
        ttk.Checkbutton(frequency_frame, text="Send C0 to ReStim",
                        variable=self.send_frequency).grid(row=0, column=8, padx=16)

        pulse_frame = self._frame("Pulse frequency (Vector 1B commissioning)", 4, 0, 2)
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
        ttk.Checkbutton(pulse_frame, text="Send P0 to ReStim",
                        variable=self.send_pulse_frequency).grid(row=0, column=10, padx=14)

        rise_frame = self._frame("Pulse rise time (Vector 1B commissioning)", 5, 0, 2)
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
        ttk.Checkbutton(rise_frame, text="Send P3 to ReStim",
                        variable=self.send_pulse_rise).grid(row=0, column=10, padx=14)
        width_frame = self._frame("Pulse width (Vector 1B commissioning)", 6, 0, 2)
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
        ttk.Checkbutton(width_frame, text="Send P1 to ReStim",
                        variable=self.send_pulse_width).grid(row=0, column=10, padx=14)

        prostate = self._frame("Prostate output (second ReStim)", 7, 0, 2)
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

        controller = self._frame("Xbox controller (keyboard-mapped profile)", 8, 0, 2)
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
                  text="Direct Xbox: D-pad frequency/pulse frequency | LB + D-pad rise/width | X/Y phase | A Resume | B Neutral | Menu Stop") \
            .grid(row=3, column=0, columnspan=7, sticky="w", padx=6, pady=(2, 2))
        ttk.Label(controller,
                  text="W/S Frequency ramp ±  •  A/D Pulse frequency range −/+  •  I/K Rise range +/−  •  J/L Width range −/+  •  Enter Resume  •  Space Neutral  •  Esc Stop") \
            .grid(row=1, column=0, columnspan=6, sticky="w", padx=6, pady=(6, 2))

        variety = self._frame("Rolling Variety", 9, 0, 2)
        ttk.Checkbutton(variety, text="Enable rolling variety", variable=self.variety_enabled,
                        command=self._variety_toggle).grid(row=0, column=0, padx=6)
        ttk.Label(variety, text="Cycle (minutes)").grid(row=0, column=1, padx=(16, 4))
        ttk.Spinbox(variety, from_=0.5, to=30, increment=.5,
                    textvariable=self.variety_cycle_minutes, width=7).grid(row=0, column=2)
        for column, (label, variable) in enumerate((
                ("Frequency 1.0-0.5", self.variety_frequency),
                ("Pulse frequency +/-0.20", self.variety_pulse_frequency),
                ("Rise +/-0.20", self.variety_pulse_rise),
                ("Width +/-0.20", self.variety_pulse_width),
                ("Phase -45/+45", self.variety_phase)), start=0):
            ttk.Checkbutton(variety, text=label, variable=variable,
                            command=self._variety_toggle).grid(row=1, column=column, padx=8)
        ttk.Label(variety, textvariable=self.variety_status,
                  font=("TkDefaultFont", 10, "bold")).grid(row=0, column=3, columnspan=3, padx=18)

        controls = self._frame("Commissioning controls", 10, 0, 2)
        ttk.Button(controls, text="Neutral", command=self.neutral, width=18).pack(side="left", padx=12)
        ttk.Button(controls, text="Resume", command=self.resume, width=18).pack(side="left", padx=12)
        ttk.Button(controls, text="STOP", command=self.stop, width=18).pack(side="left", padx=12)
        ttk.Label(controls, text="Test without FOCstim hardware connected.").pack(side="right", padx=12)

        diagnostics = self._frame("Live diagnostics", 11, 0, 2)
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
        self.root.after(0, self.mfp_status.set, text)

    def _set_restim_status(self, text: str) -> None:
        self.root.after(0, self.restim_status.set, text)

    def _set_prostate_status(self, text: str) -> None:
        self.root.after(0, self.prostate_status.set, text)

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

    def _bind_controller_keys(self) -> None:
        for key, action in (("<w>", lambda: self._adjust_frequency_ramp(1)),
                            ("<s>", lambda: self._adjust_frequency_ramp(-1)),
                            ("<a>", lambda: self._shift_control_range("pulse_frequency", -1)),
                            ("<d>", lambda: self._shift_control_range("pulse_frequency", 1)),
                            ("<i>", lambda: self._shift_control_range("pulse_rise", 1)),
                            ("<k>", lambda: self._shift_control_range("pulse_rise", -1)),
                            ("<j>", lambda: self._shift_control_range("pulse_width", -1)),
                            ("<l>", lambda: self._shift_control_range("pulse_width", 1)),
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
        ttk.Label(body, text="Cycle duration (minutes)").grid(row=1, column=0, sticky="w", pady=8)
        ttk.Spinbox(body, from_=0.5, to=30, increment=.5,
                    textvariable=self.variety_cycle_minutes, width=8).grid(row=1, column=1)
        options = (("Frequency ramp 1.0 to 0.5", self.variety_frequency),
                   ("Pulse-frequency range +/-0.20", self.variety_pulse_frequency),
                   ("Pulse-rise range +/-0.20", self.variety_pulse_rise),
                   ("Pulse-width range +/-0.20", self.variety_pulse_width),
                   ("Prostate timing phase +/-45 degrees", self.variety_phase))
        for row, (label, variable) in enumerate(options, start=2):
            ttk.Checkbutton(body, text=label, variable=variable,
                            command=self._variety_toggle).grid(row=row, column=0, columnspan=2,
                                                               sticky="w", pady=3)
        ttk.Label(body, textvariable=self.variety_status,
                  font=("TkDefaultFont", 10, "bold")).grid(row=7, column=0, sticky="w", pady=(12, 0))
        ttk.Button(body, text="Restart cycle from current settings",
                   command=self._variety_toggle).grid(row=7, column=1, padx=12, pady=(12, 0))

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
        cycle = self.variety_cycle_minutes.get()
        offset = rolling_offset(elapsed, cycle)
        if self.variety_frequency.get():
            self.frequency_ramp_level.set(round(rolling_value(elapsed, cycle, .5, 1.0), 4))
        for enabled, key, variables, depth in (
                (self.variety_pulse_frequency, "pf", (self.pulse_frequency_min, self.pulse_frequency_max), .20),
                (self.variety_pulse_rise, "rise", (self.pulse_rise_min, self.pulse_rise_max), .20),
                (self.variety_pulse_width, "width", (self.pulse_width_min, self.pulse_width_max), .20)):
            if enabled.get():
                low, high = self._bounded_shift(self._variety_baseline[key], offset * depth)
                variables[0].set(low); variables[1].set(high)
        if self.variety_phase.get():
            baseline = self._variety_baseline["phase"]
            self.prostate_phase_degrees.set(round(max(-90, min(90, baseline + offset * 45)), 1))
        self.variety_status.set(f"Running | {elapsed / 60:.1f}/{cycle:.1f} min")
        self.apply_config()

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
                                  prostate_phase_degrees=self.prostate_phase_degrees.get())
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
        return not bool(saved.get("first_run_complete"))

    def _save_settings(self) -> None:
        values = {name: getattr(self, name).get() for name in self.SETTINGS_FIELDS}
        values["first_run_complete"] = True
        save_settings(values)

    def show_setup_guide(self) -> None:
        messagebox.showinfo(
            "Vector 1A setup",
            "Safe visual commissioning\n\n"
            "1. Leave stimulation hardware disconnected.\n"
            "2. In MFP, send L0 by UDP or TCP to 127.0.0.1:12345.\n"
            "3. Set the MFP script offset to -2.00 seconds.\n"
            "4. Enable ReStim TCP on port 12347.\n"
            "5. For prostate output, run a second ReStim WebSocket server on port 12350.\n"
            "6. Start the listener, connect both outputs, then select Start / Resume.\n\n"
            "Fine-tune MFP around -2.00 seconds while leaving Vector's delay at 2.00 seconds.\n\n"
            f"Settings are saved in:\n{settings_path()}"
        )

    def _send_sample(self, sample: OutputSample) -> None:
        self.restim.send(sample.alpha, sample.beta, sample.volume,
                         sample.frequency if self.send_frequency.get() else None,
                         sample.pulse_frequency if self.send_pulse_frequency.get() else None,
                         sample.pulse_rise_time if self.send_pulse_rise.get() else None,
                         sample.pulse_width if self.send_pulse_width.get() else None)
        self.prostate_restim.send_prostate(
            sample.alpha_prostate, sample.beta_prostate, sample.volume_prostate,
            sample.frequency, sample.pulse_frequency, sample.pulse_width,
            sample.pulse_rise_time)

    def neutral(self) -> None:
        self.apply_config()
        self.engine.neutral()
        self.restim.send(0.5, 0.5, self.volume.get())
        self.prostate_restim.send_prostate(0.5, 0.5, self.volume.get(), 0.5, 0.5, 0.5, 0.5)

    def resume(self) -> None:
        self.apply_config()
        self.engine.resume()

    def stop(self) -> None:
        self.engine.stop()
        self.restim.send(0.5, 0.5, 0.0)
        self.prostate_restim.send_prostate(0.5, 0.5, 0.0, 0.5, 0.5, 0.5, 0.5)

    def _refresh(self) -> None:
        self._drain_controller_events()
        self._update_variety()
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
        }
        for key, value in values.items():
            self.diag_vars[key].set(value)
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
        if self.listener.connection_label() in ("Receiving", "Listening"):
            current = self.listener.connection_label()
            if not self.mfp_status.get().startswith("MFP "):
                self.mfp_status.set(current)
        summaries = {
            "MultiFunPlayer input": f"{self.mfp_status.get()} | {self.mfp_host.get()}:{self.mfp_port.get()}",
            "ReStim output": f"Primary: {self.restim_status.get()} | Prostate: {self.prostate_status.get()}",
            "Motion": f"{self.mode.get()} | {self.rate.get()} Hz | {self.lookahead.get():.2f} s delay",
            "Volume response": f"Ceiling {self.volume.get() * 100:.0f}% | rest {self.volume_rest_level.get() * 100:.0f}%",
            "Frequency (Vector 1B commissioning)": f"{diag.frequency:.3f} | ramp {self.frequency_ramp_level.get():.2f}",
            "Pulse frequency (Vector 1B commissioning)": f"{diag.pulse_frequency:.3f} | range {self.pulse_frequency_min.get():.2f}-{self.pulse_frequency_max.get():.2f}",
            "Pulse rise time (Vector 1B commissioning)": f"{diag.pulse_rise_time:.3f} | range {self.pulse_rise_min.get():.2f}-{self.pulse_rise_max.get():.2f}",
            "Pulse width (Vector 1B commissioning)": f"{diag.pulse_width:.3f} | range {self.pulse_width_min.get():.2f}-{self.pulse_width_max.get():.2f}",
            "Prostate output (second ReStim)": f"alpha {diag.alpha_prostate:.3f} beta {diag.beta_prostate:.3f} volume {diag.volume_prostate * 100:.0f}% | phase {self.prostate_phase_degrees.get():+.0f} degrees",
            "Xbox controller (keyboard-mapped profile)": f"{self.controller_status.get()} | step {self.controller_fine_step.get():.2f}",
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
        self.restim.disconnect()
        self.prostate_restim.disconnect()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    VectorApp(root)
    root.mainloop()
