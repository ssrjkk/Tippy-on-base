// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title USDCForwarder
/// @notice Shared implementation behind every CREATE2 per-user deposit proxy
///         (EIP-1167). Proxies delegatecall into this contract, so adding a
///         user never costs gas and every user keeps a deterministic deposit
///         address derived from their tg_id.
///
///         Lifecycle of a deposit:
///           1. User sends USDC (and/or ETH) to their unique proxy address.
///           2. The bot calls `forward()` on the proxy (or the user can call
///              it themselves — funds always go to the configured hot wallet).
///           3. This contract pulls the full USDC balance to the hot wallet
///              and sends any ETH held by the proxy to the hot wallet.
///
///         Security notes:
///           - No admin/withdraw: a proxy can never route its balance
///             anywhere except the hot wallet configured at construction.
///           - USDC and hot wallet are immutable, set once at deployment.
///           - Anyone may call forward(); it is a safety sweep, not a
///             privileged operation, and it cannot steal (target is fixed).

/// @notice Minimal ERC-20 surface (balanceOf + transfer; USDC).
interface IERC20Minimal {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
}

contract USDCForwarder {

    /// @notice Destination for all funds swept from proxies.
    address public immutable hotWallet;
    /// @notice USDC contract this forwarder sweeps (network-dependent).
    address public immutable usdc;

    constructor(address hotWallet_, address usdc_) {
        require(hotWallet_ != address(0), "USDCForwarder: zero hot wallet");
        require(usdc_ != address(0), "USDCForwarder: zero usdc");
        hotWallet = hotWallet_;
        usdc = usdc_;
    }

    /// @notice Sweep all USDC + ETH held by the calling proxy to the hot
    ///         wallet. Safe to call by anyone at any time.
    /// @return usdcForwarded USDC amount moved (micro units).
    /// @return ethForwarded  ETH amount moved (wei).
    function forward() external returns (uint256 usdcForwarded, uint256 ethForwarded) {
        uint256 bal = IERC20Minimal(usdc).balanceOf(address(this));
        if (bal > 0) {
            require(IERC20Minimal(usdc).transfer(hotWallet, bal), "USDCForwarder: usdc transfer failed");
        }

        uint256 ethBal = address(this).balance;
        if (ethBal > 0) {
            (bool ok, ) = hotWallet.call{value: ethBal}("");
            require(ok, "USDCForwarder: eth transfer failed");
        }

        return (bal, ethBal);
    }

    /// @notice Allow the proxy to receive native ETH without a fallback revert.
    receive() external payable {}
}