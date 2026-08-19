using System;
using NUnit.Framework;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// Measures managed allocation per iteration on Unity's Mono runtime, and refuses to report a
    /// number it cannot stand behind.
    /// </summary>
    /// <remarks>
    /// <b>Why this is not one line of <c>GC.GetAllocatedBytesForCurrentThread</c>.</b> That API
    /// exists on this runtime, compiles, runs, and always returns the same value: measured here,
    /// it reports a delta of <b>0 bytes</b> across a 100 KB array allocation, 1000 boxed ints, a
    /// <c>CancellationTokenSource</c> and a <c>float.ToString("R")</c>. The phase 5 pool gate was
    /// originally written on it and reported a comfortable "0 bytes/iteration" while measuring
    /// nothing whatsoever. <c>ProfilerRecorder(ProfilerCategory.Memory, "GC Allocated In Frame")</c>
    /// is likewise valid-but-empty in batchmode — it collects zero samples, the same way the
    /// render-statistics counters do.
    /// <para>
    /// What does work is the managed heap gauge, <see cref="GC.GetTotalMemory"/> (and
    /// <c>Profiler.GetMonoUsedSizeLong</c>, which returns byte-identical values). It measures
    /// occupancy rather than cumulative allocation, so it is only a valid allocation measure
    /// across a window in which no collection ran — otherwise freed memory subtracts and the
    /// delta can even come out negative, which is exactly what a 20,000-object probe produced.
    /// This helper therefore brackets each attempt with <see cref="GC.CollectionCount"/> and only
    /// accepts a window that survived without a collection.
    /// </para>
    /// </remarks>
    public static class AllocationMeasurement
    {
        /// <summary>
        /// Runs <paramref name="action"/> <paramref name="iterations"/> times after warming up,
        /// and returns the managed bytes allocated per iteration.
        /// </summary>
        /// <remarks>
        /// Fails the test rather than returning a misleading number when the runtime's counters
        /// do not respond, or when every attempt was disturbed by a collection.
        /// </remarks>
        public static double BytesPerIteration(string label, int iterations, Action action,
            int warmupIterations = 200)
        {
            AssertTheCounterActuallyCounts();

            // JIT, first-call caches and any one-time lazy initialisation inside the action are
            // not steady state and must not be charged to the budget.
            for (int i = 0; i < warmupIterations; i++)
            {
                action();
            }

            const int attempts = 5;
            for (int attempt = 0; attempt < attempts; attempt++)
            {
                GC.Collect();
                GC.WaitForPendingFinalizers();
                GC.Collect();

                int collectionsBefore = GC.CollectionCount(0);
                long before = GC.GetTotalMemory(false);

                for (int i = 0; i < iterations; i++)
                {
                    action();
                }

                long after = GC.GetTotalMemory(false);
                int collectionsAfter = GC.CollectionCount(0);

                if (collectionsAfter == collectionsBefore)
                {
                    // No collection ran, so nothing was freed and the heap grew by exactly what
                    // the loop allocated.
                    double perIteration = (after - before) / (double)iterations;
                    TestContext.Out.WriteLine(
                        $"{label}: {after - before} bytes over {iterations} iterations = " +
                        $"{perIteration:F2} bytes/iteration (attempt {attempt + 1})");
                    return perIteration;
                }
            }

            Assert.Fail(
                $"{label}: a garbage collection ran during all {attempts} measurement attempts. " +
                "A heap-occupancy gauge cannot measure allocation across a collection, and code " +
                "that fills a freshly collected heap within " + iterations + " iterations is " +
                "allocating far too much to be inside any budget worth setting.");
            return 0;
        }

        /// <summary>
        /// Proves the counter responds to a known allocation before any budget is trusted.
        /// </summary>
        /// <remarks>
        /// This is the check whose absence let the first version of the phase 5 gate pass while
        /// measuring nothing. A budget asserted against a dead counter is worse than no budget:
        /// it is a green test that certifies an unexamined claim.
        /// </remarks>
        private static void AssertTheCounterActuallyCounts()
        {
            GC.Collect();
            GC.WaitForPendingFinalizers();
            GC.Collect();

            long before = GC.GetTotalMemory(false);
            var probe = new byte[1 << 20];
            probe[0] = 1;
            long after = GC.GetTotalMemory(false);
            GC.KeepAlive(probe);

            Assert.Greater(after - before, 500_000,
                "GC.GetTotalMemory did not respond to a 1 MiB allocation on this runtime, so " +
                "every allocation budget in this suite would pass by measuring nothing. Find a " +
                "counter that works before trusting any number here.");
        }
    }
}
