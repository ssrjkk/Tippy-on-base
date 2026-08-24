#!/bin/bash
# Vendor Solidity dependencies for the OutcomeMarket contracts.
#
# Pure git-based and idempotent: works in CI (no forge binary required)
# and locally. After this, both `forge test` and the solcx-based pytest
# EVM suite (tests/test_outcome_market_evm.py) can compile contracts/.
#
# Usage: bash scripts/setup_foundry.sh
set -e
cd "$(dirname "$0")/.."
mkdir -p contracts/lib

clone() {
    local dest="$1" repo="$2" tag="$3"
    if [ -d "$dest" ]; then
        echo "OK  $dest already present"
        return
    fi
    echo "Cloning $repo ($tag)..."
    if [ "$tag" = "latest" ]; then
        git clone -q --depth 1 "$repo" "$dest"
    else
        git clone -q --depth 1 --branch "$tag" "$repo" "$dest"
    fi
}

clone contracts/lib/openzeppelin-contracts https://github.com/OpenZeppelin/openzeppelin-contracts.git v5.1.0
clone contracts/lib/prb-math           https://github.com/PaulRBerg/prb-math.git            v4.0.0
clone contracts/lib/forge-std          https://github.com/foundry-rs/forge-std.git          latest

touch contracts/lib/.gitkeep

echo "Done. Contracts deps vendored under contracts/lib/."
echo "Run 'forge test --match-contract SecurityFixesTest' or 'python -m pytest tests/test_outcome_market_evm.py'."
