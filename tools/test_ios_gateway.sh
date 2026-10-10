#!/bin/bash
# Tests actual state-store logic using an in-memory HTTP/feed fixture; no messages are sent.
set -euo pipefail
cd "$(dirname "$0")/.."
review_build_dir="$(mktemp -d "${TMPDIR:-/tmp}/shield-swift-tests.XXXXXX")"
trap 'rm -rf "$review_build_dir"' EXIT
xcrun swiftc -parse-as-library -module-cache-path "$review_build_dir/module-cache" \
  ios/Shield/Model/CallSummary.swift \
  ios/Shield/Model/Flag.swift \
  ios/Shield/Model/GatewayMessage.swift \
  ios/Shield/Model/MessageSummary.swift \
  ios/Shield/Model/ShieldEvent.swift \
  ios/Shield/Model/LiveCall.swift \
  ios/Shield/Services/ShieldStore.swift \
  ios/Tests/ShieldStoreGatewayTests.swift \
  -o "$review_build_dir/gateway-tests"
"$review_build_dir/gateway-tests"
