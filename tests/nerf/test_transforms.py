import numpy as np
import pytest

from rndf_robot.nerf.transforms import normalize_pcd, unnormalize_pcd


@pytest.fixture(scope="function")
def pcd(num_points: int = 100) -> np.ndarray:
    """Generate a random point cloud."""
    yield np.random.randn(num_points, 3)


@pytest.mark.parametrize(
    "translation, scale",
    [
        (np.array([0.1, 0.2, 0.3]), 0.6777),
    ],
)
def test_normalize_and_unnormalize_pcd(
    translation: np.ndarray, scale: float, pcd: np.ndarray
):
    normalized_pcd = normalize_pcd(pcd, translation, scale)
    assert np.allclose(normalized_pcd, (pcd - translation) * scale)

    unnormalized_pcd = unnormalize_pcd(normalized_pcd, translation, scale)
    assert np.allclose(pcd, unnormalized_pcd)
