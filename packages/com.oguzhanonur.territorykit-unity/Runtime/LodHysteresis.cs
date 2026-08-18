using System;

namespace TerritoryKit.Unity
{
    /// <summary>
    /// Picks a detail level from a zoom metric with hysteresis, so hovering near a boundary does
    /// not flip the level back and forth every frame.
    /// </summary>
    /// <remarks>
    /// Pure and stateless in the same spirit as <see cref="LodPolicy"/>: it answers "given where
    /// the camera is and which level is showing now, which level should show next", and does not
    /// itself track anything across calls. The caller (<c>ViewportStreamer</c>) owns the current
    /// level and feeds it back in every time, which is what makes this trivially testable without
    /// a camera, a scene, or time passing.
    /// <para>
    /// <b>The zoom metric</b> is whatever the caller measures distance/scale with — this class
    /// assumes only that larger means "more zoomed out, coarser detail is appropriate" and
    /// smaller means the opposite (an orthographic camera's <c>orthographicSize</c> is the
    /// natural choice for the top-down placement this package uses; see
    /// <c>TerritoryMapPlacement</c>).
    /// </para>
    /// <para>
    /// <b>The thresholds are per-scene, not universal constants.</b> What counts as "zoomed out"
    /// depends entirely on the scale of the dataset in the scene, so the defaults
    /// <c>ViewportStreamer</c> ships are a starting point to tune, not a measured or validated
    /// number the way, say, the projection error band in <c>docs/projection.md</c> is.
    /// </para>
    /// </remarks>
    public static class LodHysteresis
    {
        /// <summary>The two boundaries between three levels: high/medium and medium/low.</summary>
        public static readonly string[] LevelsFinestFirst = { "high", "medium", "low" };

        /// <summary>One boundary between two adjacent levels.</summary>
        public readonly struct Threshold
        {
            public Threshold(float switchToCoarserAt, float switchToFinerAt)
            {
                if (!(switchToFinerAt < switchToCoarserAt))
                {
                    throw new ArgumentException(
                        "switchToFinerAt (" + switchToFinerAt + ") must be less than " +
                        "switchToCoarserAt (" + switchToCoarserAt + ") or there is no dead band " +
                        "and this degenerates into a single flip-prone threshold");
                }

                SwitchToCoarserAt = switchToCoarserAt;
                SwitchToFinerAt = switchToFinerAt;
            }

            /// <summary>Zoom metric at or above this crosses from the finer side to the coarser side.</summary>
            public float SwitchToCoarserAt { get; }

            /// <summary>Zoom metric below this crosses back from the coarser side to the finer side.</summary>
            public float SwitchToFinerAt { get; }
        }

        /// <summary>
        /// Selects the level for <paramref name="zoomMetric"/>, given the level currently showing.
        /// </summary>
        /// <param name="levelsFinestFirst">Ordered finest to coarsest, e.g. <see cref="LevelsFinestFirst"/>.</param>
        /// <param name="thresholds">
        /// One fewer than <paramref name="levelsFinestFirst"/> — <c>thresholds[i]</c> is the
        /// boundary between <c>levelsFinestFirst[i]</c> and <c>levelsFinestFirst[i + 1]</c>.
        /// </param>
        /// <param name="currentLevel">
        /// The level showing now. An unrecognised value (including null, e.g. before anything
        /// has loaded) is treated as the finest level, so a fresh start always begins at maximum
        /// detail rather than guessing.
        /// </param>
        public static string SelectLevel(string[] levelsFinestFirst, Threshold[] thresholds,
            string currentLevel, float zoomMetric)
        {
            if (levelsFinestFirst == null) throw new ArgumentNullException(nameof(levelsFinestFirst));
            if (thresholds == null) throw new ArgumentNullException(nameof(thresholds));
            if (thresholds.Length != levelsFinestFirst.Length - 1)
            {
                throw new ArgumentException(
                    "thresholds must have exactly one fewer entry than levelsFinestFirst (" +
                    levelsFinestFirst.Length + " levels need " + (levelsFinestFirst.Length - 1) +
                    " boundaries, got " + thresholds.Length + ")");
            }

            int index = Array.IndexOf(levelsFinestFirst, currentLevel);
            if (index < 0)
            {
                index = 0;
            }

            // Coarsen for as long as the metric clears the next boundary up...
            while (index < thresholds.Length && zoomMetric >= thresholds[index].SwitchToCoarserAt)
            {
                index++;
            }

            // ...then refine for as long as it falls below the boundary just below. Both loops
            // run in the same call so a large jump in the metric (a teleporting camera, a test
            // driving this directly) resolves to the right level in one call rather than
            // stepping through every level in between.
            while (index > 0 && zoomMetric < thresholds[index - 1].SwitchToFinerAt)
            {
                index--;
            }

            return levelsFinestFirst[index];
        }
    }
}
