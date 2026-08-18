using System;
using NUnit.Framework;
using UnityEngine;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// The pool's one job: after warm-up, checkout and release never call
    /// <c>new GameObject()</c>, <c>AddComponent</c> or <c>new Mesh()</c> again.
    /// </summary>
    public class TerritoryPoolTests
    {
        private GameObject _parentObject;
        private Material _material;
        private TerritoryPool _pool;

        [SetUp]
        public void SetUp()
        {
            _parentObject = new GameObject("pool-parent");
            Shader shader = Shader.Find("Unlit/Color") ?? Shader.Find("Universal Render Pipeline/Unlit");
            Assert.IsNotNull(shader, "no unlit shader available in this test environment");
            _material = new Material(shader);
            _pool = new TerritoryPool(_parentObject.transform, _material);
        }

        [TearDown]
        public void TearDown()
        {
            _pool?.DestroyAll();
            if (_parentObject != null) UnityEngine.Object.DestroyImmediate(_parentObject);
            if (_material != null) UnityEngine.Object.DestroyImmediate(_material);
        }

        [Test]
        public void OneHundredCheckoutReleaseCyclesLeaveTheGameObjectCountStable()
        {
            _pool.WarmUp(4);
            TerritoryPoolStats warm = _pool.Stats;
            Assert.AreEqual(4, warm.TotalGameObjectsCreated);
            Assert.AreEqual(4, warm.TotalMeshesCreated);

            for (int i = 0; i < 100; i++)
            {
                PooledTerritory a = _pool.Checkout("a");
                PooledTerritory b = _pool.Checkout("b");
                _pool.Release(a);
                _pool.Release(b);
            }

            TerritoryPoolStats after = _pool.Stats;
            Assert.AreEqual(4, after.TotalGameObjectsCreated,
                "100 cycles of checking out at most 2 at a time must not grow a pool warmed to 4");
            Assert.AreEqual(4, after.TotalMeshesCreated);
            Assert.AreEqual(4, after.FreeGameObjects, "everything checked out must come back");
            Assert.AreEqual(4, after.FreeMeshes);
        }

        [Test]
        public void CheckoutBeyondWarmCapacityGrowsOnceAndThenStopsGrowing()
        {
            _pool.WarmUp(1);

            PooledTerritory first = _pool.Checkout("first");
            // The pool had exactly one warmed unit; a second concurrent checkout must create one
            // more rather than hand out the one already in use.
            PooledTerritory second = _pool.Checkout("second");
            Assert.AreNotSame(first.GameObject, second.GameObject);
            Assert.AreNotSame(first.Mesh, second.Mesh);
            Assert.AreEqual(2, _pool.Stats.TotalGameObjectsCreated,
                "demand past the warm count is expected to grow the pool once");

            _pool.Release(first);
            _pool.Release(second);

            // The grown capacity is now warm; the same demand a second time must not grow again.
            PooledTerritory third = _pool.Checkout("third");
            PooledTerritory fourth = _pool.Checkout("fourth");
            Assert.AreEqual(2, _pool.Stats.TotalGameObjectsCreated,
                "the pool must remember the capacity it already grew to");
            _pool.Release(third);
            _pool.Release(fourth);
        }

        [Test]
        public void ReleaseClearsTheMaterialPropertyBlockSoAStaleColourCannotSurvive()
        {
            _pool.WarmUp(1);
            PooledTerritory pooled = _pool.Checkout("coloured");

            var block = new MaterialPropertyBlock();
            block.SetColor(Shader.PropertyToID("_Color"), Color.red);
            pooled.Renderer.SetPropertyBlock(block);
            Assert.IsTrue(pooled.Renderer.HasPropertyBlock(), "the block must actually be attached before release");

            _pool.Release(pooled);

            Assert.IsFalse(pooled.Renderer.HasPropertyBlock(),
                "a released renderer must not carry the previous territory's colour into the " +
                "next checkout, even for one frame");

            // The next checkout, in this pool's LIFO order, hands back the exact same GameObject
            // — this is the case that would actually flash the stale colour if release did not
            // clear the block.
            PooledTerritory reused = _pool.Checkout("next");
            Assert.AreSame(pooled.GameObject, reused.GameObject);
            Assert.IsFalse(reused.Renderer.HasPropertyBlock());
        }

        [Test]
        public void ReleaseDetachesTheMeshSoAnIdleGameObjectNeverPointsAtAMeshInUseElsewhere()
        {
            _pool.WarmUp(2);
            PooledTerritory pooled = _pool.Checkout("x");

            _pool.Release(pooled);

            Assert.IsNull(pooled.MeshFilter.sharedMesh,
                "a pooled-but-idle GameObject must not keep pointing at a Mesh that a later " +
                "checkout is free to hand to a different territory and mutate");
        }

        // A gate, not just a measurement: "GC alloc ~= 0" cannot be asserted on its own, so this
        // pins a concrete per-iteration byte budget instead -- exceed it and the test goes red.
        // Measured at exactly 0 bytes/iteration over 500 steady-state cycles (Unity 6000.1.1f1,
        // Windows Editor): PooledTerritory/VisibleTerritory are readonly structs (no `new` per
        // checkout), Stack<T>.Push/Pop of a reference type does not allocate, and
        // GameObject.GetComponent<T>() is allocation-free on this Unity version. There is no
        // async/await and no closure anywhere on Checkout()/Release(), which is usually where an
        // "almost zero" GC budget actually goes on a hot path like this one -- there is simply
        // nothing here that would allocate. The budget is left at the measured value rather than
        // padded with slack, so a future change that reintroduces an allocation here fails this
        // test instead of quietly raising the number a padded gate would have hidden.
        private const long BudgetBytesPerIteration = 0;

        [Test]
        public void SteadyStateCheckoutReleaseStaysWithinAFixedManagedByteBudget()
        {
            _pool.WarmUp(8);

            // Let JIT warm-up, first-call component-type caches, etc. settle before measuring --
            // none of that is "steady state" and none of it should be charged to the budget.
            for (int i = 0; i < 50; i++)
            {
                PooledTerritory warm = _pool.Checkout("warm");
                _pool.Release(warm);
            }

            GC.Collect();
            GC.WaitForPendingFinalizers();
            GC.Collect();

            long before = GC.GetAllocatedBytesForCurrentThread();
            const int iterations = 500;
            for (int i = 0; i < iterations; i++)
            {
                PooledTerritory pooled = _pool.Checkout("x");
                _pool.Release(pooled);
            }

            long totalBytes = GC.GetAllocatedBytesForCurrentThread() - before;
            double perIteration = totalBytes / (double)iterations;

            TestContext.Out.WriteLine(
                $"pool checkout/release: {totalBytes} bytes over {iterations} iterations " +
                $"= {perIteration:F2} bytes/iteration");

            Assert.LessOrEqual(perIteration, BudgetBytesPerIteration,
                $"steady-state checkout/release allocated {perIteration:F2} bytes/iteration " +
                $"({totalBytes} bytes over {iterations} iterations); budget is " +
                $"{BudgetBytesPerIteration}");
        }
    }
}
