import colorsys
import os, os.path as osp
import sys
import random
from itertools import cycle
from loguru import logger

import numpy as np
import time
import signal
import torch
import argparse
import shutil
import threading
import copy
import json
import trimesh

import pybullet as p
import meshcat

from airobot import log_info, log_warn, log_debug, log_critical, set_log_level
from airobot.utils import common
from airobot.utils.pb_util import create_pybullet_client, TextureModder
from airobot.sensor.camera.rgbdcam_pybullet import RGBDCameraPybullet
from matplotlib import pyplot as plt

import rndf_robot.model.vnn_occupancy_net_pointnet_dgcnn as vnn_occupancy_network
from rndf_robot.config.default_nerf_cfg import get_nerf_cfg
from rndf_robot.nerf.copy_datasets import copy_nerf_datasets
from rndf_robot.nerf.dataset import write_instant_ngp_dataset
from rndf_robot.utils import util, path_util

from rndf_robot.opt.optimizer import OccNetOptimizer
from rndf_robot.robot.multicam import MultiCams
from rndf_robot.config.default_eval_cfg import get_eval_cfg_defaults
from rndf_robot.share.globals import bad_shapenet_mug_ids_list, bad_shapenet_bowls_ids_list, bad_shapenet_bottles_ids_list
from rndf_robot.utils.path_util import get_rndf_assets
from rndf_robot.utils.pb2mc.pybullet_meshcat import PyBulletMeshcat
from rndf_robot.utils.eval_gen_utils import constraint_obj_world, safeCollisionFilterPair, safeRemoveConstraint

from rndf_robot.eval.relation_tools.multi_ndf import infer_relation_intersection, create_target_descriptors


NOISE_VALUE_LIST = [0.01, 0.02, 0.03, 0.04, 0.06, 0.08, 0.16, 0.24, 0.32, 0.4]


def random_color() -> (float, float, float):
    """
    Generate a random color. Use HSV so that the colors are evenly distributed.
    Returns a tuple of 3 floats in the range [0, 1]
    """
    h, s, v = [random.random() for i in range(3)]
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return r, g, b


def pb2mc_update(recorder, mc_vis, stop_event, run_event):
    iters = 0
    # while True:
    while not stop_event.is_set():
        run_event.wait()
        iters += 1
        recorder.add_keyframe()
        recorder.update_meshcat_current_state(mc_vis)
        time.sleep(1/230.0)


def create_target_desc_subdir(demo_path, parent_model_path, child_model_path):
    parent_model_name_full = parent_model_path.split('ndf_vnn/')[-1]
    child_model_name_full = child_model_path.split('ndf_vnn/')[-1]

    parent_model_name_specific = parent_model_name_full.split('.pth')[0].replace('/', '--')
    child_model_name_specific = child_model_name_full.split('.pth')[0].replace('/', '--')
    
    subdir_name = f'parent_model--{parent_model_name_specific}_child--{child_model_name_specific}'
    dirname = osp.join(demo_path, subdir_name)
    util.safe_makedirs(dirname)
    return dirname


