using System;
using System.Collections.Generic;
using UnityEngine;

namespace TerritoryKit.Unity
{
    /// <summary>
    /// One checked-out unit: a GameObject and a Mesh, paired for as long as a caller holds them.
    /// </summary>
    /// <remarks>
    /// The pairing is per-checkout, not permanent. <see cref="TerritoryPool"/> recycles the
    /// GameObject and the Mesh from two independent stacks, so the same GameObject instance can
    /// end up carrying a different Mesh instance across two checkouts (and vice versa) — nothing
    /// here assumes otherwise.
    /// </remarks>
    /// <remarks>
    /// A readonly struct, not a class: <see cref="Checkout"/> is the steady-state hot path this
    /// package targets zero managed allocation for, and four reference fields wrapped in a class
    /// would be one <c>new</c> per checkout for no reason — nothing here needs reference
    /// identity or a mutable lifetime independent of the checkout that produced it.
    /// </remarks>
    public readonly struct PooledTerritory
    {
        internal PooledTerritory(GameObject gameObject, MeshFilter meshFilter, MeshRenderer renderer,
            Mesh mesh, int version)
        {
            GameObject = gameObject;
            MeshFilter = meshFilter;
            Renderer = renderer;
            Mesh = mesh;
            Version = version;
        }

        public GameObject GameObject { get; }

        public MeshFilter MeshFilter { get; }

        public MeshRenderer Renderer { get; }

        public Mesh Mesh { get; }

        /// <summary>
        /// Which checkout of <see cref="GameObject"/> this value refers to.
        /// </summary>
        /// <remarks>
        /// The pool bumps a per-GameObject counter on every checkout <em>and</em> every release,
        /// so this identifies one specific checkout rather than merely "some checkout of this
        /// object". Without it a stale struct copy could be released a second time after its
        /// GameObject had already been checked back out — the ABA case: the object looks checked
        /// out because it genuinely is, just not by the copy doing the releasing.
        /// </remarks>
        internal int Version { get; }
    }

    /// <summary>Point-in-time pool counters, for tests and the phase report.</summary>
    public readonly struct TerritoryPoolStats
    {
        public TerritoryPoolStats(int freeGameObjects, int freeMeshes, int totalGameObjectsCreated,
            int totalMeshesCreated)
        {
            FreeGameObjects = freeGameObjects;
            FreeMeshes = freeMeshes;
            TotalGameObjectsCreated = totalGameObjectsCreated;
            TotalMeshesCreated = totalMeshesCreated;
        }

        public int FreeGameObjects { get; }

        public int FreeMeshes { get; }

        /// <summary>
        /// Every GameObject the pool has ever created, including ones currently checked out.
        /// Steady state means this stops moving after warm-up.
        /// </summary>
        public int TotalGameObjectsCreated { get; }

        /// <summary>Every Mesh the pool has ever created. Same steady-state expectation.</summary>
        public int TotalMeshesCreated { get; }
    }

    /// <summary>
    /// Recycles GameObjects and Meshes instead of destroying and recreating them.
    /// </summary>
    /// <remarks>
    /// <b>Two stacks, not one.</b> A GameObject's identity (transform, components) and a Mesh's
    /// identity (its native buffers) are recycled independently: <see cref="Checkout"/> pops one
    /// of each and pairs them for this checkout only, <see cref="Release"/> pushes each back onto
    /// its own stack. The alternative — a single pool of fused GameObject+Mesh units — would work
    /// identically for this phase's one caller, but it would tie two lifetimes together that have
    /// no reason to match: nothing about recycling a transform requires recycling the buffer that
    /// happened to be attached to it last time, and a design that assumes otherwise is the kind
    /// of coupling that only shows up once something needs to scale the two pools differently.
    /// <para>
    /// <b>Where the Mesh gets cleared.</b> Not here. <see cref="MeshDecoder.Apply"/> already
    /// calls <c>mesh.Clear()</c> before writing new buffers, so clearing on <see cref="Release"/>
    /// would just be wasted work for a mesh that is never checked out again before the scene
    /// ends.
    /// </para>
    /// <para>
    /// <b>Steady-state zero allocation.</b> After <see cref="WarmUp"/>, <see cref="Checkout"/>
    /// never calls <c>new GameObject()</c>, <c>AddComponent</c> or <c>new Mesh()</c> unless
    /// demand exceeds what was warmed — that growth is logged via <see cref="Stats"/> rather than
    /// hidden, because it is supposed to be a cold-start event, not a steady-state one.
    /// </para>
    /// </remarks>
    public sealed class TerritoryPool
    {
        private readonly Transform _parent;
        private readonly Material _material;
        private readonly Stack<GameObject> _freeGameObjects = new Stack<GameObject>();
        private readonly Stack<Mesh> _freeMeshes = new Stack<Mesh>();

