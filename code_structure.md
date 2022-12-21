# Code Structure
Based on Will's introspection.

## doc/
Some documentation and more details on training and evaluation.

I think the information in the main README suffices, and we can ask Anthony for specific details.

## scripts/
Scripts for downloading data.

Probably not necessary beyond the evaluation tasks we want to consider.

## src/rndf_robot
Source code for relational NDFs.

- `assets`: some numpy serialized bad meshes
- `config`: configuration files for data generation and evaluation
- `data`: where data is downloaded to using the scripts
- `data_gen`: from my understanding, for generating point clouds, etc. 
    from the meshes for the purposes of training the Occupancy Net
- `demonstrations`: for collecting relational demos, teleop a Panda in PyBullet
- `descriptions`: URDF of the Panda, meshes of the hanging objects, and the downloaded objects
- `eval`: evaluating relational NDFs.
- `eval_data`: evaluation results in the NDF format
- `models`: models such as the occupancy network and some ResNet models
- `model_weights`: weights for the trained NDFs
- `opt`: optimizers for inferring new grasp (i.e., poses)
- `robot`: multi-camera setup
- `share`: train test split of object IDs
- `training`: training an Occupancy Net (I think)
- `utils`: lots of utilities including geometry, plotting, 3d transformations, MeshCat visualizations, etc. 
