using System;

namespace TerritoryKit.Unity
{
    /// <summary>
    /// Thrown when the geometry API could not be reached, or answered with an error.
    /// </summary>
    public class TerritoryApiException : Exception
    {
        public TerritoryApiException(string message, long responseCode = 0, string errorCode = null)
            : base(message)
        {
            ResponseCode = responseCode;
            ErrorCode = errorCode;
        }

        /// <summary>HTTP status code, or 0 when the request never got an answer.</summary>
        public long ResponseCode { get; }

        /// <summary>
        /// The API's own error code (<c>revision_gone</c>, <c>unknown_lod</c>, …), when the
        /// response carried one. Null for transport failures.
        /// </summary>
        public string ErrorCode { get; }
    }
}
