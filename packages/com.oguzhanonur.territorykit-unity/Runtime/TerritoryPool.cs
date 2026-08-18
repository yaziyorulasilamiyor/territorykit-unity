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
    public sealed class PooledTerritory
    {
        internal PooledTerritory(GameObject gameObject, MeshFilter meshFilter, MeshRenderer renderer,
            Mesh mesh)
        {
            GameObject = gameObject;
            MeshFilter = meshFilter;
            Renderer = renderer;
            Mesh = mesh;
        }

        public GameObject GameObject { get; }

        public MeshFilter MeshFilter { get; }

        public MeshRenderer Renderer { get; }

        public Mesh Mesh { get; }
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
        public PooledTerritory Checkout(string name)
        {
            GameObject go = _freeGameObjects.Count > 0 ? _freeGameObjects.Pop() : CreateGameObject();
            Mesh mesh = _freeMeshes.Count > 0 ? _freeMeshes.Pop() : CreateMesh();

            go.name = string.IsNullOrEmpty(name) ? "territory (pooled)" : name;
            var meshFilter = go.GetComponent<MeshFilter>();
            var renderer = go.GetComponent<MeshRenderer>();
            meshFilter.sharedMesh = mesh;
            go.transform.SetParent(_parent, false);
            go.SetActive(true);

            return new PooledTerritory(go, meshFilter, renderer, mesh);
        }

        /// <summary>Returns a checked-out unit to the pool.</summary>
        /// <remarks>
        /// Clears the renderer's <see cref="MaterialPropertyBlock"/> before pushing the
        /// GameObject back. Without this, the next checkout would render with the previous
        /// territory's colour for one frame — <c>sharedMesh</c> is reassigned immediately, but a
        /// property block survives on the renderer until something explicitly replaces or clears
        /// it.
        /// </remarks>
        public void Release(PooledTerritory pooled)
        {
            if (pooled == null) throw new ArgumentNullException(nameof(pooled));

            pooled.Renderer.SetPropertyBlock(null);
            pooled.MeshFilter.sharedMesh = null;
            pooled.GameObject.SetActive(false);
            pooled.GameObject.transform.SetParent(_parent, false);

            _freeGameObjects.Push(pooled.GameObject);
            _freeMeshes.Push(pooled.Mesh);
        }

        private GameObject CreateGameObject()
        {
            var go = new GameObject("territory (pooled)");
            go.transform.SetParent(_parent, false);
            go.AddComponent<MeshFilter>();
            var renderer = go.AddComponent<MeshRenderer>();
            renderer.sharedMaterial = _material;
            go.SetActive(false);
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
