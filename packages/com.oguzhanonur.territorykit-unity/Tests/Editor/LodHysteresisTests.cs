using System.Collections.Generic;
using NUnit.Framework;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// The hysteresis dead band is the entire point of this class: a naive single-threshold
    /// implementation would flip levels every time the zoom metric crosses the boundary, and
    /// these tests are written to fail against that implementation specifically.
    /// </summary>
    public class LodHysteresisTests
    {
        private static readonly string[] Levels = LodHysteresis.LevelsFinestFirst;

        // high|medium boundary: coarsen at 100, refine below 80 (dead band [80, 100)).
        // medium|low boundary: coarsen at 300, refine below 240 (dead band [240, 300)).
        private static readonly LodHysteresis.Threshold[] Thresholds =
        {
            new LodHysteresis.Threshold(switchToCoarserAt: 100f, switchToFinerAt: 80f),
            new LodHysteresis.Threshold(switchToCoarserAt: 300f, switchToFinerAt: 240f)
        };

        [Test]
        public void StaysAtTheFinestLevelWellBelowTheFirstBoundary()
        {
            Assert.AreEqual("high", LodHysteresis.SelectLevel(Levels, Thresholds, "high", 10f));
        }

        [Test]
        public void CoarsensExactlyAtTheUpperThreshold()
        {
            Assert.AreEqual("high", LodHysteresis.SelectLevel(Levels, Thresholds, "high", 99.9f));
            Assert.AreEqual("medium", LodHysteresis.SelectLevel(Levels, Thresholds, "high", 100f));
        }

        [Test]
        public void RefinesOnlyOnceBelowTheLowerThreshold()
        {
            // Right at the boundary it just crossed coarsening from: must NOT refine yet.
            Assert.AreEqual("medium", LodHysteresis.SelectLevel(Levels, Thresholds, "medium", 99f));
            Assert.AreEqual("medium", LodHysteresis.SelectLevel(Levels, Thresholds, "medium", 80f));
            Assert.AreEqual("high", LodHysteresis.SelectLevel(Levels, Thresholds, "medium", 79.9f));
        }

        [Test]
        public void HoveringInsideTheDeadBandNeverChangesTheLevel()
        {
            // Once at "medium", bouncing the zoom metric anywhere inside [80, 100) -- above the
            // refine threshold, below the coarsen threshold -- must never move off "medium".
            // This is exactly the case a single-threshold design gets wrong: it has no dead band
            // at all, so any oscillation around the crossing point flips the level every time.
            var random = new System.Random(1234);
            string current = "medium";
            for (int i = 0; i < 500; i++)
            {
                float zoom = 80f + (float)random.NextDouble() * (100f - 80f);
                current = LodHysteresis.SelectLevel(Levels, Thresholds, current, zoom);
                Assert.AreEqual("medium", current,
                    "flipped away from medium at zoom=" + zoom + " on iteration " + i);
            }
        }

        [Test]
        public void ALargeJumpResolvesDirectlyToTheRightLevelWithoutStoppingInBetween()
        {
            // A camera cut or a test driving this directly, not a smooth pan -- the state
            // machine must not require passing through "medium" to reach "low".
            Assert.AreEqual("low", LodHysteresis.SelectLevel(Levels, Thresholds, "high", 10000f));
            Assert.AreEqual("high", LodHysteresis.SelectLevel(Levels, Thresholds, "low", -10000f));
        }

        [Test]
        public void AnUnrecognisedCurrentLevelStartsFromTheFinest()
        {
            Assert.AreEqual("high", LodHysteresis.SelectLevel(Levels, Thresholds, null, 10f));
            Assert.AreEqual("medium", LodHysteresis.SelectLevel(Levels, Thresholds, "???", 150f));
        }

        [Test]
        public void ConstructingAThresholdWithNoDeadBandIsRejected()
        {
            Assert.Throws<System.ArgumentException>(() => new LodHysteresis.Threshold(100f, 100f));
            Assert.Throws<System.ArgumentException>(() => new LodHysteresis.Threshold(100f, 150f));
        }

        /// <summary>
        /// The gate this class exists for: sweep the zoom metric slowly up past both boundaries
        /// and back down again, with the metric moving in small steps and lingering with jitter
        /// around each crossing (the realistic case -- a hand on a scroll wheel does not move in
        /// one clean jump), and assert the level sequence never reverses a transition it just
        /// made.
        /// </summary>
        [Test]
        public void SweepingSlowlyUpAndBackDownNeverOscillatesAtEitherBoundary()
        {
            var zoomTrace = new List<float>();
            // Smooth ramp 0 -> 400 with jitter of +/-3 layered on top near both boundaries, so
            // the trace repeatedly re-enters and leaves each dead band on the way past it --
            // exactly the pattern that would make a flip-prone implementation visibly thrash.
            for (float baseZoom = 0f; baseZoom <= 400f; baseZoom += 0.5f)
            {
                float jitter = (baseZoom >= 70f && baseZoom <= 110f) || (baseZoom >= 270f && baseZoom <= 310f)
                    ? 3f * (float)System.Math.Sin(baseZoom)
                    : 0f;
                zoomTrace.Add(baseZoom + jitter);
            }

            for (float baseZoom = 400f; baseZoom >= 0f; baseZoom -= 0.5f)
            {
                float jitter = (baseZoom >= 70f && baseZoom <= 110f) || (baseZoom >= 270f && baseZoom <= 310f)
                    ? 3f * (float)System.Math.Sin(baseZoom)
                    : 0f;
                zoomTrace.Add(baseZoom + jitter);
            }

            string current = "high";
            var transitions = new List<(int step, string from, string to)>();
            for (int i = 0; i < zoomTrace.Count; i++)
            {
                string next = LodHysteresis.SelectLevel(Levels, Thresholds, current, zoomTrace[i]);
                if (next != current)
                {
                    transitions.Add((i, current, next));
                    current = next;
                }
            }

            // Two different failure shapes, both real:
            //
            // 1. A transition undone within a handful of steps by its exact reverse is jitter
            //    thrashing a boundary -- the up-then-down sweep legitimately reverses every
            //    transition eventually (medium->low on the way up, low->medium on the way back
            //    down), but hundreds of steps apart, while the camera travelled all the way to
            //    the far end and back. A short gap is the signature of noise, not of a real trip.
            const int jitterThrashWindow = 50;
            for (int i = 1; i < transitions.Count; i++)
            {
                bool isExactReversal = transitions[i].from == transitions[i - 1].to &&
                                        transitions[i].to == transitions[i - 1].from;
                int stepGap = transitions[i].step - transitions[i - 1].step;
                Assert.IsFalse(isExactReversal && stepGap < jitterThrashWindow,
                    "oscillated: " + transitions[i - 1].from + "->" + transitions[i - 1].to +
                    " undone by ->" + transitions[i].to + " only " + stepGap +
                    " steps later (step " + transitions[i].step + ")");
            }

            // 2. Jitter inside a dead band must add zero extra transitions beyond the four a
            //    clean monotonic up-and-down sweep produces on its own.
            Assert.AreEqual(4, transitions.Count,
                "a full up-and-down sweep across two boundaries should transition exactly four " +
                "times (high->medium->low->medium->high); jitter inside a dead band must not " +
                "add any more: " + string.Join(", ", transitions.ConvertAll(t => t.from + "->" + t.to)));
        }
    }
}
