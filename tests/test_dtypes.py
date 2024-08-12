# Copyright (c) QuantCo 2023-2024
# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import re

import numpy as np
import pytest
from typing_extensions import Self

import ndonnx as ndx
from ndonnx import (
    Array,
    CastError,
    CoreType,
)
from ndonnx._experimental import CastMixin, Schema, StructType, UniformShapeOperations


class Unsigned96Impl(UniformShapeOperations):
    def equal(self, x, y):
        return custom_equal(x, y)

    def arange(self, start, stop=None, step=None, dtype=None, device=None) -> Array:
        # Pretty naive implementation
        if stop is None or step != 1 or stop > ndx.iinfo(ndx.uint64).max:
            raise ValueError("Unsupported arange arguments")

        return Array._from_fields(
            Unsigned96(),
            upper=ndx.asarray(0, dtype=ndx.uint32),
            lower=ndx.arange(start, stop, step, dtype=ndx.uint64),
        )

    def eye(self, n_rows, n_cols=None, k=0, dtype=None, device=None) -> Array:
        return Array._from_fields(
            Unsigned96(),
            upper=ndx.asarray(0, dtype=ndx.uint32),
            lower=ndx.eye(n_rows, n_cols, k, dtype=ndx.int64, device=device).astype(
                ndx.uint64
            ),
        )

    def zeros(self, shape, dtype: CoreType | StructType | None = None, device=None):
        return Array._from_fields(
            Unsigned96(),
            upper=ndx.zeros(shape, dtype=ndx.uint32, device=device),
            lower=ndx.zeros(shape, dtype=ndx.uint64, device=device),
        )

    def ones(self, shape, dtype: CoreType | StructType | None = None, device=None):
        return Array._from_fields(
            Unsigned96(),
            upper=ndx.zeros(shape, dtype=ndx.uint32, device=device),
            lower=ndx.ones(shape, dtype=ndx.uint64, device=device),
        )

    def empty(self, shape, dtype=None, device=None) -> Array:
        return ndx.zeros(shape, dtype=Unsigned96(), device=device)


class Unsigned96(StructType, CastMixin):
    def _fields(self) -> dict[str, StructType | CoreType]:
        return {
            "upper": ndx.uint32,
            "lower": ndx.uint64,
        }

    def _parse_input(self, x: np.ndarray) -> dict:
        upper = self._fields()["upper"]._parse_input(
            np.array(x >> 64).astype(np.uint32)
        )
        lower = self._fields()["lower"]._parse_input(
            (x & np.array([0xFFFFFFFFFFFFFFFF])).astype(np.uint64)
        )
        return {
            "upper": upper,
            "lower": lower,
        }

    def _assemble_output(self, fields: dict[str, np.ndarray]) -> np.ndarray:
        return (fields["upper"].astype(object) << 64) | fields["lower"].astype(object)

    def copy(self) -> Self:
        return self

    def _schema(self) -> Schema:
        return Schema(type_name="u96", author="value from data!")

    def _cast_to(self, array: Array, dtype: CoreType | StructType) -> Array:
        raise CastError(f"Cannot cast {self} to {dtype}")

    def _cast_from(self, array: Array) -> Array:
        if isinstance(array.dtype, ndx.Integral):
            array = array.astype(ndx.uint64)
        else:
            raise CastError(f"Cannot cast {array.dtype} to {self}")

        return Array._from_fields(
            Unsigned96(), upper=ndx.asarray(0, dtype=ndx.uint32), lower=array
        )

    _ops = Unsigned96Impl()


def custom_equal(x: Array, y: Array) -> Array:
    if x.dtype != Unsigned96() or y.dtype != Unsigned96():
        raise ValueError("Can only compare Unsigned96 arrays")
    # ort doesn't implement equality on unsigned 64s, so I roll my own!
    return ((x.lower ^ y.lower) | (x.upper ^ y.upper)) == 0


@pytest.fixture
def u96():
    return Unsigned96()


def test_custom_dtype_array_creation(u96):
    ndx.asarray(np.array(0, dtype=object), dtype=u96)


def test_unsigned96_casting(u96):
    with pytest.raises(CastError):
        Array._from_fields(
            u96,
            upper=ndx.asarray(0, dtype=ndx.uint32),
            lower=ndx.asarray(0, dtype=ndx.uint64),
        ).astype(ndx.uint64)

    expected = ndx.asarray(np.array(0, dtype=object), dtype=u96)
    actual = ndx.asarray(0, dtype=ndx.uint64).astype(u96)
    custom_equal_result = custom_equal(expected, actual).to_numpy()
    assert custom_equal_result is not None and custom_equal_result.item()

    expected = ndx.asarray(np.array([1 << 40], dtype=object), dtype=u96)
    actual = ndx.asarray(np.array([1 << 40]), dtype=ndx.uint32).astype(u96)

    # Should overflow
    custom_equal_result = custom_equal(expected, actual).to_numpy()
    assert custom_equal_result is not None and not custom_equal_result.item()
    actual_value = actual.to_numpy()
    assert actual_value is not None and actual_value.item() == 0
    expected_value = expected.to_numpy()
    assert expected_value is not None and expected_value.item() == 1 << 40


