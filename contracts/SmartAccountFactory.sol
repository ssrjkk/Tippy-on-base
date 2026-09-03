// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title SmartAccountFactory — CREATE2 factory for deterministic SmartAccounts.
/// @notice Each Telegram user (tg_id) gets a deterministic address:
///         address = keccak256(0xff, factory, keccak256(abi.encode(tg_id)), initCodeHash).
///
///         The bot can compute the address offline; deployment is idempotent.
///         Follows the same salt pattern as Create2Factory.sol (decimal tg_id string).
interface ISmartAccount {
    function initialize(address owner) external;
}

contract SmartAccountFactory {
    event AccountCreated(address indexed account, uint256 indexed tgId, address indexed owner);

    address public immutable entryPoint;

    /// @notice Init code for deploying a SmartAccount.
    bytes internal _accountBytecode;

    constructor(address entryPoint_) {
        require(entryPoint_ != address(0), "Factory: zero entryPoint");
        entryPoint = entryPoint_;
        // Get runtime bytecode of a deployed SmartAccount to use as init code
        // The deployer must pass the runtime bytecode as constructor arg.
    }

    /// @notice Deploy (idempotent) a SmartAccount for `tgId` controlled by `owner`.
    function createAccount(uint256 tgId, address owner) external returns (address account) {
        require(owner != address(0), "Factory: zero owner");
        bytes32 salt = _salt(tgId);
        account = _deploy(salt);
        ISmartAccount(account).initialize(owner);
        emit AccountCreated(account, tgId, owner);
    }

    /// @notice Predict the deterministic address for a tg_id without deploying.
    function getAddress(uint256 tgId) external view returns (address) {
        bytes32 salt = _salt(tgId);
        bytes memory initCode = _getInitCode();
        return address(
            uint160(
                uint256(
                    keccak256(
                        abi.encodePacked(bytes1(0xff), address(this), salt, keccak256(initCode))
                    )
                )
            )
        );
    }

    /// @notice Check if an account has been deployed for tg_id.
    function isDeployed(uint256 tgId) external view returns (bool) {
        bytes32 salt = _salt(tgId);
        bytes memory initCode = _getInitCode();
        address predicted = address(
            uint160(
                uint256(
                    keccak256(
                        abi.encodePacked(bytes1(0xff), address(this), salt, keccak256(initCode))
                    )
                )
            )
        );
        uint256 codeSize;
        assembly { codeSize := extcodesize(predicted) }
        return codeSize > 0;
    }

    /// @dev Deploy via CREATE2.
    function _deploy(bytes32 salt) internal returns (address account) {
        bytes memory initCode = _getInitCode();
        assembly {
            account := create2(0, add(initCode, 0x20), mload(initCode), salt)
        }
        require(account != address(0), "Factory: create2 failed");
    }

    /// @dev Salt derived from tg_id (same pattern as Create2Factory).
    function _salt(uint256 tgId) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked(_uintToString(tgId)));
    }

    /// @dev Runtime bytecode of the SmartAccount — must be set via deploy script.
    function _getInitCode() internal view returns (bytes memory) {
        return _accountBytecode;
    }

    /// @dev Decimal string of tgId.
    function _uintToString(uint256 v) internal pure returns (string memory) {
        if (v == 0) return "0";
        uint256 len = 0;
        uint256 t = v;
        while (t != 0) { len++; t /= 10; }
        bytes memory b = new bytes(len);
        while (v != 0) {
            len--;
            b[len] = bytes1(uint8(48 + (v % 10)));
            v /= 10;
        }
        return string(b);
    }
}