def main(args):

    #####################################################################################
    # set up all generic experiment info
    assert args.relation_method in ['intersection', 'ebm'], 'Invalid argument for --relation_method'

    if args.debug:
        set_log_level('debug')
    else:
        set_log_level('info')
        # By default, loguru log level is DEBUG so let's set it to INFO
        logger.remove()
        logger.add(sys.stderr, level="INFO")

    signal.signal(signal.SIGINT, util.signal_handler)

    demo_path  = osp.join(path_util.get_rndf_data(), 'relation_demos', args.rel_demo_exp)
    demo_files = [fn for fn in sorted(os.listdir(demo_path)) if fn.endswith('.npz')]
    demos = []
    for f in demo_files:
        demo = np.load(demo_path+'/'+f, allow_pickle=True)
        demos.append(demo)

    parent_model_name_full = args.parent_model_path.split('ndf_vnn/')[-1]
    child_model_name_full = args.child_model_path.split('ndf_vnn/')[-1]

    parent_model_save_path = parent_model_name_full.split('/')[0]
    child_model_save_path = child_model_name_full.split('/')[0]

    parent_model_name_specific = parent_model_name_full.split('.pth')[0].replace('/', '--')
    child_model_name_specific = child_model_name_full.split('.pth')[0].replace('/', '--')
    if args.rel_model_path is None:
        ebm_model_name_specific = 'ebm_model_None'
    else:
        ebm_model_name_specific = args.rel_model_path.split('.pth')[0].replace('/', '--')
    # print(f'Parent model name specific: {parent_model_name_specific}, Child model name specific: {child_model_name_specific}')
    print(f'Parent model name specific: {parent_model_name_specific}, Child model name specific: {child_model_name_specific}, EBM name specific: {ebm_model_name_specific}')
    
    expstr = f'exp--{args.exp}_demo-exp--{args.rel_demo_exp}'
    # modelstr = f'parent_model--{args.parent_model_path}_child_model--{args.child_model_path}'
    modelstr = f'parent_model--{parent_model_save_path}_child_model--{child_model_save_path}'
    seedstr = 'seed--' + str(args.seed)
    experiment_name = '_'.join([expstr, modelstr, seedstr])
    # experiment_name_spec_model = f'parent--{parent_model_name_specific}_child--{child_model_name_specific}'
    experiment_name_spec_model = f'parent--{parent_model_name_specific}_child--{child_model_name_specific}_ebm--{ebm_model_name_specific}'

    eval_save_dir_root = osp.join(path_util.get_rndf_eval_data(), args.eval_data_dir, experiment_name)
    eval_save_dir = osp.join(eval_save_dir_root, experiment_name_spec_model)
    util.safe_makedirs(eval_save_dir_root)
    util.safe_makedirs(eval_save_dir)

    zmq_url = 'tcp://127.0.0.1:6000'
    log_warn(f'Starting meshcat at zmq_url: {zmq_url}')
    mc_vis = meshcat.Visualizer(zmq_url=zmq_url)
    mc_vis['scene'].delete()

    pb_client = create_pybullet_client(
        gui=args.pybullet_viz,
        opengl_render=True,
        realtime=True,
        server=args.pybullet_server,
        # Note: you can just modify this method in airobot in place for now
        # options=(
        #     " ".join(f"--background_color_{channel}=1" for channel in ("red", "green", "blue"))
        #     if args.pybullet_background_color == "white" else ""
        # )
    )
    # Disable preview to make things faster
    if not args.pybullet_debug_viz:
        enable = False
        p.configureDebugVisualizer(p.COV_ENABLE_GUI, enable, physicsClientId=pb_client.get_client_id())
        p.configureDebugVisualizer(p.COV_ENABLE_RGB_BUFFER_PREVIEW, enable, physicsClientId=pb_client.get_client_id())
        p.configureDebugVisualizer(p.COV_ENABLE_DEPTH_BUFFER_PREVIEW, enable, physicsClientId=pb_client.get_client_id())
        p.configureDebugVisualizer(p.COV_ENABLE_SEGMENTATION_MARK_PREVIEW, enable, physicsClientId=pb_client.get_client_id())

    recorder = PyBulletMeshcat(pb_client=pb_client)
    recorder.clear()

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    np.random.seed(args.seed)

    # general experiment + environment setup/scene generation configs
    cfg = get_eval_cfg_defaults()
    config_fname = osp.join(path_util.get_rndf_config(), 'eval_cfgs', args.config)
    if osp.exists(config_fname):
        cfg.merge_from_file(config_fname)
    else:
        log_info(f'Config file {config_fname} does not exist, using defaults')
    # cfg.freeze()

    eval_teleport_imgs_dir = osp.join(eval_save_dir, 'teleport_imgs')
    util.safe_makedirs(eval_teleport_imgs_dir)

    save_dir = osp.join(path_util.get_rndf_eval_data(), 'multi_class', args.exp)
    util.safe_makedirs(save_dir)
    
    #####################################################################################
    # load in the parent/child model and the cameras

    parent_model_path = osp.join(path_util.get_rndf_model_weights(), args.parent_model_path)
    child_model_path = osp.join(path_util.get_rndf_model_weights(), args.child_model_path)

    if args.parent_model_path_ebm is not None:
        parent_model_path_ebm = osp.join(path_util.get_rndf_model_weights(), args.parent_model_path_ebm)
    else:
        parent_model_path_ebm = parent_model_path
    if args.child_model_path_ebm is not None:
        child_model_path_ebm = osp.join(path_util.get_rndf_model_weights(), args.child_model_path_ebm)
    else:
        child_model_path_ebm = child_model_path

    parent_model = vnn_occupancy_network.VNNOccNet(latent_dim=256, model_type='pointnet', return_features=True, sigmoid=True)
    child_model = vnn_occupancy_network.VNNOccNet(latent_dim=256, model_type='pointnet', return_features=True, sigmoid=True)

    def load_ndf_weights():
        map_device = torch.device('cpu')
        if torch.cuda.is_available():
            parent_model.cuda()
            child_model.cuda()
            map_device = torch.device('cuda')

        parent_model.load_state_dict(torch.load(parent_model_path, map_location=map_device))
        child_model.load_state_dict(torch.load(child_model_path, map_location=map_device))

    cams = MultiCams(cfg.CAMERA, pb_client, n_cams=cfg.N_CAMERAS)
    cam_info = {}
    cam_info['pose_world'] = []
    for cam in cams.cams:
        cam_info['pose_world'].append(util.pose_from_matrix(cam.cam_ext_mat))

    # NeRF Cameras
    nerf_cfg = get_nerf_cfg()
    nerf_cams = MultiCams(nerf_cfg.CAMERA, pb_client, n_cams=nerf_cfg.N_CAMERAS)
    nerf_cam_info = {"pose_world": []}
    for cam in nerf_cams.cams:
        nerf_cam_info['pose_world'].append(util.pose_from_matrix(cam.cam_ext_mat))

    #####################################################################################
    # load all the multi class mesh info

    mesh_data_dirs = {
        'mug': 'mug_centered_obj_normalized', 
        'bottle': 'bottle_centered_obj_normalized', 
        'bowl': 'bowl_centered_obj_normalized',
        'syn_rack_easy': 'syn_racks_easy_obj',
        'syn_container': 'box_containers_unnormalized'
    }
    mesh_data_dirs = {k: osp.join(path_util.get_rndf_obj_descriptions(), v) for k, v in mesh_data_dirs.items()}

    bad_ids = {
        'syn_rack_easy': [],
        'bowl': bad_shapenet_bowls_ids_list,
        'mug': bad_shapenet_mug_ids_list,
        'bottle': bad_shapenet_bottles_ids_list,
        'syn_container': []
    }

    upright_orientation_dict = {
        'mug': common.euler2quat([np.pi/2, 0, 0]).tolist(), 
        'bottle': common.euler2quat([np.pi/2, 0, 0]).tolist(), 
        'bowl': common.euler2quat([np.pi/2, 0, 0]).tolist(),
        'syn_rack_easy': common.euler2quat([0, 0, 0]).tolist(),
        'syn_container': common.euler2quat([0, 0, 0]).tolist(),
    }

    mesh_names = {}
    for k, v in mesh_data_dirs.items():
        # get train samples
        objects_raw = os.listdir(v)
        objects_filtered = [fn for fn in objects_raw if (fn.split('/')[-1] not in bad_ids[k] and '_dec' not in fn)]
        # objects_filtered = objects_raw
        total_filtered = len(objects_filtered)
        train_n = int(total_filtered * 0.9); test_n = total_filtered - train_n

        train_objects = sorted(objects_filtered)[:train_n]
        test_objects = sorted(objects_filtered)[train_n:]

        log_info('\n\n\nTest objects: ')
        log_info(test_objects)
        # log_info('\n\n\n')

        mesh_names[k] = objects_filtered

    obj_classes = list(mesh_names.keys())

    scale_high, scale_low = cfg.MESH_SCALE_HIGH, cfg.MESH_SCALE_LOW
    scale_default = cfg.MESH_SCALE_DEFAULT

    # cfg.OBJ_SAMPLE_Y_HIGH_LOW = [0.3, -0.3]
    cfg.OBJ_SAMPLE_Y_HIGH_LOW = [-0.35, 0.175]
    x_low, x_high = cfg.OBJ_SAMPLE_X_HIGH_LOW
    y_low, y_high = cfg.OBJ_SAMPLE_Y_HIGH_LOW
    table_z = cfg.TABLE_Z

    #####################################################################################
    # load all the parent/child info

    parent_class = args.parent_class
    child_class = args.child_class
    is_parent_shapenet_obj = args.is_parent_shapenet_obj
    is_child_shapenet_obj = args.is_child_shapenet_obj

    pcl = ['parent', 'child']
    pc_master_dict = dict(parent={}, child={})
    pc_master_dict['parent']['class'] = parent_class
    pc_master_dict['child']['class'] = child_class
    
    valid_load_pose_types = ['any_pose', 'demo_pose', 'random_upright']
    assert args.parent_load_pose_type in valid_load_pose_types, f'Invalid string value for args.parent_load_pose_type! Must be in {", ".join(valid_load_pose_types)}'
    assert args.child_load_pose_type in valid_load_pose_types, f'Invalid string value for args.child_load_pose_type! Must be in {", ".join(valid_load_pose_types)}'

    pc_master_dict['parent']['load_pose_type'] = args.parent_load_pose_type
    pc_master_dict['child']['load_pose_type'] = args.child_load_pose_type

    # load in ids for objects that can be used for testing
    pc_master_dict['parent']['test_ids'] = np.loadtxt(osp.join(path_util.get_rndf_share(), '%s_test_object_split.txt' % parent_class), dtype=str).tolist()
    pc_master_dict['child']['test_ids'] = np.loadtxt(osp.join(path_util.get_rndf_share(), '%s_test_object_split.txt' % child_class), dtype=str).tolist()

    # process these to remove the file type
    pc_master_dict['parent']['test_ids'] = [val.split('.')[0] for val in pc_master_dict['parent']['test_ids']]
    pc_master_dict['child']['test_ids'] = [val.split('.')[0] for val in pc_master_dict['child']['test_ids']]

    log_info(f'Test ids (parent): {", ".join(pc_master_dict["parent"]["test_ids"])}')
    log_info(f'Test ids (child): {", ".join(pc_master_dict["child"]["test_ids"])}')

    for pc in pcl:
        object_class = pc_master_dict[pc]['class']
        if object_class == 'mug':
            avoid_ids = bad_shapenet_mug_ids_list + cfg.MUG.AVOID_SHAPENET_IDS
        elif object_class == 'bowl':
            avoid_ids = bad_shapenet_bowls_ids_list + cfg.BOWL.AVOID_SHAPENET_IDS
        elif object_class == 'bottle':
            avoid_ids = bad_shapenet_bottles_ids_list + cfg.BOTTLE.AVOID_SHAPENET_IDS
        else:
            avoid_ids = []

        pc_master_dict[pc]['avoid_ids'] = avoid_ids

    pc_master_dict['parent']['xhl'] = [x_high, x_low]
    # pc_master_dict['parent']['yhl'] = [y_high, 0.075]
    pc_master_dict['parent']['yhl'] = [0.2, 0.075]
    # pc_master_dict['parent']['yhl'] = [y_high, 0.05]

    pc_master_dict['child']['xhl'] = [x_high, x_low]
    # pc_master_dict['child']['yhl'] = [-0.2, y_low]
    pc_master_dict['child']['yhl'] = [-0.2, -0.3]
    # pc_master_dict['child']['yhl'] = [-0.075, y_low]
    # pc_master_dict['child']['yhl'] = [-0.05, y_low]

    # get the class specific ranges for scaling the objects
    for pc in pcl:
        if pc_master_dict[pc]['class'] == 'mug':
            pc_master_dict[pc]['scale_hl'] = [0.35, 0.25]
            pc_master_dict[pc]['scale_default'] = 0.3
        if pc_master_dict[pc]['class'] == 'bowl':
            pc_master_dict[pc]['scale_hl'] = [0.325, 0.15]
            pc_master_dict[pc]['scale_default'] = 0.3
        if pc_master_dict[pc]['class'] == 'bottle':
            pc_master_dict[pc]['scale_hl'] = [0.35, 0.2]
            pc_master_dict[pc]['scale_default'] = 0.3
        if pc_master_dict[pc]['class'] == 'syn_rack_easy':
            # pc_master_dict[pc]['scale_hl'] = [1.1, 0.9]
            # pc_master_dict[pc]['scale_default'] = 1.0
            pc_master_dict[pc]['scale_hl'] = [0.35, 0.25]
            pc_master_dict[pc]['scale_default'] = 0.3
        if pc_master_dict[pc]['class'] == 'syn_container':
            pc_master_dict[pc]['scale_hl'] = [1.1, 0.9]
            pc_master_dict[pc]['scale_default'] = 1.0

    # pc_master_dict['parent']['scale_hl'] = [0.45, 0.25]
    # pc_master_dict['parent']['scale_default'] = 0.3
    # pc_master_dict['child']['scale_hl'] = [1.1, 0.9]
    # pc_master_dict['child']['scale_default'] = 1.0

    # pc_master_dict['parent']['scale_hl'] = [1.1, 0.9]
    # pc_master_dict['parent']['scale_default'] = 1.0
    # pc_master_dict['child']['scale_hl'] = [0.45, 0.25]
    # pc_master_dict['child']['scale_default'] = 0.3

    pc_master_dict['parent']['model_path'] = parent_model_path
    pc_master_dict['child']['model_path'] = child_model_path

    pc_master_dict['parent']['model'] = parent_model
    pc_master_dict['child']['model'] = child_model
    load_ndf_weights()

    # put the data in our pc master
    pc_master_dict['parent']['demo_start_pcds'] = []
    pc_master_dict['parent']['demo_final_pcds'] = []
    pc_master_dict['child']['demo_start_pcds'] = []
    pc_master_dict['child']['demo_final_pcds'] = []
    for pc in pcl:
        for idx, demo in enumerate(demos):
            s_pcd = demo['multi_obj_start_pcd'].item()[pc]
            f_pcd = demo['multi_obj_final_pcd'].item()[pc]

            pc_master_dict[pc]['demo_start_pcds'].append(s_pcd)
            pc_master_dict[pc]['demo_final_pcds'].append(f_pcd)

    # load data from demos in case we want to test on the shapes we trained on
    for pc in pcl:
        pc_master_dict[pc]['demo_ids'] = [dat['multi_object_ids'].item()[pc] for dat in demos]
        pc_master_dict[pc]['demo_start_poses'] = [dat['multi_obj_start_obj_pose'].item()[pc] for dat in demos]

    #####################################################################################
    # prepare the target descriptors

    target_desc_subdir = create_target_desc_subdir(demo_path, parent_model_path, child_model_path)
    target_desc_fname = osp.join(demo_path, target_desc_subdir, args.target_desc_name)
    if args.relation_method == 'intersection':
        if not osp.exists(target_desc_fname) or args.new_descriptors:
            print(f'\n\n\nCreating target descriptors for this parent model + child model, and these demos\nSaving to {target_desc_fname}\n\n\n')
            n_demos = 'all' if args.n_demos < 1 else args.n_demos
            if args.add_noise:
                add_noise = True
                noise_value = NOISE_VALUE_LIST[args.noise_idx]
            else:
                add_noise = False
                noise_value = 0.0001

            if parent_class == 'syn_container' and child_class == 'bottle':
                use_keypoint_offset = True
                keypoint_offset_params = {'offset': 0.025, 'type': 'bottom'}
            else:
                use_keypoint_offset = False
                keypoint_offset_params = None
            create_target_descriptors(
                parent_model, child_model, pc_master_dict, target_desc_fname,
                cfg, query_scale=args.query_scale, scale_pcds=False,
                target_rounds=args.target_rounds, pc_reference=args.pc_reference,
                skip_alignment=args.skip_alignment, n_demos=n_demos, manual_target_idx=args.target_idx,
                add_noise=add_noise, interaction_pt_noise_std=noise_value,
                use_keypoint_offset=use_keypoint_offset, keypoint_offset_params=keypoint_offset_params,
                visualize=True, mc_vis=mc_vis)

    if osp.exists(target_desc_fname):
        log_info(f'Loading target descriptors from file:\n{target_desc_fname}')
        target_descriptors_data = np.load(target_desc_fname)
        parent_overall_target_desc = target_descriptors_data['parent_overall_target_desc']
        child_overall_target_desc = target_descriptors_data['child_overall_target_desc']
        parent_overall_target_desc = torch.from_numpy(parent_overall_target_desc).float()
        child_overall_target_desc = torch.from_numpy(child_overall_target_desc).float()
        if torch.cuda.is_available():
            parent_overall_target_desc = parent_overall_target_desc.cuda()
            child_overall_target_desc = child_overall_target_desc.cuda()
        parent_query_points = target_descriptors_data['parent_query_points']
        child_query_points = copy.deepcopy(parent_query_points)

        log_info(f'Making a copy of the target descriptors in eval folder')
        shutil.copy(target_desc_fname, eval_save_dir)

        parent_optimizer = OccNetOptimizer(
            parent_model,
            query_pts=parent_query_points,
            query_pts_real_shape=parent_query_points,
            opt_iterations=args.opt_iterations,
            cfg=cfg.OPTIMIZER)

        child_optimizer = OccNetOptimizer(
            child_model,
            query_pts=child_query_points,
            query_pts_real_shape=child_query_points,
            opt_iterations=args.opt_iterations,
            cfg=cfg.OPTIMIZER)

        parent_optimizer.setup_meshcat(mc_vis)
        child_optimizer.setup_meshcat(mc_vis)
    else:
        raise RuntimeError("Is this ever raised? Comment by willshen@")

    #########################################################################
    # Set up the relational energy model

    if args.relation_method == 'ebm' or args.refine_with_ebm:
        rel_model = EBM().cuda()
        rel_model_path = osp.join(path_util.get_rndf_model_weights(), 'relation_energy/cachedir', args.rel_model_path)
        assert osp.exists(rel_model_path), f'Path to relation energy model: {rel_model_path} does not exist!'
        rel_checkpoint = torch.load(rel_model_path, map_location=torch.device('cpu'))
        rel_model.load_state_dict(rel_checkpoint['model_state_dict'])

    #####################################################################################
    # prepare the simuation environment
    table_urdf_fname = osp.join(path_util.get_rndf_descriptions(), 'hanging/table/table.urdf')
    table_pos = [0.5, 0.0, 0.375]
    log_warn(f"Warning cfg.TABLE_POS is not being used and is hardcoded to {table_pos}")
    table_id = pb_client.load_urdf(table_urdf_fname, table_pos, cfg.TABLE_ORI, scaling=1.0)
    # recorder.register_object(table_id, table_urdf_fname)

    # Create texture modder for later use
    texture_modder = TextureModder(pb_client.get_client_id())
    # texture_modder.set_texture_path(osp.join(get_rndf_assets(), "dtd/images"))

    # Add plane
    if args.plane_texture == "plane":
        # We set the height of the plane to 0.7 so it's closer to the table
        _ = pb_client.load_urdf("plane.urdf", [0, 0, 0.7])
        # This doesn't work
        # recorder.register_object(plane_id, osp.join(pybullet_data.getDataPath(), "plane.urdf"))

    rec_stop_event = threading.Event()
    rec_run_event = threading.Event()
    rec_th = threading.Thread(target=pb2mc_update, args=(recorder, mc_vis, rec_stop_event, rec_run_event))# , mc_vis))
    rec_th.daemon = True
    rec_th.start()

    pause_mc_thread = lambda pause_bool : rec_run_event.clear() if pause_bool else rec_run_event.set()
    pause_mc_thread(False)

    table_base_id = 0
    rack_link_id = 0

    eval_imgs_dir = osp.join(eval_save_dir, 'eval_imgs')
    util.safe_makedirs(eval_imgs_dir)
    eval_cam = RGBDCameraPybullet(cams._camera_cfgs(), pb_client)
    eval_cam.setup_camera(
        focus_pt=[0.4, 0.0, table_z],
        dist=0.9,
        yaw=45,
        pitch=-25,
        roll=0)

    #####################################################################################
    # dump full experiment configs in eval folder

    full_cfg_dict = {}
    for k, v in args.__dict__.items():
        full_cfg_dict[k] = v
    for k, v in util.cn2dict(cfg).items():
        full_cfg_dict[k] = v
    full_cfg_fname = osp.join(eval_save_dir, 'full_exp_cfg.txt')
    json.dump(full_cfg_dict, open(full_cfg_fname, 'w', encoding='utf-8'), ensure_ascii=False, indent=4)

    #####################################################################################
    # start experiment: sample parent and child object on each iteration and infer the relation
    place_success_list = []

    demo_indices_cycle = cycle(range(len(demos)))

    for iteration in range(args.start_iteration, args.num_iterations):
        #####################################################################################
        # set up the trial

        # Cycle through the demos instead of just randomly sampling
        # demo_idx = np.random.randint(len(demos))
        demo_idx = next(demo_indices_cycle)
        # willshen@ comment, unused variable so commented out
        # demo = demos[demo_idx]
        if args.test_on_train:
            parent_id = pc_master_dict['parent']['demo_ids'][demo_idx]
            child_id = pc_master_dict['child']['demo_ids'][demo_idx]
            log_info(f"Using demo {demo_idx} for parent and child objects")
        else:
            parent_id = random.sample(pc_master_dict['parent']['test_ids'], 1)[0]
            child_id = random.sample(pc_master_dict['child']['test_ids'], 1)[0]

        if '_dec' in parent_id:
            parent_id = parent_id.replace('_dec', '')
        if '_dec' in child_id:
            child_id = child_id.replace('_dec', '')

        id_str = f'Parent ID: {parent_id}, Child ID: {child_id}'
        log_info(id_str)

        # make folder for saving this trial
        eval_iter_dir = osp.join(eval_save_dir, f'trial_{iteration}')
        util.safe_makedirs(eval_iter_dir)

        #####################################################################################
        # load parent/child objects into the scene -- mesh file, pose, and pybullet object id

        if is_parent_shapenet_obj:
            parent_obj_file = osp.join(mesh_data_dirs[parent_class], parent_id, 'models/model_normalized.obj')
            parent_obj_file_dec = parent_obj_file.split('.obj')[0] + '_dec.obj'
        else:
            parent_obj_file = osp.join(mesh_data_dirs[parent_class], parent_id + '.obj')
            parent_obj_file_dec = parent_obj_file.split('.obj')[0] + '_dec.obj'

        if is_child_shapenet_obj:
            child_obj_file = osp.join(mesh_data_dirs[child_class], child_id, 'models/model_normalized.obj')
            child_obj_file_dec = child_obj_file.split('.obj')[0] + '_dec.obj'
        else:
            child_obj_file = osp.join(mesh_data_dirs[child_class], child_id + '.obj')
            child_obj_file_dec = child_obj_file.split('.obj')[0] + '_dec.obj'

        new_parent_scale = None
        # check if bottle/container are the right sizes
        if parent_class == 'syn_container' and child_class == 'bottle':
            if not osp.exists(parent_obj_file_dec):
                p.vhacd(
                    parent_obj_file,
                    parent_obj_file_dec,
                    'log.txt',
                    concavity=0.0025,
                    alpha=0.04,
                    beta=0.05,
                    gamma=0.00125,
                    minVolumePerCH=0.0001,
                    resolution=1000000,
                    depth=20,
                    planeDownsampling=4,
                    convexhullDownsampling=4,
                    pca=0,
                    mode=0,
                    convexhullApproximation=1
                )
            if not osp.exists(child_obj_file_dec):
                p.vhacd(
                    child_obj_file,
                    child_obj_file_dec,
                    'log.txt',
                    concavity=0.0025,
                    alpha=0.04,
                    beta=0.05,
                    gamma=0.00125,
                    minVolumePerCH=0.0001,
                    resolution=1000000,
                    depth=20,
                    planeDownsampling=4,
                    convexhullDownsampling=4,
                    pca=0,
                    mode=0,
                    convexhullApproximation=1
                )

            container_mesh = trimesh.load(parent_obj_file_dec)
            bottle_mesh = trimesh.load(child_obj_file_dec)
            container_mesh.apply_scale(pc_master_dict['parent']['scale_default'])
            bottle_mesh.apply_scale(pc_master_dict['child']['scale_default'])

            # make upright
            container_upright_orientation = upright_orientation_dict['syn_container']
            bottle_upright_orientation = upright_orientation_dict['bottle']
            container_upright_mat = np.eye(4); container_upright_mat[:-1, :-1] = common.quat2rot(container_upright_orientation)
            bottle_upright_mat = np.eye(4); bottle_upright_mat[:-1, :-1] = common.quat2rot(bottle_upright_orientation)

            container_mesh.apply_transform(container_upright_mat)
            bottle_mesh.apply_transform(bottle_upright_mat)

            # get the 2D projection of the vertices
            container_2d = np.asarray(container_mesh.vertices)[:, :-1]
            bottle_2d = np.asarray(bottle_mesh.vertices)[:, :-1]
            container_flat = np.hstack([container_2d, np.zeros(container_2d.shape[0]).reshape(-1, 1)])
            bottle_flat = np.hstack([bottle_2d, np.zeros(bottle_2d.shape[0]).reshape(-1, 1)])

            container_box = trimesh.PointCloud(container_flat).bounding_box
            bottle_box = trimesh.PointCloud(bottle_flat).bounding_box

            with recorder.meshcat_scene_lock:
                util.meshcat_trimesh_show(mc_vis, 'scene/container_box', container_box.to_mesh().apply_translation([0.0, 0.2, 0.0]), color=(255, 0, 0))
                util.meshcat_trimesh_show(mc_vis, 'scene/bottle_box', bottle_box.to_mesh().apply_translation([0.0, -0.2, 0.0]), color=(0, 0, 255))

            container_extents = container_box.extents
            bottle_extents = bottle_box.extents

            if np.max(bottle_extents) > (0.75 * np.min(container_extents[:-1])):
                # scale up the container size so that the bottle is more likely to fit inside
                new_parent_scale = np.max(bottle_extents) * (np.random.random() * (2 - 1.5) + 1.5) / np.min(container_extents[:-1])

            ext_str = f'\nContainer extents: {", ".join([str(val) for val in container_extents])}, \nBottle extents: {", ".join([str(val) for val in bottle_extents])}\n'
            log_info(ext_str)

        for pc in pcl:
            # get the mesh files we will use
            pc_master_dict[pc]['mesh_file'] = parent_obj_file if pc == 'parent' else child_obj_file
            pc_master_dict[pc]['mesh_file_dec'] = parent_obj_file_dec if pc == 'parent' else child_obj_file_dec

            # get the object scales we will use
            scale_high, scale_low = pc_master_dict[pc]['scale_hl']
            if pc == 'parent':
                if new_parent_scale is None:
                    scale_default = pc_master_dict[pc]['scale_default']
                else:
                    log_warn(f'Setting new parent scale to: {new_parent_scale:.3f} to ensure parent is large enough for child')
                    scale_default = new_parent_scale
            else:
                scale_default = pc_master_dict[pc]['scale_default']

            if args.rand_mesh_scale:
                mesh_scale = [np.random.random() * (scale_high - scale_low) + scale_low] * 3
            else:
                mesh_scale=[scale_default] * 3

            pc_master_dict[pc]['mesh_scale'] = mesh_scale

            object_class = pc_master_dict[pc]['class']
            upright_orientation = upright_orientation_dict[object_class]

            # sample a pose to use for each object, depending on distribution of poses for this run
            load_pose_type = pc_master_dict[pc]['load_pose_type']
            x_high, x_low = pc_master_dict[pc]['xhl']
            y_high, y_low = pc_master_dict[pc]['yhl']

            if load_pose_type == 'any_pose':
                if object_class in ['bowl', 'bottle']:
                    rp = np.random.rand(2) * (2 * np.pi / 3) - (np.pi / 3)
                    ori = common.euler2quat([rp[0], rp[1], 0]).tolist()
                else:
                    rpy = np.random.rand(3) * (2 * np.pi / 3) - (np.pi / 3)
                    ori = common.euler2quat([rpy[0], rpy[1], rpy[2]]).tolist()

                pos = [
                    np.random.random() * (x_high - x_low) + x_low,
                    np.random.random() * (y_high - y_low) + y_low,
                    table_z]
                pose = pos + ori
                rand_yaw_T = util.rand_body_yaw_transform(pos, min_theta=-np.pi, max_theta=np.pi)
                pose_w_yaw = util.transform_pose(util.list2pose_stamped(pose), util.pose_from_matrix(rand_yaw_T))
                pos, ori = util.pose_stamped2list(pose_w_yaw)[:3], util.pose_stamped2list(pose_w_yaw)[3:]
            else:
                if load_pose_type == 'demo_pose':
                    obj_start_pose_demo = pc_master_dict[pc]['demo_start_poses'][demo_idx]
                    pos, ori = obj_start_pose_demo[:3], obj_start_pose_demo[3:]
                else:
                    pos = [np.random.random() * (x_high - x_low) + x_low, np.random.random() * (y_high - y_low) + y_low, table_z]
                    pose = util.list2pose_stamped(pos + upright_orientation)
                    rand_yaw_T = util.rand_body_yaw_transform(pos, min_theta=-np.pi, max_theta=np.pi)
                    pose_w_yaw = util.transform_pose(pose, util.pose_from_matrix(rand_yaw_T))
                    pos, ori = util.pose_stamped2list(pose_w_yaw)[:3], util.pose_stamped2list(pose_w_yaw)[3:]

            # convert mesh with vhacd
            obj_obj_file, obj_obj_file_dec = pc_master_dict[pc]['mesh_file'], pc_master_dict[pc]['mesh_file_dec']

            if not osp.exists(obj_obj_file_dec):
                p.vhacd(
                    obj_obj_file,
                    obj_obj_file_dec,
                    'log.txt',
                    concavity=0.0025,
                    alpha=0.04,
                    beta=0.05,
                    gamma=0.00125,
                    minVolumePerCH=0.0001,
                    resolution=1000000,
                    depth=20,
                    planeDownsampling=4,
                    convexhullDownsampling=4,
                    pca=0,
                    mode=0,
                    convexhullApproximation=1
                )

            # load the object into the simulator
            obj_id = pb_client.load_geom(
                'mesh',
                mass=0.01,
                mesh_scale=mesh_scale,
                visualfile=obj_obj_file_dec,
                collifile=obj_obj_file_dec,
                base_pos=pos,
                base_ori=ori)

            # change the texture
            random_rgba = [*random_color(), 1.0]
            texture_modder.set_rgba(obj_id, -1, random_rgba)

            # register the object with the meshcat visualizer
            recorder.register_object(obj_id, obj_obj_file_dec, scaling=mesh_scale)

            # safeCollisionFilterPair(bodyUniqueIdA=obj_id, bodyUniqueIdB=table_id, linkIndexA=-1, linkIndexB=rack_link_id, enableCollision=False)
            safeCollisionFilterPair(bodyUniqueIdA=obj_id, bodyUniqueIdB=table_id, linkIndexA=-1, linkIndexB=table_base_id, enableCollision=False)
            p.changeDynamics(obj_id, -1, lateralFriction=0.5, linearDamping=5, angularDamping=5)

            # depending on the object/pose type, constrain the object to its world frame pose
            o_cid = None
            if (object_class in ['syn_rack_easy', 'syn_rack_hard', 'syn_rack_med']) or (load_pose_type == 'any_pose' and pc == 'child'):
                o_cid = constraint_obj_world(obj_id, pos, ori)
                pb_client.set_step_sim(False)
            pc_master_dict[pc]['o_cid'] = o_cid

            # safeCollisionFilterPair(obj_id, table_id, -1, -1, enableCollision=True)
            safeCollisionFilterPair(obj_id, table_id, -1, table_base_id, enableCollision=True)

            time.sleep(1.5)

            pc_master_dict[pc]['pb_obj_id'] = obj_id

        # get object point cloud
        depth_imgs = []
        seg_idxs = []
        obj_pcd_pts = []

        pc_obs_info = {}
        pc_obs_info['pcd'] = {}
        pc_obs_info['pcd_pts'] = {}
        pc_obs_info['pcd_pts']['parent'] = []
        pc_obs_info['pcd_pts']['child'] = []

        obj_pose_world = p.getBasePositionAndOrientation(obj_id)
        obj_pose_world = util.list2pose_stamped(list(obj_pose_world[0]) + list(obj_pose_world[1]))
        for i, cam in enumerate(cams.cams):
            # get image and raw point cloud
            rgb, depth, seg = cam.get_images(get_rgb=True, get_depth=True, get_seg=True)
            # plt.figure()
            # plt.imshow(rgb)
            # plt.title(f"{iteration}_cam{i}_rgb")
            # plt.show()
            pts_raw, _ = cam.get_pcd(in_world=True, rgb_image=rgb, depth_image=depth, depth_min=0.0, depth_max=np.inf)

            # flatten and find corresponding pixels in segmentation mask
            flat_seg = seg.flatten()
            flat_depth = depth.flatten()

            for pc in pcl:
                obj_id = pc_master_dict[pc]['pb_obj_id']
                obj_inds = np.where(flat_seg == obj_id)
                seg_depth = flat_depth[obj_inds[0]]

                obj_pts = pts_raw[obj_inds[0], :]
                # obj_pcd_pts.append(util.crop_pcd(obj_pts))
                pc_obs_info['pcd_pts'][pc].append(util.crop_pcd(obj_pts))

            depth_imgs.append(seg_depth)
            seg_idxs.append(obj_inds)

        # merge point clouds from different views, and filter weird artifacts away from the object
        for pc, obj_pcd_pts in pc_obs_info['pcd_pts'].items():
            target_obj_pcd_obs = np.concatenate(obj_pcd_pts, axis=0)  # object shape point cloud
            target_pts_mean = np.mean(target_obj_pcd_obs, axis=0)
            inliers = np.where(np.linalg.norm(target_obj_pcd_obs - target_pts_mean, 2, 1) < 0.2)[0]
            target_obj_pcd_obs = target_obj_pcd_obs[inliers]

            pc_obs_info['pcd'][pc] = target_obj_pcd_obs

        parent_pcd = pc_obs_info['pcd']['parent']
        child_pcd = pc_obs_info['pcd']['child']

        # Take NeRF images of static scene
        nerf_rgbs = []
        nerf_depths = []
        nerf_cam_start_time = time.perf_counter()
        if not args.disable_nerf_cams:
            for i, cam in enumerate(nerf_cams.cams):
                rgb, depth, seg = cam.get_images(get_rgb=True, get_depth=True, get_seg=True)
                nerf_rgbs.append(rgb)
                nerf_depths.append(depth)
            log_info(f"Capturing NeRF cameras took: {time.perf_counter() - nerf_cam_start_time:.2f}s")

        log_info(f'[INTERSECTION], Loading model weights for multi NDF inference')
        load_ndf_weights()
        pause_mc_thread(True)
        opt_start_time = time.perf_counter()
        if args.skip_opt:
            # Just keep the current pose if skipping optimization
            relative_trans = np.eye(4)
        else:
            relative_trans = infer_relation_intersection(
                mc_vis, parent_optimizer, child_optimizer,
                parent_overall_target_desc, child_overall_target_desc,
                parent_pcd, child_pcd, parent_query_points, child_query_points, opt_visualize=args.opt_visualize)
        opt_end_time = time.perf_counter()
        metrics = {
            "exp": args.exp,
            "trial": iteration,
            "infer_relation_intersection_time": opt_end_time - opt_start_time
        }
        log_info(f'[INTERSECTION], Inference took: {opt_end_time - opt_start_time:.2f}s')
        pause_mc_thread(False)

        time.sleep(1.0)

        # apply the inferred transformation by updating the pose of the child object
        parent_obj_id = pc_master_dict['parent']['pb_obj_id']
        child_obj_id = pc_master_dict['child']['pb_obj_id']
        start_child_pose = np.concatenate(pb_client.get_body_state(child_obj_id)[:2]).tolist()
        start_child_pose_mat = util.matrix_from_pose(util.list2pose_stamped(start_child_pose))
        final_child_pose_mat = np.matmul(relative_trans, start_child_pose_mat)

        start_parent_pose = np.concatenate(pb_client.get_body_state(parent_obj_id)[:2]).tolist()
        start_parent_pose_mat = util.matrix_from_pose(util.list2pose_stamped(start_parent_pose))
        upright_orientation = upright_orientation_dict[pc_master_dict['parent']['class']]
        upright_parent_ori_mat = common.quat2rot(upright_orientation)

        pb_client.set_step_sim(True)
        if pc_master_dict['parent']['load_pose_type'] == 'any_pose':
            # get the relative transformation to make it upright
            upright_parent_pose_mat = copy.deepcopy(start_parent_pose_mat); upright_parent_pose_mat[:-1, :-1] = upright_parent_ori_mat
            relative_upright_pose_mat = np.matmul(upright_parent_pose_mat, np.linalg.inv(start_parent_pose_mat))

            upright_parent_pos, upright_parent_ori = start_parent_pose[:3], common.rot2quat(upright_parent_ori_mat)
            pb_client.reset_body(parent_obj_id, upright_parent_pos, upright_parent_ori)

            final_child_pose_mat = np.matmul(relative_upright_pose_mat, final_child_pose_mat)

        final_child_pose_list = util.pose_stamped2list(util.pose_from_matrix(final_child_pose_mat))
        final_child_pos, final_child_ori = final_child_pose_list[:3], final_child_pose_list[3:]

        # apply computed final pose by resetting the state
        pb_client.reset_body(child_obj_id, final_child_pos, final_child_ori)
        if pc_master_dict['parent']['class'] not in ['syn_rack_easy', 'syn_rack_med']:
            safeRemoveConstraint(pc_master_dict['parent']['o_cid'])
        if pc_master_dict['child']['class'] not in ['syn_rack_easy', 'syn_rack_med']:
            safeRemoveConstraint(pc_master_dict['child']['o_cid'])

        final_child_pcd = util.transform_pcd(pc_obs_info['pcd']['child'], relative_trans)
        with recorder.meshcat_scene_lock:
            util.meshcat_pcd_show(mc_vis, final_child_pcd, color=[255, 0, 255], name='scene/final_child_pcd')
        # safeCollisionFilterPair(pc_master_dict['child']['pb_obj_id'], table_id, -1, -1, enableCollision=False)
        safeCollisionFilterPair(pc_master_dict['child']['pb_obj_id'], table_id, -1, table_base_id, enableCollision=False)

        time.sleep(3.0)

        # turn on the physics and let things settle to evaluate success/failure
        pb_client.set_step_sim(False)

        # evaluation criteria
        time.sleep(2.0)

        success_crit_dict = {}
        kvs = {}

        obj_surf_contacts = p.getContactPoints(pc_master_dict['child']['pb_obj_id'], pc_master_dict['parent']['pb_obj_id'], -1, -1)
        touching_surf = len(obj_surf_contacts) > 0
        success_crit_dict['touching_surf'] = touching_surf
        if parent_class == 'syn_container' and child_class == 'bottle':
            bottle_final_pose = np.concatenate(p.getBasePositionAndOrientation(pc_master_dict['child']['pb_obj_id'])[:2]).tolist()

            # get the y-axis in the body frame
            bottle_body_y = common.quat2rot(bottle_final_pose[3:])[:, 1]
            bottle_body_y = bottle_body_y / np.linalg.norm(bottle_body_y)

            # get the angle deviation from the vertical
            angle_from_upright = util.angle_from_3d_vectors(bottle_body_y, np.array([0, 0, 1]))
            bottle_upright = angle_from_upright < args.upright_ori_diff_thresh
            success_crit_dict['bottle_upright'] = bottle_upright

        # take an image to make sure it looks good (post-process)
        eval_rgb = eval_cam.get_images(get_rgb=True)[0]
        eval_img_fname = osp.join(eval_imgs_dir, f'{iteration}.png')
        util.np2img(eval_rgb.astype(np.uint8), eval_img_fname)

        ##########################################################################
        # upside down check for too much inter-penetration
        pb_client.set_step_sim(True)

        # remove constraints, if there are any
        safeRemoveConstraint(pc_master_dict['parent']['o_cid'])
        safeRemoveConstraint(pc_master_dict['child']['o_cid'])

        # first, reset everything
        pb_client.reset_body(parent_obj_id, start_parent_pose[:3], start_parent_pose[3:])
        pb_client.reset_body(child_obj_id, start_child_pose[:3], start_child_pose[3:])

        # then, compute a new position + orientation for the parent object, that is upside down
        upside_down_ori_mat = np.matmul(common.euler2rot([np.pi, 0, 0]), upright_parent_ori_mat)
        upside_down_pose_mat = np.eye(4); upside_down_pose_mat[:-1, :-1] = upside_down_ori_mat; upside_down_pose_mat[:-1, -1] = start_parent_pose[:3]
        upside_down_pose_mat[2, -1] += 0.15  # move up in z a bit
        parent_upside_down_pose_list = util.pose_stamped2list(util.pose_from_matrix(upside_down_pose_mat))

        # reset parent to this state and constrain to world
        pb_client.reset_body(parent_obj_id, parent_upside_down_pose_list[:3], parent_upside_down_pose_list[3:])
        ud_cid = constraint_obj_world(parent_obj_id, parent_upside_down_pose_list[:3], parent_upside_down_pose_list[3:])

        # get the final relative pose of the child object
        final_child_pose_parent = util.convert_reference_frame(
            pose_source=util.pose_from_matrix(final_child_pose_mat),
            pose_frame_target=util.pose_from_matrix(start_parent_pose_mat),
            pose_frame_source=util.unit_pose()
        )
        # get the final world frame pose of the child object in upside down pose
        final_child_pose_upside_down = util.convert_reference_frame(
            pose_source=final_child_pose_parent,
            pose_frame_target=util.unit_pose(),
            pose_frame_source=util.pose_from_matrix(upside_down_pose_mat)
        )
        final_child_pose_upside_down_list = util.pose_stamped2list(final_child_pose_upside_down)
        final_child_pose_upside_down_mat = util.matrix_from_pose(final_child_pose_upside_down)

        # reset child to this state
        pb_client.reset_body(child_obj_id, final_child_pose_upside_down_list[:3], final_child_pose_upside_down_list[3:])

        # turn on the simulation and wait for a couple seconds
        pb_client.set_step_sim(False)
        time.sleep(2.0)

        # check if they are still in contact (they shouldn't be)
        ud_obj_surf_contacts = p.getContactPoints(parent_obj_id, child_obj_id, -1, -1)
        ud_touching_surf = len(ud_obj_surf_contacts) > 0
        success_crit_dict['fell_off_upside_down'] = not ud_touching_surf

        #########################################################################

        place_success = np.all(np.asarray(list(success_crit_dict.values())))

        place_success_list.append(place_success)
        log_str = 'Iteration: %d, ' % iteration

        kvs['Place Success'] = sum(place_success_list) / float(len(place_success_list))

        if parent_class == 'syn_container' and child_class == 'bottle':
            kvs['Angle From Upright'] = angle_from_upright

        for k, v in kvs.items():
            log_str += '%s: %.3f, ' % (k, v)
        for k, v in success_crit_dict.items():
            log_str += '%s: %s, ' % (k, v)

        id_str = f', parent_id: {parent_id}, child_id: {child_id}'
        log_info(log_str + id_str)

        eval_iter_dir = osp.join(eval_save_dir, f'trial_{iteration}')
        util.safe_makedirs(eval_iter_dir)
        sample_fname = osp.join(eval_iter_dir, 'success_rate_relation.npz')
        full_cfg_fname = osp.join(eval_iter_dir, 'full_config.json')
        results_txt_fname = osp.join(eval_iter_dir, 'results.txt')
        np.savez(
            sample_fname,
            parent_id=parent_id,
            child_id=child_id,
            is_parent_shapenet_obj=is_parent_shapenet_obj,
            is_child_shapenet_obj=is_child_shapenet_obj,
            success_criteria_dict=success_crit_dict,
            place_success=place_success,
            place_success_list=place_success_list,
            mesh_file=obj_obj_file,
            args=args.__dict__,
            cfg=util.cn2dict(cfg),
        )
        json.dump(full_cfg_dict, open(full_cfg_fname, 'w', encoding='utf-8'), ensure_ascii=False, indent=4)

        results_txt_dict = {}
        results_txt_dict['place_success'] = place_success
        results_txt_dict['place_success_list'] = place_success_list
        results_txt_dict['current_success_rate'] = sum(place_success_list) / float(len(place_success_list))
        results_txt_dict['success_criteria_dict'] = success_crit_dict
        open(results_txt_fname, 'w').write(str(results_txt_dict))

        # Metrics that Will added
        metrics["rndf_results"] = {
            "place_success": place_success,
            "success_criteria_dict": success_crit_dict,
        }
        metrics_fname = osp.join(eval_iter_dir, 'metrics.json')
        with open(metrics_fname, 'w') as f:
            json.dump(metrics, f, indent=2, default=str)

        eval_img_fname2 = osp.join(eval_iter_dir, f'{iteration}.png')
        util.np2img(eval_rgb.astype(np.uint8), eval_img_fname2)

        # Write NeRF images
        if not args.disable_nerf_cams:
            nerf_dir = osp.join(eval_iter_dir, 'nerf_dataset')
            util.safe_makedirs(nerf_dir)
            write_instant_ngp_dataset(nerf_cams, nerf_rgbs, nerf_depths, nerf_dir)
            log_info(f"Wrote NeRF dataset to {nerf_dir}")

        pause_mc_thread(True)
        for pc in pcl:
            obj_id = pc_master_dict[pc]['pb_obj_id']
            pb_client.remove_body(obj_id)
            recorder.remove_object(obj_id, mc_vis)
        mc_vis['scene/child_pcd_refine'].delete()
        mc_vis['scene/child_pcd_refine_1'].delete()
        mc_vis['scene/final_child_pcd'].delete()
        pause_mc_thread(False)

    #########################################################################
    # Completed all trials, let's copy the NeRF datasets to their own directory
    # Just use the experiment name provided in the args so we don't make things
    # too complicated.
    if args.disable_nerf_cams or args.disable_nerf_dataset_copy:
        logger.info("Skipping NeRF dataset copy.")
        return

    nerf_dataset_dir = osp.join(path_util.get_rndf_nerf_datasets(), args.exp)
    os.makedirs(nerf_dataset_dir, exist_ok=True)
    copy_nerf_datasets(eval_dir=eval_save_dir, target_dir=nerf_dataset_dir)
    log_info(f"NeRF datasets copied to {nerf_dataset_dir}")


