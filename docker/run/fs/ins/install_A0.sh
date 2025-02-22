#!/bin/bash

# branch from parameter
if [ -z "$1" ]; then
    echo "Error: Branch parameter is empty. Please provide a valid branch name."
    exit 1
fi
BRANCH="$1"

# clone project repo branch
git clone -b "$BRANCH" "https://github.com/eight-atulya/ATULYA.git" "/git/ATULYA"

# setup python environment
. "/ins/setup_venv.sh" "$@"

# Ensure the virtual environment and pip setup
pip install --upgrade pip ipython requests

# Install some packages in specific variants
pip install torch --index-url https://download.pytorch.org/whl/cpu

# Install remaining A0 python packages
pip install -r /git/ATULYA/requirements.txt

# Preload A0
python /git/ATULYA/preload.py --dockerized=true