#!/bin/zsh
set -e

cd "$(dirname "$0")"
python3 build_index_comparison.py

echo
echo "更新完成。可以重新打开或刷新：中证1000_中证500_上证指数_十年滑动对比图.html"
