""" Upload datasets to ml-logger """
from params_proto import ParamsProto, Proto


class UploadDatasetsArgs(ParamsProto):
    base_prefix: str = Proto(
        "instant-feature-distillation/datasets/baselines/rndf",
        help="Base prefix for R-NDF datasets on ml-logger",
    )


def upload_datasets_to_logger(nerf_dataset_dir: str, exp_name: str) -> str:
    """Upload NeRF datasets to ml-logger"""
    from ml_logger import ML_Logger, logger

    prefix = f"{UploadDatasetsArgs.base_prefix}/{exp_name}"
    logger.configure(prefix=prefix)
    logger.job_started()

    with logger.Sync():
        # Upload directory as a tarball then extract it
        tar_fname = "dataset.tar"
        logger.upload_dir(nerf_dataset_dir, tar_fname, archive="tar")
        logger.print(f"Uploaded NeRF dataset to {tar_fname}")

        # Untar the file on the logger, then remove the tar file
        logger.shell(f"tar -xvf {tar_fname} --directory .")
        logger.remove(tar_fname)
        logger.print(f"Decompressed and removed {tar_fname}")

    # For each trial, start a logger job and create a chart, so we can visualize the images
    trial_paths = logger.glob("trial*/")
    for trial_path in logger.glob("trial*/"):
        trial_prefix = f"{prefix}/{trial_path}"

        trial_logger = ML_Logger(prefix=trial_prefix)
        trial_logger.job_started(silent=True)
        charts_yml = """
        charts:
        - glob: rgbs/*.png
          type: image
        - glob: depths/*.png
          type: image
        """
        with trial_logger.Sync():
            trial_logger.log_text(
                charts_yml, ".charts.yml", dedent=True, overwrite=True
            )

    logger.print(f"Uploaded and processed {len(trial_paths)} trials in {prefix}")
    return prefix
