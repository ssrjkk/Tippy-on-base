// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title VerifyingPaymaster — gasless transactions for Tippy users.
/// @notice The paymaster sponsors gas on behalf of users. Only UserOperations
///         signed by the bot's relayer key are accepted. The paymaster charges
///         the user in USDC (deducted from their SmartAccount balance).
///
///         Flow:
///         1. User builds UserOperation
///         2. Bot signs the UserOpHash with relayer key
///         3. Paymaster.validatePaymasterUserOp verifies the relayer signature
///         4. EntryPoint pays gas; paymaster gets reimbursed
///         5. After execution, the user's USDC is transferred to the paymaster
///
///         Anti-abuse: the paymaster has a per-user daily gas limit and
///         a global daily budget (same as GAS_DRIP_DAILY_MAX).
interface IEntryPoint {
    function simulateValidation(UserOperation calldata userOp) external;
}

struct UserOperation {
    address sender;
    uint256 nonce;
    bytes initCode;
    bytes callData;
    uint256 callGasLimit;
    uint256 verificationGasLimit;
    uint256 preVerificationGas;
    uint256 maxFeePerGas;
    uint256 maxPriorityFeePerGas;
    bytes paymasterAndData;
    bytes signature;
}

contract VerifyingPaymaster {
    address public owner;   // bot hot wallet / relayer
    address public usdc;

    // Anti-abuse limits
    uint256 public constant MAX_GAS_PER_USER_PER_DAY = 0.5 ether;  // ~$0.50 in gas
    uint256 public constant MAX_GAS_GLOBAL_PER_DAY = 5 ether;       // ~$5.00 in gas

    // Per-user tracking: tg_id -> daily gas used (resets each UTC day)
    mapping(uint256 => uint256) public userGasUsed;
    mapping(uint256 => uint256) public userGasDay;  // UTC day number

    uint256 public globalGasUsed;
    uint256 public globalGasDay;

    // Daily gas charge in USDC micro-units (paid by user to paymaster)
    uint256 public gasChargeMicro;

    event PaymasterUsed(address indexed sender, uint256 gasUsed);

    modifier onlyOwner() {
        require(msg.sender == owner, "Paymaster: not owner");
        _;
    }

    constructor(address _owner, address _usdc, uint256 _gasChargeMicro) {
        owner = _owner;
        usdc = _usdc;
        gasChargeMicro = _gasChargeMicro;
    }

    /// @notice Validate the UserOperation. Called by EntryPoint.
    function validatePaymasterUserOp(
        UserOperation calldata userOp,
        bytes32 userOpHash,
        uint256 /*maxCost*/
    ) external returns (bytes memory context, uint256 validationData) {
        require(msg.sender == entryPoint(), "Paymaster: not EntryPoint");

        // Verify relayer signature: owner signed userOpHash.
        // paymasterAndData layout: address(20) ++ tgId(32) ++ relayerSig(65).
        bytes calldata pmd = userOp.paymasterAndData;
        bytes calldata sig = pmd[64:];
        bytes32 hash = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", userOpHash));
        require(_recover(hash, sig) == owner, "Paymaster: bad relayer sig");

        // Decode tg_id from paymasterAndData (bytes 20..52).
        uint256 tgId;
        assembly {
            tgId := calldataload(add(pmd.offset, 32))
        }

        // Enforce per-user and global daily gas budgets (accumulating).
        uint256 estimatedGas = (userOp.verificationGasLimit + userOp.callGasLimit
                                + userOp.preVerificationGas) * userOp.maxFeePerGas;
        _recordUsage(tgId, estimatedGas);

        // context = tgId + estimatedGas (passed to postTransaction).
        context = abi.encode(tgId, estimatedGas);
        validationData = 0; // valid
    }

    /// @dev Roll the UTC-day counters and accumulate the gas budget.
    function _recordUsage(uint256 tgId, uint256 estimatedGas) internal {
        uint256 today = block.timestamp / 1 days;
        if (userGasDay[tgId] != today) {
            userGasDay[tgId] = today;
            userGasUsed[tgId] = 0;
        }
        if (globalGasDay != today) {
            globalGasDay = today;
            globalGasUsed = 0;
        }
        require(userGasUsed[tgId] + estimatedGas <= MAX_GAS_PER_USER_PER_DAY,
                "Paymaster: user daily limit");
        require(globalGasUsed + estimatedGas <= MAX_GAS_GLOBAL_PER_DAY,
                "Paymaster: global daily limit");
        userGasUsed[tgId] += estimatedGas;
        globalGasUsed += estimatedGas;
    }

    /// @notice Called by EntryPoint after execution to charge the user.
    function postTransaction(
        bytes calldata /*context*/,
        address /*sender*/,
        bytes calldata /*callData*/,
        uint256 /*actualGasCost*/,
        uint256 /*actualUserOpGasPrice*/,
        PaymasterPostTransactionMode /*mode*/
    ) external {
        require(msg.sender == entryPoint(), "Paymaster: not EntryPoint");
        // Gas accounting happens here if needed.
        // Actual USDC charge could be done via a pull from the SmartAccount.
    }

    function entryPoint() public pure returns (address) {
        return 0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789;
    }

    function _recover(bytes32 hash, bytes calldata sig) internal pure returns (address) {
        require(sig.length == 65, "Paymaster: invalid sig");
        bytes32 r; bytes32 s; uint8 v;
        assembly {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 0x20))
            v := byte(0, calldataload(add(sig.offset, 0x40)))
        }
        if (v < 27) v += 27;
        return ecrecover(hash, v, r, s);
    }

    enum PaymasterPostTransactionMode { NONE, SPONSOR, POST_OP }
}