def validate_args(args):
    """ Additional checks Will Shen added to make life easier. """
    if args.test_on_train:
        assert args.parent_load_pose_type == "demo_pose", \
            "Must use demo poses for parent when test on train enabled"
        assert args.child_load_pose_type == "demo_pose", \
            "Must use demo poses for child when test on train enabled"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--parent_class', type=str, required=True)
    parser.add_argument('--child_class', type=str, required=True)
    parser.add_argument('--rel_demo_exp', type=str, required=True)

    parser.add_argument('--parent_model_path', type=str, required=True)
    parser.add_argument('--child_model_path', type=str, required=True)
    parser.add_argument('--parent_model_path_ebm', type=str, default=None)
    parser.add_argument('--child_model_path_ebm', type=str, default=None)
    parser.add_argument('--rel_model_path', type=str, default=None)

    parser.add_argument('--config', type=str, default='base_cfg')
    parser.add_argument('--exp', type=str, default='debug_eval')
    parser.add_argument('--eval_data_dir', type=str, default='eval_data')

    parser.add_argument('--opt_visualize', action='store_true')
    parser.add_argument('--opt_iterations', type=int, default=100)
    parser.add_argument('--num_iterations', type=int, default=100)
    parser.add_argument('--resume_iter', type=int, default=0)
    parser.add_argument('--save_all_opt_results', action='store_true', help='If True, then we will save point clouds for all optimization runs, otherwise just save the best one (which we execute)')
    parser.add_argument('--start_iteration', type=int, default=0)

    parser.add_argument('--single_instance', action='store_true')
    parser.add_argument('--rand_mesh_scale', action='store_true')
    parser.add_argument('--parent_load_pose_type', type=str, default='demo_pose', help='Must be in [any_pose, demo_pose, random_upright]')
    parser.add_argument('--child_load_pose_type', type=str, default='demo_pose', help='Must be in [any_pose, demo_pose, random_upright]')

    # rel ebm flags
    parser.add_argument('--no_trans', action='store_true', help='whether or not to include translation opt')
    parser.add_argument('--load_start', action='store_true', help='if we should load the start point clouds from demos')
    parser.add_argument('--rand_pose', action='store_true')
    parser.add_argument('--rand_rot', action='store_true')
    parser.add_argument('--test_idx', default=0, type=int)
    parser.add_argument('--real', action='store_true')

    parser.add_argument('--pybullet_viz', action='store_true')
    parser.add_argument('--pybullet_server', action='store_true')
    parser.add_argument('--is_parent_shapenet_obj', action='store_true')
    parser.add_argument('--is_child_shapenet_obj', action='store_true')
    parser.add_argument('--test_on_train', action='store_true')

    parser.add_argument('--relation_method', type=str, default='intersection', help='either "intersection", "ebm"')
    parser.add_argument('--create_target_desc', action='store_true', help='If True and --relation_method="intersection", then create the target descriptors if a file does not already exist containing them')
    parser.add_argument('--target_desc_name', type=str, default='target_descriptors.npz')
    parser.add_argument('--refine_with_ebm', action='store_true')
    parser.add_argument('--pc_reference', type=str, default='parent', help='either "parent" or "child"')
    parser.add_argument('--skip_alignment', action='store_true')
    parser.add_argument('--new_descriptors', action='store_true')
    parser.add_argument('--n_demos', type=int, default=0)
    parser.add_argument('--target_idx', type=int, default=-1)
    parser.add_argument('--query_scale', type=float, default=0.025)
    parser.add_argument('--target_rounds', type=int, default=3)

    # some threshold
    parser.add_argument('--upright_ori_diff_thresh', type=float, default=np.deg2rad(15))

    parser.add_argument('--add_noise', action='store_true')
    parser.add_argument('--noise_idx', type=int, default=0)

    # New args added by willshen
    parser.add_argument("--skip_opt", action="store_true",
                        help="If true, then skip the R-NDF optimization. "
                             "Used to generate NeRF datasets faster.")

    parser.add_argument("--plane-texture", type=str, choices={"plane", "none"}, default="plane")
    parser.add_argument("--pybullet_background_color", type=str, choices={"default", "white"}, default="default")
    parser.add_argument("--pybullet_debug_viz", action="store_true",
                        help="Enable debug visualization in PyBullet (makes things slower)")
    parser.add_argument("--disable_nerf_cams", action="store_true", help="Disable capturing NeRF dataset")
    parser.add_argument("--disable_nerf_dataset_copy", action="store_true",
                        help="Disable copying NeRF dataset to the dedicated rndf_robot/nerf_datasets folder")

    args = parser.parse_args()
    validate_args(args)
    main(args)
