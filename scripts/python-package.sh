#!/usr/bin/env bash
# Native GitHub releases; only generated artifact metadata is interpreted here.
set -euo pipefail

[[ $(git rev-parse HEAD) == "$SOURCE_SHA" ]] || exit 1
source_directory=$PWD
cd "$ARTIFACT_DIRECTORY"
wheel=$(jq -r .wheel python-package.json)
version=$(jq -r .version python-package.json)
[[ "$wheel" =~ ^[A-Za-z0-9_][A-Za-z0-9_.+-]*\.whl$ ]] || exit 1
jq -e --arg revision "$SOURCE_SHA" --arg digest "$EXPECTED_SHA256" \
  '.revision == $revision and .sha256 == $digest' python-package.json >/dev/null
[[ $(sha256sum "$wheel" | cut -d' ' -f1) == "$EXPECTED_SHA256" ]] || exit 1
sha256sum --check SHA256SUMS

tag="$RELEASE_PREFIX$version"
git -C "$source_directory" check-ref-format "refs/tags/$tag"
gh auth setup-git --hostname github.com
# A pre-existing tag must identify this exact source, including annotated tags.
if git -C "$source_directory" ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null; then
  git -C "$source_directory" fetch origin "refs/tags/$tag:refs/tags/$tag"
  [[ $(git -C "$source_directory" rev-list -n1 "$tag") == "$SOURCE_SHA" ]] || exit 1
elif [[ $? != 2 ]]; then
  echo 'Cannot verify the remote release tag.' >&2
  exit 1
fi
encoded=$(jq -rn --arg tag "$tag" '$tag | @uri')
lookup="repos/$GITHUB_REPOSITORY/releases/tags/$encoded"
if ! gh api "$lookup" > release.json 2> lookup-error; then
  grep -q 'HTTP 404' lookup-error || { cat lookup-error >&2; exit 1; }
  gh release create "$tag" --repo "$GITHUB_REPOSITORY" --draft --target "$SOURCE_SHA" \
    --title "$tag" --notes "Python wheel built from source commit $SOURCE_SHA. SHA256 and package identity are attached."
  gh api "$lookup" > release.json
fi
[[ $(jq -r .target_commitish release.json) == "$SOURCE_SHA" ]] || exit 1

verify=$(mktemp -d)
trap 'rm -rf "$verify"' EXIT
for file in "$wheel" python-package.json SHA256SUMS; do
  if jq -e --arg name "$file" '.assets[] | select(.name == $name)' release.json >/dev/null; then
    gh release download "$tag" --repo "$GITHUB_REPOSITORY" --pattern "$file" --dir "$verify"
    cmp "$file" "$verify/$file" || { echo 'Refusing to overwrite a different release artifact.' >&2; exit 1; }
  else
    [[ $(jq -r .draft release.json) == true ]] || { echo 'Published release is incomplete; refusing to modify it.' >&2; exit 1; }
    gh release upload "$tag" "$file" --repo "$GITHUB_REPOSITORY"
  fi
done
if [[ $(jq -r .draft release.json) == true ]]; then
  gh release edit "$tag" --repo "$GITHUB_REPOSITORY" --draft=false
fi
echo "url=https://github.com/$GITHUB_REPOSITORY/releases/download/$encoded/$wheel" >> "$GITHUB_OUTPUT"
