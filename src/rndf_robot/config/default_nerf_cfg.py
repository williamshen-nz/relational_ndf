import numpy as np
from yacs.config import CfgNode as CN

from rndf_robot.config.default_cam_cfg import get_default_cam_cfg

_C = CN()

# general configs
_C.N_CAMERAS = 36

# camera related parameters
_C.CAMERA = get_default_cam_cfg()

# The Evo-NeRF paper has some info about potentially good trajectories
# https://openreview.net/pdf?id=Bxr45keYrf#page=12
_C.CAMERA.WIDTH = 1280
_C.CAMERA.HEIGHT = 720

# 2 loops at different pitches and radii
_C.CAMERA.YAW_ANGLES = np.linspace(0, 720, _C.N_CAMERAS).tolist()
_C.CAMERA.PITCH_ANGLES = np.linspace(-15.0, -50.0, _C.N_CAMERAS).tolist()
_C.CAMERA.DISTANCE = 0.8
# _C.CAMERA.DISTANCES = np.linspace(0.9, 0.8, _C.N_CAMERAS).tolist()


def get_nerf_cfg():
    return _C.clone()
