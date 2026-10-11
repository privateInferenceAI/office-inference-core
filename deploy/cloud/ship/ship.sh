#!/bin/sh
# Ship one artifact to the audit bucket, then fetch it back and compare hashes.
#
# Usage: ship.sh <local-file> <s3-uri>
#
# The read-back is the point of this script. From the sending side, a complete upload and a
# truncated one look identical: both print nothing and exit zero. Fetching the object back and
# hashing it is what turns "we sent it" into "it is there and it matches".
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: ship.sh <local-file> <s3-uri>" >&2
  exit 2
fi

local_file="$1"
s3_uri="$2"

if [ ! -f "$local_file" ]; then
  echo "no such file: $local_file" >&2
  exit 1
fi

local_hash=$(sha256sum "$local_file" | cut -d' ' -f1)
size=$(wc -c < "$local_file")
echo "shipping $local_file ($size bytes)"
echo "  local  sha256 $local_hash"

if ! aws s3 cp --only-show-errors "$local_file" "$s3_uri"; then
  echo "FAIL: could not upload to $s3_uri" >&2
  exit 1
fi

back=$(mktemp)
trap 'rm -f "$back"' EXIT

if ! aws s3 cp --only-show-errors "$s3_uri" "$back"; then
  echo "FAIL: the upload reported success, but the object cannot be read back from $s3_uri" >&2
  exit 1
fi

remote_hash=$(sha256sum "$back" | cut -d' ' -f1)
echo "  remote sha256 $remote_hash"

if [ "$local_hash" != "$remote_hash" ]; then
  echo "FAIL: the object in the bucket does not match the file that was sent: $s3_uri" >&2
  exit 1
fi

echo "  ok: $s3_uri matches the file that was sent"
