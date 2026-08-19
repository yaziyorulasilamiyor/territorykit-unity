using System.Collections.Generic;
using UnityEngine;

namespace TerritoryKit.Unity
{
    /// <summary>
    /// Resolves a screen point to a territory id by testing it against the CPU-side geometry of
    /// whatever is currently visible — no <c>MeshCollider</c>, no physics.
    /// </summary>
    /// <remarks>
    /// <b>Why CPU triangle testing instead of MeshCollider + Raycast.</b> Viewport streaming
    /// swaps a pooled mesh's content constantly; a <c>MeshCollider</c> re-cooks its collision
    /// representation every time <c>sharedMesh</c>'s content changes, which would double the cost
    /// of every pool checkout on top of <c>SetVertexBufferData</c>, and keeps a second,
    /// physics-backend-owned copy of the geometry resident per territory. The bbox prefilter
    /// below (<see cref="TkmsMeshData.Bounds"/>, already computed and validated by
    /// <see cref="MeshDecoder"/>) narrows a click to a small number of candidates before any
    /// triangle test runs, so the CPU path stays cheap without needing physics at all — and it
    /// stays testable in EditMode with no live <c>PhysicsScene</c>.
    /// <para>
    /// <b>What this costs instead:</b> the vertex/index buffers <see cref="MeshDecoder.Decode"/>
    /// produces have to be kept alive in CPU memory for as long as a territory is visible, rather
    /// than disposed right after the GPU upload the way phase 4 did. See the phase 5 report for
    /// the measured per-territory cost and its projection to larger administrative levels — this
    /// is the scale limit of the design, not something this class hides.
    /// </para>
    /// <para>
    /// <b>Picking does not gate on <c>pickingUnsafe</c>.</b> <see cref="LodPolicy"/> already
    /// rejects a "pick the safest available level" helper because it would manufacture confidence
    /// the data does not support — on the real dataset all three levels report
    /// <c>pickingUnsafe: true</c>, so refusing to pick on that flag would make clicking
    /// non-functional rather than safer. This always resolves against the mesh actually on
    /// screen; the caller gets the active level's <see cref="LodSafety"/> back alongside the
    /// result and decides what, if anything, to show for it.
    /// </para>
    /// </remarks>
    public static class TerritoryPicker
    {
        /// <summary>One territory eligible for picking: its id and its retained mesh data.</summary>
        public readonly struct Candidate
        {
            public Candidate(string id, TkmsMeshData mesh)
            {
                Id = id;
                Mesh = mesh;
            }

            public string Id { get; }

            public TkmsMeshData Mesh { get; }
        }

        /// <summary>
        /// Resolves a screen point through <paramref name="camera"/> and <paramref name="mapRoot"/>'s
        /// ground plane, then tests it against <paramref name="candidates"/>.
        /// </summary>
        public static bool TryPickFromScreenPoint(Camera camera, Transform mapRoot, Vector2 screenPoint,
            IReadOnlyList<Candidate> candidates, out string territoryId)
        {
            territoryId = null;
            if (camera == null)
            {
                return false;
            }

            Ray ray = camera.ScreenPointToRay(screenPoint);
            return TerritoryMapPlacement.TryGroundPlanePoint(mapRoot, ray, out Vector2 local) &&
                   TryPick(local, candidates, out territoryId);
        }

        /// <summary>
        /// Tests a local-metre TKMS point against <paramref name="candidates"/> in order, bbox
        /// first, and returns the first one whose triangles actually contain the point.
        /// </summary>
        /// <remarks>
        /// If two candidates' geometry overlaps at the point (should not happen for a clean
        /// dataset, but nothing here assumes it cannot), the first match in
        /// <paramref name="candidates"/>' own order wins — callers that care about a specific
        /// tie-break order control it by the order they build the list in.
        /// </remarks>
        public static bool TryPick(Vector2 localPoint, IReadOnlyList<Candidate> candidates,
            out string territoryId)
        {
            territoryId = null;
            if (candidates == null)
            {
                return false;
            }

            for (int i = 0; i < candidates.Count; i++)
            {
                Candidate candidate = candidates[i];
                TkmsMeshData mesh = candidate.Mesh;
                Bounds bounds = mesh.Bounds;
                if (localPoint.x < bounds.min.x || localPoint.x > bounds.max.x ||
                    localPoint.y < bounds.min.y || localPoint.y > bounds.max.y)
                {
                    continue;
                }

                int triangleCount = mesh.IndexCount / 3;
                for (int t = 0; t < triangleCount; t++)
                {
                    Vector2 a = mesh.Vertices[IndexAt(mesh, t * 3)];
                    Vector2 b = mesh.Vertices[IndexAt(mesh, t * 3 + 1)];
                    Vector2 c = mesh.Vertices[IndexAt(mesh, t * 3 + 2)];
                    if (PointInTriangle(localPoint, a, b, c))
                    {
                        territoryId = candidate.Id;
                        return true;
                    }
                }
            }

            return false;
        }

        private static int IndexAt(TkmsMeshData mesh, int i)
        {
            return mesh.Header.UsesUInt32Indices ? (int)mesh.Indices32[i] : mesh.Indices16[i];
        }

        /// <summary>Barycentric-sign test: true when <paramref name="p"/> is not strictly outside any edge.</summary>
        private static bool PointInTriangle(Vector2 p, Vector2 a, Vector2 b, Vector2 c)
        {
            float d1 = Cross(p - a, b - a);
            float d2 = Cross(p - b, c - b);
            float d3 = Cross(p - c, a - c);

            bool hasNegative = d1 < 0f || d2 < 0f || d3 < 0f;
            bool hasPositive = d1 > 0f || d2 > 0f || d3 > 0f;
            return !(hasNegative && hasPositive);
        }

        private static float Cross(Vector2 lhs, Vector2 rhs)
        {
            return lhs.x * rhs.y - lhs.y * rhs.x;
        }
    }
}
