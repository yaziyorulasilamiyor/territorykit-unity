using System;
using UnityEngine;

namespace TerritoryKit.Unity
{
    /// <summary>Projection origin for a dataset. See <c>docs/projection.md</c>.</summary>
    /// <remarks>
    /// The origin is defined once per dataset, never per mesh, so every mesh in a dataset shares
    /// one local metre space. Phase 4 does not need it to draw — meshes already arrive in that
    /// space — but it is what turns a local coordinate back into longitude and latitude.
    /// </remarks>
    [Serializable]
    public class DatasetOrigin
    {
        public double lon;
        public double lat;
        public string projection;
        public double scale;
    }

    /// <summary>Per-level simplification detail, reported separately from the chain-wide flags.</summary>
    [Serializable]
    public class LevelSimplification
    {
        /// <summary>
        /// Whether the simplification step alone changed topology. Deliberately a different
        /// question from <see cref="DatasetLevel.topologyChanged"/>, which covers the whole
        /// chain including normalization and triangulation.
        /// </summary>
        public bool topologyChanged;
    }

    /// <summary>One detail level of a dataset, with the three flags a client reads before it trusts it.</summary>
    [Serializable]
    public class DatasetLevel
    {
        public string lod;
        public int territoryCount;
        public int vertexCount;
        public int triangleCount;
        public long byteLength;

        /// <summary>False means nothing present in the source is missing from the output.</summary>
        public bool lossy;

        /// <summary>False means part and hole structure matches the source.</summary>
        public bool topologyChanged;

        /// <summary>
        /// False means a click on this level resolves to the territory the source says owns
        /// that point. True means it may not. See <c>docs/mesh-format.md</c>.
        /// </summary>
        public bool pickingUnsafe;

        public LevelSimplification simplification;
    }

    /// <summary>
    /// Response of <c>GET /v1/datasets/{id}</c>: everything a client needs before it downloads
    /// a single mesh.
    /// </summary>
    /// <remarks>
    /// This is one request, not an extra one: the revision id that mesh URLs are built from,
    /// the local-metre bounds the camera is framed against, and the per-level safety flags all
    /// arrive together.
    /// </remarks>
    [Serializable]
    public class DatasetInfo
    {
        public string id;
        public string name;
        public string sourceFormat;

        /// <summary>
        /// The revision every mesh URL is pinned to. Mesh endpoints have no "current" alias on
        /// purpose — artifact URLs are canonical and immutable.
        /// </summary>
        public string revisionId;

        public bool isCurrentRevision;
        public string publishedAt;
        public DatasetOrigin origin;

        /// <summary>Source bounds in WGS84 degrees: minLon, minLat, maxLon, maxLat.</summary>
        public double[] boundsWgs84;

        /// <summary>Dataset bounds in local metres: minX, minY, maxX, maxY.</summary>
        public float[] boundsLocal;

        public int territoryCount;
        public DatasetLevel[] levels;

        /// <summary>Returns the named level, or null when the dataset does not publish it.</summary>
        public DatasetLevel FindLevel(string lod)
        {
            if (levels == null)
            {
                return null;
            }

            foreach (var level in levels)
            {
                if (level != null && string.Equals(level.lod, lod, StringComparison.Ordinal))
                {
                    return level;
                }
            }

            return null;
        }
    }

    /// <summary>One territory in a dataset listing.</summary>
    [Serializable]
    public class TerritorySummary
    {
        public string id;
        public string name;
        public string parentId;
        public int administrativeLevel;

        /// <summary>Bounds in local metres: minX, minY, maxX, maxY.</summary>
        public float[] bboxLocal;

        public string[] neighborIds;
    }

    /// <summary>One cursor-paginated page of <c>GET /v1/datasets/{id}/territories</c>.</summary>
    [Serializable]
    public class TerritoryPage
    {
        public string revisionId;
        public string lod;
        public TerritorySummary[] items;
        public string nextCursor;

        /// <summary>
        /// True when the server stopped scanning before it ran out of territories. Phase 4 loads
        /// every page, so this only matters as a signal that another page exists.
        /// </summary>
        public bool scanTruncated;
    }

    /// <summary>One page of <c>GET /v1/datasets/{id}/viewport</c>: ids only.</summary>
    [Serializable]
    public class ViewportPage
    {
        public string revisionId;
        public string lod;
        public string[] territoryIds;
        public string nextCursor;
        public bool scanTruncated;
    }

    /// <summary>The API's single error shape, unwrapped far enough to be worth reading.</summary>
    [Serializable]
    public class ApiErrorBody
    {
        public ApiErrorDetail error;
    }

    /// <summary>Code and message of an API error.</summary>
    [Serializable]
    public class ApiErrorDetail
    {
        public string code;
        public string message;
    }
}
