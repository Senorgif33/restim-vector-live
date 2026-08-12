from __future__ import annotations

from dataclasses import dataclass, replace
import heapq
from collections import deque
import math
import threading
import time
from typing import Callable

from .motion import MotionCalculator, MotionMode, MotionParameters, SegmentState


@dataclass(frozen=True)
class OutputSample:
    sequence: int
    calculated_at: float
    due_at: float
    raw_l0: float
    output_l0: float
    speed_percent: float
    alpha: float
    beta: float
    volume: float
    mode: MotionMode
    frequency: float
    pulse_frequency: float
    pulse_rise_time: float
    pulse_width: float
    alpha_prostate: float
    beta_prostate: float
    volume_prostate: float


@dataclass(frozen=True)
class Diagnostics:
    raw_l0: float = 0.5
    output_l0: float = 0.5
    speed_percent: float = 0.0
    alpha: float = 0.5
    beta: float = 0.5
    buffer_fill: int = 0
    lookahead_seconds: float = 2.0
    actual_queue_delay: float = 0.0
    input_samples: int = 0
    output_samples: int = 0
    state: str = "Stopped"
    output_mode: str = "--"
    output_volume: float = 0.0
    frequency: float = 0.0
    pulse_frequency: float = 0.0
    pulse_rise_time: float = 0.0
    pulse_width: float = 0.0
    alpha_prostate: float = 0.5
    beta_prostate: float = 0.5
    volume_prostate: float = 0.0


