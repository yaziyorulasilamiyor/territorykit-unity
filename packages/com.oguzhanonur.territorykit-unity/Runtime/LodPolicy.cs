using System.Text;

namespace TerritoryKit.Unity
{
    /// <summary>How far a level can be trusted for resolving a click to a territory.</summary>
    public enum LodPickingSafety
    {
        /// <summary>The dataset did not publish this level, so nothing is known about it.</summary>
        Unknown,

        /// <summary>
        /// The level's geometry is topologically identical to the source: a click resolves to
        /// the territory the source says owns that point.
        /// </summary>
        Safe,

        /// <summary>
        /// Something in the chain from source polygons to this mesh lost geometry or changed
        /// part/hole structure. A click near a boundary may resolve to the wrong territory.
        /// </summary>
        Unsafe
    }

    /// <summary>A level's safety verdict together with the reason behind it.</summary>
    public readonly struct LodSafety
    {
        public LodSafety(string lod, LodPickingSafety safety, string reason)
        {
            Lod = lod;
            Safety = safety;
            Reason = reason;
        }

        public string Lod { get; }

        public LodPickingSafety Safety { get; }

        /// <summary>Human-readable explanation, suitable for a log line.</summary>
        public string Reason { get; }

        public bool IsSafeForPicking => Safety == LodPickingSafety.Safe;

        public override string ToString()
        {
            return "lod '" + Lod + "': " + Safety + " — " + Reason;
        }
    }

    /// <summary>
    /// Reads the per-level flags a dataset publishes and says what they mean. Pure and static:
    /// these are facts about a dataset revision, not about a camera or a scene.
    /// </summary>
    /// <remarks>
    /// This deliberately answers a question and does not make a choice. Phase 4 draws whatever
    /// level it is told to and logs the verdict; choosing a level by distance or zoom, with
    /// hysteresis, is Phase 5's job and will consume this.
    /// <para>
    /// There is no "pick the safest available level" helper here, and that omission is the
    /// point. On the real Turkish ADM1 chain all three levels report
    /// <c>pickingUnsafe: true</c> — geoBoundaries normalization drops seven real islets before
    /// simplification even starts — so a "best available" answer would manufacture confidence
    /// that the data does not support. The honest answer is that no level is safe, and callers
    /// have to see that.
    /// </para>
    /// </remarks>
    public static class LodPolicy
    {
        /// <summary>Describes what <paramref name="lod"/> can be trusted for.</summary>
        public static LodSafety Describe(DatasetInfo dataset, string lod)
        {
            DatasetLevel level = dataset != null ? dataset.FindLevel(lod) : null;
            if (level == null)
            {
                return new LodSafety(lod, LodPickingSafety.Unknown,
                    "the dataset does not publish this level");
            }

            return Describe(level);
        }

        /// <summary>Describes what <paramref name="level"/> can be trusted for.</summary>
        public static LodSafety Describe(DatasetLevel level)
        {
            if (level == null)
            {
                return new LodSafety(null, LodPickingSafety.Unknown, "no level given");
            }

            if (!level.pickingUnsafe)
            {
                return new LodSafety(level.lod, LodPickingSafety.Safe,
                    "geometry is topologically identical to the source");
            }

            var reason = new StringBuilder();
            if (level.lossy)
            {
                reason.Append("geometry present in the source is missing from this level");
            }

            if (level.topologyChanged)
            {
                if (reason.Length > 0)
                {
                    reason.Append("; ");
                }

                reason.Append("part or hole structure differs from the source");
            }

            if (reason.Length == 0)
            {
                // pickingUnsafe is derived from the same ledger as the other two, so this is
                // reachable only if the server ever relayed a set of flags that disagree.
                reason.Append("the dataset marks this level unsafe for picking");
            }

            if (level.simplification != null && !level.simplification.topologyChanged)
            {
                reason.Append(" (simplification itself changed nothing; the cause is elsewhere " +
                              "in the chain)");
            }

            return new LodSafety(level.lod, LodPickingSafety.Unsafe, reason.ToString());
        }
    }
}
