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

        [Test]
        public void ReleasingTheSameCheckoutTwiceIsRefused()
        {
            // PooledTerritory is a readonly struct, so a caller can hold a stale copy of a
            // checkout it already returned. Releasing that copy used to push the same GameObject
            // and Mesh onto the free stacks a second time; the next two checkouts would then be
            // handed the same objects while both believed they owned them exclusively -- two
            // territories writing into one Mesh, which surfaces as one province rendering as
            // another, far from the cause.
            _pool.WarmUp(4);
            PooledTerritory pooled = _pool.Checkout("x");
            _pool.Release(pooled);

            Assert.Throws<InvalidOperationException>(() => _pool.Release(pooled));

            TerritoryPoolStats stats = _pool.Stats;
            Assert.AreEqual(4, stats.FreeGameObjects, "the double release must not have added a duplicate");
            Assert.AreEqual(4, stats.FreeMeshes);
        }

        [Test]
        public void TwoCheckoutsAfterARefusedDoubleReleaseAreStillDistinct()
        {
            // The consequence the guard exists to prevent, asserted directly rather than only
            // through the free-count above.
            _pool.WarmUp(2);
            PooledTerritory first = _pool.Checkout("x");
            _pool.Release(first);
            try
            {
                _pool.Release(first);
            }
            catch (InvalidOperationException)
            {
                // expected
            }

            PooledTerritory a = _pool.Checkout("a");
            PooledTerritory b = _pool.Checkout("b");

            Assert.AreNotSame(a.GameObject, b.GameObject);
            Assert.AreNotSame(a.Mesh, b.Mesh);
        }

        [Test]
        public void ReleasingAStaleCopyAfterTheObjectWasCheckedOutAgainIsRefused()
        {
            // ABA. A membership flag ("is this object checked out?") is not enough here: by the
            // time the stale copy is released the object genuinely *is* checked out again, so the
            // flag says yes and the pool is corrupted anyway -- the same GameObject and Mesh land
            // on the free stacks twice and two later checkouts are handed the same objects.
            //
            // With one warmed unit the pool must hand back the same GameObject, which is what
            // makes the sequence below an A-B-A rather than three different objects.
            _pool.WarmUp(1);
            PooledTerritory first = _pool.Checkout("a");
            _pool.Release(first);
            PooledTerritory second = _pool.Checkout("b");
            Assert.AreSame(first.GameObject, second.GameObject, "the ABA case needs the same object back");

            Assert.Throws<InvalidOperationException>(() => _pool.Release(first),
                "the stale copy names a checkout that has already ended");

            // The live checkout is still releasable, and releasing it leaves exactly one free unit.
            Assert.DoesNotThrow(() => _pool.Release(second));
            Assert.AreEqual(1, _pool.Stats.FreeGameObjects);
            Assert.AreEqual(1, _pool.Stats.FreeMeshes);
        }

        [Test]
        public void TwoCheckoutsAfterARefusedStaleReleaseAreStillDistinct()
        {
            // The consequence the version guard exists to prevent, asserted directly.
            _pool.WarmUp(1);
            PooledTerritory first = _pool.Checkout("a");
            _pool.Release(first);
            PooledTerritory second = _pool.Checkout("b");
            try
            {
                _pool.Release(first);
            }
            catch (InvalidOperationException)
            {
                // expected
            }

            _pool.Release(second);

            PooledTerritory x = _pool.Checkout("x");
            PooledTerritory y = _pool.Checkout("y");
            Assert.AreNotSame(x.GameObject, y.GameObject,
                "a duplicated free entry would hand the same GameObject to two territories");
            Assert.AreNotSame(x.Mesh, y.Mesh);
        }

        [Test]
        public void ReleasingSomethingThisPoolNeverHandedOutIsRefused()
        {
            var foreignParent = new GameObject("foreign-parent");
            var foreignPool = new TerritoryPool(foreignParent.transform, _material);
            try
            {
                foreignPool.WarmUp(1);
                PooledTerritory foreign = foreignPool.Checkout("elsewhere");

                Assert.Throws<InvalidOperationException>(() => _pool.Release(foreign));
            }
            finally
            {
                foreignPool.DestroyAll();
                UnityEngine.Object.DestroyImmediate(foreignParent);
            }
        }

        [Test]
        public void CheckoutResetsATransformThatDriftedWhileTheObjectWasIdle()
        {
            // Territory meshes are positioned entirely by their vertex data in the map root's
            // local space, so a non-identity local transform on a territory object is always
            // wrong. A pooled object can pick one up while idle -- a scene-view drag is the way
            // it actually happened -- and carrying it into the next checkout silently offsets a
            // province.
            _pool.WarmUp(1);
            PooledTerritory pooled = _pool.Checkout("first");
            pooled.GameObject.transform.localPosition = new Vector3(123f, 456f, 789f);
            pooled.GameObject.transform.localRotation = Quaternion.Euler(10f, 20f, 30f);
            pooled.GameObject.transform.localScale = new Vector3(2f, 3f, 4f);
            _pool.Release(pooled);

            PooledTerritory reused = _pool.Checkout("second");

            Assert.AreEqual(Vector3.zero, reused.GameObject.transform.localPosition);
            Assert.AreEqual(Quaternion.identity, reused.GameObject.transform.localRotation);
            Assert.AreEqual(Vector3.one, reused.GameObject.transform.localScale);
        }

        [Test]
        public void ReleaseAlsoLeavesTheIdleTransformAtIdentity()
        {
            _pool.WarmUp(1);
            PooledTerritory pooled = _pool.Checkout("x");
            pooled.GameObject.transform.localPosition = new Vector3(5f, 5f, 5f);

            _pool.Release(pooled);

            Assert.AreEqual(Vector3.zero, pooled.GameObject.transform.localPosition,
                "an idle pooled object should not sit somewhere arbitrary in the hierarchy");
        }

        // A gate, not just a measurement: "GC alloc ~= 0" cannot be asserted on its own, so this
        // pins a concrete per-iteration byte budget instead -- exceed it and the test goes red.
        //
        // The measurement runs through AllocationMeasurement, which validates its own counter
        // first. That matters here specifically: the first version of this gate used
        // GC.GetAllocatedBytesForCurrentThread, which on Unity's Mono returns 0 for *every*
        // allocation -- including a 100 KB array -- so it reported a confident
        // "0 bytes/iteration" while measuring nothing at all.
        //
        // Re-measured with a counter that works: still 0 bytes/iteration over 500 steady-state
        // cycles (Unity 6000.1.1f1, Windows Editor). PooledTerritory is a readonly struct (no
        // `new` per checkout), Stack<T>.Push/Pop and HashSet<T>.Add/Remove of a reference type do
        // not allocate once grown, and GetComponent<T>() is allocation-free here. There is no
        // async/await and no closure on Checkout()/Release(). The budget is left at the measured
        // value rather than padded, so a change that reintroduces an allocation fails this test
        // instead of hiding inside slack.
        private const long BudgetBytesPerIteration = 0;

        [Test]
        public void SteadyStateCheckoutReleaseStaysWithinAFixedManagedByteBudget()
        {
            _pool.WarmUp(8);

            double perIteration = AllocationMeasurement.BytesPerIteration(
                "pool checkout/release", 500, () =>
                {
                    PooledTerritory pooled = _pool.Checkout("x");
                    _pool.Release(pooled);
                });

            Assert.LessOrEqual(perIteration, BudgetBytesPerIteration,
                $"steady-state checkout/release allocated {perIteration:F2} bytes/iteration; " +
                $"budget is {BudgetBytesPerIteration}");
        }
    }
}
