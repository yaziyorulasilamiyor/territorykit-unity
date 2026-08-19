using UnityEngine;

namespace TerritoryKit.Unity.Samples.BasicMap
{
    /// <summary>
    /// Sample-only camera control for <see cref="ViewportStreamer"/>: frames the dataset once
    /// it loads, right-drag pans, the scroll wheel zooms, and a left click resolves through
    /// <see cref="ViewportStreamer.TryPick"/> and recolours whatever it hits.
    /// </summary>
    /// <remarks>
    /// This is sample content, not part of the package's public API — it lives under
    /// <c>Samples~</c> rather than <c>Runtime</c> on purpose, and uses the legacy
    /// <see cref="Input"/> class rather than the Input System package so the sample has no
    /// dependency beyond what <c>package.json</c> already declares.
    /// </remarks>
    [AddComponentMenu("TerritoryKit/Samples/Basic Map Camera Controller")]
    public sealed class BasicMapCameraController : MonoBehaviour
    {
        [SerializeField]
        private Camera targetCamera;

        [SerializeField]
        private ViewportStreamer streamer;

        [SerializeField]
        private float minOrthographicSize = 5000f;

        [SerializeField]
        private float maxOrthographicSize = 800000f;

        [SerializeField]
        private float zoomSpeed = 0.15f;

        private bool _framed;
        private bool _dragging;
        private Vector3 _dragOrigin;
        private readonly System.Random _highlightRandom = new System.Random();

        private void Reset()
        {
            targetCamera = Camera.main;
            streamer = FindObjectOfType<ViewportStreamer>();
        }

        private void Update()
        {
            if (targetCamera == null)
            {
                return;
            }

            FrameOnFirstLoad();
            HandleZoom();
            HandleDrag();
            HandleClick();
        }

        private void FrameOnFirstLoad()
        {
            if (_framed || streamer == null || streamer.Dataset == null)
            {
                return;
            }

            float[] bounds = streamer.Dataset.boundsLocal;
            if (bounds == null || bounds.Length < 4)
            {
                return;
            }

            TerritoryMapPlacement.FrameBounds(targetCamera, bounds[0], bounds[1], bounds[2], bounds[3]);
            _framed = true;
        }

        private void HandleZoom()
        {
            float scroll = Input.mouseScrollDelta.y;
            if (Mathf.Approximately(scroll, 0f))
            {
                return;
            }

            float size = targetCamera.orthographicSize * (1f - scroll * zoomSpeed);
            targetCamera.orthographicSize = Mathf.Clamp(size, minOrthographicSize, maxOrthographicSize);
        }

        private void HandleDrag()
        {
            // Right mouse button, so a left click stays free for picking.
            if (Input.GetMouseButtonDown(1))
            {
                _dragging = true;
                _dragOrigin = Input.mousePosition;
            }
            else if (Input.GetMouseButtonUp(1))
            {
                _dragging = false;
            }

            if (!_dragging)
            {
                return;
            }

            Vector3 current = Input.mousePosition;
            Vector3 delta = current - _dragOrigin;
            _dragOrigin = current;
            if (delta.sqrMagnitude < 0.01f)
            {
                return;
            }

            // World units per screen pixel at the current zoom, so a drag tracks the ground
            // under the cursor regardless of how far zoomed in or out the camera is.
            float worldPerPixelY = targetCamera.orthographicSize * 2f / Mathf.Max(1, targetCamera.pixelHeight);
            float worldPerPixelX = worldPerPixelY * targetCamera.aspect;

            Transform cameraTransform = targetCamera.transform;
            Vector3 move = -(cameraTransform.right * (delta.x * worldPerPixelX) +
                              cameraTransform.up * (delta.y * worldPerPixelY));
            cameraTransform.position += move;
        }

        private void HandleClick()
        {
            if (!Input.GetMouseButtonDown(0) || streamer == null)
            {
                return;
            }

            bool hit = streamer.TryPick(Input.mousePosition, out string territoryId, out LodSafety safety);
            if (!hit)
            {
                return;
            }

            streamer.SetTerritoryColor(territoryId, RandomHighlightColour());

            if (!safety.IsSafeForPicking)
            {
                // Not an error: the sample dataset marks every level unsafe (geoBoundaries drops
                // seven real islets before simplification starts), so this always logs on the
                // real Turkish data. It is here so the console explains why, rather than the
                // click just quietly working.
                Debug.Log("[TerritoryKit] picked '" + territoryId + "' at a level marked unsafe " +
                          "for picking: " + safety.Reason);
            }
        }

        private Color RandomHighlightColour()
        {
            return Color.HSVToRGB(
                (float)_highlightRandom.NextDouble(),
                0.60f + (float)_highlightRandom.NextDouble() * 0.30f,
                0.75f + (float)_highlightRandom.NextDouble() * 0.20f);
        }
    }
}
