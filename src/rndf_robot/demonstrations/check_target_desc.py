"""
This script just loads a target descriptor file and checks some sanity things
"""
import json
import os
import os.path as osp

import numpy as np
import open3d as o3d

from rndf_robot.utils import path_util, util

expected_keys = ["num_query_points", "parent_out_data", "child_out_data", "demo_ids"]


def load_demos(rel_demo_exp: str = "release_demos/mug_on_rack_relation"):
    demo_path = osp.join(path_util.get_rndf_data(), "relation_demos", rel_demo_exp)
    demo_files = [fn for fn in sorted(os.listdir(demo_path)) if fn.endswith(".npz")]
    demos = []
    for f in demo_files:
        demo = np.load(demo_path + "/" + f, allow_pickle=True)
        demos.append(demo)
    return demos


def main(target_desc_fname: str):
    target_descriptors_data = np.load(target_desc_fname, allow_pickle=True)
    print(f"Loaded {target_desc_fname}")

    demos = load_demos()
    print(f"Loaded {len(demos)} demos")

    # Print all available keys
    print("Keys:", json.dumps(list(target_descriptors_data.keys()), indent=2))

    # Check keys are present
    for key in expected_keys:
        assert (
            key in target_descriptors_data
        ), f"Key {key} not found in target descriptor file"

    parent_query_points = target_descriptors_data["parent_query_points"]

    # Check only parent_out_data is present
    parent_out_data = target_descriptors_data["parent_out_data"].item()
    child_out_data = target_descriptors_data["child_out_data"].item()
    assert parent_out_data, "parent_out_data is empty"
    assert not child_out_data, "child_out_data is not empty"

    for idx, (demo, demo_id) in enumerate(
        zip(demos, target_descriptors_data["demo_ids"])
    ):
        print(f"Demo {idx}: {demo_id}")
        assert idx == int(demo_id[-1]), f"Demo id {demo_id} does not match index {idx}"

        start_pcd = demo["multi_obj_start_pcd"].item()
        end_pcd = demo["multi_obj_final_pcd"].item()

        demo_parent_out_data = parent_out_data[idx]
        parent_out_tf_best = demo_parent_out_data["parent_out_tf_best"]
        parent_out_qp = demo_parent_out_data["parent_out_qp"]

        # Transform query points by best transform and check they match
        parent_out_qp_again = util.transform_pcd(
            parent_query_points, parent_out_tf_best
        )
        assert np.allclose(
            parent_out_qp, parent_out_qp_again
        ), "Transformed query points do not match"

        # Visualize the start and end point clouds
        # parent = red, child = blue, query points = green, cyan = keypoint
        for label, pcd_np, qp in [("end", end_pcd, parent_out_qp)]:
            parent = o3d.geometry.PointCloud()
            parent.points = o3d.utility.Vector3dVector(pcd_np["parent"])
            parent.paint_uniform_color([1, 0, 0])

            child = o3d.geometry.PointCloud()
            child.points = o3d.utility.Vector3dVector(pcd_np["child"])
            child.paint_uniform_color([0, 0, 1])

            query_points = o3d.geometry.PointCloud()
            query_points.points = o3d.utility.Vector3dVector(qp)
            query_points.paint_uniform_color([0, 1, 0])

            # Show a sphere at the origin transformed by the best transform
            keypoint = o3d.geometry.TriangleMesh.create_sphere(radius=0.01)
            keypoint.paint_uniform_color([0, 1, 1])
            keypoint.transform(parent_out_tf_best)

            o3d.visualization.draw_geometries(
                [parent, child, query_points, keypoint],
                window_name=f"{demo_id}_{label}",
            )

    print("Done!")


if __name__ == "__main__":
    main(
        "/home/william/workspace/vqn/relational_ndf/src/ndf_robot/data/relation_demos/release_demos/mug_on_rack_relation/parent_model--rndf_weights--ndf_rack_child--rndf_weights--ndf_mug/target_descriptors.npz"
    )
