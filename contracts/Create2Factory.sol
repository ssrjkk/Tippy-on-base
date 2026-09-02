// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title Create2Factory
/// @notice Deploys per-user EIP-1167 USDC deposit proxies with a fully
///         deterministic address: CREATE2(factory, keccak256(tgId),
///         proxyInitCode[forwarder]).
///
///         The proxy init code follows the canonical EIP-1167 minimal proxy
///         template shared with the bot (bot/create2.py, MINIMAL_PROXY_BYTECODE)
///         so the bot can compute each user's deposit address offline and only
///         needs the factory to materialise it on demand.
///
///         Deploying a user proxy is permissionless and idempotent: with the
///         same salt + same forwarder the CREATE2 address is identical, so a
///         second call is only ever wasted gas, never a second contract.
contract Create2Factory {
    /// @notice The shared forwarder implementation every proxy delegatecalls.
    address public immutable forwarder;

    /// @notice EIP-1167 minimal proxy init code built for `forwarder`.
    ///         3d602d80600a3d3981f3  deploy-time code (copies runtime)
    ///         363d3d373d3d3d363d73  runtime: RETURNDATACOPY delegatecall...
    ///         73<forwarder>          PUSH20 forwarder
    ///         5af43d82803e903d91602b57fd5bf3
    bytes internal constant _PREFIX = hex"3d602d80600a3d3981f3363d3d373d3d3d363d73";
    bytes internal constant _SUFFIX = hex"5af43d82803e903d91602b57fd5bf3";

    event UserProxyDeployed(address indexed proxy, address indexed deployer, uint256 indexed tgId);

    constructor(address forwarder_) {
        require(forwarder_ != address(0), "Create2Factory: zero forwarder");
        forwarder = forwarder_;
    }

    /// @notice The exact EIP-1167 init code proxied proxies are created with.
    function proxyInitCode() public view returns (bytes memory) {
        return bytes.concat(_PREFIX, abi.encodePacked(forwarder), _SUFFIX);
    }

    /// @dev EIP-1167 has no safeCREATE2; creation is pure CREATE2 assembly.
    function _create2Proxy(bytes32 salt) internal returns (address proxy) {
        bytes memory initCode = proxyInitCode();
        assembly ("memory-safe") {
            proxy := create2(0, add(initCode, 0x20), mload(initCode), salt)
        }
        require(proxy != address(0), "Create2Factory: create2 failed");
    }

    /// @notice Create (idempotently) the deposit proxy for a tgId.
    /// @return proxy The proxy address (== the deterministic CREATE2 address).
    function deploy(uint256 tgId) external returns (address proxy) {
        bytes32 salt = keccak256(abi.encodePacked(_uintToString(tgId)));
        proxy = _create2Proxy(salt);
        emit UserProxyDeployed(proxy, msg.sender, tgId);
    }

    /// @notice Deterministic address the proxy for `tgId` would get WITHOUT
    ///         deploying it. Must match bot's _compute_address exactly.
    function predict(uint256 tgId) public view returns (address) {
        bytes32 salt = keccak256(abi.encodePacked(_uintToString(tgId)));
        address fac = address(this);
        bytes memory initCode = proxyInitCode();
        return address(
            uint160(
                uint256(
                    keccak256(
                        abi.encodePacked(
                            bytes1(0xff),
                            fac,
                            salt,
                            keccak256(initCode)
                        )
                    )
                )
            )
        );
    }

    /// @dev Decimal representation of tgId, matched with how the bot salts
    ///      (Web3.keccak(text=str(tg_id)) -> keccak of the decimal string).
    function _uintToString(uint256 v) internal pure returns (string memory) {
        if (v == 0) return "0";
        uint256 len = 0;
        uint256 t = v;
        while (t != 0) {
            len++;
            t /= 10;
        }
        bytes memory b = new bytes(len);
        while (v != 0) {
            len--;
            b[len] = bytes1(uint8(48 + (v % 10)));
            v /= 10;
        }
        return string(b);
    }
}