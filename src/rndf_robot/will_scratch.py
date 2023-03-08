from collections import defaultdict
from itertools import product

import numpy as np
import open3d as o3d
from typing import Optional, Union, Tuple

gripper_color_schemes = {
    "default": {
        "gripper": (1.0, 0.0, 1.0),  # magenta
        "fingers": [(0.0, 0.2, 1.0), (1.0, 0.5, 0.0)],  # medium-dark blue and orange
        "sphere": (0.0, 1.0, 0.0),  # lime green
    },
    "inverted": {
        "gripper": (0.0, 0.2, 1.0),
        "fingers": (1.0, 0.0, 1.0),
        "sphere": (0.0, 0.2, 1.0),
    },
}

assert all(
    key in color_scheme for key in {"gripper", "fingers", "sphere"} for color_scheme in gripper_color_schemes.values()
), "All color schemes must specify gripper, fingers, sphere"


def get_gripper_pcd(
    translation: Optional[np.ndarray] = None,
    rotation: Optional[np.ndarray] = None,
    use_graphics_convention: bool = False,
    color_scheme: Union[str, Tuple[float, float, float]] = "default",
    gripper_width: float = 0.08,
    finger_height: float = 0.02,
    gripper_body_height: float = 0.065,
    gripper_height: float = 0.15,
    step_size: float = 0.002,
) -> o3d.geometry.PointCloud:
    """
    Get a point cloud of a simple gripper, in GraspNet visualization style.
    This should be a mesh, but we're just hacking things in the meantime.

    The bottom of the fingers of the point cloud are centered at (0, 0, 0)
    before any translation or rotation is applied.

    The point cloud is represented in the robot coordinate convention, unless
    use_graphics_convention is True, in which case it is represented in the
    graphics convention with y-up.

    Parameters
    ----------
    translation: np.ndarray, optional
        Translation to apply to the point cloud. Must be of length 3.
        Defaults to None.
    rotation: np.ndarray, optional
        Rotation to apply to the point cloud. Must be a 3x3 rotation matrix.
        Defaults to None.
    use_graphics_convention: bool
        Whether to use the graphics convention (y-up). Note translation and
        rotation is applied after the convention is switched.
    color_scheme: str or Tuple[float, float, float]
        Color scheme to use for the gripper. Must either be a key in gripper_color_schemes,
        or a tuple of RGB values.
    gripper_width: float
        Width of the gripper. The Franka Emika Panda gripper width is 0.08m.
    finger_height: float
        Height of the fingers.
    gripper_body_height: float
        Height of the gripper body.
    gripper_height: float
        Height of the overall gripper including the body.
    step_size: float
        Controls the resolution of the point cloud.

    Returns
    -------
    o3d.geometry.PointCloud
        Point cloud of the gripper.
    """
    assert translation is None or len(translation) == 3, "Translation must be of length 3"
    assert rotation is None or rotation.shape == (3, 3), "Rotation must be 3x3"
    assert (color_scheme in gripper_color_schemes) or (
        isinstance(color_scheme, tuple) and len(color_scheme) == 3
    ), f"Unsupported color scheme {color_scheme}"
    assert gripper_width > 0.0, "Gripper width must be positive"
    assert finger_height > 0.0, "Finger height must be positive"
    assert gripper_height > gripper_body_height, "Gripper height must be greater than gripper body height"
    assert gripper_body_height > 0.0, "Gripper body height must be positive"
    assert step_size > 0.0, "Step size must be positive"

    gripper_points = []

    gripper_finger_idxs = defaultdict(list)
    finger_top = finger_height / 2
    finger_bottom = -finger_top

    # For making thicker points
    # displace = (-0.001, 0.00, 0.001)
    displace = (0.0,)
    for x_displace, y_displace in product(displace, displace):
        # Fingers extending up into top of gripper body
        for finger_idx, y in enumerate((gripper_width / 2, -gripper_width / 2)):
            # We need to start at bottom of fingers
            for z in np.arange(finger_bottom, gripper_body_height + step_size, step_size):
                gripper_points.append((x_displace, y + y_displace, z))
                if z < finger_top:
                    gripper_finger_idxs[finger_idx].append(len(gripper_points) - 1)

        # Horizontal part of gripper body between the fingers
        for y in np.arange(-gripper_width / 2, gripper_width / 2 + step_size, step_size):
            for z_displace in displace:
                gripper_points.append((x_displace, y + y_displace, gripper_body_height + z_displace))

        # Vertical part extending up from gripper body to gripper height
        for z in np.arange(gripper_body_height, gripper_height + step_size, step_size):
            gripper_points.append((x_displace, y_displace, z))

    # Create open3d point cloud - color is magenta
    gripper_points = np.array(gripper_points)
    # if use_graphics_convention:
    #     gripper_points = convert_to_graphics_convention(gripper_points)

    if isinstance(color_scheme, str):
        gripper_rgb, finger_rgb, sphere_rgb = gripper_color_schemes[color_scheme].values()
    else:
        gripper_rgb, finger_rgb, sphere_rgb = color_scheme, color_scheme, color_scheme
    gripper_colors = np.tile(gripper_rgb, (len(gripper_points), 1))
    assert gripper_points.shape == gripper_colors.shape, "Points and colors must have same shape"

    if not isinstance(finger_rgb, np.ndarray):
        finger_rgb = np.array(finger_rgb)

    # Set finger points to different color
    for finger_idx, finger_idxs in gripper_finger_idxs.items():
        gripper_colors[finger_idxs] = finger_rgb[finger_idx]

    gripper_pcd = o3d.geometry.PointCloud()
    gripper_pcd.points = o3d.utility.Vector3dVector(gripper_points)
    gripper_pcd.colors = o3d.utility.Vector3dVector(gripper_colors)

    # Sphere around center of fingers, with green points
    sphere = o3d.geometry.TriangleMesh.create_sphere(radius=finger_top / 2)
    sphere_pcd = sphere.sample_points_poisson_disk(number_of_points=50)
    sphere_pcd.paint_uniform_color(sphere_rgb)

    # Concatenate gripper and sphere point clouds
    gripper_pcd += sphere_pcd

    # Apply translation and rotation
    if translation is not None:
        gripper_pcd.translate(translation)
    if rotation is not None:
        if translation is None:
            translation = np.zeros(3)
        gripper_pcd.rotate(rotation, center=translation)

    return gripper_pcd

if __name__ == "__main__":
    # mesh_fname = "/Users/william/Downloads/panda_gripper_visual.obj"
    # mesh_fname = "panda_gripper_zup.obj"
    mesh_fname = "panda_gripper_visual_zup.obj"
    mesh = o3d.io.read_triangle_mesh(mesh_fname)

    # flip in z-axis
    # mesh = mesh.rotate(
    #     o3d.geometry.get_rotation_matrix_from_axis_angle(np.array([0, 1, 0], dtype=np.float64) * np.pi),
    #     center=[0,0,0]
    # )
    # finger_height = 0.053767
    #
    # mesh = mesh.translate(
    #     [0, 0, 0.0584 + finger_height - 0.01]
    # )

    # write mesh to file
    o3d.io.write_triangle_mesh("panda_gripper_visual_zup.obj", mesh)

    # paint pink
    # mesh.paint_uniform_color([1, 0.5, 0.5])

    # gripper_pcd = mesh.sample_points_uniformly(number_of_points=1000)
    gripper_pcd = mesh

    # gripper = get_gripper_pcd()


    o3d.visualization.draw_geometries(
        [gripper_pcd, o3d.geometry.TriangleMesh.create_coordinate_frame(size=0.1)]
    )
    print('yup')
