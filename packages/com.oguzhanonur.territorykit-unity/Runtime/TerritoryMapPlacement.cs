using UnityEngine;

namespace TerritoryKit.Unity
{
    /// <summary>
    /// The single definition of how TKMS local-metre XY lands in Unity world space.
    /// </summary>
    /// <remarks>
    /// TKMS guarantees clockwise winding in its own XY space, with +X right and +Y up. Unity
    /// treats a face as front-facing when its indices read clockwise <em>from the camera</em>
    /// (Unity Manual, "Mesh index data"), so whether that guarantee survives depends entirely
    /// on where the mesh is put and where the camera looks. Both halves of that answer live
    /// here so they cannot drift apart.
    /// <para>
    /// Meshes keep the format's own coordinates, (x, y, 0). <see cref="RootRotation"/> lays that
    /// plane flat into world XZ by rotating +90° about X, which maps local +Y to world +Z and
    /// local +Z to world −Y. A clockwise triangle has a local −Z normal, so after the rotation
    /// its normal points to world +Y and a camera above sees the front face.
    /// </para>
    /// <para>
    /// Two mistakes break this and both break it silently, by making the whole map invisible
    /// rather than wrong: negating an axis while placing the mesh, and giving the camera an up
    /// vector of −Z. Both reverse the handedness of the mapping. The derivation above is not
    /// trusted on its own — a PlayMode test renders the sample with back-face culling on and
    /// asserts the pixels are there.
    /// </para>
    /// </remarks>
    /// <summary>A local-metre TKMS bounding box: min/max, the same convention as the API's own bbox fields.</summary>
    public readonly struct LocalBounds
    {
        public LocalBounds(float minX, float minY, float maxX, float maxY)
        {
            MinX = minX;
            MinY = minY;
            MaxX = maxX;
            MaxY = maxY;
        }

        public float MinX { get; }

        public float MinY { get; }

        public float MaxX { get; }

        public float MaxY { get; }

        /// <summary>True when no point can satisfy both bounds — nothing hit the ground plane.</summary>
        public bool IsEmpty => MinX > MaxX || MinY > MaxY;

        /// <summary>A copy grown by <paramref name="margin"/> on every side.</summary>
        public LocalBounds Expanded(float margin)
        {
            return new LocalBounds(MinX - margin, MinY - margin, MaxX + margin, MaxY + margin);
        }

        /// <summary>Whether this box and another overlap (touching edges count as intersecting).</summary>
        public bool Intersects(LocalBounds other)
        {
            return MinX <= other.MaxX && MaxX >= other.MinX &&
                   MinY <= other.MaxY && MaxY >= other.MinY;
        }

        public override string ToString()
        {
            return "(" + MinX + ", " + MinY + ")-(" + MaxX + ", " + MaxY + ")";
        }
    }

    public static class TerritoryMapPlacement
    {
        /// <summary>
        /// Rotation for the root object that holds every territory, laying mesh XY flat into
        /// world XZ. Territory objects below it keep an identity local transform.
        /// </summary>
        public static readonly Quaternion RootRotation = Quaternion.Euler(90f, 0f, 0f);

        /// <summary>
        /// Rotation for a camera looking straight down at the map, with world +X to the right
        /// of the screen and world +Z up the screen — the orientation the winding derivation
        /// above assumes.
        /// </summary>
        public static readonly Quaternion TopDownCameraRotation = Quaternion.Euler(90f, 0f, 0f);

        /// <summary>Maps a local-metre XY coordinate to the world position it renders at.</summary>
        public static Vector3 ToWorld(float localX, float localY)
        {
            return RootRotation * new Vector3(localX, localY, 0f);
        }

