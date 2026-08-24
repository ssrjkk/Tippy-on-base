#!/bin/bash
# Install Foundry dependencies for OutcomeMarket contracts.
# Run once after cloning: bash scripts/setup_foundry.sh
set -e
cd "$(dirname "$0")/.."

echo "Installing OpenZeppelin contracts..."
forge install OpenZeppelin/openzeppelin-contracts@v5.1.0 --no-commit

echo "Installing PRBMath..."
forge install PaulRBerg/prb-math@v4.0.0 --no-commit

echo "Installing forge-std (required by contracts/test/forge)..."
forge install foundry-rs/forge-std@v1.9.7 --no-commit

echo "Creating .gitkeep for contracts/lib/"
touch contracts/lib/.gitkeep

echo "Done. Run 'forge test' to verify."
