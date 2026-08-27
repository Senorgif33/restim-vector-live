"""Tk UI for the Restim /tcode capture proxy."""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk

from .proxy import ChannelConfig, ProxyService, default_log_dir


class ProxyApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Restim T-code capture proxy")
        self.root.geometry("980x640")
        self.root.minsize(720, 480)

        self.primary_enabled = tk.BooleanVar(value=True)
        self.prostate_enabled = tk.BooleanVar(value=False)
        self.primary_listen_host = tk.StringVar(value="127.0.0.1")
        self.primary_listen_port = tk.IntVar(value=13346)
        self.primary_upstream_host = tk.StringVar(value="127.0.0.1")
        self.primary_upstream_port = tk.IntVar(value=12346)
        self.prostate_listen_host = tk.StringVar(value="127.0.0.1")
        self.prostate_listen_port = tk.IntVar(value=13350)
        self.prostate_upstream_host = tk.StringVar(value="127.0.0.1")
        self.prostate_upstream_port = tk.IntVar(value=12350)

        self.status_primary = tk.StringVar(value="Primary: idle")
        self.status_prostate = tk.StringVar(value="Prostate: idle")
        self.log_path_var = tk.StringVar(value=f"Logs folder: {default_log_dir()}")
        self.running = False
        self.service: ProxyService | None = None
        self._line_queue: queue.Queue[str] = queue.Queue()
        self._status_queue: queue.Queue[str] = queue.Queue()

        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._drain_queues)

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        body = ttk.Frame(self.root, padding=10)
        body.pack(fill="both", expand=True)

        ttk.Label(
            body,
            text=(
                "Transparent /tcode proxy: Vector connects here, frames are always "
                "saved to disk, then forwarded to Restim."
            ),
            wraplength=920,
        ).pack(anchor="w", pady=(0, 8))

        channels = ttk.Frame(body)
        channels.pack(fill="x")
        self._channel_row(
            channels, 0, "Primary",
            self.primary_enabled,
            self.primary_listen_host, self.primary_listen_port,
            self.primary_upstream_host, self.primary_upstream_port,
            self.status_primary,
        )
        self._channel_row(
            channels, 1, "Prostate",
            self.prostate_enabled,
            self.prostate_listen_host, self.prostate_listen_port,
            self.prostate_upstream_host, self.prostate_upstream_port,
            self.status_prostate,
        )

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=8)
        self.start_btn = ttk.Button(buttons, text="Start proxy", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(buttons, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(buttons, text="Open log folder", command=self.open_log_folder).pack(
            side="left", padx=6)
        ttk.Label(buttons, textvariable=self.log_path_var).pack(side="left", padx=12)

        ttk.Label(body, text="Live capture (every frame is also flushed to the session log):").pack(
            anchor="w")
        text_frame = ttk.Frame(body)
        text_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.text = tk.Text(text_frame, wrap="none", height=24)
        yscroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        xscroll = ttk.Scrollbar(text_frame, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        self.text.insert(
            "1.0",
            "Point Vector Primary WS at the Primary listen port and Prostate at the "
            "Prostate listen port.\n"
            "Leave Restim WebSocket servers on the upstream ports.\n"
            "Logging starts when you click Start proxy and continues until Stop.\n",
        )
        self.text.configure(state="disabled")

    def _channel_row(
        self, parent, row, title, enabled, listen_host, listen_port,
        upstream_host, upstream_port, status_var,
    ) -> None:
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.grid(row=row, column=0, sticky="ew", pady=4)
        parent.columnconfigure(0, weight=1)
        ttk.Checkbutton(frame, text="Enabled", variable=enabled).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="Listen").grid(row=0, column=1, sticky="e", padx=(12, 4))
        ttk.Entry(frame, textvariable=listen_host, width=14).grid(row=0, column=2)
        ttk.Spinbox(frame, from_=1, to=65535, textvariable=listen_port, width=7).grid(
            row=0, column=3, padx=4)
        ttk.Label(frame, text="→ Restim").grid(row=0, column=4, sticky="e", padx=(12, 4))
        ttk.Entry(frame, textvariable=upstream_host, width=14).grid(row=0, column=5)
        ttk.Spinbox(frame, from_=1, to=65535, textvariable=upstream_port, width=7).grid(
            row=0, column=6, padx=4)
        ttk.Label(frame, textvariable=status_var).grid(
            row=1, column=0, columnspan=7, sticky="w", pady=(6, 0))

    def _configs(self) -> list[ChannelConfig]:
        return [
            ChannelConfig(
                "Primary",
                self.primary_listen_host.get().strip() or "127.0.0.1",
                int(self.primary_listen_port.get()),
                self.primary_upstream_host.get().strip() or "127.0.0.1",
                int(self.primary_upstream_port.get()),
                bool(self.primary_enabled.get()),
            ),
            ChannelConfig(
                "Prostate",
                self.prostate_listen_host.get().strip() or "127.0.0.1",
                int(self.prostate_listen_port.get()),
                self.prostate_upstream_host.get().strip() or "127.0.0.1",
                int(self.prostate_upstream_port.get()),
                bool(self.prostate_enabled.get()),
            ),
        ]

    def _on_status(self, text: str) -> None:
        self._status_queue.put(text)

    def _on_line(self, line: str) -> None:
        self._line_queue.put(line)

    def start(self) -> None:
        if self.running:
            return
        configs = [cfg for cfg in self._configs() if cfg.enabled]
        if not configs:
            self._append_ui("Enable at least one channel before starting.\n")
            return
        self.service = ProxyService(
            configs, on_status=self._on_status, on_line=self._on_line)
        path = self.service.start()
        self.running = True
        self.log_path_var.set(f"Saving to: {path}")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._append_ui(f"Started. Always saving to {path}\n")

    def stop(self) -> None:
        if self.service:
            self.service.stop()
            self.service = None
        self.running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_primary.set("Primary: idle")
        self.status_prostate.set("Prostate: idle")
        self._append_ui("Stopped.\n")

    def open_log_folder(self) -> None:
        folder = default_log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            import os
            os.startfile(folder)  # type: ignore[attr-defined]
        except Exception:
            self._append_ui(f"Log folder: {folder}\n")

    def _drain_queues(self) -> None:
        while True:
            try:
                status = self._status_queue.get_nowait()
            except queue.Empty:
                break
            lower = status.lower()
            if lower.startswith("primary"):
                self.status_primary.set(status)
            elif lower.startswith("prostate"):
                self.status_prostate.set(status)
            else:
                self._append_ui(status + "\n")
        lines = []
        while True:
            try:
                lines.append(self._line_queue.get_nowait())
            except queue.Empty:
                break
        if lines:
            self._append_ui("\n".join(lines) + "\n")
        self.root.after(100, self._drain_queues)

    def _append_ui(self, text: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", text)
        # Keep UI from unbounded growth; file still has everything.
        if int(self.text.index("end-1c").split(".")[0]) > 4000:
            self.text.delete("1.0", "2000.0")
        self.text.see("end")
        self.text.configure(state="disabled")

    def close(self) -> None:
        self.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    ProxyApp().run()