        // Current checkout version per GameObject, so Release can reject anything that is not the
        // live checkout. PooledTerritory is a readonly struct, which means a caller can hold a
        // stale *copy* of a checkout it already returned; releasing that copy would push the same
        // GameObject and Mesh onto the free stacks twice and two later checkouts would be handed
        // the same objects while both believed they owned them exclusively.
        //
        // The counter is bumped on checkout *and* on release, which is what makes it an identity
        // for one specific checkout rather than a mere "is it out right now" flag. A flag alone
        // (the previous HashSet) closed the immediate double release but not the ABA case:
        // release, check the same object back out, then release the stale copy again -- the
        // object really is checked out, so a membership test says yes and the pool is corrupted
        // anyway. Version equality says no, because the live checkout is two bumps further on.
        //
        // Keyed on GameObject rather than Mesh because the two stacks are independent: after
        // enough cycles a given GameObject and Mesh need not be paired the way they once were.
        // Dictionary<GameObject, int> does not allocate on lookup or on assigning an existing
        // key, so this stays inside the zero-allocation budget TerritoryPoolTests asserts.
        private readonly Dictionary<GameObject, int> _checkoutVersion =
            new Dictionary<GameObject, int>();

        private int _totalGameObjectsCreated;
        private int _totalMeshesCreated;

        public TerritoryPool(Transform parent, Material material)
        {
            _parent = parent != null ? parent : throw new ArgumentNullException(nameof(parent));
            _material = material != null ? material : throw new ArgumentNullException(nameof(material));
        }

        public TerritoryPoolStats Stats => new TerritoryPoolStats(
            _freeGameObjects.Count, _freeMeshes.Count, _totalGameObjectsCreated, _totalMeshesCreated);

        /// <summary>
        /// Preallocates <paramref name="count"/> GameObjects and Meshes so the first frames of
        /// streaming never have to create either. Call once, before checkout starts — a warm-up
        /// allocation is a one-time cost, not a steady-state one.
        /// </summary>
        public void WarmUp(int count)
        {
            if (count <= 0)
            {
                return;
            }

            // Created in two passes rather than interleaved so a profiler sampling mid-warm-up
            // sees "creating GameObjects" then "creating Meshes", not an interleaving that means
            // nothing.
            var objects = new GameObject[count];
            var meshes = new Mesh[count];
            for (int i = 0; i < count; i++)
            {
                objects[i] = CreateGameObject();
            }

            for (int i = 0; i < count; i++)
            {
                meshes[i] = CreateMesh();
            }

            for (int i = 0; i < count; i++)
            {
                _freeGameObjects.Push(objects[i]);
                _freeMeshes.Push(meshes[i]);
            }
        }

        /// <summary>
        /// Checks out one GameObject and one Mesh, active and parented, ready for
        /// <see cref="MeshDecoder.Apply"/>.
        /// </summary>
        /// <remarks>
        /// The transform is reset to identity here, not merely reparented. A pooled GameObject
        /// carries whatever transform it had when it was last released, and nothing guarantees
        /// that was identity — a scene-view drag, an editor tool, or a caller reaching into
        /// <see cref="PooledTerritory.GameObject"/> can all leave one behind. Territory meshes are
        /// positioned entirely by their vertex data in the map root's local space
        /// (<see cref="TerritoryMapPlacement"/>), so a non-identity local transform on a territory
        /// object is always wrong; carrying a stale one into the next checkout would silently
        /// offset a province.
        /// </remarks>
        public PooledTerritory Checkout(string name)
        {
            GameObject go = _freeGameObjects.Count > 0 ? _freeGameObjects.Pop() : CreateGameObject();
            Mesh mesh = _freeMeshes.Count > 0 ? _freeMeshes.Pop() : CreateMesh();

            go.name = string.IsNullOrEmpty(name) ? "territory (pooled)" : name;
            var meshFilter = go.GetComponent<MeshFilter>();
            var renderer = go.GetComponent<MeshRenderer>();
            meshFilter.sharedMesh = mesh;
            ResetTransform(go.transform);
            go.SetActive(true);

            int version = _checkoutVersion[go] + 1;
            _checkoutVersion[go] = version;
            return new PooledTerritory(go, meshFilter, renderer, mesh, version);
        }