def test_custom_dtype_function_dispatch(u96):
    x = ndx.asarray(np.array([22314, 21 << 12, 12], dtype=object), dtype=u96)
    y = ndx.asarray(np.array([22314, 21 << 12, 12], dtype=object), dtype=u96)
    z = ndx.asarray(np.array([223, 21 << 12, 13], dtype=object), dtype=u96)

    np.testing.assert_equal(ndx.equal(x, y).to_numpy(), [True, True, True])
    np.testing.assert_equal(ndx.equal(x, z).to_numpy(), [False, True, False])


def test_custom_dtype_layout_transformations(u96):
    x = ndx.asarray(np.array([22314, 21 << 12, 12, 1242134], dtype=object), dtype=u96)
    x_value = x.to_numpy()
    if x_value is None:
        raise ValueError("x.to_numpy() is None")
    expected = x_value.reshape(2, 2)
    actual = ndx.reshape(x, (2, 2)).to_numpy()
    np.testing.assert_equal(expected, actual)

    expected = ndx.roll(x, 1).to_numpy()
    if x.to_numpy() is None:
        raise ValueError("x.to_numpy() is None")
    actual = np.roll(x.to_numpy(), 1)
    np.testing.assert_equal(expected, actual)


def test_error_message_unimplemented_dtype_dispatch(u96):
    x = ndx.asarray(np.array([22314, 21 << 12, 12], dtype=object), dtype=u96)
    y = ndx.asarray(np.array([223, 21 << 12, 13], dtype=object), dtype=u96)

    with pytest.raises(
        TypeError,
        match=re.escape("Unsupported operand type(s) for less_equal: 'Unsigned96'"),
    ):
        ndx.less_equal(x, y)
    with pytest.raises(
        TypeError,
        match=re.escape("Unsupported operand type for bitwise_invert: 'Unsigned96'"),
    ):
        ~x


def test_isinstance_i32():
    assert isinstance(ndx.int32, ndx.CoreType)
    assert isinstance(ndx.int32, ndx.Numerical)
    assert isinstance(ndx.int32, ndx.Integral)
    assert not isinstance(ndx.int32, ndx.Floating)


def test_isinstance_f32():
    assert isinstance(ndx.float32, ndx.CoreType)
    assert isinstance(ndx.float32, ndx.Numerical)
    assert isinstance(ndx.float32, ndx.Floating)
    assert not isinstance(ndx.float32, ndx.NullableIntegral)


def test_equality():
    assert ndx.int32 == ndx.int32
    assert ndx.int32 != ndx.int64
    assert ndx.int32 != ndx.nint32


def test_custom_dtype_incapable_of_complex_dispatch():
    # We might consider enabling this once we have a good
    # plan for dispatching in an intuitive way where you
    # don't shoot yourself in the foot.

    x = ndx.asarray(np.array([22314, 21 << 12, 12], dtype=object), dtype=Unsigned96())
    y = ndx.asarray(np.array([223, 21 << 12, 13], dtype=object), dtype=Unsigned96())

    with pytest.raises(ndx.UnsupportedOperationError):
        ndx.concat([x, y], axis=0)


def test_custom_dtype_capable_creation_functions():
    x = ndx.full((2, 2), 0, dtype=Unsigned96())
    assert x.shape == (2, 2)
    np.testing.assert_equal(x.to_numpy(), np.array([[0, 0], [0, 0]], dtype=object))

    x = ndx.arange(0, 4, 1, dtype=Unsigned96())
    np.testing.assert_equal(x.to_numpy(), np.array([0, 1, 2, 3], dtype=object))

    x = ndx.zeros((2, 3, 2), dtype=Unsigned96())
    np.testing.assert_equal(x.to_numpy(), np.zeros((2, 3, 2), dtype=object))

    x = ndx.ones((2, 3, 2), dtype=Unsigned96())
    np.testing.assert_equal(x.to_numpy(), np.ones((2, 3, 2), dtype=object))

    x = ndx.empty((2, 3, 2), dtype=Unsigned96())
    assert x.shape == (2, 3, 2)

    x = ndx.eye(3, dtype=Unsigned96())
    np.testing.assert_equal(x.to_numpy(), np.eye(3, dtype=object))
