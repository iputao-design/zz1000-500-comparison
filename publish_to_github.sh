#!/bin/zsh
set -euo pipefail

REPO="iputao-design/zz1000-500-comparison"
PAGES_URL="https://iputao-design.github.io/zz1000-500-comparison/"

cd "$(dirname "$0")"

if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI is not logged in. Run: gh auth login -h github.com"
  exit 1
fi

if [ ! -d .git ]; then
  git init
  git branch -M main
fi

python3 build_index_comparison.py

git add README.md .gitignore .nojekyll build_index_comparison.py publish_to_github.sh 每日更新.command \
  index.html index_close_comparison_2014-2026.csv index_close_comparison_latest.csv \
  index_stats_summary_2014-2026.csv index_stats_summary_latest.csv \
  中证1000_中证500_上证指数_十年滑动对比图.html

if ! git diff --cached --quiet; then
  git commit -m "Publish index comparison site"
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  gh repo create "$REPO" --public --description "中证1000、中证500与上证指数历史对比图" --source . --remote origin --push
else
  git push -u origin main
fi

gh api --method POST "repos/$REPO/pages" -f source.branch=main -f source.path=/ >/dev/null 2>&1 || true

echo "Published: $PAGES_URL"
