#!/bin/bash
set -e

export GITHUB_TOKEN="$INPUT_GITHUB_TOKEN"
export PR_NUMBER="$INPUT_PR_NUMBER"
export NOTION_TOKEN="$INPUT_NOTION_TOKEN"

python /app/notion_d_day_label.py
