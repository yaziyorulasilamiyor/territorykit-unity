using System;

namespace TerritoryKit.Unity
{
    /// <summary>
    /// Thrown when a byte payload is not a TKMS v1 mesh this reader can trust.
    /// </summary>
    /// <remarks>
    /// The decoder mirrors the reference implementation in
    /// <c>services/geometry-api/src/geometry_api/encoding.py</c>: it rejects everything that
    /// makes a payload <em>unreadable or dangerous to trust</em>, and deliberately does not
    /// check the two rules that only affect whether an otherwise valid mesh <em>renders</em>
    /// correctly (clockwise winding, zero-area triangles). Those are the encoder's contract,
    /// paid for by the build pipeline and its tests, not by every client on every mesh.
    /// </remarks>
    public class TkmsFormatException : Exception
    {
        public TkmsFormatException(string message) : base(message)
        {
        }

        public TkmsFormatException(string message, Exception inner) : base(message, inner)
        {
        }
    }
}
