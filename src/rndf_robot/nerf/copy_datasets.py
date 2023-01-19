import glob
import json
import os
import re
import shutil
from datetime import datetime

from loguru import logger

_regex_pattern = r".*(trial_\d+)\/nerf_dataset"


def copy_nerf_datasets(eval_dir: str, target_dir: str) -> None:
    """
    Find all NeRF datasets (named nerf_dataset) and copy them to the target_dir
    under their respective trial name.

    Also copy the full_exp_cfg.txt and other useful files.
    """
    num_datasets = 0
    og_dataset_path_to_new_path = {}

    # Clear the target directory
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)
        os.makedirs(target_dir)
        logger.debug(f"Removed and recreated {target_dir}")

    # Glob eval directory recursively to find directories named nerf_dataset
    for nerf_dataset_path in glob.glob(eval_dir + "/**/nerf_dataset", recursive=True):
        # Extract trial name from path
        trial_name = re.match(_regex_pattern, nerf_dataset_path).group(1)
        target_trial_dir = os.path.join(target_dir, trial_name)

        # Copy dataset to target_trial_dir using shutil
        if os.path.exists(target_trial_dir):
            raise RuntimeError(
                f"Target directory {target_trial_dir} already exists!"
                "Check the `eval_dir` does not contain multiple experiments."
            )
        shutil.copytree(nerf_dataset_path, target_trial_dir)
        logger.debug(f"Copied {nerf_dataset_path} to {target_trial_dir}")

        # Using the relative path to the target directory
        og_dataset_path_to_new_path[nerf_dataset_path] = trial_name
        num_datasets += 1

    # Find the full_exp_cfg.txt and copy it to the target_dir
    for full_exp_cfg_path in glob.glob(
        eval_dir + "/**/full_exp_cfg.txt", recursive=True
    ):
        # The txt file is actually a JSON file, so let's make change the
        # extension when copying.
        target_cfg_path = os.path.join(target_dir, "full_exp_cfg.json")
        shutil.copyfile(full_exp_cfg_path, target_cfg_path)
        logger.debug(f"Copied {full_exp_cfg_path} to {target_dir}")
        break

    # Write a debug JSON file
    debug = {
        "num_datasets": num_datasets,
        "og_dataset_path_to_new_path": og_dataset_path_to_new_path,
        "timestamp": str(datetime.now()),
    }
    with open(os.path.join(target_dir, "debug.json"), "w") as f:
        json.dump(debug, f, indent=2)

    logger.success(
        f"Found {num_datasets} datasets in {eval_dir} and copied them to {target_dir}"
    )


if __name__ == "__main__":
    # Example usage
    copy_nerf_datasets(
        "/Users/william/workspace/vqn/relational_ndf/src/rndf_robot/eval_data/eval_data",
        "data",
    )