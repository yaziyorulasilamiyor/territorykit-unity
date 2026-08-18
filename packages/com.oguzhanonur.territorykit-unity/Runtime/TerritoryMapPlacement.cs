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
    }
}
