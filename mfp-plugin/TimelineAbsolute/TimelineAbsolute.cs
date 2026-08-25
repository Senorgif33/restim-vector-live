// MultiFunPlayer plugin for Vector 1A — absolute media timeline on T0/T1.
// Compatible with MultiFunPlayer 1.32.x (no #: directives; those require 1.34+).
// Place this .cs file in MultiFunPlayer's Plugins folder (or one subfolder).

using System;
using System.Threading.Tasks;
using MultiFunPlayer.Common;
using MultiFunPlayer.Plugin;
using Newtonsoft.Json;
using NLog;

public class TimelineAbsolute : PluginBase
{
    public const double TimelineScaleSeconds = 10000.0;
    public const int RequiredOutputPrecision = 5;
    // Values larger than this are treated as milliseconds (common player quirk).
    private const double AssumeMillisecondsAboveSeconds = 12.0 * 3600.0;

    private static readonly Logger Logger = LogManager.GetCurrentClassLogger();

    private double? _positionSeconds;
    private double? _durationSeconds;
    private bool _loggedMissingPositionAxis;
    private bool _loggedMissingDurationAxis;
    private bool _configuredAxes;

    [JsonProperty]
    public string PositionAxis { get; set; } = "T0";

    [JsonProperty]
    public string DurationAxis { get; set; } = "T1";

    protected override void OnInitialize()
    {
        RegisterAction<string>("TimelineAbsolute::PositionAxis::Set",
            s => s.WithLabel("Position axis").WithDefaultValue("T0"),
            value =>
            {
                PositionAxis = string.IsNullOrWhiteSpace(value) ? "T0" : value.Trim().ToUpperInvariant();
                _loggedMissingPositionAxis = false;
                _configuredAxes = false;
                ConfigureTimelineAxes();
                PublishAxes();
            });

        RegisterAction<string>("TimelineAbsolute::DurationAxis::Set",
            s => s.WithLabel("Duration axis").WithDefaultValue("T1"),
            value =>
            {
                DurationAxis = string.IsNullOrWhiteSpace(value) ? "T1" : value.Trim().ToUpperInvariant();
                _loggedMissingDurationAxis = false;
                _configuredAxes = false;
                ConfigureTimelineAxes();
                PublishAxes();
            });

        ConfigureTimelineAxes();
        RefreshDurationFromProperty();
        PublishAxes();

        Logger.Info(
            "Timeline Absolute: set MFP Device Output precision to {0} for sub-second T0/T1 (0.1 s steps at scale {1}).",
            RequiredOutputPrecision,
            TimelineScaleSeconds);

        // Poll duration only. Position must come from MediaPositionChangedMessage —
        // Media::Position can lag/stick and was overwriting live T0 updates.
        StartTask(async token =>
        {
            while (!token.IsCancellationRequested)
            {
                try
                {
                    ConfigureTimelineAxes();
                    RefreshDurationFromProperty();
                    if (_durationSeconds is double duration)
                        PublishDuration(duration);
                }
                catch (Exception ex)
                {
                    Logger.Trace(ex, "Timeline Absolute: duration poll failed");
                }

                try
                {
                    await Task.Delay(250, token);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
            }
        });
    }

    protected override void HandleMessage(MediaPositionChangedMessage message)
    {
        ApplyPositionSeconds(message.Position.TotalSeconds);
        if (_positionSeconds is double position)
            PublishPosition(position);

        // Keep T1 alive on the wire while position is moving.
        RefreshDurationFromProperty();
        if (_durationSeconds is double duration)
            PublishDuration(duration);
    }

    protected override void HandleMessage(MediaDurationChangedMessage message)
    {
        ApplyDurationSeconds(message.Duration.TotalSeconds);
        if (_durationSeconds is double duration)
            PublishDuration(duration);
    }

    protected override void HandleMessage(MediaPathChangedMessage message)
    {
        ClearCache();
        RefreshDurationFromProperty();
        PublishAxes();
    }

    protected override void HandleMessage(MediaResetMessage message)
    {
        ClearCache();
        PublishAxes();
    }

    private void RefreshDurationFromProperty()
    {
        try
        {
            ApplyDurationSeconds(ReadProperty<double>("Media::Duration"));
        }
        catch (Exception ex)
        {
            Logger.Trace(ex, "Timeline Absolute: Media::Duration unavailable");
        }
    }

    private void ApplyPositionSeconds(double value)
    {
        var seconds = NormalizeMediaSeconds(value);
        if (seconds is null)
            return;
        _positionSeconds = seconds;
    }

    private void ApplyDurationSeconds(double value)
    {
        var seconds = NormalizeMediaSeconds(value);
        if (seconds is null || seconds <= 0.0)
            return;
        _durationSeconds = seconds;
    }

    private static double? NormalizeMediaSeconds(double value)
    {
        if (!double.IsFinite(value) || value < 0.0)
            return null;
        if (value > AssumeMillisecondsAboveSeconds)
            value /= 1000.0;
        if (!double.IsFinite(value) || value < 0.0)
            return null;
        return value;
    }

    private void ClearCache()
    {
        _positionSeconds = null;
        _durationSeconds = null;
    }

    private void PublishAxes()
    {
        if (_positionSeconds is double position)
            PublishPosition(position);
        if (_durationSeconds is double duration)
            PublishDuration(duration);
    }

    private void PublishPosition(double positionSeconds)
    {
        SetAxis(PositionAxis, EncodeSeconds(positionSeconds), ref _loggedMissingPositionAxis);
    }

    private void PublishDuration(double durationSeconds)
    {
        SetAxis(DurationAxis, EncodeSeconds(durationSeconds), ref _loggedMissingDurationAxis);
    }

    private static double EncodeSeconds(double seconds)
    {
        if (seconds <= 0.0)
            return 0.0;
        if (seconds >= TimelineScaleSeconds)
            return 1.0;
        return seconds / TimelineScaleSeconds;
    }

    private void ConfigureTimelineAxes()
    {
        if (_configuredAxes)
            return;

        foreach (var name in new[] { PositionAxis, DurationAxis })
        {
            if (!DeviceAxis.TryParse(name.Trim().ToUpperInvariant(), out var axis) || axis is null)
                continue;
            try
            {
                InvokeAction("Axis::Bypass::Script::Set", axis, true);
                InvokeAction("Axis::Bypass::MotionProvider::Set", axis, true);
                InvokeAction("Axis::AutoHomeEnabled::Set", axis, false);
            }
            catch (Exception ex)
            {
                Logger.Trace(ex, "Timeline Absolute: could not configure axis {0}", name);
            }
        }

        _configuredAxes = true;
    }

    private void SetAxis(string axisName, double value, ref bool loggedMissing)
    {
        if (string.IsNullOrWhiteSpace(axisName))
            return;

        if (!DeviceAxis.TryParse(axisName.Trim().ToUpperInvariant(), out var axis) || axis is null)
        {
            if (!loggedMissing)
            {
                Logger.Warn(
                    "Timeline Absolute: device axis {0} is not enabled. Add it to the MFP device profile and include it on the T-code output to Vector.",
                    axisName);
                loggedMissing = true;
            }
            return;
        }

        InvokeAction("Axis::Value::Set", axis, value, 0.0);
    }
}
