// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {OutcomeMarket} from "../contracts/OutcomeMarket.sol";

/// @title DeployOutcomeMarket
/// @notice Deploys the OutcomeMarket contract for on-chain LMSR prediction.
///
///   Usage:
///     # Local anvil (default):
///     forge script script/DeployOutcomeMarket.s.sol --broadcast
///
///     # Base mainnet:
///     forge script script/DeployOutcomeMarket.s.sol \
///       --rpc-url base --broadcast --verify
///
///   Required env vars:
///     PRIVATE_KEY          — deployer key (anvil default account 0)
///     OUTCOME_USDC_ADDRESS — USDC on target chain
///       Base mainnet: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
///       Base sepolia: 0x036CbD53842c541265379f025513D4e4fB13B783
///       Anvil: deploy MiniUSDC first, use its address
contract DeployOutcomeMarket is Script {
    function run() external {
        string memory usdcAddr = vm.envOr("OUTCOME_USDC_ADDRESS", string(""));
        if (bytes(usdcAddr).length == 0) {
            console.log("OUTCOME_USDC_ADDRESS not set — deploy MiniUSDC first or set it.");
            return;
        }

        address deployer = vm.addr(vm.envUint("PRIVATE_KEY"));
        console.log("Deployer:", deployer);
        console.log("USDC:", usdcAddr);

        vm.startBroadcast();
        OutcomeMarket market = new OutcomeMarket(
            address(uint160(vm.parseAddress(usdcAddr))),
            deployer
        );
        vm.stopBroadcast();

        console.log("OutcomeMarket deployed at:", address(market));
        console.log("  MAX_OUTCOMES:", market.MAX_OUTCOMES());
        console.log("  MIN_SUBSIDY:", market.MIN_SUBSIDY_MICRO());
    }
}
