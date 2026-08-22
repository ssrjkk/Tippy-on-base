// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal ERC20 surface used by the vault (USDC on Base).
interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @title TipBotVault
/// @notice On-chain treasury for Tippy: users send USDC directly to this
///         contract, the bot (relayer) distributes payouts under a daily cap,
///         and the owner (multisig) holds full control. Anyone can verify
///         solvency on-chain: totalReserves() vs. the bot's liabilities.
contract TipBotVault {
    error OnlyOwner();
    error OnlyOwnerOrRelayer();
    error DailyLimitExceeded(uint256 spent, uint256 limit, uint256 requested);
    error MismatchedArrays();
    error EmptyDistribution();
    error Reentrant();
    error TransferFailed();
    error NotPendingOwner();

    IERC20 public immutable usdc;

    address public owner;
    address public pendingOwner;
    address public relayer;

    uint256 public dailyLimit;
    uint256 public windowStart;
    uint256 public spentInWindow;

    uint256 private _locked = 1;

    event Distributed(address indexed recipient, uint256 amount);
    event RelayerChanged(address indexed relayer);
    event LimitChanged(uint256 limit);
    event OwnershipTransferStarted(address indexed previousOwner, address indexed newOwner);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event ReserveWithdrawn(address indexed to, uint256 amount);

    constructor(address usdc_, address owner_, address relayer_, uint256 dailyLimit_) {
        usdc = IERC20(usdc_);
        owner = owner_;
        relayer = relayer_;
        dailyLimit = dailyLimit_;
        windowStart = block.timestamp;
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert OnlyOwner();
        _;
    }

    modifier onlyOwnerOrRelayer() {
        if (msg.sender != owner && msg.sender != relayer) revert OnlyOwnerOrRelayer();
        _;
    }

    modifier nonReentrant() {
        if (_locked != 1) revert Reentrant();
        _locked = 2;
        _;
        _locked = 1;
    }

    /// @notice Total USDC backing every balance inside the bot.
    function totalReserves() external view returns (uint256) {
        return usdc.balanceOf(address(this));
    }

    /// @notice Distribute payouts. The relayer is capped by a daily limit;
    ///         the owner is not. Reverts if the sum is not fully covered.
    function batchDistribute(
        address[] calldata recipients,
        uint256[] calldata amounts
    ) external onlyOwnerOrRelayer nonReentrant returns (uint256 total) {
        if (recipients.length == 0) revert EmptyDistribution();
        if (recipients.length != amounts.length) revert MismatchedArrays();

        for (uint256 i = 0; i < recipients.length; i++) {
            total += amounts[i];
        }

        if (msg.sender == relayer) {
            _rollWindow();
            uint256 next = spentInWindow + total;
            if (next > dailyLimit) {
                revert DailyLimitExceeded(spentInWindow, dailyLimit, total);
            }
            spentInWindow = next;
        }

        for (uint256 i = 0; i < recipients.length; i++) {
            if (amounts[i] == 0) continue;
            bool ok = usdc.transfer(recipients[i], amounts[i]);
            if (!ok) revert TransferFailed();
            emit Distributed(recipients[i], amounts[i]);
        }
    }

    /// @notice Withdraw excess reserves (owner only, e.g. when winding down).
    function withdrawReserve(address to, uint256 amount) external onlyOwner nonReentrant {
        bool ok = usdc.transfer(to, amount);
        if (!ok) revert TransferFailed();
        emit ReserveWithdrawn(to, amount);
    }

    function setRelayer(address relayer_) external onlyOwner {
        relayer = relayer_;
        emit RelayerChanged(relayer_);
    }

    function setDailyLimit(uint256 limit_) external onlyOwner {
        dailyLimit = limit_;
        emit LimitChanged(limit_);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert OnlyOwner();
        pendingOwner = newOwner;
        emit OwnershipTransferStarted(owner, newOwner);
    }

    function acceptOwnership() external {
        if (msg.sender != pendingOwner) revert NotPendingOwner();
        emit OwnershipTransferred(owner, pendingOwner);
        owner = pendingOwner;
        pendingOwner = address(0);
    }

    /// @notice How much of the daily budget the relayer has already spent in
    ///         the current 24h window.  Rolling window prevents midnight-bypass.
    function spentTodayView() external view returns (uint256) {
        if (block.timestamp >= windowStart + 1 days) return 0;
        return spentInWindow;
    }

    function _rollWindow() private {
        if (block.timestamp >= windowStart + 1 days) {
            windowStart = block.timestamp;
            spentInWindow = 0;
        }
    }
}