class VectorEngine:
    """Fixed-cadence calculator and deterministic due-time FIFO."""

    def __init__(self, send_sample: Callable[[OutputSample], None], rate_hz: int = 50,
                 lookahead_seconds: float = 2.0, volume: float = 0.7,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.send_sample = send_sample
        self.rate_hz = rate_hz
        self.lookahead_seconds = lookahead_seconds
        self.volume = volume
        self.clock = clock
        self.mode = MotionMode.CIRCULAR
        self.params = MotionParameters()
        self.calculator = MotionCalculator()
        self._lock = threading.RLock()
        now = clock()
        self._segment = SegmentState(0, now, now, 0.5, 0.5)
        self._raw_l0 = 0.5
        self._last_input_time = now
        self._last_input_position = 0.5
        self._velocity_history: deque[tuple[float, float]] = deque()
        self._speed_peak_history: deque[tuple[float, float]] = deque()
        self._stroke_start_time = now
        self._stroke_start_position = 0.5
        self._stroke_direction = 0
        self._stroke_sequence = 0
        self._completed_strokes: deque[SegmentState] = deque(maxlen=256)
        self.dynamic_volume = True
        self.volume_rest_level = 0.4
        self.volume_ramp_speed_ratio = 20.0
        self.volume_ramp_up_seconds = 1.0
        self.volume_idle_seconds = 0.25
        self._volume_was_active = False
        self._volume_active_since = now
        self.frequency_ramp_level = 1.0
        self.frequency_ramp_speed_ratio = 2.0
        self.pulse_frequency_ratio = 3.0
        self.pulse_frequency_min = 0.40
        self.pulse_frequency_max = 0.95
        self._pulse_was_active = False
        self._pulse_active_since = now
        self.pulse_rise_ratio = 2.0
        self.pulse_rise_min = 0.0
        self.pulse_rise_max = 0.80
        self.pulse_width_ratio = 3.0
        self.pulse_width_min = 0.10
        self.pulse_width_max = 0.45
        self._width_was_active = False
        self._width_active_since = now
        self.prostate_narrow_ratio = 1.0
        self.prostate_arc_depth = 0.25
        self.prostate_stroke_threshold = 0.25
        self.prostate_volume_multiplier = 1.5
        self.prostate_rest_level = 0.7
        self.prostate_phase_degrees = 0.0
        self._released_prostate: tuple[float, float] | None = None
        self._input_count = 0
        self._output_count = 0
        self._sequence = 0
        self._queue: list[tuple[float, int, OutputSample]] = []
        self._state = "Stopped"
        self._diag = Diagnostics()
        self._run = threading.Event()
        self._thread: threading.Thread | None = None

    def configure(self, *, rate_hz: int, lookahead_seconds: float, volume: float,
                  mode: MotionMode, params: MotionParameters,
                  dynamic_volume: bool = True, volume_rest_level: float = 0.4,
                  volume_ramp_speed_ratio: float = 20.0,
                  volume_ramp_up_seconds: float = 1.0,
                  frequency_ramp_level: float = 1.0,
                  frequency_ramp_speed_ratio: float = 2.0,
                  pulse_frequency_ratio: float = 3.0,
                  pulse_frequency_min: float = 0.40,
                  pulse_frequency_max: float = 0.95,
                  pulse_rise_ratio: float = 2.0,
                  pulse_rise_min: float = 0.0,
                  pulse_rise_max: float = 0.80,
                  pulse_width_ratio: float = 3.0,
                  pulse_width_min: float = 0.10,
                  pulse_width_max: float = 0.45,
                  prostate_narrow_ratio: float = 1.0,
                  prostate_arc_depth: float = 0.25,
                  prostate_stroke_threshold: float = 0.25,
                  prostate_volume_multiplier: float = 1.5,
                  prostate_rest_level: float = 0.7,
                  prostate_phase_degrees: float = 0.0) -> None:
        with self._lock:
            mode_changed = mode != self.mode
            self.rate_hz = max(1, min(200, int(rate_hz)))
            self.lookahead_seconds = max(0.05, min(10.0, float(lookahead_seconds)))
            self.volume = min(1.0, max(0.0, float(volume)))
            self.mode = mode
            self.params = params
            self.dynamic_volume = bool(dynamic_volume)
            self.volume_rest_level = min(1.0, max(0.0, float(volume_rest_level)))
            self.volume_ramp_speed_ratio = min(40.0, max(10.0, float(volume_ramp_speed_ratio)))
            self.volume_ramp_up_seconds = min(10.0, max(0.0, float(volume_ramp_up_seconds)))
            self.frequency_ramp_level = min(1.0, max(0.0, float(frequency_ramp_level)))
            self.frequency_ramp_speed_ratio = min(10.0, max(1.0, float(frequency_ramp_speed_ratio)))
            self.pulse_frequency_ratio = min(10.0, max(1.0, float(pulse_frequency_ratio)))
            low = min(1.0, max(0.0, float(pulse_frequency_min)))
            high = min(1.0, max(0.0, float(pulse_frequency_max)))
            self.pulse_frequency_min, self.pulse_frequency_max = sorted((low, high))
            self.pulse_rise_ratio = min(10.0, max(1.0, float(pulse_rise_ratio)))
            rise_low = min(1.0, max(0.0, float(pulse_rise_min)))
            rise_high = min(1.0, max(0.0, float(pulse_rise_max)))
            self.pulse_rise_min, self.pulse_rise_max = sorted((rise_low, rise_high))
            self.pulse_width_ratio = min(10.0, max(1.0, float(pulse_width_ratio)))
            width_low = min(1.0, max(0.0, float(pulse_width_min)))
            width_high = min(1.0, max(0.0, float(pulse_width_max)))
            self.pulse_width_min, self.pulse_width_max = sorted((width_low, width_high))
            self.prostate_narrow_ratio = min(1.0, max(0.0, float(prostate_narrow_ratio)))
            self.prostate_arc_depth = min(1.0, max(0.0, float(prostate_arc_depth)))
            self.prostate_stroke_threshold = min(1.0, max(0.0, float(prostate_stroke_threshold)))
            self.prostate_volume_multiplier = min(3.0, max(1.0, float(prostate_volume_multiplier)))
            self.prostate_rest_level = min(1.0, max(0.0, float(prostate_rest_level)))
            self.prostate_phase_degrees = min(90.0, max(-90.0, float(prostate_phase_degrees)))
            if mode_changed and self._state in ("Buffering", "Running"):
                # Never release samples calculated by a previously selected
                # geometry under a newly-labelled UI state.
                self._queue.clear()
                self._state = "Buffering"

    def receive_l0(self, value: float, interval_ms: int = 0, received_at: float | None = None) -> None:
        now = self.clock() if received_at is None else received_at
        value = min(1.0, max(0.0, value))
        with self._lock:
            current = self._segment.position(now)
            if interval_ms > 0:
                duration = interval_ms / 1000.0
                velocity = abs(value - current) / max(duration, 1e-6)
                start_time, end_time = now, now + duration
                start_position = current
            else:
                # MFP's fixed-update outputs normally send sampled positions without
                # T-code interval fields. Reconstruct the just-observed segment from
                # consecutive arrival timestamps instead of treating every sample as
                # a zero-duration stroke.
                duration = max(1e-6, now - self._last_input_time)
                velocity = abs(value - self._last_input_position) / duration
                start_time, end_time = self._last_input_time, now
                start_position = self._last_input_position

            self._velocity_history.append((now, velocity))
            while self._velocity_history and self._velocity_history[0][0] < now - 5.0:
                self._velocity_history.popleft()
            rolling_velocity = sum(item[1] for item in self._velocity_history) / len(self._velocity_history)
            self._speed_peak_history.append((now, rolling_velocity))
            while self._speed_peak_history and self._speed_peak_history[0][0] < now - 30.0:
                self._speed_peak_history.popleft()
            rolling_peak = max((item[1] for item in self._speed_peak_history), default=0.0)
            normalized_speed = 100.0 * rolling_velocity / rolling_peak if rolling_peak > 1e-9 else 0.0
            self._input_count += 1
            self._raw_l0 = value
            delta = value - self._last_input_position
            new_direction = 1 if delta > 0.00005 else (-1 if delta < -0.00005 else 0)
            completed_stroke = None
            if new_direction and self._stroke_direction and new_direction != self._stroke_direction:
                self._stroke_sequence += 1
                completed_stroke = SegmentState(
                    self._stroke_sequence, self._stroke_start_time, self._last_input_time,
                    self._stroke_start_position, self._last_input_position, normalized_speed,
                )
                self._completed_strokes.append(completed_stroke)
                self._stroke_start_time = self._last_input_time
                self._stroke_start_position = self._last_input_position
            if new_direction:
                self._stroke_direction = new_direction
            self._segment = SegmentState(
                self._input_count, start_time, end_time, start_position, value,
                normalized_speed,
            )
            self._last_input_time = now
            self._last_input_position = value
            if completed_stroke is not None:
                self._rewrite_completed_stroke(completed_stroke)

    def _rewrite_completed_stroke(self, stroke: SegmentState) -> None:
        """Apply a discovered reversal endpoint to samples still in look-ahead.

        MFP sends interpolated positions, not original funscript keyframes. The
        reversal identifies the equivalent RFP stroke endpoint. Since calculated
        samples are held for one second, ordinary strokes can be corrected before
        they are released to ReStim.
        """
        rewritten: list[tuple[float, int, OutputSample]] = []
        for due_at, sequence, sample in self._queue:
            if (sample.mode == MotionMode.RESTIM_ORIGINAL
                    and stroke.start_time <= sample.calculated_at <= stroke.end_time):
                alpha, beta, position, speed = self.calculator.calculate(
                    MotionMode.RESTIM_ORIGINAL, stroke, sample.calculated_at, self.params
                )
                sample = replace(sample, output_l0=position, speed_percent=speed,
                                 alpha=alpha, beta=beta)
            if stroke.start_time <= sample.calculated_at <= stroke.end_time:
                pa, pb = self._calculate_prostate(stroke, sample.calculated_at)
                sample = replace(sample, alpha_prostate=pa, beta_prostate=pb)
            rewritten.append((due_at, sequence, sample))
        self._queue = rewritten
        heapq.heapify(self._queue)

    def _stroke_for_time(self, scheduled_at: float) -> SegmentState:
        for stroke in reversed(self._completed_strokes):
            if stroke.start_time <= scheduled_at <= stroke.end_time:
                return stroke
        # Provisional current stroke. It will be rewritten on the next reversal.
        return SegmentState(
            1_000_000_000 + self._stroke_sequence + 1, self._stroke_start_time,
            max(scheduled_at, self._last_input_time), self._stroke_start_position,
            self._last_input_position, self._segment.speed_percent,
        )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._run.set()
        self._thread = threading.Thread(target=self._loop, name="vector-engine", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._run.clear()
        if self._thread:
            self._thread.join(timeout=2.0)

    def resume(self) -> None:
        with self._lock:
            self._queue.clear()
            self._released_prostate = None
            self._state = "Buffering"

    def neutral(self) -> None:
        with self._lock:
            self._queue.clear()
            self._released_prostate = None
            self._state = "Neutral"

    def stop(self) -> None:
        with self._lock:
            self._queue.clear()
            self._released_prostate = None
            self._state = "Stopped"

    def diagnostics(self) -> Diagnostics:
        with self._lock:
            return replace(self._diag)

    def _calculate_and_queue(self, scheduled_at: float) -> None:
        with self._lock:
            if self._state not in ("Buffering", "Running"):
                return
            calculation_segment = (self._stroke_for_time(scheduled_at)
                                   if self.mode == MotionMode.RESTIM_ORIGINAL
                                   else self._segment)
            alpha, beta, output_l0, speed = self.calculator.calculate(
                self.mode, calculation_segment, scheduled_at, self.params)
            output_volume = self._calculate_volume(scheduled_at, speed)
            frequency = self._calculate_frequency(scheduled_at, speed)
            pulse_frequency = self._calculate_pulse_frequency(scheduled_at, speed, alpha)
            pulse_rise_time = self._calculate_pulse_rise_time(speed)
            pulse_width = self._calculate_pulse_width(scheduled_at, speed, output_l0)
            prostate_segment = self._stroke_for_time(scheduled_at)
            alpha_prostate, beta_prostate = self._calculate_prostate(
                prostate_segment, scheduled_at)
            volume_prostate = self._calculate_prostate_volume(scheduled_at, speed)
            self._sequence += 1
            sample = OutputSample(
                self._sequence, scheduled_at, scheduled_at + self.lookahead_seconds,
                self._raw_l0, output_l0, speed, alpha, beta, output_volume, self.mode,
                frequency,
                pulse_frequency,
                pulse_rise_time,
                pulse_width,
                alpha_prostate, beta_prostate, volume_prostate,
            )
            heapq.heappush(self._queue, (sample.due_at, sample.sequence, sample))

    def _release_due(self, now: float) -> None:
        due: list[OutputSample] = []
        with self._lock:
            while self._queue and self._queue[0][0] <= now:
                due.append(heapq.heappop(self._queue)[2])
            if due and self._state == "Buffering":
                self._state = "Running"
        for sample in due:
            sample = self._stabilize_prostate_phase(sample)
            self.send_sample(sample)
            actual = self.clock() - sample.calculated_at
            with self._lock:
                self._output_count += 1
                self._diag = Diagnostics(
                    sample.raw_l0, sample.output_l0, sample.speed_percent,
                    sample.alpha, sample.beta, len(self._queue),
                    self.lookahead_seconds, actual, self._input_count,
                    self._output_count, self._state, sample.mode.value, sample.volume,
                    sample.frequency,
                    sample.pulse_frequency,
                    sample.pulse_rise_time,
                    sample.pulse_width,
                    sample.alpha_prostate, sample.beta_prostate, sample.volume_prostate,
                )
        with self._lock:
            if not due:
                self._diag = replace(
                    self._diag, raw_l0=self._raw_l0,
                    buffer_fill=len(self._queue), lookahead_seconds=self.lookahead_seconds,
                    input_samples=self._input_count, state=self._state,
                )

    def _stabilize_prostate_phase(self, sample: OutputSample) -> OutputSample:
        """Slew only discontinuous phase projections at provisional reversals.

        Normal trajectory samples pass unchanged. A phase-shifted sample which
        jumps farther than physically plausible in one output tick is moved
        toward its target over subsequent ticks, eliminating visible snaps while
        retaining the requested lead/lag.
        """
        with self._lock:
            target = (sample.alpha_prostate, sample.beta_prostate)
            if self.prostate_phase_degrees == 0.0 or self._released_prostate is None:
                self._released_prostate = target
                return sample
            previous = self._released_prostate
            # At 50 Hz this permits 0.04 axis units per sample. The allowance
            # scales with rate so its real-time slew remains two units/second.
            max_step = 2.0 / self.rate_hz
            def move(current: float, desired: float) -> float:
                return current + min(max_step, max(-max_step, desired - current))
            stable = (move(previous[0], target[0]), move(previous[1], target[1]))
            self._released_prostate = stable
            return replace(sample, alpha_prostate=stable[0], beta_prostate=stable[1])

    def step(self, scheduled_at: float, release_at: float | None = None) -> None:
        """One deterministic scheduler step, also used by tests."""
        self._calculate_and_queue(scheduled_at)
        self._release_due(scheduled_at if release_at is None else release_at)

    def _calculate_volume(self, at_time: float, speed_percent: float) -> float:
        if not self.dynamic_volume:
            return self.volume
        active = ((at_time - self._last_input_time) <= self.volume_idle_seconds
                  and speed_percent > 0.5)
        ratio = self.volume_ramp_speed_ratio
        generated = ((ratio - 1.0) + min(1.0, max(0.0, speed_percent / 100.0))) / ratio
        if not active:
            self._volume_was_active = False
            return self.volume * generated * self.volume_rest_level
        if not self._volume_was_active:
            self._volume_active_since = at_time
            self._volume_was_active = True
        if self.volume_ramp_up_seconds <= 0:
            attack = 1.0
        else:
            progress = min(1.0, max(0.0, (at_time - self._volume_active_since) /
                                        self.volume_ramp_up_seconds))
            attack = self.volume_rest_level + (1.0 - self.volume_rest_level) * progress
        return self.volume * generated * attack

    def _calculate_frequency(self, at_time: float, speed_percent: float) -> float:
        speed = (speed_percent / 100.0
                 if (at_time - self._last_input_time) <= self.volume_idle_seconds else 0.0)
        ratio = self.frequency_ramp_speed_ratio
        return min(1.0, max(0.0,
            (self.frequency_ramp_level * (ratio - 1.0) + speed) / ratio))

    def _calculate_pulse_frequency(self, at_time: float, speed_percent: float,
                                   alpha: float) -> float:
        active = ((at_time - self._last_input_time) <= self.volume_idle_seconds
                  and speed_percent > 0.5 and alpha > 1e-6)
        speed = min(1.0, max(0.0, speed_percent / 100.0))
        ratio = self.pulse_frequency_ratio
        combined = (speed * (ratio - 1.0) + min(1.0, max(0.0, alpha))) / ratio
        if not active:
            self._pulse_was_active = False
            combined *= self.volume_rest_level
        else:
            if not self._pulse_was_active:
                self._pulse_active_since = at_time
                self._pulse_was_active = True
            if self.volume_ramp_up_seconds > 0:
                progress = min(1.0, max(0.0,
                    (at_time - self._pulse_active_since) / self.volume_ramp_up_seconds))
                combined *= self.volume_rest_level + (1.0 - self.volume_rest_level) * progress
        return self.pulse_frequency_min + combined * (
            self.pulse_frequency_max - self.pulse_frequency_min)

    def _calculate_pulse_rise_time(self, speed_percent: float) -> float:
        inverted_ramp = 1.0 - self.frequency_ramp_level
        inverted_speed = 1.0 - min(1.0, max(0.0, speed_percent / 100.0))
        ratio = self.pulse_rise_ratio
        combined = (inverted_ramp * (ratio - 1.0) + inverted_speed) / ratio
        return self.pulse_rise_min + combined * (
            self.pulse_rise_max - self.pulse_rise_min)

    def _calculate_pulse_width(self, at_time: float, speed_percent: float,
                               output_l0: float) -> float:
        active = ((at_time - self._last_input_time) <= self.volume_idle_seconds
                  and speed_percent > 0.5)
        speed = min(1.0, max(0.0, speed_percent / 100.0))
        inverted_l0 = 1.0 - min(1.0, max(0.0, output_l0))
        limited = min(self.pulse_width_max, max(self.pulse_width_min, inverted_l0))
        ratio = self.pulse_width_ratio
        combined = (speed * (ratio - 1.0) + limited) / ratio
        if not active:
            self._width_was_active = False
            combined *= self.volume_rest_level
            return min(self.pulse_width_max, max(self.pulse_width_min, combined))
        if not self._width_was_active:
            self._width_active_since = at_time
            self._width_was_active = True
        if self.volume_ramp_up_seconds > 0:
            progress = min(1.0, max(0.0,
                (at_time - self._width_active_since) / self.volume_ramp_up_seconds))
            combined *= self.volume_rest_level + (1.0 - self.volume_rest_level) * progress
        # Unlike upstream's intermediate input limit, Vector exposes Min/Max as
        # a live controller-adjustable output range. Enforce it after blending
        # so the actual P1 command always remains inside the displayed range.
        return min(self.pulse_width_max, max(self.pulse_width_min, combined))

    def _calculate_prostate(self, stroke: SegmentState, at_time: float) -> tuple[float, float]:
        # RFP tear-shaped prostate path is generated from inverted L0.
        start = 1.0 - stroke.start_position
        end = 1.0 - stroke.end_position
        progress = stroke.progress(at_time) + self.prostate_phase_degrees / 180.0
        # One source stroke is half of a reciprocating 360-degree cycle. Crossing
        # either endpoint therefore continues into the mirrored neighbouring
        # stroke instead of clamping and dwelling at the endpoint.
        if progress > 1.0:
            start, end = end, start
            progress -= 1.0
        elif progress < 0.0:
            start, end = end, start
            progress += 1.0
        progress = min(1.0, max(0.0, progress))
        alpha = start + progress * (end - start)
        stroke_range = abs(end - start)
        beta = 0.5
        if stroke_range >= self.prostate_stroke_threshold:
            going_up = end > start
            bulge = min(stroke_range / 2.0, 0.5) * self.prostate_arc_depth
            # A true tear occupies opposite sides of beta=0.5. Because the
            # working position is inverted L0, going_up here is the source
            # downstroke: beta is positive on the way down and negative on the
            # way up. Side arc depth keeps both deviations deliberately small.
            beta_direction = bulge if going_up else -(bulge * self.prostate_narrow_ratio)
            beta = 0.5 + beta_direction * math.sin(progress * math.pi)
        return min(1.0, max(0.0, alpha)), min(1.0, max(0.0, beta))

    def _calculate_prostate_volume(self, at_time: float, speed_percent: float) -> float:
        ratio = min(40.0, self.volume_ramp_speed_ratio * self.prostate_volume_multiplier)
        speed = min(1.0, max(0.0, speed_percent / 100.0))
        generated = ((ratio - 1.0) + speed) / ratio
        active = ((at_time - self._last_input_time) <= self.volume_idle_seconds
                  and speed_percent > 0.5)
        if not active:
            generated *= self.prostate_rest_level
        return min(self.volume, max(0.0, self.volume * generated))

    def _loop(self) -> None:
        next_tick = self.clock()
        while self._run.is_set():
            with self._lock:
                period = 1.0 / self.rate_hz
                state = self._state
                volume = self.volume
            now = self.clock()
            if state in ("Neutral", "Stopped"):
                # Networking layer emits transition commands; scheduler stays idle.
                time.sleep(0.01)
                next_tick = self.clock()
                continue
            if now < next_tick:
                time.sleep(min(next_tick - now, 0.01))
                continue
            # If the OS stalls, skip stale calculations instead of burst-sending them.
            if now - next_tick > period * 2:
                next_tick = now
            self.step(next_tick, now)
            next_tick += period
