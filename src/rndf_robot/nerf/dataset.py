"""
Methods in this module are adapted from:
- https://github.com/yenchenlin/mira/blob/master/ravens/utils/demo_utils.py
- https://github.com/NVlabs/instant-ngp/blob/master/scripts/record3d2nerf.py

Thanks to Yen-Chen Lin and the Instant-NGP team!
"""
import copy
import json
import os.path as osp
from typing import List

import numpy as np
from tqdm import tqdm

from rndf_robot.robot.multicam import MultiCams
from rndf_robot.utils import util

_robot_to_graphics_rotation = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]])
graphics_transformation = np.eye(4)
graphics_transformation[:3, :3] = _robot_to_graphics_rotation


def convert_pose(c2w: np.ndarray) -> np.ndarray:
    """Convert a pose from pybullet convention to OpenGL camera convention (I think)."""
    flip_yz = np.eye(4)
    flip_yz[1, 1] = -1
    flip_yz[2, 2] = -1
    c2w = np.matmul(c2w, flip_yz)
    return c2w


def find_transforms_center_and_scale(raw_transforms: dict) -> (np.ndarray, float):
    """Automatic rescale & offset the poses."""
    print("Computing center of attention...")
    frames = raw_transforms["frames"]
    for frame in frames:
        frame["transform_matrix"] = np.array(frame["transform_matrix"])

    rays_o = []
    rays_d = []
    for f in tqdm(frames):
        mf = f["transform_matrix"][0:3, :]
        rays_o.append(mf[:3, 3:])
        rays_d.append(mf[:3, 2:3])
    rays_o = np.asarray(rays_o)
    rays_d = np.asarray(rays_d)

    # Find the point that minimizes its distances to all rays.
    def min_line_dist(rays_o, rays_d):
        A_i = np.eye(3) - rays_d * np.transpose(rays_d, [0, 2, 1])
        b_i = -A_i @ rays_o
        pt_mindist = np.squeeze(
            -np.linalg.inv((np.transpose(A_i, [0, 2, 1]) @ A_i).mean(0)) @ (b_i).mean(0)
        )
        return pt_mindist

    translation = min_line_dist(rays_o, rays_d)
    normalized_transforms = copy.deepcopy(raw_transforms)
    for f in normalized_transforms["frames"]:
        f["transform_matrix"][0:3, 3] -= translation

    # Find the scale.
    avglen = 0.0
    for f in normalized_transforms["frames"]:
        avglen += np.linalg.norm(f["transform_matrix"][0:3, 3])
    nframes = len(normalized_transforms["frames"])
    avglen /= nframes
    print("avg camera distance from origin", avglen)
    # Will Shen comment: don't want to scale it too much, avglen ensures unit cube covers the scene
    # scale = 4.0 / avglen  # scale to "nerf sized"
    scale = avglen

    return translation, scale


def normalize_transforms(
    transforms: dict, translation: np.ndarray, scale: float
) -> dict:
    """
    Normalize the transforms by subtracting the center of attention and scaling the scene to fit in a unit cube.
    """
    normalized_transforms = copy.deepcopy(transforms)
    for f in normalized_transforms["frames"]:
        f["transform_matrix"] = np.asarray(f["transform_matrix"])
        f["transform_matrix"][0:3, 3] -= translation
        f["transform_matrix"][0:3, 3] *= scale
        f["transform_matrix"] = f["transform_matrix"].tolist()
    return normalized_transforms


def unnormalize_transforms(
    transforms: dict, translation: np.ndarray, scale: float
) -> dict:
    """
    Unnormalize the transforms.
    """
    unnormalized_transforms = copy.deepcopy(transforms)
    for f in unnormalized_transforms["frames"]:
        f["transform_matrix"] = np.asarray(f["transform_matrix"])
        f["transform_matrix"][0:3, 3] /= scale
        f["transform_matrix"][0:3, 3] += translation
        f["transform_matrix"] = f["transform_matrix"].tolist()
    return unnormalized_transforms


def write_instant_ngp_dataset(
    cams: MultiCams,
    rgbs: List[np.ndarray],
    depths: List[np.ndarray],
    nerf_dir: str,
    aabb_scale: int = 4,
    scale: float = 1.0,
) -> None:
    # Write RGB and depth images to disk
    metadata = []
    util.safe_makedirs(osp.join(nerf_dir, "rgbs"))
    util.safe_makedirs(osp.join(nerf_dir, "depths"))

    for i, (cam, rgb, depth) in enumerate(zip(cams.cams, rgbs, depths)):
        img_fname = f"{i:03d}.png"
        util.np2img(
            rgb.astype(np.uint8),
            osp.join(
                nerf_dir,
                "rgbs",
                img_fname,
            ),
        )
        # Depth is float32, convert to uint16 and use mm as unit (i.e., depth scale = 1000)
        depth_uint16 = (depth * 1000).astype(np.uint16)
        util.np2img(
            depth_uint16,
            osp.join(
                nerf_dir,
                "depths",
                img_fname,
            ),
        )

        c2w = cam.get_cam_ext()
        # Convert to graphics convention - some funny stuff going on
        c2w = graphics_transformation @ c2w

        # Convert to Instant-NGP convention
        c2w = convert_pose(c2w)

        # Add this camera info to metadata
        metadata.append(
            {
                "file_path": f"./rgbs/{img_fname}",
                "transform_matrix": c2w.tolist(),
            }
        )

    # Form transforms dict
    intrinsic_matrix = cams.intrinsic_matrix
    assert intrinsic_matrix.shape == (3, 3)

    transforms = {
        "fl_x": intrinsic_matrix[0, 0],
        "fl_y": intrinsic_matrix[1, 1],
        "cx": intrinsic_matrix[0, 2],
        "cy": intrinsic_matrix[1, 2],
        "w": cams.width,
        "h": cams.height,
        "aabb_scale": aabb_scale,
        "scale": scale,
    }
    transforms["camera_angle_x"] = 2 * np.arctan(
        transforms["w"] / (2 * transforms["fl_x"])
    )
    transforms["camera_angle_y"] = 2 * np.arctan(
        transforms["h"] / (2 * transforms["fl_y"])
    )
    transforms["frames"] = metadata

    # Write transforms_unnormalized.json
    with open(osp.join(nerf_dir, "transforms_unnormalized.json"), "w") as fp:
        json.dump(transforms, fp, indent=2)

    # Normalize the pose
    translation, scale = find_transforms_center_and_scale(transforms)
    normalized_transforms = normalize_transforms(transforms, translation, scale)

    # Write transforms.json with normalized poses
    with open(osp.join(nerf_dir, "transforms.json"), "w") as fp:
        json.dump(normalized_transforms, fp, indent=2)

    # Write normalization parameters
    normalization_params = {
        "translation": translation.tolist(),
        "scale": scale,
    }
    with open(osp.join(nerf_dir, "normalization_params.json"), "w") as fp:
        json.dump(normalization_params, fp, indent=2)

    print(f"Wrote Instant-NGP dataset to {nerf_dir}")
