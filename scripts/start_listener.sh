#!/bin/bash


echo "Starting shellbot listener"

uv run $SHELLBOT_PATH/src/shellbot2/cli.py daemon watch & 
