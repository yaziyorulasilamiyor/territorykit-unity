using System.Runtime.CompilerServices;

// Two claims this package makes are about threading and object ownership, and neither is
// observable through the public API: that UnityWebRequest.Abort() is never issued off the main
// thread, and that meshes are destroyed if cancellation lands between decoding them and drawing
// them. The test seams that make those observable are internal rather than public, so they stay
// out of the surface callers program against.
[assembly: InternalsVisibleTo("TerritoryKit.Unity.RuntimeTests")]
[assembly: InternalsVisibleTo("TerritoryKit.Unity.EditorTests")]
