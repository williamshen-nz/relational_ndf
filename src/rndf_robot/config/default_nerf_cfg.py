import numpy as np
from yacs.config import CfgNode as CN

from rndf_robot.config.default_cam_cfg import get_default_cam_cfg

_C = CN()

# general configs
_C.N_CAMERAS = 30

# camera related parameters
_C.CAMERA = get_default_cam_cfg()

# 2 loops at different pitches and radii
_C.CAMERA.YAW_ANGLES = np.linspace(0, 720, _C.N_CAMERAS).tolist()
_C.CAMERA.PITCH_ANGLES = np.linspace(-25.0, -50.0, _C.N_CAMERAS).tolist()
_C.CAMERA.DISTANCES = np.linspace(0.9, 0.8, _C.N_CAMERAS).tolist()


def get_nerf_cfg():
    return _C.clone()
