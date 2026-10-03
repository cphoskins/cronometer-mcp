#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: ./release.sh <version>"
    echo ""
    echo "Bumps version, commits, tags, and pushes a release."
    echo "A GitHub Release from the tag then publishes to PyPI and the MCP Registry."
    echo ""
    echo "Examples:"
    echo "  ./release.sh 0.2.0"
    echo "  ./release.sh 1.0.0"
    exit 1
fi

VERSION="$1"
TAG="v${VERSION}"

cd "$(dirname "$0")"

# Check for uncommitted changes
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Error: You have uncommitted changes. Commit or stash them first."
    exit 1
fi

# Check tag doesn't already exist
if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "Error: Tag $TAG already exists."
    exit 1
fi

# Bump version in pyproject.toml
sed -i "s/^version = \".*\"/version = \"${VERSION}\"/" pyproject.toml

# Bump version in __init__.py
sed -i "s/^__version__ = \".*\"/__version__ = \"${VERSION}\"/" cronometer_mcp/__init__.py

# Bump version in server.json (top-level and package version)
sed -i "s/\"version\": \".*\"/\"version\": \"${VERSION}\"/" server.json

# Verify all files were updated
if ! grep -q "version = \"${VERSION}\"" pyproject.toml; then
    echo "Error: Failed to update pyproject.toml"
    exit 1
fi
if ! grep -q "__version__ = \"${VERSION}\"" cronometer_mcp/__init__.py; then
    echo "Error: Failed to update __init__.py"
    exit 1
fi
if ! grep -q "\"version\": \"${VERSION}\"" server.json; then
    echo "Error: Failed to update server.json"
    exit 1
fi

echo "Bumped version to ${VERSION}"
echo "  pyproject.toml: version = \"${VERSION}\""
echo "  __init__.py:    __version__ = \"${VERSION}\""
echo "  server.json:    version: \"${VERSION}\""

git add pyproject.toml cronometer_mcp/__init__.py server.json
git commit -m "Bump version to ${VERSION}"
git tag "$TAG"
git push origin main --tags

echo ""
echo "Pushed ${TAG} to origin."
echo "Now create a GitHub Release from the tag to publish to PyPI, then the MCP Registry:"
echo "  https://github.com/cphoskins/cronometer-mcp/releases/new?tag=${TAG}"