        /// <summary>
        /// Places a top-down orthographic camera so that the local-metre box
        /// (<paramref name="minX"/>, <paramref name="minY"/>)–(<paramref name="maxX"/>,
        /// <paramref name="maxY"/>) fits in view.
        /// </summary>
        public static void FrameBounds(Camera camera, float minX, float minY, float maxX, float maxY,
            float padding = 1.05f)
        {
            if (camera == null)
            {
                return;
            }

            float centreX = (minX + maxX) * 0.5f;
            float centreY = (minY + maxY) * 0.5f;
            float width = Mathf.Max(maxX - minX, Mathf.Epsilon);
            float height = Mathf.Max(maxY - minY, Mathf.Epsilon);

            float aspect = camera.aspect > 0f ? camera.aspect : 1f;
            // Orthographic size is the half-height; a wide map has to be fitted by width.
            float halfHeight = Mathf.Max(height * 0.5f, width * 0.5f / aspect) * padding;

            Vector3 centre = ToWorld(centreX, centreY);
            float distance = Mathf.Max(halfHeight * 4f, 1000f);

            camera.orthographic = true;
            camera.orthographicSize = halfHeight;
            camera.transform.SetPositionAndRotation(
                new Vector3(centre.x, distance, centre.z), TopDownCameraRotation);
            camera.nearClipPlane = 0.1f;
            camera.farClipPlane = distance * 2f;
        }

        /// <summary>
        /// Intersects a world-space ray with the map's ground plane and returns the local-metre
        /// TKMS (x, y) it lands on.
        /// </summary>
        /// <remarks>
        /// The ground plane is <paramref name="mapRoot"/>'s own local XY plane — mesh vertices
        /// are always (x, y, 0) in that space — so <c>mapRoot.forward</c> (its local +Z axis in
        /// world space) is the plane's normal regardless of how mapRoot itself is positioned or
        /// rotated in the scene. This does not assume <see cref="RootRotation"/> or a map sitting
        /// at the world origin; it reads the actual transform. Shared by
        /// <c>ViewportStreamer</c> (four viewport corners → a culling box) and
        /// <c>TerritoryPicker</c> (one click point), so the two can never disagree about where a
        /// screen point lands.
        /// </remarks>
        /// <returns>False if the ray is parallel to the plane or points away from it.</returns>
        public static bool TryGroundPlanePoint(Transform mapRoot, Ray ray, out Vector2 local)
        {
            if (mapRoot == null)
            {
                local = default;
                return false;
            }

            var plane = new Plane(mapRoot.forward, mapRoot.position);
            if (!plane.Raycast(ray, out float distance))
            {
                local = default;
                return false;
            }

            Vector3 worldPoint = ray.GetPoint(distance);
            Vector3 localPoint = mapRoot.InverseTransformPoint(worldPoint);
            local = new Vector2(localPoint.x, localPoint.y);
            return true;
        }

        /// <summary>
        /// A conservative local-metre box for everything <paramref name="camera"/> can see on
        /// the map's ground plane: the axis-aligned box around the four viewport corners'
        /// intersections with the plane.
        /// </summary>
        /// <remarks>
        /// Exact for a top-down orthographic camera. For a tilted or perspective one the visible
        /// ground footprint is not itself axis-aligned in local space, so this deliberately
        /// over-approximates rather than under-approximates: viewport culling would rather fetch
        /// a few extra territories at the edge than silently miss ones that are actually on
        /// screen. A corner ray that never reaches the plane (pointed at the sky) is skipped;
        /// null means none of the four did.
        /// </remarks>
        public static LocalBounds? CameraGroundBounds(Camera camera, Transform mapRoot)
        {
            if (camera == null || mapRoot == null)
            {
                return null;
            }

            float minX = float.PositiveInfinity, minY = float.PositiveInfinity;
            float maxX = float.NegativeInfinity, maxY = float.NegativeInfinity;
            bool any = false;

            for (int i = 0; i < 4; i++)
            {
                float vx = i == 1 || i == 3 ? 1f : 0f;
                float vy = i == 2 || i == 3 ? 1f : 0f;
                Ray ray = camera.ViewportPointToRay(new Vector3(vx, vy, 0f));
                if (!TryGroundPlanePoint(mapRoot, ray, out Vector2 local))
                {
                    continue;
                }

                any = true;
                if (local.x < minX) minX = local.x;
                if (local.x > maxX) maxX = local.x;
                if (local.y < minY) minY = local.y;
                if (local.y > maxY) maxY = local.y;
            }

            return any ? new LocalBounds(minX, minY, maxX, maxY) : (LocalBounds?)null;
        }
    }
}
