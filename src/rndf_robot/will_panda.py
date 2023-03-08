import pybullet as p
import pybullet_data

from rndf_robot.my_utils import (
    draw_aabb,
    get_buffered_aabb,
    get_joints,
    get_movable_joints,
    set_joint_positions,
    wait_for_user, get_aabbs, remove_handles, connect,
)

if __name__ == "__main__":
    # Load panda
    connect()

    # add data path
    p.setAdditionalSearchPath(pybullet_data.getDataPath())

    # load panda
    panda = p.loadURDF("franka_panda/panda.urdf", useFixedBase=True)

    joints = get_movable_joints(panda)
    print(joints)

    set_joint_positions(panda, joints, [0, 0, 0, -1.57, 0, 1.57, 0.79, 0.04, 0.04])

    aabbs = get_aabbs(panda)
    handles = []
    for aabb in aabbs:
        print(aabb)
        handles.extend(draw_aabb(aabb))
    wait_for_user()

    remove_handles(handles)

    # move to different joints
    set_joint_positions(panda, joints, [0.5, 0.2, 0.1, -1.2, 0, 1.27, 0.59, 0.02, 0.02])
    aabbs = get_aabbs(panda)
    for aabb in aabbs:
        print(aabb)
        draw_aabb(aabb)
    wait_for_user()