        /// <summary>Returns a checked-out unit to the pool.</summary>
        /// <remarks>
        /// Clears the renderer's <see cref="MaterialPropertyBlock"/> before pushing the
        /// GameObject back. Without this, the next checkout would render with the previous
        /// territory's colour for one frame — <c>sharedMesh</c> is reassigned immediately, but a
        /// property block survives on the renderer until something explicitly replaces or clears
        /// it.
        /// </remarks>
        /// <exception cref="InvalidOperationException">
        /// <paramref name="pooled"/> is not currently checked out — either it was released
        /// already, or it never came from this pool.
        /// </exception>
        public void Release(PooledTerritory pooled)
        {
            if (pooled.GameObject == null)
            {
                throw new ArgumentException(
                    "pooled is default(PooledTerritory), not a real checkout", nameof(pooled));
            }

            if (!_checkoutVersion.TryGetValue(pooled.GameObject, out int current))
            {
                throw new InvalidOperationException(
                    "'" + pooled.GameObject.name + "' did not come from this pool");
            }

            if (current != pooled.Version)
            {
                // Loud rather than quiet: a double release corrupts the pool in a way that only
                // shows up much later, as two territories rendering into the same Mesh. The
                // version mismatch covers both shapes -- releasing the same checkout twice, and
                // releasing a stale copy after the object has been checked out again (ABA).
                throw new InvalidOperationException(
                    "'" + pooled.GameObject.name + "' is not the live checkout (this value is " +
                    "version " + pooled.Version + ", the pool is on " + current + "); releasing " +
                    "it would put the same GameObject and Mesh on the free stacks twice and hand " +
                    "them to two different territories at once");
            }

            _checkoutVersion[pooled.GameObject] = current + 1;

            pooled.Renderer.SetPropertyBlock(null);
            pooled.MeshFilter.sharedMesh = null;
            pooled.GameObject.SetActive(false);
            ResetTransform(pooled.GameObject.transform);

            _freeGameObjects.Push(pooled.GameObject);
            _freeMeshes.Push(pooled.Mesh);
        }

        /// <summary>
        /// Puts a transform back to identity under the pool's parent, reparenting only when it
        /// has actually drifted — <c>SetParent</c> on an unchanged parent still costs a hierarchy
        /// change notification, and this runs on the streaming hot path.
        /// </summary>
        private void ResetTransform(Transform target)
        {
            if (target.parent != _parent)
            {
                target.SetParent(_parent, false);
            }

            target.localPosition = Vector3.zero;
            target.localRotation = Quaternion.identity;
            target.localScale = Vector3.one;
        }

        private GameObject CreateGameObject()
        {
            var go = new GameObject("territory (pooled)");
            go.transform.SetParent(_parent, false);
            go.AddComponent<MeshFilter>();
            var renderer = go.AddComponent<MeshRenderer>();
            renderer.sharedMaterial = _material;
            go.SetActive(false);
            // Seeded here so Checkout's read-modify-write never has to branch on a missing key,
            // and so an object from another pool is distinguishable by its absence.
            _checkoutVersion[go] = 0;
            _totalGameObjectsCreated++;
            return go;
        }

        private Mesh CreateMesh()
        {
            _totalMeshesCreated++;
            return new Mesh { name = "territory (pooled)" };
        }

        /// <summary>
        /// Destroys every free GameObject and Mesh. Callers must release everything checked out
        /// first — this walks the free stacks, not a registry of everything ever created.
        /// </summary>
        public void DestroyAll()
        {
            while (_freeGameObjects.Count > 0)
            {
                DestroyObject(_freeGameObjects.Pop());
            }

            while (_freeMeshes.Count > 0)
            {
                DestroyObject(_freeMeshes.Pop());
            }

            // The pool is finished after this; anything still checked out is the caller's to
            // destroy, and keeping it listed here would only make a later Release throw about a
            // pool that no longer exists.
            _checkoutVersion.Clear();
        }

        private static void DestroyObject(UnityEngine.Object target)
        {
            if (target == null)
            {
                return;
            }

            if (Application.isPlaying)
            {
                UnityEngine.Object.Destroy(target);
            }
            else
            {
                UnityEngine.Object.DestroyImmediate(target);
            }
        }
    }
}
