// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title SmartAccount — ERC-4337 compatible account for Tippy users.
/// @notice Each user gets a deterministic SmartAccount controlled by the bot's
///         hot wallet (owner). The account holds USDC and can execute arbitrary
///         calls through the EntryPoint. Gas is sponsored by the VerifyingPaymaster.
///
///         This replaces the current raw-EOA model: users no longer need ETH
///         for gas or manage their own private keys. The bot signs UserOperations
///         on behalf of the user and the paymaster sponsors the gas.
///
///         Follows ERC-4337 (Account Abstraction) standard:
///         - validateUserOp: verifies owner signature
///         - execute / executeBatch: arbitrary call execution
///         - owner can be transferred (recovery)
interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

contract SmartAccount {
    address public owner;
    uint256 public nonce;
    bool public initialized;

    event AccountInitialized(address indexed owner);
    event Executed(address indexed dest, uint256 value, bytes data);
    event OwnerTransferred(address indexed oldOwner, address indexed newOwner);

    /// @notice Validate a UserOperation. Called by the EntryPoint before execution.
    /// @dev owner signs hash(sender, nonce, chainId, callData); we verify here.
    function validateUserOp(
        bytes calldata userOp,
        bytes32 userOpHash,
        uint256 /*missingAccountFunds*/
    ) external returns (uint256 validationData) {
        require(msg.sender == entryPoint(), "SmartAccount: not EntryPoint");

        // Extract signature from last 65 bytes of userOp.signature
        bytes calldata sig = userOp[userOp.length - 65:];
        bytes32 hash = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", userOpHash));
        address signer = _recover(hash, sig);

        if (signer == owner) {
            return 0; // validation succeeded
        } else {
            return 1; // validation failed
        }
    }

    /// @notice Execute a single call from the account.
    function execute(address dest, uint256 value, bytes calldata data) external {
        require(msg.sender == entryPoint() || msg.sender == owner, "SmartAccount: not authorized");
        (bool ok, ) = dest.call{value: value}(data);
        require(ok, "SmartAccount: call failed");
        emit Executed(dest, value, data);
    }

    /// @notice Execute two calls atomically (useful for approve + trade).
    function executeBatch(
        address dest1, bytes calldata data1,
        address dest2, bytes calldata data2
    ) external {
        require(msg.sender == entryPoint() || msg.sender == owner, "SmartAccount: not authorized");
        (bool ok1, ) = dest1.call(data1);
        require(ok1, "SmartAccount: first call failed");
        (bool ok2, ) = dest2.call(data2);
        require(ok2, "SmartAccount: second call failed");
        emit Executed(dest1, 0, data1);
        emit Executed(dest2, 0, data2);
    }

    /// @notice Transfer ownership.
    function transferOwnership(address newOwner) external {
        require(msg.sender == owner, "SmartAccount: not owner");
        require(newOwner != address(0), "SmartAccount: zero address");
        emit OwnerTransferred(owner, newOwner);
        owner = newOwner;
    }

    /// @notice Fund the account with ETH (for gas reserve).
    receive() external payable {}

    /// @dev Recover signer address from signature.
    function _recover(bytes32 hash, bytes calldata sig) internal pure returns (address) {
        require(sig.length == 65, "SmartAccount: invalid sig length");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 0x20))
            v := byte(0, calldataload(add(sig.offset, 0x40)))
        }
        // Normalize v
        if (v < 27) v += 27;
        require(v == 27 || v == 28, "SmartAccount: invalid v");
        return ecrecover(hash, v, r, s);
    }

    /// @dev Returns the EntryPoint address (deployed on Base).
    function entryPoint() public view returns (address) {
        // EntryPoint v0.6 on Base mainnet
        return 0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789;
    }

    /// @notice Initialize the account (called once by factory).
    function initialize(address _owner) external {
        require(!initialized, "SmartAccount: already initialized");
        require(_owner != address(0), "SmartAccount: zero owner");
        initialized = true;
        owner = _owner;
        emit AccountInitialized(_owner);
    }

    /// @notice Get USDC balance of this account.
    function usdcBalance(address usdc) external view returns (uint256) {
        return IERC20(usdc).balanceOf(address(this));
    }
}
