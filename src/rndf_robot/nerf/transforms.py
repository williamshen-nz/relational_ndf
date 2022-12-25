"""
Utility functions for transformations from coordinate systems for NeRF.
This is here, so we can test it more easily and then copy it into other projects.
"""
import numpy as np


def normalize_pcd(pcd: np.ndarray, translation: np.ndarray, scale: float) -> np.ndarray:
    """Normalize the point cloud by the given translation and scale."""
    return (pcd - translation) * scale


def unnormalize_pcd(
    pcd: np.ndarray, translation: np.ndarray, scale: float
) -> np.ndarray:
    """Unnormalize the point cloud by the given translation and scale."""
    return pcd / scale + translation
