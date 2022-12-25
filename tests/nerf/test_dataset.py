import numpy as np
import pytest

from rndf_robot.nerf.dataset import normalize_transforms, unnormalize_transforms


@pytest.mark.parametrize(
    "translation, scale",
    [
        (np.array([0, 0, 0]), 1),
        (np.array([1, 2, 3]), 2),
        (np.array([0.1, 0.2, 0.3]), 0.75),
    ],
)
def test_normalize_unnormalize_transforms(
    translation: np.ndarray, scale: float, transforms: dict
):
    """
    Test that normalizing and unnormalizing the transformation matrix by
    the given translation and scale is as expected.
    """
    # Normalize the transforms, check that the translation and scale are correct
    normalized_transforms = normalize_transforms(transforms, translation, scale)
    for frame, normalized_frame in zip(
        transforms["frames"], normalized_transforms["frames"]
    ):
        expected_normalized_transform = np.array(frame["transform_matrix"])
        expected_normalized_transform[:3, 3] -= translation
        expected_normalized_transform[:3, 3] *= scale
        assert np.allclose(
            normalized_frame["transform_matrix"], expected_normalized_transform
        )

    # Check transform matrices are the same after unnormalizing
    unnormalized_transforms = unnormalize_transforms(
        normalized_transforms, translation, scale
    )
    for frame, unnormalized_frame in zip(
        transforms["frames"], unnormalized_transforms["frames"]
    ):
        assert np.allclose(
            frame["transform_matrix"], unnormalized_frame["transform_matrix"]
        )
