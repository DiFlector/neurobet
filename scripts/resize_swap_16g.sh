#!/usr/bin/env bash
# Replace /swap.img with a 16 GiB swap file (safe: enable new swap before removing old).
# Usage: sudo ./scripts/resize_swap_16g.sh
# Or without local sudo: ./scripts/resize_swap_16g.sh --docker

set -euo pipefail

SWAP_FILE="/swap.img"
TEMP_SWAP="/swap.img.new"
NEW_SIZE_GB=16

run_host() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Need root. Run: sudo $0" >&2
    exit 1
  fi

  echo "Current swap:"
  swapon --show
  free -h | grep -i swap

  if swapon --show | awk '{print $1}' | grep -qx "$SWAP_FILE"; then
    echo "Creating ${NEW_SIZE_GB}G swap at $TEMP_SWAP (keeping old swap active)..."
    rm -f "$TEMP_SWAP"
    if fallocate -l "${NEW_SIZE_GB}G" "$TEMP_SWAP" 2>/dev/null; then
      :
    else
      dd if=/dev/zero of="$TEMP_SWAP" bs=1M count=$((NEW_SIZE_GB * 1024)) status=progress
    fi
    chmod 600 "$TEMP_SWAP"
    mkswap "$TEMP_SWAP"
    swapon "$TEMP_SWAP"

    echo "Disabling old swap $SWAP_FILE..."
    swapoff "$SWAP_FILE" || {
      echo "swapoff failed — freeing memory first or stop heavy workloads" >&2
      swapoff "$TEMP_SWAP" || true
      exit 1
    }
    rm -f "$SWAP_FILE"
    mv "$TEMP_SWAP" "$SWAP_FILE"

    if ! grep -qE "^${SWAP_FILE}[[:space:]]" /etc/fstab; then
      echo "$SWAP_FILE none swap sw 0 0" >> /etc/fstab
    fi

    echo "Done. New swap:"
    swapon --show
    free -h | grep -i swap
  else
    echo "Expected active swap at $SWAP_FILE; found:"
    swapon --show
    exit 1
  fi
}

run_docker() {
  docker run --rm --privileged \
    -v /:/host \
    alpine:3.20 sh -eu -c '
      apk add --no-cache util-linux >/dev/null
      SWAP_FILE="/host/swap.img"
      TEMP_SWAP="/host/swap.img.new"
      NEW_SIZE_GB=16

      echo "Current swap:"
      swapon --show
      free -h | grep -i swap

      rm -f "$TEMP_SWAP"
      if fallocate -l "${NEW_SIZE_GB}G" "$TEMP_SWAP" 2>/dev/null; then
        :
      else
        dd if=/dev/zero of="$TEMP_SWAP" bs=1M count=$((NEW_SIZE_GB * 1024)) status=none
      fi
      chmod 600 "$TEMP_SWAP"
      mkswap "$TEMP_SWAP"
      swapon "$TEMP_SWAP"

      swapoff "$SWAP_FILE"
      rm -f "$SWAP_FILE"
      mv "$TEMP_SWAP" "$SWAP_FILE"

      if ! grep -qE "^/swap.img[[:space:]]" /host/etc/fstab; then
        echo "/swap.img none swap sw 0 0" >> /host/etc/fstab
      fi

      echo "Done. New swap:"
      swapon --show
      free -h | grep -i swap
    '
}

if [[ "${1:-}" == "--docker" ]]; then
  run_docker
else
  run_host
fi
