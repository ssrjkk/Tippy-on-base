// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

// Test-only USDC stand-in for the Base Sepolia smoke test. Open mint so the
// test harness can mint however much it needs; NEVER deploy on produciton.
contract TestUSDC is ERC20 {
    uint8 private immutable _decimals;

    constructor(uint8 dec) ERC20("Test USDC", "tUSDC") {
        _decimals = dec;
    }

    function decimals() public view override returns (uint8) {
        return _decimals;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}
