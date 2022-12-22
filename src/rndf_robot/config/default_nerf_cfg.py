import numpy as np
from yacs.config import CfgNode as CN

from rndf_robot.config.default_cam_cfg import get_default_cam_cfg

_C = CN()

# general configs
_C.N_CAMERAS = 30

# camera related parameters
_C.CAMERA = get_default_cam_cfg()
_C.CAMERA.YAW_ANGLES = np.linspace(0, 360, _C.N_CAMERAS, endpoint=False).tolist()
_C.CAMERA.PITCH_ANGLES = np.linspace(-10.0, -25.0, _C.N_CAMERAS).tolist()


def get_nerf_cfg():
    return _C.clone()
