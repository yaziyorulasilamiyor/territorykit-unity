using UnityEngine;
#if ENABLE_INPUT_SYSTEM
using UnityEngine.InputSystem;
#endif

namespace TerritoryKit.Unity.Samples.BasicMap
{
    /// <summary>
    /// Sample-only camera control for <see cref="ViewportStreamer"/>: frames the dataset once
    /// it loads, right-drag pans, the scroll wheel zooms, and a left click resolves through
    /// <see cref="ViewportStreamer.TryPick"/> and recolours whatever it hits.
    /// </summary>
    /// <remarks>
    /// This is sample content, not part of the package's public API — it lives under
    /// <c>Samples~</c> rather than <c>Runtime</c> on purpose, and there is no <c>.asmdef</c> here:
    /// once imported it compiles into the consuming project's own assembly, which is what lets it
    /// read <c>ENABLE_INPUT_SYSTEM</c>/<c>ENABLE_LEGACY_INPUT_MANAGER</c> — Unity's own switches
    /// for which input backend Project Settings has active — without the package ever declaring a
    /// hard dependency on the Input System package. A Unity 6 project defaults to
    /// "Input System Package (New)" alone, where the old <see cref="Input"/> class throws
    /// <c>InvalidOperationException</c> on every read; this sample was only ever exercised in this
    /// repo's dev project, which had "Input Manager (Old)" selected, so that crash shipped
    /// unnoticed until a clean-project install hit it on the very first frame with scroll input.
    /// <para>
    /// Both backends are supported below through the four <c>Read*</c>/<c>*Button*</c> helpers.
    /// When "Both" is active, the legacy branch is preferred — it is the code path this sample
    /// already had coverage on. When neither is active (a configuration Unity itself does not
    /// normally produce, but not one this file trusts either), pan/zoom/click are disabled after
    /// one warning instead of throwing every frame; <see cref="FrameOnFirstLoad"/> still runs, so
    /// the map still renders and frames itself.
    /// </para>
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

        private bool _inputAvailable;
        private bool _warnedNoInput;

        private void Reset()
        {
            targetCamera = Camera.main;
            streamer = FindObjectOfType<ViewportStreamer>();
        }

        private void Awake()
        {
#if ENABLE_LEGACY_INPUT_MANAGER || ENABLE_INPUT_SYSTEM
            _inputAvailable = true;
#else
            _inputAvailable = false;
#endif
        }

        private void Update()
        {
            if (targetCamera == null)
            {
                return;
            }

            FrameOnFirstLoad();

            if (!_inputAvailable)
            {
                if (!_warnedNoInput)
                {
                    Debug.LogWarning("[TerritoryKit] BasicMapCameraController found neither the " +
                        "legacy Input Manager nor the Input System package active (Project " +
                        "Settings > Player > Active Input Handling). Pan/zoom/click are disabled " +
                        "for this sample; the map itself still renders.");
                    _warnedNoInput = true;
                }

                return;
            }

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
            float scroll = ReadScrollDelta();
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
            if (RightButtonDown())
            {
                _dragging = true;
                _dragOrigin = ReadMousePosition();
            }
            else if (RightButtonUp())
            {
                _dragging = false;
            }

            if (!_dragging)
            {
                return;
            }

            Vector3 current = ReadMousePosition();
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
            if (!LeftButtonDown() || streamer == null)
            {
                return;
            }

            bool hit = streamer.TryPick(ReadMousePosition(), out string territoryId, out LodSafety safety);
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

        // -- Input backend shims -------------------------------------------------------------
        // Legacy is preferred when both are active: it is the branch this sample originally
        // shipped and was exercised with. Each pair below compiles to exactly one backend; there
        // is no runtime branching cost beyond the _inputAvailable check in Update.

        private static float ReadScrollDelta()
        {
#if ENABLE_LEGACY_INPUT_MANAGER
            return Input.mouseScrollDelta.y;
#elif ENABLE_INPUT_SYSTEM
            Mouse mouse = Mouse.current;
            // New Input System reports scroll in ~120-unit notches (Windows wheel convention);
            // legacy reports roughly 1-3 per notch. Scaled down so zoomSpeed feels the same either way.
            return mouse != null ? mouse.scroll.ReadValue().y / 120f : 0f;
#else
            return 0f;
#endif
        }

        private static Vector3 ReadMousePosition()
        {
#if ENABLE_LEGACY_INPUT_MANAGER
            return Input.mousePosition;
#elif ENABLE_INPUT_SYSTEM
            Mouse mouse = Mouse.current;
            return mouse != null ? (Vector3)mouse.position.ReadValue() : Vector3.zero;
#else
            return Vector3.zero;
#endif
        }

        private static bool RightButtonDown()
        {
#if ENABLE_LEGACY_INPUT_MANAGER
            return Input.GetMouseButtonDown(1);
#elif ENABLE_INPUT_SYSTEM
            return Mouse.current != null && Mouse.current.rightButton.wasPressedThisFrame;
#else
            return false;
#endif
        }

        private static bool RightButtonUp()
        {
#if ENABLE_LEGACY_INPUT_MANAGER
            return Input.GetMouseButtonUp(1);
#elif ENABLE_INPUT_SYSTEM
            return Mouse.current != null && Mouse.current.rightButton.wasReleasedThisFrame;
#else
            return false;
#endif
        }

        private static bool LeftButtonDown()
        {
#if ENABLE_LEGACY_INPUT_MANAGER
            return Input.GetMouseButtonDown(0);
#elif ENABLE_INPUT_SYSTEM
            return Mouse.current != null && Mouse.current.leftButton.wasPressedThisFrame;
#else
            return false;
#endif
        }
    }
}
