// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title SmartAccountFactory — CREATE2 factory for deterministic SmartAccounts.
/// @notice Each Telegram user (tg_id) gets a deterministic address:
///         address = keccak256(0xff, factory, keccak256(decimal(tgId) || owner), initCodeHash).
///
///         The owner is bound INTO the salt, so the counterfactual address of a
///         user is only claimable by the intended owner. A permissionless caller
///         cannot front-run `createAccount` with its own owner: that would yield
///         a different (harmless) address, never the one the bot advertises as
///         the deposit address. Deployment remains idempotent.
interface ISmartAccount {
    function initialize(address owner) external;
}

contract SmartAccountFactory {
    event AccountCreated(address indexed account, uint256 indexed tgId, address indexed owner);

    address public immutable entryPoint;

    /// @notice Init code for deploying a SmartAccount.
    bytes internal _accountBytecode;

    constructor(address entryPoint_, bytes memory accountBytecode_) {
        require(entryPoint_ != address(0), "Factory: zero entryPoint");
        require(accountBytecode_.length > 0, "Factory: empty account bytecode");
        entryPoint = entryPoint_;
        // Init code = the compiled SmartAccount runtime bytecode, passed by the
        // deploy script so CREATE2 deploys a fresh copy of the account contract.
        _accountBytecode = accountBytecode_;
    }

    /// @notice Deploy (idempotent) a SmartAccount for `tgId` controlled by `owner`.
    ///
    /// @dev The deterministic address binds the owner into the salt: only the
    ///      intended owner's address produces the address an external party would
    ///      fund, so account squatting with a foreign owner is not possible.
    ///      Idempotent: if the exact (tgId, owner) account already exists it is
    ///      returned; the account can only ever be initialized with its owner.
    function createAccount(uint256 tgId, address owner) external returns (address account) {
        require(owner != address(0), "Factory: zero owner");
        account = getAddress(tgId, owner);
        if (account.code.length > 0) {
            // Already deployed for this (tgId, owner) — return idempotently.
            return account;
        }
        account = _deploy(_salt(tgId, owner));
        require(account == getAddress(tgId, owner), "Factory: address mismatch");
        ISmartAccount(account).initialize(owner);
        emit AccountCreated(account, tgId, owner);
    }

    /// @notice Predict the deterministic address for a tg_id + owner without deploying.
    function getAddress(uint256 tgId, address owner) public view returns (address) {
        bytes32 salt = _salt(tgId, owner);
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

    /// @notice Check if an account has been deployed for tg_id + owner.
    function isDeployed(uint256 tgId, address owner) external view returns (bool) {
        return getAddress(tgId, owner).code.length > 0;
    }

    /// @dev Deploy via CREATE2.
    function _deploy(bytes32 salt) internal returns (address account) {
        bytes memory initCode = _getInitCode();
        assembly {
            account := create2(0, add(initCode, 0x20), mload(initCode), salt)
        }
        require(account != address(0), "Factory: create2 failed");
    }

    /// @dev Salt derived from tg_id AND owner: the counterfactual address can
    ///      only be claimed by the intended owner (see createAccount notes).
    function _salt(uint256 tgId, address owner) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked(_uintToString(tgId), owner));
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
