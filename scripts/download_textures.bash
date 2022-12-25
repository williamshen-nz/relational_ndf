# Will Shen's script to download textures
set -e

wget https://www.robots.ox.ac.uk/~vgg/data/dtd/download/dtd-r1.0.1.tar.gz

# untar into assets
tar -xzf dtd-r1.0.1.tar.gz -C $RNDF_SOURCE_DIR/assets

# Remove tarball
rm dtd-r1.0.1.tar.gz
